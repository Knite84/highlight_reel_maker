import type { Job } from '../api/types'

export const isActiveJob = (job: Job) => job.status === 'queued' || job.status === 'running'

type Observer = { projectId: number; seen: Set<number>; terminal: (job: Job) => void }

// Both HTTP snapshots and SSE must pass through this revision-checked merge.
export class JobState {
  private observers = new Set<Observer>()

  readonly jobs: Map<number, Job>

  constructor(jobs: Map<number, Job>) { this.jobs = jobs }

  merge(job: Job) {
    const previous = this.jobs.get(job.id)
    if (previous && previous.revision >= job.revision) return
    // Terminal jobs are not replayed; late progress must never revive one.
    if (previous && !isActiveJob(previous) && isActiveJob(job)) return
    this.jobs.set(job.id, job)
    for (const observer of this.observers) this.notify(observer, job)
  }

  private notify(observer: Observer, job: Job) {
    if (job.project_id !== observer.projectId || isActiveJob(job) || observer.seen.has(job.id)) return
    observer.seen.add(job.id)
    observer.terminal(job)
  }

  observe(projectId: number, terminal: (job: Job) => void) {
    const observer = { projectId, terminal, seen: new Set<number>() }
    this.observers.add(observer)
    for (const job of this.jobs.values()) this.notify(observer, job)
    return () => this.observers.delete(observer)
  }

  latest(projectId: number, kinds: string[]) {
    const matches = [...this.jobs.values()]
      .filter(job => job.project_id === projectId && kinds.includes(job.kind))
      .sort((a, b) => Number(isActiveJob(b)) - Number(isActiveJob(a)) || b.id - a.id)
    return matches[0] ?? null
  }

  async refresh(projectId: number, list: () => Promise<Job[]>, get: (id: number) => Promise<Job>) {
    const rows = await list()
    for (const job of rows) this.merge(job)
    const returned = new Set(rows.map(job => job.id))
    // A previously active job may finish and fall outside the history window.
    const missing = [...this.jobs.values()].filter(job =>
      job.project_id === projectId && isActiveJob(job) && !returned.has(job.id),
    )
    await Promise.all(missing.map(async job => this.merge(await get(job.id))))
  }
}
