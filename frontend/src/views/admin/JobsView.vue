<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api'
import { jobStatus, refreshJobs, setJobs } from '../../jobStatus'
import { fmtRelative } from '../../utils'

const busy = ref('')
const error = ref('')

async function runNow(name) {
  busy.value = name
  error.value = ''
  try {
    setJobs(await api(`/api/admin/jobs/${name}/run`, { method: 'POST' }))
    // Il job gira in background: si rilegge l'esito dopo qualche secondo
    setTimeout(refreshJobs, 5000)
    setTimeout(refreshJobs, 15000)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = ''
  }
}

onMounted(refreshJobs)
</script>

<template>
  <div style="max-width: 48rem">
    <div class="d-flex align-items-center justify-content-between mb-3">
      <h1 class="h4 mb-0">Job periodici</h1>
      <button class="btn btn-sm btn-outline-secondary" @click="refreshJobs">
        <i class="bi bi-arrow-clockwise me-1"></i>Aggiorna
      </button>
    </div>
    <div v-if="error || jobStatus.error" class="alert alert-danger">{{ error || jobStatus.error }}</div>

    <div v-if="jobStatus.auto_pauses.length" class="alert alert-warning small">
      <div class="fw-semibold mb-1"><i class="bi bi-pause-circle me-1"></i>Fasi in pausa automatica</div>
      <div v-for="p in jobStatus.auto_pauses" :key="p.phase">
        Fase {{ p.phase }} · {{ p.label }}: {{ p.reason }}
      </div>
    </div>

    <div v-for="j in jobStatus.jobs" :key="j.name" class="card border-0 shadow-sm mb-3">
      <div class="card-body">
        <div class="d-flex align-items-start justify-content-between gap-2">
          <div>
            <h2 class="h6 mb-1">
              {{ j.label }}
              <span v-if="!j.enabled" class="badge text-bg-secondary ms-1">Disattivato</span>
              <span v-else-if="j.ok === true" class="badge text-bg-success ms-1">OK</span>
              <span v-else-if="j.ok === false" class="badge text-bg-danger ms-1">Errore</span>
            </h2>
            <div class="small text-secondary">{{ j.description }}</div>
          </div>
          <button
            class="btn btn-sm btn-outline-primary text-nowrap"
            :disabled="!j.enabled || busy === j.name"
            @click="runNow(j.name)"
          >
            <i class="bi bi-play me-1"></i>Esegui ora
          </button>
        </div>

        <div class="small text-secondary mt-2">
          Ogni {{ j.interval_minutes }} min
          <template v-if="j.last_run_at"> · ultimo controllo {{ fmtRelative(j.last_run_at) }}</template>
          <template v-if="j.enabled && j.next_run_at"> · prossimo {{ fmtRelative(j.next_run_at) }}</template>
        </div>

        <div v-if="j.name === 'claude_usage' && j.data?.session !== undefined" class="row g-2 mt-1">
          <div v-for="k in ['session', 'week']" :key="k" class="col-sm-6">
            <div class="small">
              {{ k === 'session' ? 'Sessione' : 'Settimana (tutti i modelli)' }}:
              <strong>{{ j.data[k] }}%</strong>
              <span class="text-secondary"> · reset {{ j.data[k + '_resets'] }}</span>
            </div>
            <div class="progress" style="height: 6px">
              <div
                class="progress-bar"
                :class="j.data[k + '_over'] ? 'bg-warning' : 'bg-success'"
                :style="{ width: Math.min(j.data[k], 100) + '%' }"
              ></div>
            </div>
          </div>
        </div>

        <pre v-if="j.message" class="small bg-body-tertiary rounded p-2 mt-2 mb-0 text-wrap">{{ j.message }}</pre>
      </div>
    </div>

    <p class="small text-secondary">
      Attivazione, intervalli e soglie si cambiano in
      <RouterLink :to="{ name: 'settings' }">Impostazioni</RouterLink> → Job periodici.
    </p>
  </div>
</template>
