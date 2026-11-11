<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { auth, logout } from './auth'
import JobIndicators from './components/JobIndicators.vue'

const open = ref(false)
const route = useRoute()
const router = useRouter()
watch(() => route.fullPath, () => (open.value = false))

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
        <span class="d-none d-sm-inline">Mettete dei libri nei vostri cannoni</span>
        <span class="d-sm-none">Libri nei cannoni</span>
      </RouterLink>
      <JobIndicators class="order-md-last" />
      <button class="navbar-toggler ms-auto ms-md-0" type="button" aria-label="Menu" @click="open = !open">
        <span class="navbar-toggler-icon"></span>
      </button>
      <div class="collapse navbar-collapse" :class="{ show: open }">
        <ul class="navbar-nav ms-auto align-items-md-center">
          <li v-if="auth.user" class="nav-item">
            <RouterLink class="nav-link" to="/area">Area riservata</RouterLink>
          </li>
          <li v-if="auth.user?.role === 'admin'" class="nav-item">
            <RouterLink class="nav-link" to="/admin">Amministrazione</RouterLink>
          </li>
          <li v-if="auth.user" class="nav-item d-flex align-items-center gap-2 ms-md-3 py-2 py-md-0">
            <span class="text-white-50 small text-truncate" style="max-width: 14rem">{{ auth.user.email }}</span>
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
</template>
