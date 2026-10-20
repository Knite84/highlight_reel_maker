<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '@/api/client'
import type { ArchivedProject, Project } from '@/api/types'

const router = useRouter()

const { data: projects, isLoading, refetch: refetchProjects } = useQuery({
  queryKey: ['projects'],
  queryFn: () => api.get<Project[]>('/projects'),
})

const { data: archives, refetch: refetchArchives } = useQuery({
  queryKey: ['archived-projects'],
  queryFn: () => api.get<ArchivedProject[]>('/projects/archived'),
})
const mutatingProject = ref<number | null>(null)
const restoreNames = ref<Record<number, string>>({})

const name = ref('')
const mediaPath = ref('')
const error = ref('')
const busy = ref(false)
const browsing = ref(false)
interface FolderListing {
  path: string | null
  parent: string | null
  folders: { name: string; path: string }[]
}
const folderListing = ref<FolderListing | null>(null)

async function loadFolder(path: string | null = null) {
  try {
    folderListing.value = await api.get<FolderListing>(
      `/system/folders${path === null ? '' : `?${new URLSearchParams({ path })}`}`,
    )
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

function chooseFolder() {
  if (folderListing.value?.path) mediaPath.value = folderListing.value.path
  folderListing.value = null
}

async function browseFolder() {
  browsing.value = true
  try {
    const result = await api.post<{ path: string | null; error?: string; browse?: boolean }>('/system/pick-folder')
    if (result.path) mediaPath.value = result.path
    else if (result.browse) await loadFolder()
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

async function changeProject(id: number, action: 'archive' | 'restore' | 'purge') {
  if (action === 'archive' && !confirm(
    'Archive this project? Analysis, plans, and exports will be retained. You can restore it below.',
  )) return
  if (action === 'purge' && !confirm(
    'Permanently purge this archived project? Its analysis, plans, and exports will be deleted '
    + 'and it cannot be restored. Original source media will be kept.',
  )) return
  mutatingProject.value = id
  error.value = ''
  try {
    if (action === 'restore') {
      await api.post(`/projects/archived/${id}/restore`, {
        name: restoreNames.value[id]?.trim() || null,
      })
    } else {
      await api.del(action === 'archive' ? `/projects/${id}` : `/projects/archived/${id}`)
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    // A failed purge can still have archived a project through the legacy API.
    await Promise.all([refetchProjects(), refetchArchives()])
    mutatingProject.value = null
  }
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

    <section v-if="folderListing" class="card" aria-label="Choose source folder">
      <h2>Choose source folder</h2>
      <p class="muted">{{ folderListing.path ?? 'Shared Windows folders' }}</p>
      <div class="actions">
        <button @click="loadFolder()">Shared folders</button>
        <button v-if="folderListing.parent" @click="loadFolder(folderListing.parent)">Parent folder</button>
        <button :disabled="!folderListing.path" @click="chooseFolder">Use this folder</button>
        <button class="ghost" @click="folderListing = null">Cancel</button>
      </div>
      <ul>
        <li v-for="folder in folderListing.folders" :key="folder.path">
          <button class="ghost" @click="loadFolder(folder.path)">{{ folder.name }}</button>
        </li>
      </ul>
    </section>

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
          <button
            :disabled="mutatingProject !== null"
            @click.stop="changeProject(project.id, 'archive')"
          >Archive</button>
        </div>
      </article>
    </div>

    <p v-if="!isLoading && (projects ?? []).length === 0" class="muted">
      No projects yet. Create one above to get started.
    </p>

    <h2>Archived projects</h2>
    <p class="muted">Archived knowledge and exports are retained. Restore a project to use them again.</p>
    <div class="grid">
      <article v-for="project in archives ?? []" :key="project.id" class="card">
        <h3>{{ project.name }}</h3>
        <p class="muted file-name">{{ project.media_path }}</p>
        <p class="muted">Archived {{ new Date(project.archived_at).toLocaleString() }}</p>
        <label>
          Restore name (optional)
          <input v-model="restoreNames[project.id]" :placeholder="project.name" />
        </label>
        <div class="actions">
          <button :disabled="mutatingProject !== null" @click="changeProject(project.id, 'restore')">
            Restore
          </button>
          <button
            class="danger"
            :disabled="mutatingProject !== null"
            @click="changeProject(project.id, 'purge')"
          >Permanently purge</button>
        </div>
      </article>
    </div>
    <p v-if="archives?.length === 0" class="muted">No archived projects.</p>
  </section>
</template>
