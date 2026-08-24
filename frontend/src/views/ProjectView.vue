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
const busyAction = ref<'scan' | 'analyze' | null>(null)

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

const waitingForPlanner = computed(
  () =>
    activeJobId.value !== null &&
    job.value === null &&
    passiveJob.value === null &&
    (polledJob.value === null ||
      polledJob.value.status === 'queued' ||
      polledJob.value.status === 'running'),
)

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
const percent = computed(() => {
  const current = displayedJob.value
  if (!current) return 0
  if (current.total && current.total > 0) {
    return Math.min(100, Math.round((current.done / current.total) * 100))
  }
  return Math.round(current.progress * 100)
})

watch(finished, async (isDone, wasDone) => {
  if (isDone && !wasDone) {
    if (!handledJobIds.has(job.value!.id)) {
      handledJobIds.add(job.value!.id)
      await refreshAfterJob()
    }
  }
})

async function runAction(kind: 'scan' | 'analyze', suffix: string) {
  busyAction.value = kind
  actionError.value = ''
  try {
    const result = await api.post<{ job_id: number }>(`/projects/${projectId}${suffix}`)
    activeJobId.value = result.job_id
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  } finally {
    busyAction.value = null
  }
}

function startScan() {
  void runAction('scan', '/scan')
}

function startAnalysis() {
  void runAction('analyze', '/analyze')
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

const reelList = computed(() => plansQuery.data.value ?? [])
const activePlanDetail = computed(() => planDetailQuery.data.value ?? null)

async function renderPlan(id: number) {
  actionError.value = ''
  try {
    const result = await api.post<{ job_id: number }>(
      `/projects/${projectId}/plans/${id}/render`,
      { profile: 'proxy' },
    )
    activeJobId.value = result.job_id
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : String(e)
  }
}

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
        <button class="ghost" :disabled="busyAction !== null" @click="startScan">Scan</button>
        <button :disabled="busyAction !== null" @click="startAnalysis">
          {{ busyAction === 'analyze' ? 'Analyzing…' : 'Analyze' }}
        </button>
      </div>
    </div>
    <p class="muted">{{ project.media_path }}</p>

    <div class="card status-row">
      <StatusPill label="ffmpeg" :ok="!!systemStatus?.tools?.ffmpeg" />
      <StatusPill label="ffprobe" :ok="!!systemStatus?.tools?.ffprobe" />
      <StatusPill label="exiftool" :ok="!!systemStatus?.tools?.exiftool" />
      <StatusPill label="GPU" :ok="!!systemStatus?.gpu?.ok" :detail="systemStatus?.gpu?.name ?? ''" />
      <StatusPill label="NVENC" :ok="!!systemStatus?.nvenc?.ok" />
      <StatusPill
        label="Unsloth"
        :ok="!!systemStatus?.unsloth?.ok"
        :detail="systemStatus?.unsloth?.error ?? ''"
      />
      <button class="ghost" @click="refreshStatus">Refresh</button>
    </div>

    <div v-if="displayedJob" class="card progress">
      <div class="progress-head">
        <span>{{ displayedJob.kind }} {{ displayedJob.status }}</span>
        <span>{{ displayedJob.done }}/{{ displayedJob.total ?? '?' }}</span>
      </div>
      <div class="bar"><div class="bar-fill" :style="{ width: percent + '%' }" /></div>
      <p class="muted">{{ displayedJob.message }}</p>
      <p v-if="displayedJob.error" class="error" :title="displayedJob.error">
        {{ displayedJob.error.slice(0, 300) }}{{ (displayedJob.error.length ?? 0) > 300 ? '…' : '' }}
      </p>
    </div>

    <p v-if="actionError" class="error">{{ actionError }}</p>

    <h2>Scene search</h2>
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

    <h2>Generate reel</h2>
    <div class="card form plan-form">
      <textarea
        v-model="promptText"
        class="plan-prompt"
        rows="2"
        placeholder="Describe your reel… e.g. 'Chill sunset montage with the best scenery'"
      />
      <label class="muted duration-label">
        Target seconds
        <input v-model.number="targetDuration" type="number" min="10" max="600" />
      </label>
      <button
        :disabled="generating || !promptText.trim()"
        @click="generatePlan"
      >
        {{ generating ? 'Sending…' : 'Generate plan' }}
      </button>
      <p v-if="waitingForPlanner" class="muted">
        Planning… your GPU is thinking (typically 15–60 seconds).
      </p>
    </div>

    <h2>Reels</h2>
    <div v-if="reelList.length === 0" class="muted">
      No reels yet. Generate a plan above.
    </div>
    <div v-else class="reel-list">
      <article v-for="reel in reelList" :key="reel.id" class="card reel-card">
        <div class="page-head">
          <button class="ghost" @click="toggleExpanded(reel.id)">
            {{ expandedId === reel.id ? '▾' : '▸' }} Reel #{{ reel.id }}
          </button>
          <span class="badge" :class="reel.status">{{ reel.status }}</span>
        </div>
        <p class="file-name">{{ reel.prompt }}</p>
        <p class="muted">
          target {{ reel.target_duration_sec }}s · {{ reel.model_id ?? 'unknown model' }} ·
          {{ reel.created_at }}
        </p>
        <p v-if="reel.error" class="error">{{ reel.error }}</p>
        <div class="actions">
          <button
            :disabled="reel.status === 'rendering'"
            @click="renderPlan(reel.id)"
          >
            Render (proxy)
          </button>
          <a
            v-if="reel.status === 'rendered'"
            class="download-link"
            :href="`/api/projects/${projectId}/plans/${reel.id}/download`"
          >
            Download MP4
          </a>
        </div>
        <div v-if="expandedId === reel.id && activePlanDetail" class="plan-detail">
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
      </article>
    </div>

    <h2>Media files <span class="muted">({{ project.files_total }})</span></h2>
    <div class="grid">
      <article v-for="file in fileList" :key="file.id" class="card file-card">
        <span class="badge" :class="file.kind">{{ file.kind.toUpperCase() }}</span>
        <p class="file-name">{{ file.rel_path }}</p>
        <p class="muted">{{ file.scene_count ?? 0 }} scenes</p>
      </article>
    </div>
  </section>
  <p v-else class="muted">Loading project…</p>
</template>
