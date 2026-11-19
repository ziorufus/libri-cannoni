<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { auth, logout } from './auth'
import JobIndicators from './components/JobIndicators.vue'

const open = ref(false)
const userOpen = ref(false)
const userMenu = ref(null)
const route = useRoute()
const router = useRouter()
watch(() => route.fullPath, () => {
  open.value = false
  userOpen.value = false
})

function onDocClick(e) {
  if (userOpen.value && userMenu.value && !userMenu.value.contains(e.target)) userOpen.value = false
}
function onKey(e) {
  if (e.key === 'Escape') userOpen.value = false
}
onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})

function doLogout() {
  logout()
  router.push({ name: 'home' })
}
</script>

<template>
  <nav class="navbar navbar-expand-md navbar-dark navbar-lc sticky-top shadow-sm">
    <div class="container">
      <RouterLink class="navbar-brand font-serif d-flex align-items-center gap-2" to="/">
        <i class="bi bi-book"></i>
        <span>Mettete dei libri nei vostri cannoni</span>
      </RouterLink>
      <JobIndicators class="order-md-last" />
      <button class="navbar-toggler ms-auto ms-md-0" type="button" aria-label="Menu" @click="open = !open">
        <span class="navbar-toggler-icon"></span>
      </button>
      <div class="collapse navbar-collapse" :class="{ show: open }">
        <ul class="navbar-nav ms-auto align-items-md-center">
          <li v-if="auth.user" class="nav-item">
            <RouterLink class="nav-link text-nowrap" to="/area">Area riservata</RouterLink>
          </li>
          <li v-if="auth.user?.role === 'admin'" class="nav-item">
            <RouterLink class="nav-link text-nowrap" to="/admin">Amministrazione</RouterLink>
          </li>
          <li v-if="auth.user" class="nav-item d-flex align-items-center gap-2 ms-md-3 py-2 py-md-0">
            <div ref="userMenu" class="position-relative">
              <button
                class="btn btn-sm btn-outline-light user-btn"
                type="button"
                aria-label="Utente"
                :aria-expanded="userOpen"
                @click="userOpen = !userOpen"
              >
                <i class="bi bi-person-fill"></i>
              </button>
              <div v-if="userOpen" class="dropdown-menu show user-popup shadow">
                <div class="px-3 py-1 small text-muted">Accesso come</div>
                <div class="px-3 pb-1 text-break">{{ auth.user.email }}</div>
              </div>
            </div>
            <button class="btn btn-sm btn-outline-light" @click="doLogout">
              <i class="bi bi-box-arrow-right"></i> Esci
            </button>
          </li>
          <li v-else-if="auth.loaded" class="nav-item">
            <RouterLink class="nav-link" :to="{ name: 'login' }"><i class="bi bi-person-circle"></i> Accedi</RouterLink>
          </li>
        </ul>
      </div>
    </div>
  </nav>

  <RouterView />

  <footer class="site-footer mt-auto py-4 border-top small text-secondary text-center">
    <div class="container">
      <p class="mb-1">
        Questo è un sito personale, fatto col
        <i class="bi bi-heart-fill text-danger-emphasis" role="img" aria-label="cuore"></i>
        (e un po' di
        <i class="bi bi-robot" role="img" aria-label="intelligenza artificiale"></i>)
        da Alessio Palmero Aprosio.
      </p>
      <p class="mb-0">
        I libri mi sono stati regalati. Il ricavato viene interamente versato a Emergency, senza trattenute.
        Il sito non è affiliato a Emergency.
      </p>
    </div>
  </footer>
</template>
