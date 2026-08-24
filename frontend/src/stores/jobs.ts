import { defineStore } from 'pinia'
import { reactive } from 'vue'
import type { Job } from '@/api/types'

let source: EventSource | null = null

export const useJobStore = defineStore('jobs', () => {
  const jobs = reactive(new Map<number, Job>())
  let connected = false

  function connect() {
    if (connected) return
    connected = true
    source = new EventSource('/api/jobs/stream')
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as { type: string; job: Job }
        if (payload.type === 'job') jobs.set(payload.job.id, payload.job)
      } catch {}
    }
  }

  function byId(id: number): Job | null {
    return jobs.get(id) ?? null
  }

  return { jobs, connect, byId }
})
