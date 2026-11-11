<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'
import { auth } from '../auth'
import { jobByName, jobStatus, startJobsPolling, stopJobsPolling } from '../jobStatus'

const isAdmin = computed(() => auth.user?.role === 'admin')
watch(isAdmin, (v) => (v ? startJobsPolling() : stopJobsPolling()), { immediate: true })
onBeforeUnmount(stopJobsPolling)

const vinted = computed(() => {
  const j = jobByName('vinted_session')
  if (!j?.enabled || j.ok === null) return null
  return j
})
const claude = computed(() => {
  const j = jobByName('claude_usage')
  if (!j?.enabled || j.data?.session === undefined) return null
  return j
})
const pct = (v) => `${Math.round(v)}%`
</script>

<template>
  <RouterLink
    v-if="isAdmin && jobStatus.loaded && (vinted || claude)"
    :to="{ name: 'jobs' }"
    class="job-indicators d-flex align-items-center gap-2 text-decoration-none small me-2 me-md-0 ms-md-3"
    title="Job periodici"
  >
    <span
      v-if="vinted"
      class="badge rounded-pill"
      :class="vinted.ok ? 'text-bg-success' : 'text-bg-danger'"
      :title="vinted.ok ? 'Sessione Vinted valida' : 'Sessione Vinted non valida: fasi Vinted in pausa'"
    >
      <i class="bi" :class="vinted.ok ? 'bi-check-circle' : 'bi-x-circle'"></i>
      <span class="d-none d-sm-inline ms-1">Vinted</span>
    </span>
    <span
      v-if="claude"
      class="badge rounded-pill"
      :class="claude.data.paused ? 'text-bg-warning' : claude.ok ? 'text-bg-light' : 'text-bg-secondary'"
      :title="`Claude · sessione ${pct(claude.data.session)} (reset ${claude.data.session_resets}) · settimana ${pct(claude.data.week)} (reset ${claude.data.week_resets})${claude.data.paused ? ' · analisi in pausa' : ''}`"
    >
      <i class="bi" :class="claude.data.paused ? 'bi-pause-circle' : 'bi-cpu'"></i>
      {{ pct(claude.data.session) }} · {{ pct(claude.data.week) }}
    </span>
  </RouterLink>
</template>
