<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, loginUrl } from '../api'
import { auth } from '../auth'

const route = useRoute()
const providers = ref([])
const loading = ref(true)
const loadError = ref('')

const next = computed(() => (typeof route.query.next === 'string' ? route.query.next : '/admin'))

const ERRORS = {
  not_allowed: (email) =>
    `L'account ${email || ''} non è abilitato. Chiedi a un amministratore di aggiungerlo alla lista.`,
  forbidden: () => 'Questa sezione è riservata agli amministratori.',
  expired: () => 'La sessione è scaduta: accedi di nuovo.',
  cancelled: () => 'Accesso annullato.',
  state: () => 'Il tentativo di accesso è scaduto o non è valido. Riprova.',
  provider: () => 'Non è stato possibile completare l’accesso con il servizio scelto. Riprova.',
}
const error = computed(() => {
  const fn = ERRORS[route.query.error]
  return fn ? fn(route.query.email) : ''
})

const ICONS = { google: 'bi-google', microsoft: 'bi-microsoft' }

onMounted(async () => {
  try {
    providers.value = await api('/api/auth/providers')
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="container py-5" style="max-width: 26rem">
    <div class="card shadow-sm border-0">
      <div class="card-body p-4">
        <h1 class="h4 font-serif mb-1">Accedi</h1>
        <p class="text-secondary small mb-4">Usa uno dei tuoi account: serve solo a riconoscere il tuo indirizzo e-mail.</p>

        <div v-if="error" class="alert alert-warning small">{{ error }}</div>
        <div v-if="auth.user && !error" class="alert alert-info small">
          Hai già effettuato l'accesso come <strong>{{ auth.user.email }}</strong>.
        </div>

        <div v-if="loading" class="text-center py-3"><div class="spinner-border text-secondary"></div></div>
        <div v-else-if="loadError" class="alert alert-danger small">{{ loadError }}</div>
        <div v-else-if="!providers.length" class="alert alert-secondary small">
          Nessun servizio di accesso configurato nel backend.
        </div>
        <div v-else class="d-grid gap-2">
          <a
            v-for="p in providers"
            :key="p.name"
            :href="loginUrl(p.name, next)"
            class="btn btn-outline-dark btn-lg d-flex align-items-center justify-content-center gap-2"
          >
            <i class="bi" :class="ICONS[p.name] || 'bi-person'"></i> Continua con {{ p.label }}
          </a>
        </div>
      </div>
    </div>
  </main>
</template>
