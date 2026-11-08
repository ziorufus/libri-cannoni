<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../api'
import StatusBadges from '../../components/StatusBadges.vue'
import { meta, statusLabel } from '../../meta'
import { fmtRelative, STATUS_STYLE } from '../../utils'

const books = ref([])
const counts = ref({})
const loading = ref(true)
const error = ref('')
const statusFilter = ref('')
const phaseFilter = ref('')
let timer = null

const total = computed(() => Object.values(counts.value).reduce((a, b) => a + b, 0))

async function load() {
  const params = new URLSearchParams()
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (phaseFilter.value !== '') params.set('phase', phaseFilter.value)
  try {
    const data = await api(`/api/admin/books?${params}`)
    books.value = data.books
    counts.value = data.counts
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function setStatus(s) {
  statusFilter.value = statusFilter.value === s ? '' : s
  load()
}

// Monitoraggio: aggiornamento automatico mentre la pagina è visibile
const tick = () => document.visibilityState === 'visible' && load()
onMounted(() => {
  load()
  timer = setInterval(tick, 10000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div>
    <div class="d-flex align-items-center justify-content-between mb-3">
      <h1 class="h4 mb-0">Libri <span class="text-secondary fw-normal fs-6">({{ total }})</span></h1>
      <RouterLink :to="{ name: 'book-new' }" class="btn btn-primary d-none d-md-inline-block">
        <i class="bi bi-camera me-1"></i>Nuovo libro
      </RouterLink>
    </div>

    <div class="d-flex flex-wrap gap-2 mb-3 align-items-center">
      <button
        v-for="(n, s) in counts"
        :key="s"
        class="btn btn-sm rounded-pill"
        :class="statusFilter === s ? STATUS_STYLE[s]?.cls : 'btn-light border'"
        @click="setStatus(s)"
      >
        {{ statusLabel(s) }} <span class="opacity-75">{{ n }}</span>
      </button>
      <select v-model="phaseFilter" class="form-select form-select-sm w-auto ms-auto" @change="load">
        <option value="">Tutte le fasi</option>
        <option v-for="p in meta.phases" :key="p.number" :value="p.number">{{ p.number }} · {{ p.label }}</option>
      </select>
    </div>

    <div v-if="error" class="alert alert-danger">{{ error }}</div>
    <div v-if="loading" class="text-center py-5"><div class="spinner-border text-secondary"></div></div>
    <div v-else-if="!books.length" class="text-center text-secondary py-5">
      <i class="bi bi-inbox display-5 d-block mb-2"></i>
      Nessun libro{{ statusFilter || phaseFilter !== '' ? ' con questi filtri' : '' }}.
    </div>

    <div v-else class="list-group shadow-sm mb-5">
      <RouterLink
        v-for="b in books"
        :key="b.id"
        :to="{ name: 'book', params: { id: b.id } }"
        class="list-group-item list-group-item-action d-flex gap-3 align-items-center py-2"
      >
        <img v-if="b.cover_url" :src="b.cover_url" class="thumb" loading="lazy" alt="" />
        <div v-else class="thumb d-flex align-items-center justify-content-center text-secondary">
          <i class="bi bi-image"></i>
        </div>
        <div class="flex-grow-1 min-w-0" style="min-width: 0">
          <div class="d-flex justify-content-between gap-2">
            <span class="text-break">
              <strong v-if="b.description">{{ b.description }}</strong>
              <span class="text-secondary small" :class="{ 'fw-bold text-body fs-6': !b.description }">
                #{{ b.id }}
              </span>
            </span>
            <small class="text-secondary text-nowrap">{{ fmtRelative(b.created_at) }}</small>
          </div>
          <div class="small text-truncate text-secondary">
            {{ b.notes || 'Nessuna nota' }} · {{ b.photo_count }} foto
          </div>
          <div class="d-flex flex-wrap gap-1 align-items-center mt-1">
            <StatusBadges :book="b" />
            <span v-if="b.condition" class="badge rounded-pill text-bg-light border text-capitalize fw-normal">
              {{ b.condition }}
            </span>
          </div>
          <div v-if="b.status === 'waiting' && b.next_attempt_at" class="small text-secondary">
            Nuovo tentativo {{ fmtRelative(b.next_attempt_at) }}
          </div>
        </div>
      </RouterLink>
    </div>

    <RouterLink :to="{ name: 'book-new' }" class="btn btn-primary fab d-md-none" aria-label="Nuovo libro">
      <i class="bi bi-camera"></i>
    </RouterLink>
  </div>
</template>
