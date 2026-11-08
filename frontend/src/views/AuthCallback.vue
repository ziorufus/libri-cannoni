<script setup>
// Il backend rimanda qui con #token=...&next=... dopo il login.
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { setToken } from '../api'
import { loadUser } from '../auth'

const router = useRouter()

onMounted(async () => {
  const params = new URLSearchParams(window.location.hash.slice(1))
  const token = params.get('token')
  let next = params.get('next') || '/'
  if (!next.startsWith('/') || next.startsWith('//')) next = '/'
  // Togli il token dalla barra degli indirizzi e dalla cronologia
  history.replaceState(null, '', window.location.pathname)
  if (!token) return router.replace({ name: 'login', query: { error: 'state' } })
  setToken(token)
  const user = await loadUser()
  if (!user) return router.replace({ name: 'login', query: { error: 'not_allowed' } })
  router.replace(next)
})
</script>

<template>
  <main class="container py-5 text-center text-secondary">
    <div class="spinner-border mb-3"></div>
    <p>Accesso in corso…</p>
  </main>
</template>
