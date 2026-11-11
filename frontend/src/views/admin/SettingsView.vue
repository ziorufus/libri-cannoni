<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../../api'

const settings = ref([])
const values = ref({})
const error = ref('')
const saved = ref(false)
const busy = ref(false)

const groups = computed(() => {
  const out = {}
  for (const s of settings.value) (out[s.group] ??= []).push(s)
  return out
})
const dirty = computed(() => settings.value.some((s) => String(values.value[s.key]) !== String(s.value)))

function apply(list) {
  settings.value = list
  values.value = Object.fromEntries(list.map((s) => [s.key, s.value]))
}

async function load() {
  try {
    apply(await api('/api/admin/settings'))
  } catch (e) {
    error.value = e.message
  }
}

async function save() {
  busy.value = true
  error.value = ''
  saved.value = false
  try {
    const changed = Object.fromEntries(
      settings.value.filter((s) => String(values.value[s.key]) !== String(s.value)).map((s) => [s.key, values.value[s.key]]),
    )
    apply(await api('/api/admin/settings', { method: 'PUT', body: { values: changed } }))
    saved.value = true
    setTimeout(() => (saved.value = false), 3000)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <form class="mx-auto" style="max-width: 48rem" @submit.prevent="save">
    <h1 class="h4 mb-3">Impostazioni</h1>
    <div v-if="error" class="alert alert-danger small">{{ error }}</div>

    <div v-for="(items, group) in groups" :key="group" class="card border-0 shadow-sm mb-3">
      <div class="card-header bg-transparent fw-semibold">{{ group }}</div>
      <div class="card-body">
        <div v-for="s in items" :key="s.key" class="mb-3">
          <div class="d-flex justify-content-between align-items-baseline">
            <label :for="s.key" class="form-label fw-semibold small mb-1">{{ s.label }}</label>
            <button
              v-if="String(values[s.key]) !== String(s.default)"
              type="button"
              class="btn btn-link btn-sm p-0 small"
              @click="values[s.key] = s.default"
            >
              Valore predefinito
            </button>
          </div>
          <input
            v-if="s.type === 'int'"
            :id="s.key"
            v-model.number="values[s.key]"
            type="number"
            class="form-control"
            style="max-width: 10rem"
            :min="s.min ?? undefined"
            :max="s.max ?? undefined"
            required
          />
          <div v-else-if="s.type === 'bool'" class="form-check form-switch">
            <input :id="s.key" v-model="values[s.key]" class="form-check-input" type="checkbox" role="switch" />
            <label :for="s.key" class="form-check-label small">{{ values[s.key] ? 'Sì' : 'No' }}</label>
          </div>
          <textarea
            v-else-if="s.type === 'text'"
            :id="s.key"
            v-model="values[s.key]"
            class="form-control font-monospace small"
            rows="16"
          ></textarea>
          <input v-else :id="s.key" v-model="values[s.key]" class="form-control font-monospace small" />
          <div v-if="s.help" class="form-text">{{ s.help }}</div>
        </div>
      </div>
    </div>

    <div class="d-flex align-items-center gap-3 mb-5 sticky-bottom py-2" style="background: var(--lc-paper)">
      <button class="btn btn-primary" :disabled="!dirty || busy">
        <span v-if="busy" class="spinner-border spinner-border-sm me-1"></span>Salva
      </button>
      <span v-if="saved" class="text-success small"><i class="bi bi-check2"></i> Salvato</span>
      <span v-else-if="dirty" class="text-secondary small">Modifiche non salvate</span>
    </div>
  </form>
</template>
