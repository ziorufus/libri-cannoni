<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, uploadForm } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import StatusBadges from '../../components/StatusBadges.vue'
import { meta, statusLabel } from '../../meta'
import { fmtRelative, STATUS_STYLE } from '../../utils'

const books = ref([])
const counts = ref({})
const loading = ref(true)
const error = ref('')
const statusFilter = ref('')
const route = useRoute()
const phaseFilter = ref(route.query.phase !== undefined ? Number(route.query.phase) : '')
let timer = null
const router = useRouter()

// --- selezione dei libri venduti o pagati per un nuovo versamento ---------------------
const SOLD = 9
const PAID = 10
const donatable = (b) => b.phase === SOLD || b.phase === PAID
const selected = ref([]) // id dei libri venduti o pagati selezionati
const isSelected = (id) => selected.value.includes(id)
function toggle(id) {
  selected.value = isSelected(id) ? selected.value.filter((x) => x !== id) : [...selected.value, id]
}
const paidVisible = computed(() => books.value.filter((b) => b.phase === PAID))
const selectedBooks = computed(() => books.value.filter((b) => isSelected(b.id)))
const selectedUnpaid = computed(() => selectedBooks.value.filter((b) => b.phase === SOLD).length)
const selectedTotal = computed(() => selectedBooks.value.reduce((t, b) => t + (b.sold_price || 0), 0))
const eur = (v) => (v ?? 0).toLocaleString('it-IT', { style: 'currency', currency: 'EUR' })

const donationModal = ref(false)
const form = ref({ date: '', amount: '', description: '', file: null, preview: '' })
const saving = ref(false)
const progress = ref(0)
const formError = ref('')
const parseAmount = (t) => {
  const v = t.trim().replace(/\s*€$/, '').replace(',', '.')
  return /^\d+(\.\d{1,2})?$/.test(v) ? Number(v) : NaN
}

function openDonation() {
  const today = new Date()
  const local = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
  form.value = { date: local, amount: selectedTotal.value.toFixed(2).replace('.', ','), description: '', file: null, preview: '' }
  formError.value = ''
  donationModal.value = true
}
function closeDonation() {
  if (form.value.preview) URL.revokeObjectURL(form.value.preview)
  donationModal.value = false
}
function onReceipt(e) {
  const file = e.target.files[0] || null
  if (form.value.preview) URL.revokeObjectURL(form.value.preview)
  form.value.file = file
  form.value.preview = file ? URL.createObjectURL(file) : ''
}
async function saveDonation() {
  const amount = parseAmount(form.value.amount)
  if (Number.isNaN(amount)) return (formError.value = 'Cifra non valida: scrivi un importo come 12,50')
  if (!form.value.date) return (formError.value = 'Indica la data del versamento')
  if (!form.value.file) return (formError.value = 'Allega la ricevuta del bonifico')
  const fd = new FormData()
  fd.append('date', form.value.date)
  fd.append('amount', String(amount))
  fd.append('description', form.value.description)
  selected.value.forEach((id) => fd.append('book_ids', String(id)))
  fd.append('receipt', form.value.file)
  saving.value = true
  formError.value = ''
  try {
    await uploadForm('/api/admin/donations', fd, (p) => (progress.value = p))
    selected.value = []
    closeDonation()
    router.push({ name: 'donations' })
  } catch (e) {
    formError.value = e.message
  } finally {
    saving.value = false
    progress.value = 0
  }
}

// --- libro venduto da un altro account Vinted: inserito direttamente come pagato ----
const importModal = ref(false)
const importForm = ref({ url: '', date: '', amount: '' })
const importPreview = ref(null)
const importBusy = ref('') // '' | 'read' | 'save'
const importError = ref('')
const todayLocal = () => {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

function openImport() {
  importForm.value = { url: '', date: todayLocal(), amount: '' }
  importPreview.value = null
  importError.value = ''
  importModal.value = true
}
async function readImport() {
  if (!importForm.value.url || importBusy.value) return
  importBusy.value = 'read'
  importError.value = ''
  try {
    importPreview.value = await api('/api/admin/books/vinted-import/preview', {
      method: 'POST',
      body: { url: importForm.value.url },
    })
  } catch (e) {
    importPreview.value = null
    importError.value = e.message
  } finally {
    importBusy.value = ''
  }
}
async function saveImport() {
  const amount = parseAmount(importForm.value.amount)
  if (!importForm.value.url) return (importError.value = "Incolla il link all'annuncio Vinted")
  if (!importForm.value.date) return (importError.value = 'Indica la data di vendita')
  if (Number.isNaN(amount)) return (importError.value = 'Cifra non valida: scrivi un importo come 6,50')
  importBusy.value = 'save'
  importError.value = ''
  try {
    const book = await api('/api/admin/books/vinted-import', {
      method: 'POST',
      body: { url: importForm.value.url, sold_at: importForm.value.date, sold_price: amount },
    })
    importModal.value = false
    router.push({ name: 'book', params: { id: book.id } })
  } catch (e) {
    importError.value = e.message
  } finally {
    importBusy.value = ''
  }
}

const total = computed(() => Object.values(counts.value).reduce((a, b) => a + b, 0))

async function load() {
  const params = new URLSearchParams()
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (phaseFilter.value !== '') params.set('phase', phaseFilter.value)
  try {
    const data = await api(`/api/admin/books?${params}`)
    books.value = data.books
    counts.value = data.counts
    // Restano selezionati solo i libri ancora venduti o pagati, e visibili
    const ok = new Set(data.books.filter(donatable).map((b) => b.id))
    selected.value = selected.value.filter((id) => ok.has(id))
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
      <div class="d-flex gap-2">
        <button class="btn btn-outline-primary" title="Inserisci libro venduto su Vinted" @click="openImport">
          <i class="bi bi-bag-check"></i><span class="d-none d-md-inline ms-1">Inserisci libro venduto su Vinted</span>
        </button>
        <RouterLink :to="{ name: 'book-new' }" class="btn btn-primary d-none d-md-inline-block">
          <i class="bi bi-camera me-1"></i>Nuovo libro
        </RouterLink>
      </div>
    </div>

    <BaseModal v-if="importModal" title="Libro venduto su Vinted" @close="importModal = false">
      <p class="small text-secondary">
        Per i libri venduti da un altro account Vinted: titolo, descrizione e una foto si leggono dalla pagina
        dell'annuncio; il libro entra direttamente nella fase <strong>Pagato</strong>.
      </p>
      <label class="form-label small mb-1" for="imp-url">Link all'annuncio Vinted</label>
      <div class="input-group mb-2">
        <input
          id="imp-url"
          v-model.trim="importForm.url"
          class="form-control"
          type="url"
          placeholder="https://www.vinted.it/items/…"
          @change="importPreview = null"
          @keyup.enter="readImport"
        />
        <button class="btn btn-outline-secondary" :disabled="importBusy || !importForm.url" @click="readImport">
          <span v-if="importBusy === 'read'" class="spinner-border spinner-border-sm me-1"></span>Leggi annuncio
        </button>
      </div>
      <div v-if="importPreview" class="d-flex gap-3 align-items-start border rounded p-2 mb-3">
        <img
          v-if="importPreview.image_url"
          :src="importPreview.image_url"
          alt=""
          referrerpolicy="no-referrer"
          style="width: 72px; height: 96px; object-fit: cover"
          class="rounded flex-shrink-0"
        />
        <div class="small">
          <div class="fw-semibold">{{ importPreview.title }}</div>
          <div class="text-secondary">
            Prezzo dell'annuncio {{ importPreview.price != null ? eur(importPreview.price) : '—' }}
            <template v-if="importPreview.condition"> · {{ importPreview.condition }}</template>
          </div>
          <div v-if="importPreview.is_closed === false" class="text-warning-emphasis">
            <i class="bi bi-exclamation-triangle me-1"></i>Su Vinted l'annuncio non risulta venduto
          </div>
          <div v-if="importPreview.existing_book_id" class="text-danger">
            <i class="bi bi-exclamation-octagon me-1"></i>Già collegato al
            <RouterLink :to="{ name: 'book', params: { id: importPreview.existing_book_id } }">libro n. {{ importPreview.existing_book_id }}</RouterLink>
          </div>
        </div>
      </div>
      <div class="row g-2">
        <div class="col-6">
          <label class="form-label small mb-1" for="imp-date">Data di vendita</label>
          <input id="imp-date" v-model="importForm.date" class="form-control" type="date" />
        </div>
        <div class="col-6">
          <label class="form-label small mb-1" for="imp-price">Cifra incassata</label>
          <div class="input-group">
            <input id="imp-price" v-model="importForm.amount" class="form-control" inputmode="decimal" placeholder="es. 6,50" />
            <span class="input-group-text">€</span>
          </div>
        </div>
      </div>
      <div v-if="importError" class="alert alert-danger py-2 small mt-3 mb-0">{{ importError }}</div>
      <template #footer>
        <button class="btn btn-light" @click="importModal = false">Annulla</button>
        <button class="btn btn-primary" :disabled="!!importBusy || !!importPreview?.existing_book_id" @click="saveImport">
          <span v-if="importBusy === 'save'" class="spinner-border spinner-border-sm me-1"></span>Inserisci come pagato
        </button>
      </template>
    </BaseModal>

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
      <button
        v-if="paidVisible.length"
        class="btn btn-sm btn-outline-dark rounded-pill ms-auto"
        title="Seleziona tutti i libri pagati in elenco (incasso già accreditato), per un nuovo versamento"
        @click="selected = paidVisible.map((b) => b.id)"
      >
        <i class="bi bi-check2-square me-1"></i>Seleziona i pagati ({{ paidVisible.length }})
      </button>
      <select
        v-model="phaseFilter"
        class="form-select form-select-sm w-auto"
        :class="{ 'ms-auto': !paidVisible.length }"
        @change="load"
      >
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
        :class="{ 'list-group-item-warning': isSelected(b.id) }"
      >
        <span
          v-if="donatable(b)"
          class="select-box"
          role="checkbox"
          :aria-checked="isSelected(b.id)"
          :title="isSelected(b.id) ? 'Togli dal versamento' : 'Aggiungi a un nuovo versamento'"
          @click.stop.prevent="toggle(b.id)"
        >
          <input class="form-check-input m-0 pe-none" type="checkbox" :checked="isSelected(b.id)" tabindex="-1" />
        </span>
        <span v-else class="select-box invisible" aria-hidden="true">
          <!-- Segnaposto invisibile: tiene allineate le miniature di tutti i libri -->
          <input class="form-check-input m-0" type="checkbox" disabled tabindex="-1" />
        </span>
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
            {{ b.notes || 'Nessuna nota' }} · {{ b.photo_count }} foto<template v-if="b.created_by">
              · <i class="bi bi-person"></i> {{ b.created_by }}</template>
          </div>
          <div class="d-flex flex-wrap gap-1 align-items-center mt-1">
            <StatusBadges :book="b" />
            <span v-if="b.condition" class="badge rounded-pill text-bg-light border text-capitalize fw-normal">
              {{ b.condition }}
            </span>
            <span v-if="b.price !== null && b.phase < SOLD" class="badge rounded-pill text-bg-light border fw-normal">
              {{ b.price.toLocaleString('it-IT', { style: 'currency', currency: 'EUR' }) }}
            </span>
            <span v-if="b.sold_price !== null" class="badge rounded-pill text-bg-dark fw-normal" title="Incassato">
              <i class="bi bi-bag-check me-1"></i>{{ b.sold_price.toLocaleString('it-IT', { style: 'currency', currency: 'EUR' }) }}
            </span>
          </div>
          <div v-if="b.status === 'waiting' && b.next_attempt_at" class="small text-secondary">
            Nuovo tentativo {{ fmtRelative(b.next_attempt_at) }}
          </div>
        </div>
      </RouterLink>
    </div>

    <!-- Barra della selezione: nuovo versamento -->
    <div v-if="selected.length" class="selection-bar shadow-lg">
      <div class="container d-flex flex-wrap align-items-center gap-2 py-2">
        <span class="fw-semibold">{{ selected.length }} {{ selected.length === 1 ? 'libro' : 'libri' }}</span>
        <span class="text-white-50">· incassato {{ eur(selectedTotal) }}</span>
        <button class="btn btn-sm btn-outline-light ms-auto" @click="selected = []">Annulla</button>
        <button class="btn btn-sm btn-light" @click="openDonation">
          <i class="bi bi-heart me-1"></i>Nuovo versamento
        </button>
      </div>
    </div>

    <BaseModal v-if="donationModal" title="Nuovo versamento" @close="closeDonation">
      <p class="small text-secondary">
        {{ selectedBooks.length }} libri venduti, per un incasso di {{ eur(selectedTotal) }}<template v-if="selectedUnpaid">
          (di cui {{ selectedUnpaid }} non ancora pagati)</template>: dopo il versamento passano alla fase "Versato". Il versamento e la ricevuta saranno visibili sulla pagina pubblica.
      </p>
      <div class="row g-2 mb-2">
        <div class="col-6">
          <label class="form-label small fw-semibold" for="don-date">Data</label>
          <input id="don-date" v-model="form.date" type="date" class="form-control" />
        </div>
        <div class="col-6">
          <label class="form-label small fw-semibold" for="don-amount">Cifra versata</label>
          <div class="input-group">
            <input id="don-amount" v-model="form.amount" class="form-control" inputmode="decimal" />
            <span class="input-group-text">€</span>
          </div>
        </div>
      </div>
      <label class="form-label small fw-semibold" for="don-desc">Descrizione <span class="fw-normal text-secondary">(facoltativa)</span></label>
      <textarea id="don-desc" v-model="form.description" class="form-control mb-2" rows="2"></textarea>
      <label class="form-label small fw-semibold" for="don-receipt">Ricevuta del bonifico (immagine)</label>
      <input id="don-receipt" type="file" accept="image/*" class="form-control" @change="onReceipt" />
      <div class="form-text">Sarà pubblica: oscura prima IBAN, nomi o indirizzi che non vuoi mostrare.</div>
      <img v-if="form.preview" :src="form.preview" class="img-fluid rounded border mt-2" style="max-height: 12rem" alt="" />
      <details class="small mt-3">
        <summary class="text-secondary">Libri compresi</summary>
        <ul class="mb-0 mt-1 ps-3">
          <li v-for="b in selectedBooks" :key="b.id">{{ b.description || `#${b.id}` }} · {{ eur(b.sold_price) }}</li>
        </ul>
      </details>
      <div v-if="formError" class="alert alert-danger small py-2 mt-3 mb-0">{{ formError }}</div>
      <template #footer>
        <button class="btn btn-light" @click="closeDonation">Annulla</button>
        <button class="btn btn-primary" :disabled="saving" @click="saveDonation">
          <span v-if="saving" class="spinner-border spinner-border-sm me-1"></span>
          {{ saving ? `Invio… ${Math.round(progress * 100)}%` : 'Conferma versamento' }}
        </button>
      </template>
    </BaseModal>

    <div v-if="selected.length" style="height: 4rem"></div>
    <RouterLink
      v-if="!selected.length"
      :to="{ name: 'book-new' }"
      class="btn btn-primary fab d-md-none"
      aria-label="Nuovo libro"
    >
      <i class="bi bi-camera"></i>
    </RouterLink>
  </div>
</template>

<style scoped>
.select-box {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  margin: -0.25rem -0.5rem -0.25rem -0.25rem;
  cursor: pointer;
  flex-shrink: 0;
}
.select-box .form-check-input {
  width: 1.25rem;
  height: 1.25rem;
}
.selection-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1030;
  background: #212529;
  color: #fff;
}
</style>
