import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import { api } from '@/api/client'
import type { Job } from '@/api/types'
import { JobState } from './jobState'

export const useJobStore = defineStore('jobs', () => {
  const jobs = reactive(new Map<number, Job>())
  const state = new JobState(jobs)
  const syncError = ref('')
  const scopes = new Map<number, number>()
  let source: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let generation = 0

  async function refresh(projectId: number) {
    try {
      await state.refresh(
        projectId,
        () => api.get<Job[]>(`/jobs?project_id=${projectId}&limit=200`, { signal: AbortSignal.timeout(10000) }),
        id => api.get<Job>(`/jobs/${id}`, { signal: AbortSignal.timeout(10000) }),
      )
      syncError.value = ''
    } catch {
      syncError.value = 'Job updates are unavailable. Retrying automatically; work may still be running.'
    }
  }

  async function refreshAll() {
    await Promise.all([...scopes.keys()].map(refresh))
  }

  async function poll(epoch: number) {
    await refreshAll()
    if (scopes.size && epoch === generation) timer = setTimeout(() => { void poll(epoch) }, 3000)
  }

  function connect(projectId: number) {
    scopes.set(projectId, (scopes.get(projectId) ?? 0) + 1)
    if (source === null) {
      source = new EventSource('/api/jobs/stream')
      source.onopen = () => { void refreshAll() }
      source.onmessage = event => {
        try {
          const payload = JSON.parse(event.data) as { type: string; job: Job }
          if (payload.type === 'job' && scopes.has(payload.job.project_id!)) state.merge(payload.job)
        } catch { /* A bad event must not stop durable-state polling. */ }
      }
      void poll(++generation)
    } else {
      void refresh(projectId)
    }
  }

  function disconnect(projectId: number) {
    const count = scopes.get(projectId) ?? 0
    if (count > 1) scopes.set(projectId, count - 1)
    else scopes.delete(projectId)
    if (!scopes.size) {
      generation++
      source?.close()
      source = null
      if (timer !== null) clearTimeout(timer)
      timer = null
    }
  }

  async function track(id: number) {
    try {
      state.merge(await api.get<Job>(`/jobs/${id}`, { signal: AbortSignal.timeout(10000) }))
    } catch {
      syncError.value = 'The job was accepted. Waiting to recover its status…'
    }
  }

  async function cancel(id: number) {
    state.merge(await api.post<Job>(`/jobs/${id}/cancel`))
  }

  return {
    jobs, syncError, connect, disconnect, track, cancel,
    latest: (projectId: number, kinds: string[]) => state.latest(projectId, kinds),
    observe: (projectId: number, terminal: (job: Job) => void) => state.observe(projectId, terminal),
  }
})
