import assert from 'node:assert/strict'
import { test } from 'node:test'
import { JobState } from '../src/stores/jobState.ts'

const row = (overrides = {}) => ({
  id: 1, project_id: 7, kind: 'analyze', revision: 1, status: 'running',
  progress: 0.2, done: 1, total: 5, message: 'Working', error: null,
  result_json: null, created_at: '', started_at: '', finished_at: null, ...overrides,
})
const unexpectedGet = () => { throw new Error('Unexpected detail request') }

test('reload hydrates active jobs without any SSE event or local job ID', async () => {
  const state = new JobState(new Map())
  await state.refresh(7, async () => [row()], unexpectedGet)
  assert.equal(state.latest(7, ['analyze']).status, 'running')
})

for (const status of ['done', 'failed', 'cancelled']) {
  test(`disconnected SSE: polling reports ${status} once to the terminal refresh handler`, async () => {
    const state = new JobState(new Map())
    const notifications = []
    state.observe(7, job => notifications.push(job))
    state.merge(row())
    const terminal = row({ revision: 3, status, error: status === 'failed' ? 'Load Unsloth' : null })
    await state.refresh(7, async () => [terminal], unexpectedGet)
    state.merge(terminal) // SSE reconnect replay / duplicate poll.
    state.merge(row({ revision: 2 })) // Late HTTP response.
    assert.equal(state.latest(7, ['analyze']).status, status)
    assert.deepEqual(notifications, [terminal])
    if (status === 'failed') assert.equal(notifications[0].error, 'Load Unsloth')
  })
}

test('late snapshot cannot roll back newer SSE progress', async () => {
  const state = new JobState(new Map())
  let resolve
  const pending = state.refresh(7, () => new Promise(done => { resolve = done }), unexpectedGet)
  state.merge(row({ revision: 5, done: 4, progress: 0.8 }))
  resolve([row({ revision: 2 })])
  await pending
  assert.equal(state.jobs.get(1).done, 4)
})

test('reconnect snapshot finds missed jobs, including failed plans and retained summary', async () => {
  const state = new JobState(new Map())
  const seen = []
  state.observe(7, job => seen.push(job.id))
  await state.refresh(7, async () => [
    row({ id: 2, kind: 'plan', status: 'failed', error: 'Planner unavailable' }),
    row({ status: 'failed', result_json: '{"errors":[{"file":"broken.jpg","error":"decode"}]}' }),
  ], unexpectedGet)
  assert.deepEqual(seen, [2, 1])
  assert.equal(state.latest(7, ['plan']).error, 'Planner unavailable')
  assert.equal(JSON.parse(state.latest(7, ['analyze']).result_json).errors[0].file, 'broken.jpg')
})

test('switching projects isolates handlers and re-entry refreshes retained terminal jobs', () => {
  const state = new JobState(new Map())
  const first = [], second = [], returned = []
  const leave = state.observe(7, job => first.push(job.id))
  state.merge(row())
  leave()
  state.observe(8, job => second.push(job.id))
  state.merge(row({ revision: 2, status: 'done' }))
  state.merge(row({ id: 2, project_id: 8, status: 'cancelled' }))
  state.observe(7, job => returned.push(job.id))
  assert.deepEqual(first, [])
  assert.deepEqual(second, [2])
  assert.deepEqual(returned, [1])
})

test('a second operation takes precedence; unrelated project and kind cannot hide it', () => {
  const state = new JobState(new Map())
  state.merge(row({ status: 'done' }))
  state.merge(row({ id: 2, status: 'queued' }))
  state.merge(row({ id: 3, kind: 'render' }))
  state.merge(row({ id: 4, project_id: 8 }))
  assert.equal(state.latest(7, ['analyze']).id, 2)
  assert.equal(state.latest(7, ['render']).id, 3)
})

test('active jobs falling out of the bounded snapshot get a detail refresh', async () => {
  const state = new JobState(new Map())
  state.merge(row())
  const requested = []
  await state.refresh(7, async () => [row({ id: 300 })], async id => {
    requested.push(id)
    return row({ revision: 9, status: 'done' })
  })
  assert.deepEqual(requested, [1])
  assert.equal(state.jobs.get(1).status, 'done')
})

test('HTTP failure retains known state and later polling recovers', async () => {
  const state = new JobState(new Map())
  state.merge(row())
  await assert.rejects(state.refresh(7, async () => { throw new Error('Offline') }, unexpectedGet))
  assert.equal(state.jobs.get(1).status, 'running')
  await state.refresh(7, async () => [row({ revision: 2, status: 'done' })], unexpectedGet)
  assert.equal(state.jobs.get(1).status, 'done')
})
