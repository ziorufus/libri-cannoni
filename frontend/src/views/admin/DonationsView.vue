<script setup>
// Versamenti a Emergency: elenco, modifica della descrizione, eliminazione.
// Si creano dall'elenco dei libri (selezione dei venduti o pagati → "Nuovo versamento").
import { onMounted, ref } from 'vue'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'

const donations = ref([])
const pendingCount = ref(0)
const pendingTotal = ref(0)
const loading = ref(true)
const error = ref('')
const busy = ref(false)
const editing = ref(null) // id del versamento di cui si modifica la descrizione
const descDraft = ref('')
const toDelete = ref(null)
const receipt = ref(null) // versamento di cui si guarda la ricevuta

const eur = (v) => (v ?? 0).toLocaleString('it-IT', { style: 'currency', currency: 'EUR' })
const fmtDay = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('it-IT', { day: 'numeric', month: 'long', year: 'numeric' }) : '')

async function load() {
  try {
    const data = await api('/api/admin/donations')
    donations.value = data.donations
    pendingCount.value = data.pending_count
    pendingTotal.value = data.pending_total
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

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

function startEdit(d) {
  editing.value = d.id
  descDraft.value = d.description
}

const saveDesc = (d) =>
  run(async () => {
    const updated = await api(`/api/admin/donations/${d.id}`, { method: 'PATCH', body: { description: descDraft.value } })
    Object.assign(d, updated)
    editing.value = null
  })

const confirmDelete = () =>
  run(async () => {
    await api(`/api/admin/donations/${toDelete.value.id}`, { method: 'DELETE' })
    toDelete.value = null
    await load()
  })

onMounted(load)
</script>

<template>
  <div>
    <div class="d-flex align-items-center justify-content-between mb-3">
      <h1 class="h4 mb-0">Versamenti <span class="text-secondary fw-normal fs-6">({{ donations.length }})</span></h1>
    </div>

    <div class="alert alert-light border small d-flex flex-wrap align-items-center gap-2">
      <span>
        <strong>Prossimo versamento:</strong> {{ pendingCount }}
        {{ pendingCount === 1 ? 'libro venduto o pagato non ancora versato' : 'libri venduti o pagati non ancora versati' }}, incasso
        {{ eur(pendingTotal) }}.
      </span>
      <RouterLink v-if="pendingCount" :to="{ name: 'books', query: { phase: 10 } }" class="ms-auto">
        Seleziona i libri <i class="bi bi-arrow-right"></i>
      </RouterLink>
    </div>

    <div v-if="error" class="alert alert-danger">{{ error }}</div>
    <div v-if="loading" class="text-center py-5"><div class="spinner-border text-secondary"></div></div>
    <div v-else-if="!donations.length" class="text-center text-secondary py-5">
      <i class="bi bi-heart display-5 d-block mb-2"></i>
      Nessun versamento. Per crearne uno, seleziona i libri venduti o pagati nell'elenco dei libri e premi "Nuovo versamento".
    </div>

    <div v-for="d in donations" :key="d.id" class="card border-0 shadow-sm mb-3">
      <div class="card-body">
        <div class="d-flex gap-3">
          <button
            v-if="d.receipt_thumb_url"
            type="button"
            class="btn p-0 border-0 flex-shrink-0"
            title="Ricevuta del bonifico"
            @click="receipt = d"
          >
            <img :src="d.receipt_thumb_url" class="rounded border receipt-thumb" alt="Ricevuta del bonifico" />
          </button>
          <div class="flex-grow-1" style="min-width: 0">
            <div class="d-flex flex-wrap align-items-baseline gap-2">
              <h2 class="h5 mb-0">{{ eur(d.amount) }}</h2>
              <span class="text-secondary">{{ fmtDay(d.date) }}</span>
              <button class="btn btn-sm btn-outline-danger ms-auto" :disabled="busy" @click="toDelete = d">
                <i class="bi bi-trash me-1"></i>Elimina
              </button>
            </div>
            <div class="small text-secondary">
              {{ d.books.length }} libri · incasso {{ eur(d.books_total) }}
              <span v-if="Math.abs(d.books_total - d.amount) >= 0.01" class="text-warning-emphasis">
                (diverso dalla cifra versata)
              </span>
            </div>

            <div v-if="editing === d.id" class="mt-2">
              <textarea v-model="descDraft" class="form-control form-control-sm" rows="2"></textarea>
              <div class="d-flex gap-2 mt-1">
                <button class="btn btn-sm btn-primary" :disabled="busy" @click="saveDesc(d)">Salva</button>
                <button class="btn btn-sm btn-light" @click="editing = null">Annulla</button>
              </div>
            </div>
            <p v-else class="small mb-0 mt-2">
              <span v-if="d.description" style="white-space: pre-line">{{ d.description }}</span>
              <span v-else class="text-secondary fst-italic">Nessuna descrizione</span>
              <button class="btn btn-sm btn-link py-0" @click="startEdit(d)"><i class="bi bi-pencil"></i> Modifica</button>
            </p>
          </div>
        </div>

        <details class="mt-2">
          <summary class="small text-secondary">Libri compresi</summary>
          <ul class="list-unstyled small mb-0 mt-2">
            <li v-for="b in d.books" :key="b.id" class="d-flex align-items-center gap-2 py-1 border-top">
              <img v-if="b.cover_url" :src="b.cover_url" class="rounded" style="width: 32px; height: 40px; object-fit: cover" alt="" />
              <RouterLink :to="{ name: 'book', params: { id: b.id } }" class="text-truncate">{{ b.title }}</RouterLink>
              <span class="ms-auto text-nowrap">{{ eur(b.sold_price) }}</span>
            </li>
          </ul>
        </details>
      </div>
    </div>

    <BaseModal v-if="toDelete" title="Eliminare il versamento?" @close="toDelete = null">
      Il versamento di {{ eur(toDelete.amount) }} del {{ fmtDay(toDelete.date) }} e la sua ricevuta verranno eliminati.
      I {{ toDelete.books.length }} libri compresi tornano alla fase "Venduto" e potranno entrare in un altro versamento.
      <template #footer>
        <button class="btn btn-light" @click="toDelete = null">Annulla</button>
        <button class="btn btn-danger" :disabled="busy" @click="confirmDelete">
          <i class="bi bi-trash me-1"></i>Elimina versamento
        </button>
      </template>
    </BaseModal>

    <BaseModal v-if="receipt" :title="`Ricevuta del ${fmtDay(receipt.date)}`" size="lg" @close="receipt = null">
      <img :src="receipt.receipt_url" class="img-fluid rounded mx-auto d-block" alt="Ricevuta del bonifico" />
    </BaseModal>
  </div>
</template>

<style scoped>
.receipt-thumb {
  width: 72px;
  height: 96px;
  object-fit: cover;
}
</style>
