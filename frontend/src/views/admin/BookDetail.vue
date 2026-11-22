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
// Prezzo: testo libero ("6,50" o "6.5"); vuoto = nessun prezzo
const priceText = (v) => (v === null || v === undefined ? '' : v.toFixed(2).replace('.', ','))
const parsePrice = (s) => {
  const t = s.trim().replace(/\s*€$/, '').replace(',', '.')
  if (!t) return null
  return /^\d+(\.\d{1,2})?$/.test(t) ? Number(t) : NaN
}
const priceDraft = ref('')
const priceDirty = computed(() => book.value && !Object.is(parsePrice(priceDraft.value), book.value.price ?? null))
const priceInvalid = computed(() => Number.isNaN(parsePrice(priceDraft.value)))
const editingOutput = ref(false)
const outputDraft = ref('')

const vinted = computed(() => book.value?.vinted_prices || null)
// Libro fermo in una fase automatica (es. arrivato lì prima che la fase esistesse)
const idleAutomatic = computed(
  () => book.value?.status === 'idle' && meta.phases.find((p) => p.number === book.value.phase)?.automatic,
)

function fmtPrice(amount, currency) {
  if (amount === null || amount === undefined) return '—'
  try {
    return new Intl.NumberFormat('it-IT', { style: 'currency', currency: currency || 'EUR' }).format(amount)
  } catch {
    return `${amount} ${currency || ''}`.trim()
  }
}

// Annuncio uscito dall'approvazione: fasi 6-9, cambiate solo dalle azioni qui sotto
const LISTING = {
  6: { label: 'Pubblicato', cls: 'text-bg-success', icon: 'bi-check-circle' },
  7: { label: 'Nascosto', cls: 'text-bg-secondary', icon: 'bi-eye-slash' },
  8: { label: 'Riservato', cls: 'text-bg-warning', icon: 'bi-bookmark-check' },
  9: { label: 'Venduto', cls: 'text-bg-dark', icon: 'bi-bag-check' },
  10: { label: 'Versato', cls: 'text-bg-danger', icon: 'bi-heart-fill' },
}
const listing = computed(() => LISTING[book.value?.phase] || null)
const movablePhases = computed(() => meta.phases.filter((p) => !LISTING[p.number]))
const listingMessage = ref('')
const soldModal = ref(false)
const soldDraft = ref('')
const soldAction = ref('delete') // cosa fare dell'annuncio su Vinted
const SOLD_ACTIONS = [
  { value: 'none', label: "Non modificare l'annuncio", help: 'Resta su Vinted così com\'è (es. venduto proprio su Vinted).' },
  { value: 'hide', label: "Nascondi l'annuncio", help: 'Non più visibile né acquistabile; si può rimostrare dal sito.' },
  { value: 'delete', label: "Elimina l'annuncio", help: 'Rimosso definitivamente da Vinted (non si può annullare).' },
]
const LISTING_ACTION_TEXT = { none: 'annuncio non modificato', hide: 'annuncio nascosto', delete: 'annuncio eliminato' }
const soldInvalid = computed(() => Number.isNaN(parsePrice(soldDraft.value)))
const soldPriceDraft = ref('')
const soldPriceDirty = computed(
  () => book.value && !Object.is(parsePrice(soldPriceDraft.value), book.value.sold_price ?? null),
)

const lightbox = ref(null) // indice della foto aperta
const confirmDelete = ref(false)
const confirmDeleteDraft = ref(false)
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
    const keepPrice = !first && priceDirty.value
    book.value = b
    if (!keepName) nameDraft.value = b.description || ''
    if (!keepNotes) notesDraft.value = b.notes
    if (!keepPrice) priceDraft.value = priceText(b.price)
    if (first || !soldPriceDirty.value) soldPriceDraft.value = priceText(b.sold_price)
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

const savePrice = () =>
  act(async () => {
    if (priceInvalid.value) throw new Error('Prezzo non valido: scrivi un importo come 6,50 (o lascia vuoto)')
    book.value = await api(`/api/admin/books/${props.id}`, {
      method: 'PATCH',
      body: { price: parsePrice(priceDraft.value) },
    })
    priceDraft.value = priceText(book.value.price)
  })

const saveCondition = (value) =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}`, { method: 'PATCH', body: { condition: value } })
  })

const deleteDraft = () =>
  act(async () => {
    try {
      book.value = await api(`/api/admin/books/${props.id}/delete-draft`, { method: 'POST' })
    } finally {
      confirmDeleteDraft.value = false
    }
  })

const refreshListing = () =>
  act(async () => {
    listingMessage.value = ''
    const { message, ...b } = await api(`/api/admin/books/${props.id}/vinted-refresh`, { method: 'POST' })
    book.value = b
    priceDraft.value = priceText(b.price)
    soldPriceDraft.value = priceText(b.sold_price)
    listingMessage.value = message
  })

const setHidden = (hidden) =>
  act(async () => {
    listingMessage.value = ''
    book.value = await api(`/api/admin/books/${props.id}/vinted-${hidden ? 'hide' : 'unhide'}`, { method: 'POST' })
  })

function openSold() {
  actionError.value = ''
  soldDraft.value = priceText(book.value.price)
  soldAction.value = 'delete'
  soldModal.value = true
}

const markSold = () =>
  act(async () => {
    if (soldInvalid.value) throw new Error('Prezzo non valido: scrivi un importo come 6,50')
    listingMessage.value = ''
    book.value = await api(`/api/admin/books/${props.id}/mark-sold`, {
      method: 'POST',
      body: { sold_price: parsePrice(soldDraft.value), listing_action: soldAction.value },
    })
    soldPriceDraft.value = priceText(book.value.sold_price)
    soldModal.value = false
  })

const saveSoldPrice = () =>
  act(async () => {
    const value = parsePrice(soldPriceDraft.value)
    if (Number.isNaN(value)) throw new Error('Prezzo non valido: scrivi un importo come 6,50 (o lascia vuoto)')
    book.value = await api(`/api/admin/books/${props.id}`, { method: 'PATCH', body: { sold_price: value } })
    soldPriceDraft.value = priceText(book.value.sold_price)
  })

const forcePhase = () =>
  act(async () => {
    if (!confirm(`Eseguire la fase ${book.value.phase} (${phaseLabel(book.value.phase)}) per questo libro anche se è in pausa?`)) return
    book.value = await api(`/api/admin/books/${props.id}/force`, { method: 'POST' })
  })

const conditionFromOutput = () =>
  act(async () => {
    book.value = await api(`/api/admin/books/${props.id}/condition-from-output`, { method: 'POST' })
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
          <div v-if="book.blocks?.length" class="alert alert-secondary small py-2 mb-2">
            <div v-for="(b, i) in book.blocks" :key="i">
              <i class="bi me-1" :class="b.kind === 'pause' ? 'bi-pause-circle' : 'bi-lock'"></i>{{ b.message }}
            </div>
          </div>
          <div v-if="book.forced" class="small text-secondary mb-2">
            <i class="bi bi-fast-forward me-1"></i>Esecuzione forzata: questa fase parte anche se è in pausa.
          </div>
          <div v-if="book.last_error && book.status !== 'blocked'" class="alert alert-warning small py-2 mb-2 pre-wrap">
            {{ book.last_error }}
          </div>

          <div class="d-flex flex-wrap gap-2 align-items-center">
            <button
              v-if="['failed', 'waiting'].includes(book.status)"
              class="btn btn-sm btn-outline-primary"
              :disabled="busy"
              @click="retry"
            >
              <i class="bi bi-arrow-clockwise me-1"></i>Riprova ora
            </button>
            <button
              v-if="idleAutomatic && !book.blocks?.length"
              class="btn btn-sm btn-outline-primary"
              :disabled="busy"
              @click="retry"
            >
              <i class="bi bi-play me-1"></i>Esegui la fase
            </button>
            <button v-if="book.can_force" class="btn btn-sm btn-outline-primary" :disabled="busy" @click="forcePhase">
              <i class="bi bi-fast-forward me-1"></i>Esegui comunque
            </button>
            <div v-if="!listing" class="input-group input-group-sm w-auto">
              <select v-model="targetPhase" class="form-select" aria-label="Fase">
                <option v-for="p in movablePhases" :key="p.number" :value="p.number">
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
        <button v-if="book.claude_output" class="btn btn-link btn-sm p-0 align-baseline ms-1" :disabled="busy" @click="nameFromOutput">
          Ricalcola dal JSON
        </button>
      </div>

      <!-- Condizioni -->
      <h2 class="h6 text-uppercase text-secondary">Condizioni</h2>
      <select
        class="form-select mb-1 text-capitalize"
        style="max-width: 20rem"
        :value="book.condition || ''"
        :disabled="busy"
        @change="saveCondition($event.target.value)"
      >
        <option v-for="c in meta.conditions || []" :key="c" :value="c">{{ c }}</option>
        <option value="">Non indicate (le prende Claude)</option>
      </select>
      <div class="small mb-4">
        <span class="text-secondary">
          <template v-if="!book.condition">
            Verranno prese dal JSON di Claude al termine della fase 1, se il valore è valido.
          </template>
          <template v-else-if="book.condition_manual">
            Scelte a mano: valgono anche per la bozza su Vinted.
          </template>
          <template v-else>
            Prese da Claude: per la bozza su Vinted si usa il campo "condizioni_vinted" di Claude.
          </template>
        </span>
        <button
          v-if="book.claude_output"
          class="btn btn-link btn-sm p-0 ms-1 align-baseline"
          :disabled="busy"
          @click="conditionFromOutput"
        >
          Ricalcola dal JSON
        </button>
      </div>

      <!-- Prezzi su Vinted -->
      <h2 class="h6 text-uppercase text-secondary">Prezzi su Vinted</h2>
      <div class="mb-4">
        <template v-if="vinted">
          <div class="small text-secondary mb-2">
            Ricerca per ISBN <span class="user-select-all">{{ vinted.query }}</span>
            <template v-if="vinted.conditions?.length"> · condizioni {{ vinted.conditions.join(', ') }}</template>
            · {{ fmtRelative(vinted.searched_at) }}
          </div>
          <div v-if="vinted.summary" class="d-flex flex-wrap gap-2 mb-2">
            <span class="badge text-bg-light border fw-normal fs-6">
              min <strong>{{ fmtPrice(vinted.summary.min, vinted.items[0]?.currency) }}</strong>
            </span>
            <span class="badge text-bg-primary fw-normal fs-6">
              mediana <strong>{{ fmtPrice(vinted.summary.median, vinted.items[0]?.currency) }}</strong>
            </span>
            <span class="badge text-bg-light border fw-normal fs-6">
              max <strong>{{ fmtPrice(vinted.summary.max, vinted.items[0]?.currency) }}</strong>
            </span>
          </div>
          <div
            v-if="vinted.check"
            class="small mb-2"
            :class="vinted.check.compatible?.length ? 'text-secondary' : 'text-danger'"
          >
            <template v-if="vinted.check.compatible?.length">
              Claude: {{ vinted.check.compatible.length }} annunci compatibili su {{ vinted.items.length }}
              (la mediana è calcolata solo su questi)
            </template>
            <template v-else>Claude: nessuno degli annunci riguarda questo libro, inserisci il prezzo a mano</template>
            <template v-if="vinted.check.reason"> — {{ vinted.check.reason }}</template>
          </div>
          <div v-if="vinted.items.length" class="list-group shadow-sm">
            <a
              v-for="it in vinted.items"
              :key="it.id"
              :href="it.url"
              target="_blank"
              rel="noopener"
              class="list-group-item list-group-item-action d-flex gap-3 align-items-baseline small"
              :class="{ 'text-decoration-line-through text-secondary': it.compatible === false }"
              :title="it.compatible === false ? 'Scartato da Claude: non riguarda questo libro' : undefined"
            >
              <strong class="text-nowrap" style="min-width: 4.5rem">{{ fmtPrice(it.price, it.currency) }}</strong>
              <span class="flex-grow-1 text-break">{{ it.title }}</span>
              <span class="text-secondary text-nowrap">{{ it.condition }}</span>
            </a>
          </div>
          <p v-else class="small text-secondary mb-0">Nessun annuncio trovato.</p>
        </template>
        <p v-else-if="book.phase > 2 && !book.isbn" class="small text-secondary mb-0">
          Ricerca saltata: il JSON di Claude non contiene un ISBN.
        </p>
        <p v-else-if="idleAutomatic && book.phase === 2" class="small text-secondary mb-0">
          Ricerca non ancora eseguita<template v-if="book.isbn"> (ISBN {{ book.isbn }})</template>: premi
          «Esegui la fase» per avviarla.
        </p>
        <p v-else class="small text-secondary mb-0">
          <template v-if="book.isbn">La ricerca per ISBN {{ book.isbn }} verrà fatta nella fase 2.</template>
          <template v-else>La ricerca verrà fatta nella fase 2, se il JSON di Claude contiene l'ISBN.</template>
        </p>
      </div>

      <!-- Prezzo -->
      <h2 class="h6 text-uppercase text-secondary">Prezzo</h2>
      <div class="input-group mb-1" style="max-width: 20rem">
        <input
          v-model="priceDraft"
          class="form-control"
          :class="{ 'is-invalid': priceInvalid }"
          inputmode="decimal"
          placeholder="Non impostato"
          aria-label="Prezzo in euro"
          @keyup.enter="priceDirty && savePrice()"
        />
        <span class="input-group-text">€</span>
        <button class="btn btn-primary" :disabled="!priceDirty || priceInvalid || busy" @click="savePrice">Salva</button>
      </div>
      <div class="small mb-4">
        <span class="text-secondary">
          Se è vuoto, al termine della fase 2 viene calcolato (mediana di Vinted o prezzo di Claude).
          Senza prezzo la bozza su Vinted non viene creata.
        </span>
        <button
          v-if="book.suggested_price !== null && book.suggested_price !== book.price"
          class="btn btn-link btn-sm p-0 ms-1 align-baseline"
          :disabled="busy"
          @click="priceDraft = priceText(book.suggested_price)"
        >
          Usa il prezzo calcolato ({{ fmtPrice(book.suggested_price, 'EUR') }})
        </button>
      </div>

      <!-- Bozza su Vinted -->
      <h2 class="h6 text-uppercase text-secondary">Annuncio su Vinted</h2>
      <div class="mb-4">
        <div v-if="book.vinted_draft" class="card border-0 shadow-sm">
          <div class="card-body small">
            <a :href="book.vinted_draft.edit_url" target="_blank" rel="noopener" class="fw-semibold">
              <i class="bi bi-box-arrow-up-right me-1"></i>Bozza {{ book.vinted_draft.draft_id }}
            </a>
            <span class="text-secondary"> · creata {{ fmtRelative(book.vinted_draft.created_at) }}</span>
            <div class="mt-1">
              <template v-if="listing">
                <div class="d-flex flex-wrap align-items-center gap-1">
                  <span class="badge me-1" :class="listing.cls"><i class="bi me-1" :class="listing.icon"></i>{{ listing.label }}</span>
                  <span class="text-secondary">pubblicato {{ fmtRelative(book.vinted_draft.published_at) }} · </span>
                  <a
                    v-if="!(book.sold_manual && book.vinted_draft.listing_action === 'delete')"
                    :href="book.vinted_draft.item_url"
                    target="_blank"
                    rel="noopener"
                  >
                    <i class="bi bi-box-arrow-up-right me-1"></i>vedi l'annuncio
                  </a>
                  <button
                    v-if="!book.sold_manual && book.phase <= 9"
                    class="btn btn-sm btn-link py-0"
                    :disabled="busy"
                    title="Legge stato e prezzo dall'annuncio su Vinted"
                    @click="refreshListing"
                  >
                    <i class="bi bi-arrow-repeat me-1"></i>Aggiorna
                  </button>
                </div>
                <div v-if="listingMessage" class="alert alert-info py-1 px-2 small mt-2 mb-0">{{ listingMessage }}</div>
                <div v-if="book.donation" class="mt-2">
                  <i class="bi bi-heart-fill text-danger me-1"></i>Versato a Emergency con il
                  <RouterLink :to="{ name: 'donations' }">versamento del {{ new Date(book.donation.date + 'T12:00:00').toLocaleDateString('it-IT') }}</RouterLink>
                  ({{ fmtPrice(book.donation.amount, 'EUR') }})
                </div>
                <div v-if="book.phase >= 9" class="mt-2">
                  <div class="text-secondary mb-1">
                    Venduto {{ book.sold_at ? fmtDate(book.sold_at) : '' }} · prezzo dell'annuncio {{ fmtPrice(book.price, 'EUR') }}
                    <template v-if="!book.sold_manual && book.vinted_draft.sold_price_source">
                      · incasso {{ { order: "dall'ordine Vinted", listing: 'provvisorio (prezzo dell\'annuncio, ordine non ancora trovato)', manual: 'corretto a mano' }[book.vinted_draft.sold_price_source] }}
                    </template>
                    <template v-if="book.sold_manual">
                      · segnato a mano, {{ LISTING_ACTION_TEXT[book.vinted_draft.listing_action] || 'annuncio non modificato' }}
                      (non si sincronizza più con Vinted)
                    </template>
                  </div>
                  <div v-if="book.phase === 10" class="small">Incassato <strong>{{ fmtPrice(book.sold_price, 'EUR') }}</strong></div>
                  <div v-else class="input-group input-group-sm" style="max-width: 18rem">
                    <span class="input-group-text">Incassato</span>
                    <input
                      v-model="soldPriceDraft"
                      class="form-control"
                      inputmode="decimal"
                      placeholder="—"
                      @keyup.enter="soldPriceDirty && saveSoldPrice()"
                    />
                    <span class="input-group-text">€</span>
                    <button class="btn btn-primary" :disabled="busy || !soldPriceDirty" @click="saveSoldPrice">Salva</button>
                  </div>
                </div>
                <div v-if="book.status !== 'running'" class="d-flex flex-wrap gap-2 mt-2">
                  <button v-if="book.phase === 6" class="btn btn-sm btn-outline-secondary" :disabled="busy" @click="setHidden(true)">
                    <i class="bi bi-eye-slash me-1"></i>Nascondi
                  </button>
                  <button v-if="book.phase === 7" class="btn btn-sm btn-outline-success" :disabled="busy" @click="setHidden(false)">
                    <i class="bi bi-eye me-1"></i>Mostra di nuovo
                  </button>
                  <button v-if="book.phase >= 6 && book.phase <= 8" class="btn btn-sm btn-outline-dark" :disabled="busy" @click="openSold">
                    <i class="bi bi-bag-check me-1"></i>Segna come venduto
                  </button>
                </div>
              </template>
              <template v-else-if="book.vinted_draft.publish_requested_at">
                <span class="badge text-bg-info me-1"><i class="bi bi-hourglass-split me-1"></i>In approvazione</span>
                <span class="text-secondary">pubblicazione richiesta {{ fmtRelative(book.vinted_draft.publish_requested_at) }}</span>
              </template>
              <template v-else>
                <span class="badge text-bg-light border me-1">Bozza</span>
                <span v-if="book.vinted_draft.returned_to_draft_at" class="text-danger">
                  riportata in bozza da Vinted {{ fmtRelative(book.vinted_draft.returned_to_draft_at) }}
                </span>
              </template>
            </div>
            <div class="text-secondary mt-1">
              {{ fmtPrice(book.vinted_draft.price, book.vinted_draft.currency) }} · {{ book.vinted_draft.category }} ·
              {{ book.vinted_draft.condition }} · {{ book.vinted_draft.language }} · ISBN {{ book.vinted_draft.isbn }} ·
              {{ book.vinted_draft.photos }} foto
            </div>
            <button
              v-if="book.status !== 'running' && book.phase <= 4"
              class="btn btn-sm btn-outline-danger mt-2"
              :disabled="busy"
              @click="confirmDeleteDraft = true"
            >
              <i class="bi bi-trash me-1"></i>Elimina bozza e torna alla fase 3
            </button>
          </div>
        </div>
        <p v-else class="small text-secondary mb-0">Verrà creata nella fase 3.</p>
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

      <BaseModal v-if="soldModal" title="Segna come venduto" @close="soldModal = false">
        <p class="small text-secondary">
          Il libro passa a "Venduto" e da quel momento non viene più aggiornato da Vinted. Inserisci la cifra
          effettivamente incassata (proposta: il prezzo dell'annuncio).
        </p>
        <label class="form-label fw-semibold small" for="sold-price">Incassato</label>
        <div class="input-group mb-1" style="max-width: 14rem">
          <input id="sold-price" v-model="soldDraft" class="form-control" inputmode="decimal" placeholder="es. 6,50" />
          <span class="input-group-text">€</span>
        </div>
        <div v-if="soldInvalid" class="small text-danger">Importo non valido</div>
        <div class="form-label fw-semibold small mt-3">Annuncio su Vinted</div>
        <div v-for="a in SOLD_ACTIONS" :key="a.value" class="form-check mb-1">
          <input :id="`sold-${a.value}`" v-model="soldAction" class="form-check-input" type="radio" :value="a.value" />
          <label class="form-check-label small" :for="`sold-${a.value}`">
            <span class="fw-semibold">{{ a.label }}</span>
            <span class="d-block text-secondary">{{ a.help }}</span>
          </label>
        </div>
        <div v-if="actionError" class="alert alert-danger small py-2 mt-3 mb-0">{{ actionError }}</div>
        <template #footer>
          <button class="btn btn-light" @click="soldModal = false">Annulla</button>
          <button class="btn btn-dark" :disabled="busy || soldInvalid" @click="markSold">
            <span v-if="busy" class="spinner-border spinner-border-sm me-1"></span>
            <i v-else class="bi bi-bag-check me-1"></i>{{ soldAction === 'delete' ? 'Segna venduto ed elimina' : 'Segna come venduto' }}
          </button>
        </template>
      </BaseModal>

      <BaseModal v-if="confirmDeleteDraft" title="Eliminare la bozza su Vinted?" @close="confirmDeleteDraft = false">
        La bozza {{ book.vinted_draft?.draft_id }} verrà eliminata definitivamente da Vinted e dal libro. Il libro
        tornerà alla fase 3 (Bozza Vinted), fermo: potrai ricreare la bozza con «Esegui la fase» o «Esegui comunque».
        <template #footer>
          <button class="btn btn-light" @click="confirmDeleteDraft = false">Annulla</button>
          <button class="btn btn-danger" :disabled="busy" @click="deleteDraft">
            <span v-if="busy" class="spinner-border spinner-border-sm me-1"></span>Elimina bozza
          </button>
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
