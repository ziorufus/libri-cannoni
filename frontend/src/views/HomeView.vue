<script setup>
// Homepage pubblica: versamenti a Emergency e libri venduti in attesa di versamento.
import { computed, nextTick, onMounted, ref } from 'vue'
import { api } from '../api'
import BaseModal from '../components/BaseModal.vue'

const data = ref(null)
const error = ref('')
const selectedId = ref(null) // null = "Prossimo versamento"
const receiptOpen = ref(false)
const detail = ref(null)

const eur = (v) => (v ?? 0).toLocaleString('it-IT', { style: 'currency', currency: 'EUR' })
const fmtDay = (iso) =>
  iso ? new Date(`${iso}T12:00:00`).toLocaleDateString('it-IT', { day: 'numeric', month: 'long', year: 'numeric' }) : ''

const current = computed(() => data.value?.donations.find((d) => d.id === selectedId.value) || null)
const shownBooks = computed(() => (current.value ? current.value.books : data.value?.pending.books) || [])
const donatedTotal = computed(() => (data.value?.donations || []).reduce((t, d) => t + d.amount, 0))
const donatedBooks = computed(() => (data.value?.donations || []).reduce((t, d) => t + d.books.length, 0))

async function select(id) {
  selectedId.value = id
  receiptOpen.value = false
  // Su telefono l'elenco sta sopra il dettaglio: lo si porta in vista
  if (window.matchMedia('(max-width: 767.98px)').matches) {
    await nextTick()
    detail.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

onMounted(async () => {
  try {
    data.value = await api('/api/public/donations')
  } catch {
    error.value = 'Al momento non è possibile caricare i versamenti. Riprova più tardi.'
  }
})
</script>

<template>
  <main>
    <section class="py-4 border-bottom bg-white">
      <div class="container py-md-4" style="max-width: 50rem">
        <p class="text-uppercase small fw-semibold text-secondary mb-2">
          Salviamo libri, salviamo vite.
        </p>
        <h1 class="display-5 font-serif mb-3">Mettete dei libri nei vostri cannoni</h1>
        <p class="lead text-secondary mb-4">
          In questo sito trovate i libri che ho deciso di cedere in cambio di
          un'offerta per <a href="https://www.emergency.it" target="_blank" rel="noopener">Emergency</a>,
          l'associazione fondata da Gino Strada.
        </p>
      </div>
    </section>

    <section class="container py-5" style="max-width: 72rem">
      <div v-if="error" class="alert alert-light border text-center text-secondary">{{ error }}</div>
      <div v-else-if="!data" class="text-center py-5"><div class="spinner-border text-secondary"></div></div>
      <template v-else>
        <div class="d-flex flex-wrap align-items-baseline gap-2 mb-3">
          <h2 class="h4 font-serif mb-0">Versamenti a Emergency</h2>
          <span v-if="data.donations.length" class="text-secondary">
            · {{ eur(donatedTotal) }} versati finora per {{ donatedBooks }} libri
          </span>
        </div>
        <div class="row g-4">
          <!-- Elenco: prossimo versamento, poi i versamenti dal più recente -->
          <div class="col-md-4">
            <div class="list-group shadow-sm">
              <button
                type="button"
                class="list-group-item list-group-item-action py-3"
                :class="{ active: selectedId === null }"
                @click="select(null)"
              >
                <div class="fw-semibold"><i class="bi bi-hourglass-split me-1"></i>Prossimo versamento</div>
                <small :class="selectedId === null ? 'text-white-50' : 'text-secondary'">
                  {{ data.pending.books.length }} libri · {{ eur(data.pending.total) }}
                </small>
              </button>
              <button
                v-for="d in data.donations"
                :key="d.id"
                type="button"
                class="list-group-item list-group-item-action py-3"
                :class="{ active: selectedId === d.id }"
                @click="select(d.id)"
              >
                <div class="d-flex justify-content-between gap-2">
                  <span class="fw-semibold">{{ fmtDay(d.date) }}</span>
                  <span class="fw-semibold text-nowrap">{{ eur(d.amount) }}</span>
                </div>
                <small :class="selectedId === d.id ? 'text-white-50' : 'text-secondary'">{{ d.books.length }} libri</small>
              </button>
            </div>
          </div>

          <!-- Dettaglio -->
          <div ref="detail" class="col-md-8">
            <div class="card border-0 shadow-sm">
              <div class="card-body p-md-4">
                <template v-if="current">
                  <div class="d-flex flex-wrap align-items-start gap-3 mb-3">
                    <div class="flex-grow-1">
                      <h3 class="h5 mb-1">Versamento del {{ fmtDay(current.date) }}</h3>
                      <div class="fs-4 fw-semibold text-danger-emphasis">{{ eur(current.amount) }}</div>
                      <p v-if="current.description" class="mb-0 mt-2" style="white-space: pre-line">{{ current.description }}</p>
                    </div>
                    <button
                      v-if="current.receipt_thumb_url"
                      type="button"
                      class="btn p-0 border-0 text-center"
                      title="Ingrandisci la ricevuta"
                      @click="receiptOpen = true"
                    >
                      <img :src="current.receipt_thumb_url" class="rounded border shadow-sm receipt-thumb" alt="Ricevuta del bonifico" />
                      <small class="d-block text-secondary mt-1"><i class="bi bi-zoom-in me-1"></i>Ricevuta</small>
                    </button>
                  </div>
                  <h4 class="h6 text-uppercase text-secondary">Libri compresi ({{ current.books.length }})</h4>
                </template>
                <template v-else>
                  <h3 class="h5 mb-1">Prossimo versamento</h3>
                  <p class="text-secondary mb-3">
                    Libri già ceduti: il ricavato, {{ eur(data.pending.total) }}, sarà compreso nel prossimo versamento a
                    Emergency.
                  </p>
                </template>

                <ul v-if="shownBooks.length" class="list-unstyled mb-0">
                  <li v-for="b in shownBooks" :key="b.id" class="d-flex align-items-center gap-3 py-2 border-top">
                    <img v-if="b.cover_url" :src="b.cover_url" class="rounded cover" loading="lazy" alt="" />
                    <div v-else class="rounded cover bg-body-tertiary d-flex align-items-center justify-content-center text-secondary">
                      <i class="bi bi-book"></i>
                    </div>
                    <div class="flex-grow-1" style="min-width: 0">
                      <div class="text-break">{{ b.title }}</div>
                      <small v-if="b.sold_at" class="text-secondary">ceduto il {{ fmtDay(b.sold_at) }}</small>
                    </div>
                    <span class="fw-semibold text-nowrap">{{ eur(b.sold_price) }}</span>
                  </li>
                </ul>
                <p v-else class="text-secondary mb-0">
                  <i class="bi bi-bookshelf me-1"></i>
                  {{ current ? 'Nessun libro in questo versamento.' : 'Al momento non ci sono libri in attesa di versamento.' }}
                </p>
              </div>
            </div>
          </div>
        </div>
      </template>

      <BaseModal v-if="receiptOpen && current" :title="`Ricevuta del ${fmtDay(current.date)}`" size="lg" @close="receiptOpen = false">
        <img :src="current.receipt_url" class="img-fluid rounded mx-auto d-block" alt="Ricevuta del bonifico" />
      </BaseModal>
    </section>
  </main>
</template>

<style scoped>
.list-group-item.active {
  background-color: var(--lc-red);
  border-color: var(--lc-red);
}
.cover {
  width: 44px;
  height: 60px;
  object-fit: cover;
  flex-shrink: 0;
}
.receipt-thumb {
  width: 96px;
  height: 128px;
  object-fit: cover;
}
</style>
