<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/client'
import type {
  Job,
  MediaFile,
  Project,
  ReelPlan,
  ReelPlanDetail,
  RenderReview,
  SceneResult,
  SystemStatus,
  TagCount,
} from '@/api/types'
import PlanPreview from '@/components/PlanPreview.vue'
import RenderReviewCard from '@/components/RenderReview.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useJobStore } from '@/stores/jobs'
import { isActiveJob } from '@/stores/jobState'

type ProjectDetail = Project & { files_total: number }

const route = useRoute()
const projectId = Number(route.params.id)
const queryClient = useQueryClient()
const jobStore = useJobStore()

let stopObserving: (() => void) | null = null
let refreshScheduled = false
onMounted(() => {
  stopObserving = jobStore.observe(projectId, () => {
    // Snapshots may contain many terminal jobs; refresh once per batch.
    if (refreshScheduled) return
    refreshScheduled = true
    queueMicrotask(() => {
      refreshScheduled = false
      void refreshAfterJob()
    })
  })
  jobStore.connect(projectId)
})
onBeforeUnmount(() => {
  stopObserving?.()
  jobStore.disconnect(projectId)
})

const { data: project } = useQuery({
  queryKey: ['project', projectId],
  queryFn: () => api.get<ProjectDetail>(`/projects/${projectId}`),
})

const filesQuery = useQuery({
  queryKey: ['files', projectId],
  queryFn: () => api.get<MediaFile[]>(`/projects/${projectId}/files`),
})

const { data: systemStatus } = useQuery({
  queryKey: ['system'],
  queryFn: () => api.get<SystemStatus>('/system/status'),
})

const actionError = ref('')
const busyAction = ref<'analyze' | null>(null)
const deepVerify = ref(false)
const cancelling = ref<number | null>(null)
const topJob = computed(() => jobStore.latest(projectId, ['scan', 'analyze']))
const planJob = computed(() => jobStore.latest(projectId, ['plan']))
const activeRenderJob = computed(() => jobStore.latest(projectId, ['render']))
const projectBusy = computed(() => [...jobStore.jobs.values()].some(
  job => job.project_id === projectId && isActiveJob(job),
))
const jobCards = computed(() => [topJob.value, planJob.value, activeRenderJob.value].filter(
  (job): job is Job => job !== null,
))

function jobPercent(job: Job) {
  const fraction = job.total && job.total > 0 ? job.done / job.total : job.progress
  return Math.max(0, Math.min(100, Math.round(fraction * 100)))
}

function jobResult(job: Job): Record<string, unknown> | null {
  try {
    const result = JSON.parse(job.result_json ?? 'null')
    return result && typeof result === 'object' && !Array.isArray(result) ? result : null
  } catch { return null }
}

function resultCounts(job: Job): string {
  const result = jobResult(job)
  if (!result) return ''
  const labels: Record<string, string> = {
    videos: 'videos', photos: 'photos', removed: 'removed from inventory',
    metadata: 'metadata files', scenes: 'scenes', visuals: 'visuals',
    embedded: 'embeddings', described: 'descriptions',
  }
  return Object.entries(labels).filter(([key]) => typeof result[key] === 'number')
    .map(([key, label]) => `${result[key]} ${label}`).join(' · ')
}

function resultErrors(job: Job): { file: string; error: string }[] {
  const errors = jobResult(job)?.errors
  if (!Array.isArray(errors)) return []
  return errors.filter(entry => entry && typeof entry.error === 'string')
}

function coverageLabel(job: Job): string {
  const coverage = jobResult(job)?.coverage as Record<string, number> | undefined
  if (!coverage) return ''
  return `Stored knowledge for current sources: ${coverage.files_current}/${coverage.files_total} files; ` +
    `${coverage.scenes_current} scenes; ${coverage.scenes_embedded}/${coverage.scenes_current} with embeddings; ` +
    `${coverage.scenes_described}/${coverage.scenes_current} with descriptions. ${coverage.files_failed} files report errors.`
}

async function cancelJob(id: number) {
  cancelling.value = id
  actionError.value = ''
  try { await jobStore.cancel(id) }
  catch (e) { actionError.value = e instanceof Error ? e.message : String(e) }
  finally { cancelling.value = null }
}

async function refreshAfterJob() {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['projects'] }),
    ...['files', 'project', 'tags', 'scene-search', 'plans', 'plan-detail'].map(key =>
      queryClient.invalidateQueries({ queryKey: [key, projectId] }),
    ),
  ])
}

async function runAnalysis() {
  busyAction.value = 'analyze'
  actionError.value = ''
  try {
    const result = await api.post<{ job_id: number }>(`/projects/${projectId}/analyze?deep_verify=${deepVerify.value}`)
    await jobStore.track(result.job_id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busyAction.value = null
  }
}

const promptText = ref('')
const targetDuration = ref(45)
const generating = ref(false)

async function generatePlan() {
  generating.value = true
  actionError.value = ''
  try {
    const result = await api.post<{ job_id: number }>(`/projects/${projectId}/plans`, {
      prompt: promptText.value,
      target_duration_sec: targetDuration.value,
    })
    await jobStore.track(result.job_id)
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  } finally {
    generating.value = false
  }
}

const plansQuery = useQuery({
  queryKey: ['plans', projectId],
  queryFn: () => api.get<ReelPlan[]>(`/projects/${projectId}/plans`),
  refetchInterval: (query) =>
    (query.state.data ?? []).some((reel) => reel.status === 'rendering') ? 4000 : false,
})

const expandedId = ref<number | null>(null)

const planDetailQuery = useQuery({
  queryKey: ['plan-detail', projectId, expandedId],
  queryFn: () => api.get<ReelPlanDetail>(`/projects/${projectId}/plans/${expandedId.value}`),
  enabled: computed(() => expandedId.value !== null),
})

function toggleExpanded(id: number) {
  expandedId.value = expandedId.value === id ? null : id
}

function persistedFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function persistFlag(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? '1' : '0')
  } catch {}
}

const showFiles = ref(persistedFlag(`reelmaker:${projectId}:files-open`))
const showScenes = ref(persistedFlag(`reelmaker:${projectId}:scenes-open`))
watch(showFiles, (v) => persistFlag(`reelmaker:${projectId}:files-open`, v))
watch(showScenes, (v) => persistFlag(`reelmaker:${projectId}:scenes-open`, v))
const reelList = computed(() => plansQuery.data.value ?? [])
const activePlanDetail = computed(() => planDetailQuery.data.value ?? null)

async function renderPlan(id: number) {
  actionError.value = ''
  pendingRender.value = null
  reviewingReelId.value = id
  try {
    const review = await api.get<RenderReview>(`/projects/${projectId}/plans/${id}/render-review?profile=proxy`)
    pendingRender.value = { id, review }
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  } finally { reviewingReelId.value = null }
}

const pendingRender = ref<{ id: number; review: RenderReview } | null>(null)
const reviewingReelId = ref<number | null>(null)
const submittingRender = ref(false)

async function confirmRender() {
  const selected = pendingRender.value
  if (!selected) return
  const id = selected.id
  submittingRender.value = true
  actionError.value = ''
  renderingReelId.value = id
  try {
    const result = await api.post<{ job_id: number }>(
      `/projects/${projectId}/plans/${id}/render`,
      { profile: selected.review.profile, review_token: selected.review.token },
    )
    await jobStore.track(result.job_id)
    pendingRender.value = null
  } catch (e) {
    renderingReelId.value = null
    actionError.value = e instanceof Error ? e.message : String(e)
    pendingRender.value = null
  } finally { submittingRender.value = false }
}

const renderingReelId = ref<number | null>(null)

const renderInProgress = computed(() => activeRenderJob.value !== null && isActiveJob(activeRenderJob.value))
const renderPercent = computed(() => activeRenderJob.value ? jobPercent(activeRenderJob.value) : 0)
watch(activeRenderJob, current => {
  if (current && !isActiveJob(current)) renderingReelId.value = null
})

async function refreshStatus() {
  try {
    const fresh = await api.post<SystemStatus>('/system/refresh')
    queryClient.setQueryData(['system'], fresh)
  } catch {}
}

const searchInput = ref('')
const activeTag = ref<string | null>(null)

const tagsQuery = useQuery({
  queryKey: ['tags', projectId],
  queryFn: () => api.get<TagCount[]>(`/projects/${projectId}/tags`),
})

const scenesQuery = useQuery({
  queryKey: ['scene-search', projectId, searchInput, activeTag],
  queryFn: () => {
    const params = new URLSearchParams()
    if (searchInput.value.trim()) params.set('q', searchInput.value.trim())
    if (activeTag.value) params.set('tag', activeTag.value)
    const qs = params.toString()
    return api.get<SceneResult[]>(
      `/projects/${projectId}/scenes/search${qs ? `?${qs}` : ''}`,
    )
  },
})

function formatTime(seconds: number): string {
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

function formatDateTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function durationLabel(reel: ReelPlan): string {
  const seconds = reel.rendered_duration_sec ?? reel.target_duration_sec
  return `${Math.round(seconds)}s`
}

function renderLabel(reel: ReelPlan): string {
  if (reel.status === 'rendering' || (renderInProgress.value && renderingReelId.value === reel.id)) {
    return 'Rendering…'
  }
  if (reel.status === 'failed') return 'Retry render'
  if (reel.status === 'rendered') return 'Re-render'
  return 'Render'
}

function onDownloadReel() {
  window.setTimeout(() => {
    void queryClient.invalidateQueries({ queryKey: ['plans', projectId] })
  }, 1500)
}

function thumbUrl(sceneId: number): string {
  return `/api/projects/${projectId}/thumbs/${sceneId}`
}

const sceneResults = computed(() => scenesQuery.data.value ?? [])
const tagList = computed(() => tagsQuery.data.value ?? [])
const fileList = computed(() => filesQuery.data.value ?? [])
</script>

<template>
  <section v-if="project">
    <div class="page-head">
      <h1>{{ project.name }}</h1>
      <div class="actions">
        <label title="Reads every source file to detect changes that preserve file size and modification time. Unchanged analysis is reused.">
          <input v-model="deepVerify" type="checkbox" :disabled="busyAction !== null || projectBusy" /> Verify file contents
        </label>
        <button :disabled="busyAction !== null || projectBusy" @click="runAnalysis">
          {{ busyAction === 'analyze' ? 'Analyzing…' : 'Analyze' }}
        </button>
      </div>
    </div>
    <p class="muted">{{ project.media_path }}</p>

    <div class="status-row status-inline">
      <StatusPill compact label="ffmpeg" :ok="!!systemStatus?.tools?.ffmpeg" />
      <StatusPill compact label="ffprobe" :ok="!!systemStatus?.tools?.ffprobe" />
      <StatusPill compact label="exiftool" :ok="!!systemStatus?.tools?.exiftool" />
      <StatusPill compact label="GPU" :ok="!!systemStatus?.gpu?.ok" :detail="systemStatus?.gpu?.name ?? ''" />
      <StatusPill compact label="NVENC" :ok="!!systemStatus?.nvenc?.ok" />
      <StatusPill
        compact
        label="Unsloth"
        :ok="!!systemStatus?.unsloth?.ok"
        :detail="systemStatus?.unsloth?.error ?? ''"
      />
      <button class="ghost refresh-link" @click="refreshStatus">Refresh</button>
    </div>

    <p v-if="jobStore.syncError" role="status" class="muted">{{ jobStore.syncError }}</p>
    <div v-for="current in jobCards" :key="current.id" class="card progress" aria-live="polite">
      <div class="progress-head">
        <span>{{ current.kind }} — {{ current.status }}</span>
        <span v-if="isActiveJob(current)">{{ current.done }}/{{ current.total ?? '?' }}</span>
        <button v-if="isActiveJob(current)" :disabled="cancelling === current.id" @click="cancelJob(current.id)">
          {{ cancelling === current.id ? 'Requesting cancellation…' : 'Cancel' }}
        </button>
      </div>
      <div v-if="isActiveJob(current)" class="bar" role="progressbar" :aria-label="current.kind" :aria-valuenow="jobPercent(current)" aria-valuemin="0" aria-valuemax="100">
        <div class="bar-fill" :style="{ width: jobPercent(current) + '%' }" />
      </div>
      <p v-if="isActiveJob(current)" class="muted">{{ current.message }}</p>
      <p v-if="current.error" class="error">{{ current.error }}</p>
      <template v-if="!isActiveJob(current) && (current.kind === 'analyze' || current.kind === 'scan')">
        <p v-if="coverageLabel(current)" class="muted">{{ coverageLabel(current) }}</p>
        <p v-if="resultCounts(current)" class="muted">{{ current.kind === 'analyze' ? 'Work completed in this run (cached work is reused):' : 'Inventory:' }} {{ resultCounts(current) }}</p>
        <details v-if="resultErrors(current).length">
          <summary>{{ resultErrors(current).length }} analysis issue(s)</summary>
          <ul><li v-for="(issue, index) in resultErrors(current)" :key="index">{{ issue.file || 'Analysis service' }}: {{ issue.error }}</li></ul>
        </details>
        <p v-if="current.status === 'failed' || current.status === 'cancelled'" class="muted">
          Analysis may be incomplete. Successful cached stages are retained. Correct the reported issue, ensure Unsloth is loaded if descriptions failed, then retry Analyze.
        </p>
      </template>
      <p v-if="current.kind === 'plan' && (current.status === 'failed' || current.status === 'cancelled')" class="muted">Check the reported issue and the loaded Unsloth model, then retry Generate plan.</p>
    </div>

    <p v-if="actionError" class="error">{{ actionError }}</p>

    <h2>Generate reel</h2>
    <p class="muted">Chronological by default. To change sequencing, explicitly request “narrative order”, “reverse chronological order”, or flashbacks in your brief.</p>
    <div class="card form plan-form">
      <textarea
        v-model="promptText"
        class="plan-prompt"
        rows="2"
        placeholder="Describe your reel… e.g. 'Chill sunset montage with the best scenery'"
      />
      <label class="muted duration-label">
        Length (seconds)
        <input v-model.number="targetDuration" type="number" min="10" max="600" />
      </label>
      <button
        :disabled="generating || projectBusy || !promptText.trim()"
        @click="generatePlan"
      >
        {{ generating ? 'Sending…' : 'Generate plan' }}
      </button>
      <p
        v-if="!filesQuery.isLoading.value && fileList.every(file => !file.scene_count)"
        class="muted"
      >
        No indexed scenes yet — run Analyze first for best results.
      </p>
    </div>

    <h2>Reels</h2>
    <div v-if="reelList.length === 0" class="muted">
      No reels yet. Generate a plan above.
    </div>
    <div v-else class="reel-grid">
      <div class="reel-head">
        <span>Reel</span>
        <span>Render</span>
        <span>Length</span>
        <span>Created</span>
        <span>Description</span>
        <span>Model</span>
        <span></span>
      </div>
      <template v-for="reel in reelList" :key="reel.id">
        <div class="reel-row" :class="reel.downloaded_at ? 'read' : 'unread'">
          <button class="ghost reel-expand" @click="toggleExpanded(reel.id)">
            {{ expandedId === reel.id ? '▾' : '▸' }} #{{ reel.id }}
          </button>
          <div class="reel-render">
            <button
              :disabled="projectBusy || reviewingReelId !== null || submittingRender || reel.status === 'rendering' || (renderInProgress && renderingReelId === reel.id)"
              :title="reel.error ?? ''"
              @click="renderPlan(reel.id)"
            >
              {{ reviewingReelId === reel.id ? 'Loading review…' : renderLabel(reel) }}
            </button>
            <div
              v-if="renderInProgress && renderingReelId === reel.id && activeRenderJob"
              class="bar slim"
              :title="`${renderPercent}%`"
            >
              <div class="bar-fill" :style="{ width: renderPercent + '%' }" />
            </div>
          </div>
          <span>{{ durationLabel(reel) }}</span>
          <span class="muted reel-created">{{ formatDateTime(reel.created_at) }}</span>
          <span class="reel-desc" :title="reel.prompt">{{ reel.prompt }}</span>
          <span class="reel-model" :title="reel.model_id ?? ''">{{ reel.model_id ?? '—' }}</span>
          <a
            v-if="reel.status === 'rendered'"
            class="download-link"
            :href="`/api/projects/${projectId}/plans/${reel.id}/download`"
            title="Download MP4"
            @click="onDownloadReel"
          >⬇</a>
          <span v-else class="muted reel-nodl" title="Render first to download">—</span>
        </div>
        <div v-if="pendingRender?.id === reel.id" class="reel-detail">
          <RenderReviewCard :review="pendingRender.review" :disabled="projectBusy || submittingRender" @render="confirmRender" @dismiss="pendingRender = null" />
        </div>
        <p
          v-if="reel.status === 'rendering' && !(renderInProgress && renderingReelId === reel.id)"
          class="muted pulse reel-pulse"
        >
          Reel #{{ reel.id }} render in progress… this row updates automatically.
        </p>
        <div v-if="expandedId === reel.id && activePlanDetail" class="reel-detail">
          <p v-if="reel.error" class="error">{{ reel.error }}</p>
          <PlanPreview
            v-if="activePlanDetail.plan"
            :project-id="projectId"
            :plan="activePlanDetail.plan"
            :files="fileList"
          />
          <ol class="clip-list">
            <li v-for="(clip, ci) in activePlanDetail.plan.clips" :key="ci">
              <span class="file-name">{{ clip.rel_path }}</span>
              <span class="muted">
                {{ clip.start_sec.toFixed(1) }}–{{ clip.end_sec.toFixed(1) }}s ·
                {{ clip.transition_in }}
                <template v-if="clip.ken_burns"> · {{ clip.ken_burns.direction }}</template>
              </span>
              <span v-if="clip.reason" class="muted"> — {{ clip.reason }}</span>
            </li>
          </ol>
        </div>
      </template>
    </div>

    <h2 class="collapsible" @click="showScenes = !showScenes">
      {{ showScenes ? '▾' : '▸' }} Scene library
      <span v-if="tagList.length" class="muted">({{ tagList.length }} tags)</span>
    </h2>
    <template v-if="showScenes">
      <div class="card search-panel">
        <input
          v-model="searchInput"
          class="search-input"
          placeholder='Search scenes… try "sunset", "water", "people"'
        />
        <div v-if="tagList.length" class="chip-row">
          <button
            v-for="tag in tagList"
            :key="tag.tag"
            class="chip"
            :class="{ 'chip-active': activeTag === tag.tag }"
            @click="activeTag = activeTag === tag.tag ? null : tag.tag"
          >
            {{ tag.tag }} <span class="chip-count">{{ tag.count }}</span>
          </button>
        </div>
      </div>

      <div class="grid scene-grid">
        <article v-for="scene in sceneResults" :key="scene.scene_id" class="card scene-card">
          <img
            v-if="scene.thumb_rel"
            :src="thumbUrl(scene.scene_id)"
            :alt="scene.rel_path"
            loading="lazy"
          />
          <div v-else class="thumb-placeholder">no preview</div>
          <p class="file-name">{{ scene.rel_path }}</p>
          <p class="muted">
            {{ scene.kind === 'video' ? `${formatTime(scene.start_sec)}–${formatTime(scene.end_sec)}` : 'photo' }}
            &middot; score {{ scene.score.toFixed(3) }}
          </p>
          <div class="chip-row">
            <button
              v-for="t in scene.tags.slice(0, 4)"
              :key="t.tag"
              class="chip"
              @click="activeTag = t.tag"
            >
              {{ t.tag }}
            </button>
          </div>
        </article>
      </div>
      <p v-if="!scenesQuery.isLoading.value && sceneResults.length === 0" class="muted">
        No indexed scenes yet. Run Analyze to build the knowledge base.
      </p>
    </template>

    <h2 class="collapsible" @click="showFiles = !showFiles">
      {{ showFiles ? '▾' : '▸' }} Media files
      <span class="muted">({{ project.files_total }})</span>
    </h2>
    <template v-if="showFiles">
      <div class="grid">
        <article v-for="file in fileList" :key="file.id" class="card file-card">
          <span class="badge" :class="file.kind">{{ file.kind.toUpperCase() }}</span>
          <p class="file-name">{{ file.rel_path }}</p>
          <p class="muted">{{ file.scene_count ?? 0 }} scenes</p>
          <p v-if="file.provenance_status === 'stale'" class="muted">Source changed — Analyze to refresh its knowledge.</p>
          <p v-else-if="file.provenance_status === 'legacy'" class="muted">Retained knowledge — original analysis provenance is unverified.</p>
        </article>
      </div>
    </template>
  </section>
  <p v-else class="muted">Loading project…</p>
</template>
