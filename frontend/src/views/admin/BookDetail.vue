<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import StatusBadges from '../../components/StatusBadges.vue'
import { meta, phaseLabel } from '../../meta'
import { fmtBytes, fmtDate, fmtRelative } from '../../utils'

const props = defineProps({ id: { type: Number, required: true } })
const router = useRouter()

const book = ref(null)
const error = ref('')
const actionError = ref('')
const busy = ref(false)

const nameDraft = ref('')
const nameDirty = computed(() => book.value && nameDraft.value !== (book.value.description || ''))
const notesDraft = ref('')
const notesDirty = computed(() => book.value && notesDraft.value !== book.value.notes)
const editingOutput = ref(false)
const outputDraft = ref('')

const lightbox = ref(null) // indice della foto aperta
const confirmDelete = ref(false)
const targetPhase = ref(0)
let timer = null
let prevPhase = null

async function load() {
  try {
    const b = await api(`/api/admin/books/${props.id}`)
    const first = !book.value
    // Le modifiche dell'utente si valutano rispetto ai dati PRECEDENTI: se il
    // campo non era stato toccato, prende il valore nuovo (es. il nome appena
    // calcolato dal processo in background).
    const keepName = !first && nameDirty.value
    const keepNotes = !first && notesDirty.value
    book.value = b
    if (!keepName) nameDraft.value = b.description || ''
    if (!keepNotes) notesDraft.value = b.notes
    if (first || b.phase !== prevPhase) targetPhase.value = b.phase
    prevPhase = b.phase
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

async function act(fn) {
  busy.value = true
  actionError.value = ''
  try {
    await fn()
  } catch (e) {
    actionError.value = e.message
  } finally {
    busy.value = false
  }
}

const saveName = () =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}`, { method: 'PATCH', body: { description: nameDraft.value } })
    nameDraft.value = book.value.description || ''
  })

const nameFromOutput = () =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}/name-from-output`, { method: 'POST' })
    nameDraft.value = book.value.description || ''
  })

const saveNotes = () =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}`, { method: 'PATCH', body: { notes: notesDraft.value } })
    notesDraft.value = book.value.notes
  })

const saveOutput = () =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}`, {
      method: 'PATCH',
      body: { claude_output: outputDraft.value },
    })
    editingOutput.value = false
    if (!nameDirty.value) nameDraft.value = book.value.description || ''
  })

const retry = () =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}/retry`, { method: 'POST' })
  })

const movePhase = () =>
  act(async () => {
    if (!confirm(`Spostare il libro alla fase ${targetPhase.value} (${phaseLabel(targetPhase.value)})?`)) return
    book.value = await api(`/api/admin/books/${props.id}/phase`, {
      method: 'POST',
      body: { phase: Number(targetPhase.value) },
    })
  })

const remove = () =>
  act(async () => {
    await api(`/api/admin/books/${props.id}`, { method: 'DELETE' })
    router.push({ name: 'books' })
  })

function startEditOutput() {
  outputDraft.value = book.value.claude_output || ''
  editingOutput.value = true
}

function step(delta) {
  const n = book.value.photos.length
  lightbox.value = (lightbox.value + delta + n) % n
}

// Aggiornamento automatico finché il processo in background sta lavorando
watch(
  () => props.id,
  () => {
    book.value = null
    load()
  },
  { immediate: true },
)
timer = setInterval(() => {
  if (document.visibilityState === 'visible' && book.value && book.value.status !== 'idle') load()
}, 5000)
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="mx-auto" style="max-width: 56rem">
    <div class="d-flex align-items-center gap-2 mb-3">
      <RouterLink :to="{ name: 'books' }" class="btn btn-sm btn-light"><i class="bi bi-arrow-left"></i></RouterLink>
      <h1 class="h4 mb-0 text-break">
        <template v-if="book?.description">
          {{ book.description }} <span class="text-secondary fs-6 fw-normal">#{{ id }}</span>
        </template>
        <template v-else>Libro #{{ id }}</template>
      </h1>
    </div>

    <div v-if="error" class="alert alert-danger">{{ error }}</div>
    <div v-if="!book && !error" class="text-center py-5"><div class="spinner-border text-secondary"></div></div>

    <template v-if="book">
      <div v-if="actionError" class="alert alert-danger small">{{ actionError }}</div>

      <!-- Stato -->
      <div class="card border-0 shadow-sm mb-3">
        <div class="card-body">
          <StatusBadges :book="book" class="mb-2" />
          <p class="small text-secondary mb-2">
            {{ meta.phases.find((p) => p.number === book.phase)?.description }}
          </p>
          <div v-if="book.status === 'waiting'" class="small mb-2">
            Tentativo {{ book.attempts }} fallito: nuovo tentativo {{ fmtRelative(book.next_attempt_at) }}
            ({{ fmtDate(book.next_attempt_at) }}).
          </div>
          <div v-if="book.last_error" class="alert alert-warning small py-2 mb-2 pre-wrap">{{ book.last_error }}</div>

          <div class="d-flex flex-wrap gap-2 align-items-center">
            <button
              v-if="['failed', 'waiting'].includes(book.status)"
              class="btn btn-sm btn-outline-primary"
              :disabled="busy"
              @click="retry"
            >
              <i class="bi bi-arrow-clockwise me-1"></i>Riprova ora
            </button>
            <div class="input-group input-group-sm w-auto">
              <select v-model="targetPhase" class="form-select" aria-label="Fase">
                <option v-for="p in meta.phases" :key="p.number" :value="p.number">
                  {{ p.number }} · {{ p.label }}
                </option>
              </select>
              <button class="btn btn-outline-secondary" :disabled="busy" @click="movePhase">Sposta a fase</button>
            </div>
            <button class="btn btn-sm btn-outline-danger ms-auto" :disabled="busy" @click="confirmDelete = true">
              <i class="bi bi-trash me-1"></i>Elimina
            </button>
          </div>
        </div>
        <div class="card-footer bg-transparent small text-secondary">
          Inserito {{ fmtDate(book.created_at) }} da {{ book.created_by || '—' }} ·
          cartella <code class="user-select-all">{{ book.folder }}</code>
        </div>
      </div>

      <!-- Nome -->
      <h2 class="h6 text-uppercase text-secondary">Nome</h2>
      <div class="input-group mb-1">
        <input
          v-model="nameDraft"
          class="form-control"
          placeholder="Titolo - Autore"
          @keyup.enter="nameDirty && saveName()"
        />
        <button class="btn btn-primary" :disabled="!nameDirty || busy" @click="saveName">Salva</button>
      </div>
      <div class="mb-4 small">
        <span class="text-secondary">Ricavato dal JSON di Claude al termine della fase 1.</span>
        <button v-if="book.claude_output" class="btn btn-link btn-sm p-0 align-baseline" :disabled="busy" @click="nameFromOutput">
          Ricalcola dal JSON
        </button>
      </div>

      <!-- Foto -->
      <h2 class="h6 text-uppercase text-secondary">Fotografie ({{ book.photos.length }})</h2>
      <div class="d-flex flex-wrap gap-2 mb-4">
        <button
          v-for="(p, i) in book.photos"
          :key="p.id"
          type="button"
          class="btn p-0 border-0 position-relative"
          @click="lightbox = i"
        >
          <img :src="p.thumb_url" class="thumb shadow-sm" style="width: 96px; height: 96px" loading="lazy" alt="" />
          <span class="badge rounded-pill text-bg-dark position-absolute bottom-0 start-0 m-1" style="top: auto">{{ p.position }}</span>
        </button>
      </div>

      <!-- Note -->
      <h2 class="h6 text-uppercase text-secondary">Note</h2>
      <textarea v-model="notesDraft" class="form-control mb-2" rows="3"></textarea>
      <div class="mb-4">
        <button class="btn btn-sm btn-primary" :disabled="!notesDirty || busy" @click="saveNotes">Salva note</button>
        <button v-if="notesDirty" class="btn btn-sm btn-link" @click="notesDraft = book.notes">Annulla</button>
      </div>

      <!-- Risultato Claude -->
      <div class="d-flex align-items-center justify-content-between">
        <h2 class="h6 text-uppercase text-secondary mb-2">Risultato di Claude</h2>
        <button v-if="book.claude_output && !editingOutput" class="btn btn-sm btn-link" @click="startEditOutput">
          <i class="bi bi-pencil"></i> Modifica
        </button>
      </div>
      <div v-if="editingOutput" class="mb-4">
        <textarea v-model="outputDraft" class="form-control font-monospace small mb-2" rows="14"></textarea>
        <button class="btn btn-sm btn-primary" :disabled="busy" @click="saveOutput">Salva</button>
        <button class="btn btn-sm btn-link" @click="editingOutput = false">Annulla</button>
      </div>
      <div v-else-if="book.claude_output" class="card border-0 shadow-sm mb-4">
        <div class="card-body pre-wrap small">{{ book.claude_output }}</div>
      </div>
      <p v-else class="text-secondary small mb-4">Non ancora disponibile.</p>

      <!-- Storico -->
      <details class="mb-5">
        <summary class="h6 text-uppercase text-secondary">Storico elaborazioni ({{ book.runs.length }})</summary>
        <div v-if="!book.runs.length" class="small text-secondary mt-2">Nessuna elaborazione eseguita.</div>
        <div class="list-group mt-2">
          <div v-for="r in book.runs" :key="r.id" class="list-group-item small">
            <div class="d-flex justify-content-between flex-wrap gap-1">
              <span>
                <i class="bi" :class="r.success ? 'bi-check-circle text-success' : 'bi-x-circle text-danger'"></i>
                Fase {{ r.phase }} · {{ phaseLabel(r.phase) }} · tentativo {{ r.attempt }}
              </span>
              <span class="text-secondary">{{ fmtDate(r.started_at) }} → {{ fmtDate(r.finished_at) }}</span>
            </div>
            <div v-if="r.error" class="text-danger pre-wrap mt-1">{{ r.error }}</div>
            <div class="text-secondary mt-1 text-truncate">worker {{ r.worker }}</div>
          </div>
        </div>
      </details>

      <!-- Lightbox -->
      <BaseModal
        v-if="lightbox !== null"
        :title="`Foto ${lightbox + 1} di ${book.photos.length}`"
        size="xl"
        body-class="p-2 text-center"
        @close="lightbox = null"
      >
        <img :src="book.photos[lightbox].full_url" class="img-fluid rounded" style="max-height: 72vh" alt="" />
        <template #footer>
          <span class="small text-secondary me-auto">
            {{ book.photos[lightbox].filename }} · {{ book.photos[lightbox].width }}×{{ book.photos[lightbox].height }}
            · {{ fmtBytes(book.photos[lightbox].size_bytes) }}
          </span>
          <a :href="book.photos[lightbox].full_url" target="_blank" class="btn btn-sm btn-light" title="Apri originale">
            <i class="bi bi-box-arrow-up-right"></i>
          </a>
          <button class="btn btn-sm btn-light" @click="step(-1)"><i class="bi bi-chevron-left"></i></button>
          <button class="btn btn-sm btn-light" @click="step(1)"><i class="bi bi-chevron-right"></i></button>
        </template>
      </BaseModal>

      <BaseModal v-if="confirmDelete" title="Eliminare il libro?" @close="confirmDelete = false">
        Il libro #{{ book.id }}<template v-if="book.description"> ({{ book.description }})</template> e le sue {{ book.photos.length }} fotografie verranno eliminati definitivamente.
        <template #footer>
          <button class="btn btn-light" @click="confirmDelete = false">Annulla</button>
          <button class="btn btn-danger" :disabled="busy" @click="remove">Elimina</button>
        </template>
      </BaseModal>
    </template>
  </div>
</template>
