<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../../api'

const curl = ref('')
const busy = ref(false)
const error = ref('')
const status = ref(null) // { ok, output } di `vinted auth status`
const imported = ref(null) // { ok, output } di `vinted auth import`

async function run(fn) {
  busy.value = true
  error.value = ''
  try {
    await fn()
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

const check = () =>
  run(async () => {
    status.value = await api('/api/admin/vinted/status')
  })

const doImport = () =>
  run(async () => {
    imported.value = await api('/api/admin/vinted/auth-import', { method: 'POST', body: { curl: curl.value } })
    // Il cURL contiene i token: non lo si lascia nella pagina
    if (imported.value.ok) curl.value = ''
    status.value = null
  })

onMounted(check)
</script>

<template>
  <div style="max-width: 48rem">
    <h1 class="h4 mb-3">Sessione Vinted</h1>
    <div v-if="error" class="alert alert-danger">{{ error }}</div>

    <div class="card border-0 shadow-sm mb-4">
      <div class="card-body">
        <div class="d-flex align-items-center justify-content-between gap-2 mb-2">
          <h2 class="h6 mb-0">Stato</h2>
          <button class="btn btn-sm btn-outline-secondary" :disabled="busy" @click="check">
            <i class="bi bi-arrow-clockwise me-1"></i>Verifica
          </button>
        </div>
        <div v-if="!status" class="small text-secondary">
          <span v-if="busy" class="spinner-border spinner-border-sm me-1"></span>Verifica in corso…
        </div>
        <template v-else>
          <span class="badge mb-2" :class="status.ok ? 'text-bg-success' : 'text-bg-danger'">
            <i class="bi me-1" :class="status.ok ? 'bi-check-circle' : 'bi-x-circle'"></i>
            {{ status.ok ? 'Collegato' : 'Non collegato' }}
          </span>
          <pre class="small bg-body-tertiary rounded p-2 mb-0 text-wrap">{{ status.output }}</pre>
        </template>
      </div>
    </div>

    <div class="card border-0 shadow-sm">
      <div class="card-body">
        <h2 class="h6">Importa i token (<code>vinted auth import</code>)</h2>
        <ol class="small text-secondary ps-3">
          <li>Su vinted.it, da loggato, apri DevTools → <strong>Network</strong> e ricarica la pagina.</li>
          <li>Tasto destro su una richiesta a <code>/api/v2/…</code> → <strong>Copy → Copy as cURL (bash)</strong>.</li>
          <li>Incolla qui sotto e premi Importa.</li>
        </ol>
        <textarea
          v-model="curl"
          class="form-control font-monospace small mb-2"
          rows="8"
          placeholder="curl 'https://www.vinted.it/api/v2/…' -H '…' -b '…'"
          spellcheck="false"
          autocomplete="off"
        ></textarea>
        <div class="form-text mb-2">
          Il cURL contiene i token della tua sessione: incollalo solo qui, non in chat o in altri posti.
        </div>
        <button class="btn btn-primary" :disabled="busy || !curl.trim()" @click="doImport">
          <span v-if="busy" class="spinner-border spinner-border-sm me-1"></span>
          <i v-else class="bi bi-key me-1"></i>Importa
        </button>
        <div v-if="imported" class="mt-3">
          <div class="alert py-2 small mb-2" :class="imported.ok ? 'alert-success' : 'alert-danger'">
            {{ imported.ok ? 'Token importati e sessione verificata.' : 'Importazione non riuscita.' }}
          </div>
          <pre class="small bg-body-tertiary rounded p-2 mb-0 text-wrap">{{ imported.output }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>
