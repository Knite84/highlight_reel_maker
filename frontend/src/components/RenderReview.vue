<script setup lang="ts">
import type { RenderReview } from '@/api/types'
defineProps<{ review: RenderReview; disabled: boolean }>()
defineEmits<{ render: []; dismiss: [] }>()
const labels = {
  chronological: 'Chronological', narrative: 'Narrative order from your brief',
  reverse_chronological: 'Reverse chronological', saved_legacy_order: 'Saved order (older plan)',
}
</script>

<template>
  <div class="card" role="region" aria-label="Review render timing">
    <p>Requested: {{ review.target_duration_sec.toFixed(1) }}s · Planned output: {{ review.expected_duration_sec.toFixed(1) }}s</p>
    <p>{{ labels[review.sequencing] }}</p>
    <p v-if="review.sequencing_evidence" class="muted">Brief directive: {{ review.sequencing_evidence }}</p>
    <ul v-if="review.warnings.length"><li v-for="warning in review.warnings" :key="warning">{{ warning }}</li></ul>
    <p v-else class="muted">No long still holds, frozen frames, added filler, or other recorded timing repairs.</p>
    <div class="actions">
      <button :disabled="disabled" @click="$emit('render')">Render this plan</button>
      <button class="ghost" :disabled="disabled" @click="$emit('dismiss')">Back</button>
    </div>
  </div>
</template>
