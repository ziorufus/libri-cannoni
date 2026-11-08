<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api } from '../../api'
import { fmtDate } from '../../utils'

const entries = ref([])
const users = ref([])
const error = ref('')
const busy = ref(false)
const form = reactive({ kind: 'email', value: '', role: 'user', note: '' })

async function load() {
  try {
    ;[entries.value, users.value] = await Promise.all([api('/api/admin/access'), api('/api/admin/users')])
  } catch (e) {
    error.value = e.message
  }
}

async function run(fn) {
  busy.value = true
  error.value = ''
  try {
    await fn()
    await load()
  } catch (e) {
    error.value = e.message
    await load()
  } finally {
    busy.value = false
  }
}

const add = () =>
  run(async () => {
    await api('/api/admin/access', { method: 'POST', body: { ...form } })
    Object.assign(form, { value: '', note: '' })
  })

const setRole = (e, role) => run(() => api(`/api/admin/access/${e.id}`, { method: 'PATCH', body: { role } }))

const remove = (e) =>
  run(async () => {
    if (confirm(`Togliere ${e.value} dalla lista?`)) await api(`/api/admin/access/${e.id}`, { method: 'DELETE' })
  })

onMounted(load)
</script>

<template>
  <div class="mx-auto" style="max-width: 56rem">
    <h1 class="h4">Accessi</h1>
    <p class="text-secondary small">
      Possono accedere solo gli indirizzi presenti in lista, oppure tutti gli indirizzi di un dominio.
      <strong>Utente</strong> = area riservata; <strong>Amministratore</strong> = anche amministrazione.
      Se più voci corrispondono vale il ruolo più alto.
    </p>

    <div v-if="error" class="alert alert-danger small">{{ error }}</div>

    <form class="card border-0 shadow-sm mb-4" @submit.prevent="add">
      <div class="card-body row g-2 align-items-end">
        <div class="col-6 col-md-2">
          <label class="form-label small">Tipo</label>
          <select v-model="form.kind" class="form-select">
            <option value="email">E-mail</option>
            <option value="domain">Dominio</option>
          </select>
        </div>
        <div class="col-6 col-md-2 order-md-3">
          <label class="form-label small">Ruolo</label>
          <select v-model="form.role" class="form-select">
            <option value="user">Utente</option>
            <option value="admin">Amministratore</option>
          </select>
        </div>
        <div class="col-12 col-md-5 order-md-2">
          <label class="form-label small">{{ form.kind === 'email' ? 'Indirizzo e-mail' : 'Dominio' }}</label>
          <input
            v-model="form.value"
            class="form-control"
            :type="form.kind === 'email' ? 'email' : 'text'"
            :placeholder="form.kind === 'email' ? 'nome@example.com' : 'example.com'"
            required
            autocapitalize="off"
          />
        </div>
        <div class="col-12 col-md-3 order-md-4">
          <label class="form-label small">Nota</label>
          <input v-model="form.note" class="form-control" placeholder="facoltativa" />
        </div>
        <div class="col-12 order-md-5">
          <button class="btn btn-primary" :disabled="busy"><i class="bi bi-plus-lg me-1"></i>Aggiungi</button>
        </div>
      </div>
    </form>

    <h2 class="h6 text-uppercase text-secondary">Lista ({{ entries.length }})</h2>
    <div class="list-group shadow-sm mb-4">
      <div v-for="e in entries" :key="e.id" class="list-group-item d-flex flex-wrap gap-2 align-items-center">
        <i class="bi" :class="e.kind === 'email' ? 'bi-envelope' : 'bi-globe2'"></i>
        <div class="flex-grow-1" style="min-width: 0">
          <div class="text-break">
            <span v-if="e.kind === 'domain'" class="text-secondary">*@</span>{{ e.value }}
          </div>
          <div v-if="e.note" class="small text-secondary">{{ e.note }}</div>
        </div>
        <span v-if="e.protected" class="badge text-bg-dark">Amministratore (ADMIN_EMAIL)</span>
        <template v-else>
          <select
            :value="e.role"
            class="form-select form-select-sm w-auto"
            :disabled="busy"
            @change="setRole(e, $event.target.value)"
          >
            <option value="user">Utente</option>
            <option value="admin">Amministratore</option>
          </select>
          <button class="btn btn-sm btn-outline-danger" :disabled="busy" aria-label="Rimuovi" @click="remove(e)">
            <i class="bi bi-trash"></i>
          </button>
        </template>
      </div>
    </div>

    <h2 class="h6 text-uppercase text-secondary">Utenti che hanno effettuato l'accesso ({{ users.length }})</h2>
    <div class="list-group shadow-sm mb-5">
      <div v-for="u in users" :key="u.id" class="list-group-item small d-flex flex-wrap gap-2 justify-content-between">
        <div>
          <strong>{{ u.name || u.email }}</strong>
          <span v-if="u.name" class="text-secondary"> · {{ u.email }}</span>
        </div>
        <div class="text-secondary">
          <span class="badge" :class="u.role === 'admin' ? 'text-bg-dark' : u.role ? 'text-bg-secondary' : 'text-bg-danger'">
            {{ u.role === 'admin' ? 'Amministratore' : u.role ? 'Utente' : 'Non più abilitato' }}
          </span>
          ultimo accesso {{ fmtDate(u.last_login_at) }} <template v-if="u.last_provider">({{ u.last_provider }})</template>
        </div>
      </div>
      <div v-if="!users.length" class="list-group-item small text-secondary">Nessuno.</div>
    </div>
  </div>
</template>
