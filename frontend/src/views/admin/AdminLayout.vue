<script setup>
import { onMounted } from 'vue'
import { loadMeta } from '../../meta'

onMounted(() => loadMeta().catch(() => {}))
</script>

<template>
  <div class="container py-3 py-md-4">
    <ul class="nav nav-pills mb-3 mb-md-4 flex-nowrap overflow-auto">
      <li class="nav-item">
        <RouterLink class="nav-link text-nowrap" :to="{ name: 'books' }" active-class="active">
          <i class="bi bi-book me-1"></i>Libri
        </RouterLink>
      </li>
      <li class="nav-item">
        <RouterLink class="nav-link text-nowrap" :to="{ name: 'access' }" active-class="active">
          <i class="bi bi-people me-1"></i>Accessi
        </RouterLink>
      </li>
      <li class="nav-item">
        <RouterLink class="nav-link text-nowrap" :to="{ name: 'vinted' }" active-class="active">
          <i class="bi bi-key me-1"></i>Vinted
        </RouterLink>
      </li>
      <li class="nav-item">
        <RouterLink class="nav-link text-nowrap" :to="{ name: 'settings' }" active-class="active">
          <i class="bi bi-gear me-1"></i>Impostazioni
        </RouterLink>
      </li>
    </ul>
    <RouterView />
  </div>
</template>
