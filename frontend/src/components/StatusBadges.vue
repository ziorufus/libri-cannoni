<script setup>
import { phaseLabel, statusLabel } from '../meta'
import { STATUS_STYLE } from '../utils'

defineProps({ book: { type: Object, required: true } })
</script>

<template>
  <span class="d-inline-flex flex-wrap gap-1 align-items-center">
    <span class="badge rounded-pill text-bg-light border">
      {{ book.phase }} · {{ book.phase_label || phaseLabel(book.phase) }}
    </span>
    <span class="badge rounded-pill" :class="STATUS_STYLE[book.status]?.cls">
      <i class="bi me-1" :class="STATUS_STYLE[book.status]?.icon"></i>{{ statusLabel(book.status) }}
      <template v-if="book.attempts && book.status !== 'idle'"> · {{ book.attempts }}✗</template>
    </span>
  </span>
</template>
