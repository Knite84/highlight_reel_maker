<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
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
      <input v-model="mediaPath" placeholder="Absolute path to media folder" required />
      <button type="submit" :disabled="busy || !name || !mediaPath">Create project</button>
      <p v-if="error" class="error">{{ error }}</p>
    </form>

    <div class="grid">
      <article v-for="project in projects ?? []" :key="project.id" class="card project-card">
        <h2><RouterLink :to="`/projects/${project.id}`">{{ project.name }}</RouterLink></h2>
        <p class="muted file-name">{{ project.media_path }}</p>
        <p class="muted">{{ project.video_count }} videos &middot; {{ project.photo_count }} photos</p>
        <button class="danger" @click="removeProject(project.id)">Delete</button>
      </article>
    </div>

    <p v-if="!isLoading && (projects ?? []).length === 0" class="muted">
      No projects yet. Create one above to get started.
    </p>
  </section>
</template>
