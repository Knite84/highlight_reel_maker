<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EditPlanData, MediaFile } from '@/api/types'

const props = defineProps<{ projectId: number; plan: EditPlanData; files: MediaFile[] }>()

const fileIdByPath = computed(() => {
  const map = new Map<string, number>()
  for (const file of props.files) map.set(file.rel_path, file.id)
  return map
})

const IMAGE_EXT = /\.(jpg|jpeg|png|webp|avif)$/i

interface Segment {
  url: string
  start: number
  end: number
  label: string
  isImage: boolean
}

const segments = computed<Segment[]>(() =>
  props.plan.clips
    .map((clip) => {
      const fileId = fileIdByPath.value.get(clip.rel_path)
      if (!fileId) return null
      return {
        url: `/api/projects/${props.projectId}/media/${fileId}`,
        start: clip.start_sec,
        end: clip.end_sec,
        label: `${clip.rel_path} · ${clip.start_sec.toFixed(1)}s–${clip.end_sec.toFixed(1)}s`,
        isImage: IMAGE_EXT.test(clip.rel_path),
      }
    })
    .filter((segment): segment is Segment => segment !== null),
)

const index = ref(0)
const playing = ref(false)
const videoEl = ref<HTMLVideoElement | null>(null)
let imageTimer: number | null = null
const current = computed(() => segments.value[index.value] ?? null)

function clearImageTimer() {
  if (imageTimer !== null) {
    clearTimeout(imageTimer)
    imageTimer = null
  }
}

function goToNext() {
  if (index.value + 1 < segments.value.length) {
    index.value += 1
    void playSegment(index.value)
  } else {
    playing.value = false
    clearImageTimer()
  }
}

async function playSegment(position: number) {
  const element = videoEl.value
  const segment = segments.value[position]
  if (!element || !segment) return
  clearImageTimer()
  element.src = segment.url
  await new Promise<void>((resolve) => {
    element.onloadeddata = () => resolve()
  })
  try {
    element.currentTime = segment.isImage ? 0 : segment.start
  } catch {}
  await element.play().catch(() => {})
  if (segment.isImage) {
    const spanMs = Math.max((segment.end - segment.start) * 1000, 500)
    imageTimer = window.setTimeout(goToNext, spanMs)
  }
}

async function start() {
  if (segments.value.length === 0) return
  playing.value = true
  index.value = 0
  await playSegment(0)
}

function onTimeUpdate() {
  const element = videoEl.value
  const segment = current.value
  if (!element || !segment || !playing.value || segment.isImage) return
  if (element.currentTime >= segment.end - 0.05) {
    goToNext()
  }
}
</script>

<template>
  <div class="preview-box">
    <video
      v-if="current"
      ref="videoEl"
      class="preview-video"
      controls
      playsinline
      @timeupdate="onTimeUpdate"
    />
    <div v-if="current" class="muted">{{ index + 1 }}/{{ segments.length }} · {{ current.label }}</div>
    <div class="actions">
      <button v-if="!playing" @click="start">Preview plan ({{ segments.length }} clips)</button>
      <button v-else class="ghost" @click="playing = false">Stop</button>
    </div>
    <p v-if="segments.length === 0" class="error">No source files matched this plan.</p>
  </div>
</template>

<style scoped>
.preview-video {
  width: 100%;
  border-radius: 8px;
  background: black;
  aspect-ratio: 16 / 9;
}
.preview-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
</style>
