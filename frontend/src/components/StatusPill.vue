<script setup lang="ts">
defineProps<{ label: string; ok: boolean; detail?: string; compact?: boolean }>()
</script>

<template>
  <span
    class="pill"
    :class="[ok ? 'pill-ok' : 'pill-bad', { 'pill-compact': compact && ok }]"
    :title="`${label}${detail ? ` — ${detail}` : ''}`"
  >
    <span class="dot" /><template v-if="!compact || !ok">{{ label }}</template>
  </span>
</template>
