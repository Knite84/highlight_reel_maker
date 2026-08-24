<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/client'
import type { Project } from '@/api/types'

const router = useRouter()

const { data: projects, isLoading, refetch: refetchProjects } = useQuery({
  queryKey: ['projects'],
  queryFn: () => api.get<Project[]>('/projects'),
})

const name = ref('')
const mediaPath = ref('')
const error = ref('')
const busy = ref(false)
const browsing = ref(false)

async function browseFolder() {
  browsing.value = true
  try {
    const result = await api.post<{ path: string | null; error?: string }>('/system/pick-folder')
    if (result.path) mediaPath.value = result.path
    else if (result.error) error.value = result.error
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    browsing.value = false
  }
}

async function createProject() {
  busy.value = true
  error.value = ''
  try {
    const created = await api.post<Project>('/projects', {
      name: name.value,
      media_path: mediaPath.value,
    })
    name.value = ''
    mediaPath.value = ''
    await refetchProjects()
    await router.push(`/projects/${created.id}`)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function removeProject(id: number) {
  if (!confirm('Delete this project?')) return
  await api.del(`/projects/${id}`)
  await refetchProjects()
}
</script>

<template>
  <section>
    <h1>Projects</h1>
    <form class="card form" @submit.prevent="createProject">
      <input v-model="name" placeholder="Project name" required />
      <input v-model="mediaPath" placeholder="Path to your media folder" required />
      <button type="button" class="ghost" :disabled="browsing" @click="browseFolder">
        {{ browsing ? 'Browsing…' : 'Browse…' }}
      </button>
      <button type="submit" :disabled="busy || !name || !mediaPath">Create project</button>
      <p v-if="error" class="error">{{ error }}</p>
    </form>

    <div class="grid">
      <article
        v-for="project in projects ?? []"
        :key="project.id"
        class="card project-card clickable"
        @click="router.push(`/projects/${project.id}`)"
      >
        <h2>{{ project.name }}</h2>
        <p class="muted file-name">{{ project.media_path }}</p>
        <div class="card-footer">
          <span class="muted">{{ project.video_count }} videos &middot; {{ project.photo_count }} photos</span>
          <button class="danger" @click.stop="removeProject(project.id)">Delete</button>
        </div>
      </article>
    </div>

    <p v-if="!isLoading && (projects ?? []).length === 0" class="muted">
      No projects yet. Create one above to get started.
    </p>
  </section>
</template>
