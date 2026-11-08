<script setup>
import { auth } from '../auth'
</script>

<template>
  <main class="container py-5" style="max-width: 46rem">
    <h1 class="h3 font-serif">Area riservata</h1>
    <p class="text-secondary">Ciao {{ auth.user?.name || auth.user?.email }}, questa sezione è ancora in costruzione.</p>
  </main>
</template>
