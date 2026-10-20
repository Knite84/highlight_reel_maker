import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'
import { createPinia, setActivePinia } from 'pinia'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'

let server, useJobStore
const originalFetch = globalThis.fetch
const originalEventSource = globalThis.EventSource
const streams = []
const calls = []
let snapshot = []
let offline = false
let cancelResponse = null

const row = (overrides = {}) => ({
  id: 1, project_id: 7, kind: 'plan', revision: 1, status: 'running',
  progress: 0.2, done: 1, total: 5, message: 'Working', error: null,
  result_json: null, created_at: '', started_at: '', finished_at: null, ...overrides,
})

before(async () => {
  server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  ;({ useJobStore } = await server.ssrLoadModule('/src/stores/jobs.ts'))
  globalThis.EventSource = class {
    closed = false
    constructor() { streams.push(this) }
    close() { this.closed = true }
  }
  globalThis.fetch = async (url, options = {}) => {
    calls.push(url)
    if (offline) throw new Error('Offline')
    const data = url.endsWith('/cancel') ? cancelResponse
      : url.includes('?') ? snapshot.filter(job => url.includes(`project_id=${job.project_id}&`))
      : snapshot.find(job => url.endsWith(`/${job.id}`))
    assert.ok(options.signal || url.endsWith('/cancel'), 'status requests must have a timeout')
    return new Response(JSON.stringify(data), { status: 200 })
  }
})

after(async () => {
  globalThis.fetch = originalFetch
  globalThis.EventSource = originalEventSource
  await server?.close()
})

// Vite loads the actual Pinia store, including transports, with no browser/model dependency.
test('store hydrates, refreshes on reconnect, reports failure once, and closes the stream', async () => {
  setActivePinia(createPinia())
  const store = useJobStore()
  const seen = []
  const leave = store.observe(7, job => seen.push(job.status))
  snapshot = [row()]
  store.connect(7)
  try {
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(store.latest(7, ['plan']).status, 'running')
    const stream = streams.at(-1)
    snapshot = [row({ revision: 3, status: 'failed', error: 'Load model' })]
    stream.onopen()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(store.latest(7, ['plan']).error, 'Load model')
    assert.deepEqual(seen, ['failed'])
    stream.onmessage({ data: JSON.stringify({ type: 'job', job: row({ revision: 2 }) }) })
    assert.equal(store.latest(7, ['plan']).status, 'failed')
    stream.onopen()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(seen, ['failed'])
    stream.onmessage({ data: 'broken event' })
  } finally {
    leave()
    store.disconnect(7)
  }
  assert.equal(streams.at(-1).closed, true)
})

test('store exposes an outage and recovers on reconnect; accepted cancel remains active until terminal', async () => {
  setActivePinia(createPinia())
  const store = useJobStore()
  offline = true
  store.connect(7)
  try {
    await new Promise(resolve => setImmediate(resolve))
    assert.match(store.syncError, /unavailable/)
    offline = false
    snapshot = [row()]
    streams.at(-1).onopen()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(store.syncError, '')
    cancelResponse = row()
    await store.cancel(1)
    assert.equal(store.latest(7, ['plan']).status, 'running')
    streams.at(-1).onmessage({ data: JSON.stringify({ type: 'job', job: row({ revision: 2, status: 'cancelled' }) }) })
    assert.equal(store.latest(7, ['plan']).status, 'cancelled')
  } finally { offline = false; store.disconnect(7) }
})

test('scheduled polling finishes a hydrated job with no SSE; leaving stops polling', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  setActivePinia(createPinia())
  const store = useJobStore()
  const terminal = []
  store.observe(7, job => terminal.push(job.status))
  snapshot = [row()]
  store.connect(7)
  try {
    await new Promise(resolve => setImmediate(resolve))
    snapshot = [row({ revision: 4, status: 'done' })]
    t.mock.timers.tick(3000)
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(terminal, ['done'])
  } finally { store.disconnect(7) }
  const count = calls.length
  t.mock.timers.tick(30000)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls.length, count)
})

async function projectHtml(jobs) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useJobStore()
  for (const job of jobs) store.jobs.set(job.id, job)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  queryClient.setQueryData(['project', 7], { id: 7, name: 'QA project', media_path: 'C:/Media', files_total: 2 })
  for (const key of ['files', 'tags', 'plans', 'scene-search']) queryClient.setQueryData([key, 7], [])
  queryClient.setQueryData(['system'], {})
  const { default: view } = await server.ssrLoadModule('/src/views/ProjectView.vue')
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/projects/:id', component: view }] })
  await router.push('/projects/7')
  const app = createSSRApp(view)
  app.use(pinia).use(router).use(VueQueryPlugin, { queryClient })
  try { return await renderToString(app) }
  finally { queryClient.clear() }
}

test('project template displays failed planning and structured incomplete analysis', async () => {
  const html = await projectHtml([
    row({ status: 'failed', error: 'Planner unavailable' }),
    row({ id: 2, kind: 'analyze', status: 'failed', error: 'Analysis incomplete', result_json: JSON.stringify({
      metadata: 1, embedded: 0,
      coverage: { files_current: 1, files_total: 2, files_failed: 1, scenes_current: 1, scenes_embedded: 0, scenes_described: 0 },
      errors: [{ file: 'broken.jpg', error: 'Cannot decode' }],
    }) }),
  ])
  assert.match(html, /Planner unavailable/)
  assert.match(html, /retry Generate plan/)
  assert.match(html, /broken.jpg: Cannot decode/)
  assert.match(html, /0\/1 with embeddings/)
  assert.match(html, /cached work is reused/)
  assert.match(html, /retry Analyze/)
  assert.doesNotMatch(html, /Contacting planner/)
})

test('project template exposes cancel and disables new operations while recovered work runs', async () => {
  const html = await projectHtml([row({ kind: 'analyze' })])
  assert.match(html, />Cancel<\/button>/)
  assert.match(html, /role="progressbar"/)
  assert.match(html, /<button disabled>Analyze<\/button>/)
  assert.match(html, /<button disabled>Generate plan<\/button>/)
})

test('render review template discloses tradeoffs before its render action', async () => {
  const { default: card } = await server.ssrLoadModule('/src/components/RenderReview.vue')
  const review = {
    target_duration_sec: 45, expected_duration_sec: 45, profile: 'proxy', token: 'reviewed',
    sequencing: 'narrative', sequencing_evidence: 'Use narrative order',
    warnings: ['Frozen end frames add 12.0s to retain the requested duration.', '2 additional scenes were added for duration.'],
  }
  const html = await renderToString(createSSRApp(card, { review, disabled: false }))
  assert.match(html, /Requested: 45.0s/)
  assert.match(html, /Narrative order from your brief/)
  assert.match(html, /Brief directive: Use narrative order/)
  assert.ok(html.indexOf('Frozen end frames') < html.indexOf('Render this plan'))
  assert.match(html, /2 additional scenes/)
})
