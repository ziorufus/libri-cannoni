<script setup>
// Modale Bootstrap gestita da Vue (senza il JavaScript di Bootstrap).
import { onBeforeUnmount, onMounted } from 'vue'

defineProps({
  title: { type: String, default: '' },
  size: { type: String, default: '' }, // '', 'sm', 'lg', 'xl', 'fullscreen-sm-down'
  bodyClass: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const onKey = (e) => e.key === 'Escape' && emit('close')
onMounted(() => {
  document.addEventListener('keydown', onKey)
  document.body.classList.add('modal-open')
})
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKey)
  document.body.classList.remove('modal-open')
})
</script>

<template>
  <Teleport to="body">
    <div class="modal d-block" tabindex="-1" role="dialog" aria-modal="true" @click.self="emit('close')">
      <div
        class="modal-dialog modal-dialog-centered modal-dialog-scrollable"
        :class="size ? `modal-${size}` : ''"
      >
        <div class="modal-content">
          <div v-if="title || $slots.header" class="modal-header">
            <slot name="header">
              <h5 class="modal-title">{{ title }}</h5>
            </slot>
            <button type="button" class="btn-close" aria-label="Chiudi" @click="emit('close')"></button>
          </div>
          <div class="modal-body" :class="bodyClass"><slot /></div>
          <div v-if="$slots.footer" class="modal-footer"><slot name="footer" /></div>
        </div>
      </div>
    </div>
    <div class="modal-backdrop show"></div>
  </Teleport>
</template>
