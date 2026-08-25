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
  SceneResult,
  SystemStatus,
  TagCount,
} from '@/api/types'
import PlanPreview from '@/components/PlanPreview.vue'
import StatusPill from '@/components/StatusPill.vue'
import { useJobStore } from '@/stores/jobs'

type ProjectDetail = Project & { files_total: number }

const route = useRoute()
const projectId = Number(route.params.id)
const queryClient = useQueryClient()
const jobStore = useJobStore()

onMounted(() => jobStore.connect())

const { data: project } = useQuery({
  queryKey: ['project', projectId],
  queryFn: () => api.get<ProjectDetail>(`/projects/${projectId}`),
})

const scanVersion = ref(0)
const filesQuery = useQuery({
  queryKey: ['files', projectId, scanVersion],
  queryFn: () => api.get<MediaFile[]>(`/projects/${projectId}/files`),
})

const { data: systemStatus } = useQuery({
  queryKey: ['system'],
  queryFn: () => api.get<SystemStatus>('/system/status'),
})

const activeJobId = ref<number | null>(null)
const actionError = ref('')
const busyAction = ref<'analyze' | null>(null)

const job = computed(() => (activeJobId.value === null ? null : jobStore.byId(activeJobId.value)))

const passiveJob = computed(() => {
  let latest: Job | null = null
  for (const candidate of jobStore.jobs.values()) {
    if (
      candidate.project_id === projectId &&
      (candidate.status === 'queued' || candidate.status === 'running')
    ) {
      if (latest === null || candidate.id > latest.id) latest = candidate
    }
  }
  return latest
})

const displayedJob = computed(() => job.value ?? passiveJob.value ?? polledJob.value)

const activePlanJob = computed(() => {
  const current = displayedJob.value
  return current && current.kind === 'plan' && current.status !== 'done' && current.status !== 'failed'
    ? current
    : null
})

const waitingForPlanner = computed(
  () =>
    activeJobId.value !== null &&
    activePlanJob.value === null &&
    (polledJob.value === null ||
      polledJob.value.status === 'queued' ||
      polledJob.value.status === 'running') &&
    (job.value === null || job.value.kind === 'plan'),
)

const planningPercent = computed(() => {
  const current = activePlanJob.value
  if (!current) return null
  if (current.total && current.total > 0) {
    return Math.min(100, Math.round((current.done / current.total) * 100))
  }
  return Math.round(current.progress * 100)
})

const polledJob = ref<Job | null>(null)
let pollTimer: number | null = null

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function refreshAfterJob() {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['files', projectId] }),
    queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
    queryClient.invalidateQueries({ queryKey: ['tags', projectId] }),
    queryClient.invalidateQueries({ queryKey: ['scene-search', projectId] }),
    queryClient.invalidateQueries({ queryKey: ['plans', projectId] }),
    queryClient.invalidateQueries({ queryKey: ['plan-detail'] }),
  ])
}

watch(activeJobId, (id) => {
  stopPolling()
  if (id === null) return
  pollTimer = window.setInterval(async () => {
    try {
      const fresh = await api.get<Job>(`/jobs/${id}`)
      polledJob.value = fresh
      if (fresh.status === 'done' || fresh.status === 'failed' || fresh.status === 'cancelled') {
        stopPolling()
        if (!handledJobIds.has(fresh.id)) {
          handledJobIds.add(fresh.id)
          await refreshAfterJob()
        }
      }
    } catch {}
  }, 3000)
}, { immediate: true })

onBeforeUnmount(stopPolling)

const handledJobIds = new Set<number>()

watch(displayedJob, (current, previous) => {
  if (
    current &&
    current.status === 'done' &&
    (!previous || previous.id !== current.id) &&
    !handledJobIds.has(current.id)
  ) {
    handledJobIds.add(current.id)
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ['files', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['tags', projectId] }),
      queryClient.invalidateQueries({ queryKey: ['scene-search', projectId] }),
    ])
  }
})

const finished = computed(() => job.value?.status === 'done')

watch(finished, async (isDone, wasDone) => {
  if (isDone && !wasDone) {
    if (!handledJobIds.has(job.value!.id)) {
      handledJobIds.add(job.value!.id)
      await refreshAfterJob()
    }
  }
})

async function runAnalysis() {
  busyAction.value = 'analyze'
  actionError.value = ''
  try {
    const result = await api.post<{ job_id: number }>(`/projects/${projectId}/analyze`)
    activeJobId.value = result.job_id
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
    activeJobId.value = result.job_id
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
  renderingReelId.value = id
  try {
    const result = await api.post<{ job_id: number }>(
      `/projects/${projectId}/plans/${id}/render`,
      { profile: 'proxy' },
    )
    activeJobId.value = result.job_id
  } catch (e) {
    renderingReelId.value = null
    actionError.value = e instanceof Error ? e.message : String(e)
  }
}

const renderingReelId = ref<number | null>(null)

const activeRenderJob = computed(() => {
  const current = displayedJob.value
  return current && current.kind === 'render' ? current : null
})

const renderInProgress = computed(
  () =>
    activeRenderJob.value !== null &&
    activeRenderJob.value.status !== 'done' &&
    activeRenderJob.value.status !== 'failed' &&
    activeRenderJob.value.status !== 'cancelled',
)

const renderPercent = computed(() => {
  const current = activeRenderJob.value
  if (!current) return 0
  if (current.total && current.total > 0) {
    return Math.min(100, Math.round((current.done / current.total) * 100))
  }
  return Math.round(current.progress * 100)
})

watch(renderInProgress, (active, wasActive) => {
  if (!active && wasActive && activeRenderJob.value?.status === 'done') {
    renderingReelId.value = null
  }
})

const topJob = computed(() => {
  const current = displayedJob.value
  return current && (current.kind === 'scan' || current.kind === 'analyze') ? current : null
})

const topPercent = computed(() => {
  const current = topJob.value
  if (!current) return 0
  if (current.total && current.total > 0) {
    return Math.min(100, Math.round((current.done / current.total) * 100))
  }
  return Math.round(current.progress * 100)
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
        <button :disabled="busyAction !== null" @click="runAnalysis">
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

    <div v-if="topJob" class="card progress">
      <div class="progress-head">
        <span>{{ topJob.kind }} {{ topJob.status }}</span>
        <span>{{ topJob.done }}/{{ topJob.total ?? '?' }}</span>
      </div>
      <div class="bar"><div class="bar-fill" :style="{ width: topPercent + '%' }" /></div>
      <p class="muted">{{ topJob.message }}</p>
      <p v-if="topJob.error" class="error" :title="topJob.error">
        {{ topJob.error.slice(0, 300) }}{{ (topJob.error.length ?? 0) > 300 ? '…' : '' }}
      </p>
    </div>

    <p v-if="actionError" class="error">{{ actionError }}</p>

    <h2>Generate reel</h2>
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
        :disabled="generating || !promptText.trim()"
        @click="generatePlan"
      >
        {{ generating ? 'Sending…' : 'Generate plan' }}
      </button>
      <p
        v-if="!tagsQuery.isLoading.value && tagList.length === 0"
        class="muted"
      >
        No indexed scenes yet — run Analyze first for best results.
      </p>
      <div
        v-if="waitingForPlanner || activePlanJob"
        class="inline-progress"
      >
        <div class="progress-head">
          <span>{{
            waitingForPlanner && !activePlanJob
              ? 'Contacting planner…'
              : `Planning ${activePlanJob?.status}`
          }}</span>
          <span v-if="planningPercent !== null">{{ planningPercent }}%</span>
        </div>
        <div class="bar">
          <div class="bar-fill" :style="{ width: (planningPercent ?? 8) + '%' }" />
        </div>
        <p v-if="activePlanJob?.error" class="error" :title="activePlanJob.error">
          {{ activePlanJob.error.slice(0, 250) }}
        </p>
      </div>
      <p v-else-if="waitingForPlanner" class="muted">
        Planning… your GPU is thinking.
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
              :disabled="reel.status === 'rendering' || (renderInProgress && renderingReelId === reel.id)"
              :title="reel.error ?? ''"
              @click="renderPlan(reel.id)"
            >
              {{ renderLabel(reel) }}
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
        </article>
      </div>
    </template>
  </section>
  <p v-else class="muted">Loading project…</p>
</template>
