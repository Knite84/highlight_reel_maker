<script setup lang="ts">
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { api } from '@/api/client'
import type { AppConfig, SystemStatus } from '@/api/types'
import StatusPill from '@/components/StatusPill.vue'

const queryClient = useQueryClient()

const { data: config } = useQuery({
  queryKey: ['config'],
  queryFn: () => api.get<AppConfig>('/system/config'),
})

const { data: status } = useQuery({
  queryKey: ['system'],
  queryFn: () => api.get<SystemStatus>('/system/status'),
})

async function refresh() {
  try {
    const fresh = await api.post<SystemStatus>('/system/refresh')
    queryClient.setQueryData(['system'], fresh)
  } catch {}
}
</script>

<template>
  <section>
    <h1>Settings</h1>

    <div v-if="status" class="card status-row">
      <StatusPill label="ffmpeg" :ok="!!status.tools?.ffmpeg" />
      <StatusPill label="ffprobe" :ok="!!status.tools?.ffprobe" />
      <StatusPill label="exiftool" :ok="!!status.tools?.exiftool" />
      <StatusPill
        label="GPU"
        :ok="!!status.gpu?.ok"
        :detail="`${status.gpu?.name ?? ''} ${status.gpu?.vram_gb ?? ''}GB`"
      />
      <StatusPill
        label="NVENC"
        :ok="!!status.nvenc?.ok"
        :detail="(status.nvenc?.encoders ?? []).join(', ')"
      />
      <StatusPill
        label="Unsloth"
        :ok="!!status.unsloth?.ok"
        :detail="status.unsloth?.error ?? ''"
      />
      <button class="ghost" @click="refresh">Refresh</button>
    </div>

    <div v-if="config" class="card">
      <h2>Backend configuration</h2>
      <dl class="kv">
        <dt>Data root</dt>
        <dd>{{ config.data_root }}</dd>
        <dt>Projects root</dt>
        <dd>{{ config.projects_root }}</dd>
        <dt>Unsloth URL</dt>
        <dd>{{ config.unsloth_base_url }}</dd>
        <dt>API key set</dt>
        <dd>{{ config.unsloth_api_key_set ? 'yes' : 'no' }}</dd>
        <dt>Planner model</dt>
        <dd>{{ config.planner_model_id }}</dd>
      </dl>
      <p class="muted">
        Configure via REELMAKER_ environment variables or backend/.env:
        REELMAKER_UNSLOTH_BASE_URL, REELMAKER_UNSLOTH_API_KEY, REELMAKER_PLANNER_MODEL_ID,
        REELMAKER_PROJECTS_ROOT.
      </p>
    </div>

    <div v-if="status?.unsloth?.models?.length" class="card">
      <h2>Models loaded in Unsloth</h2>
      <ul class="models">
        <li v-for="model in status.unsloth.models" :key="model">{{ model }}</li>
      </ul>
    </div>
  </section>
</template>
