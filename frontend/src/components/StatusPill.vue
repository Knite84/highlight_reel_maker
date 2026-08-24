<script setup lang="ts">
defineProps<{ label: string; ok: boolean; detail?: string }>()
</script>

<template>
  <span class="pill" :class="ok ? 'pill-ok' : 'pill-bad'" :title="detail ?? ''">
    <span class="dot" />{{ label }}
  </span>
</template>
