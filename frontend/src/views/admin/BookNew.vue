<script setup>
// Inserimento di un libro: si scattano N foto con getUserMedia(), se ne vedono
// le miniature (nell'ordine di scatto), si eliminano quelle venute male e si
// conferma. Le foto inviate al backend sono quelle ad alta risoluzione.
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { uploadForm } from '../../api'
import BaseModal from '../../components/BaseModal.vue'
import { fmtBytes } from '../../utils'

const router = useRouter()

const video = ref(null)
const stream = ref(null)
const starting = ref(false)
const cameraError = ref('')
const devices = ref([])
const deviceId = ref('')
const resolution = ref('')
const flash = ref(false)
const capturing = ref(false)
let imageCapture = null

const photos = ref([]) // { key, blob, thumb, width, height }
const notes = ref('')
const preview = ref(null) // { index, url }
const strip = ref(null)

const uploading = ref(false)
const progress = ref(0)
const submitError = ref('')
let submitted = false

const totalSize = computed(() => photos.value.reduce((s, p) => s + p.blob.size, 0))

// --- fotocamera --------------------------------------------------------------

function stopCamera() {
  stream.value?.getTracks().forEach((t) => t.stop())
  stream.value = null
  imageCapture = null
}

async function startCamera() {
  stopCamera()
  cameraError.value = ''
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraError.value = window.isSecureContext
      ? 'Questo browser non permette di usare la fotocamera.'
      : 'La fotocamera funziona solo in HTTPS (o su localhost). Puoi comunque caricare le foto da file.'
    return
  }
  starting.value = true
  const constraints = {
    audio: false,
    video: {
      width: { ideal: 4096 },
      height: { ideal: 3072 },
      ...(deviceId.value ? { deviceId: { exact: deviceId.value } } : { facingMode: { ideal: 'environment' } }),
    },
  }
  try {
    const s = await navigator.mediaDevices.getUserMedia(constraints)
    stream.value = s
    await nextTick()
    video.value.srcObject = s
    await video.value.play().catch(() => {})
    const track = s.getVideoTracks()[0]
    const settings = track.getSettings()
    resolution.value = settings.width ? `${settings.width}×${settings.height}` : ''
    if (!deviceId.value) deviceId.value = settings.deviceId || ''
    imageCapture = 'ImageCapture' in window ? new window.ImageCapture(track) : null
    // Le etichette dei dispositivi sono disponibili solo dopo il permesso
    const all = await navigator.mediaDevices.enumerateDevices()
    devices.value = all.filter((d) => d.kind === 'videoinput')
  } catch (e) {
    const msg = {
      NotAllowedError: 'Permesso di usare la fotocamera negato. Abilitalo nelle impostazioni del browser.',
      NotFoundError: 'Nessuna fotocamera trovata.',
      NotReadableError: 'La fotocamera è usata da un’altra applicazione.',
      OverconstrainedError: 'La fotocamera scelta non è disponibile.',
    }[e.name]
    cameraError.value = msg || `Impossibile avviare la fotocamera (${e.message || e.name}).`
  } finally {
    starting.value = false
  }
}

// Alcuni browser restituiscono PNG: a piena risoluzione pesano troppo,
// quindi li ricodifichiamo in JPEG di alta qualità (stessa risoluzione).
async function toJpeg(blob) {
  if (blob.type === 'image/jpeg') return blob
  try {
    const bmp = await createImageBitmap(blob)
    const canvas = document.createElement('canvas')
    canvas.width = bmp.width
    canvas.height = bmp.height
    canvas.getContext('2d').drawImage(bmp, 0, 0)
    bmp.close()
    return (await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))) || blob
  } catch {
    return blob
  }
}

async function grabFrame() {
  // 1) ImageCapture.takePhoto(): risoluzione piena del sensore (Chrome/Android)
  if (imageCapture) {
    try {
      const blob = await imageCapture.takePhoto()
      if (blob?.size) return toJpeg(blob)
    } catch {
      /* non supportato da questo dispositivo: si usa il fotogramma video */
    }
  }
  // 2) Fotogramma del video alla massima risoluzione dello stream
  const v = video.value
  const canvas = document.createElement('canvas')
  canvas.width = v.videoWidth
  canvas.height = v.videoHeight
  canvas.getContext('2d').drawImage(v, 0, 0)
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92))
}

async function makeThumb(blob) {
  try {
    const bmp = await createImageBitmap(blob)
    const scale = Math.min(1, 240 / Math.max(bmp.width, bmp.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bmp.width * scale)
    canvas.height = Math.round(bmp.height * scale)
    canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height)
    const out = { thumb: canvas.toDataURL('image/jpeg', 0.75), width: bmp.width, height: bmp.height }
    bmp.close()
    return out
  } catch {
    return { thumb: URL.createObjectURL(blob), width: 0, height: 0 }
  }
}

let keySeq = 0
async function addPhoto(blob) {
  const t = await makeThumb(blob)
  photos.value.push({ key: ++keySeq, blob, ...t })
  await nextTick()
  strip.value?.scrollTo({ left: strip.value.scrollWidth, behavior: 'smooth' })
}

async function capture() {
  if (!stream.value || capturing.value) return
  capturing.value = true
  flash.value = true
  setTimeout(() => (flash.value = false), 150)
  navigator.vibrate?.(30)
  try {
    const blob = await grabFrame()
    if (blob) await addPhoto(blob)
  } finally {
    capturing.value = false
  }
}

async function onFiles(e) {
  for (const file of e.target.files) {
    if (file.type.startsWith('image/')) await addPhoto(file)
  }
  e.target.value = ''
}

// --- anteprima ed eliminazione -------------------------------------------------

function openPreview(index) {
  preview.value = { index, url: URL.createObjectURL(photos.value[index].blob) }
}
function closePreview() {
  if (preview.value) URL.revokeObjectURL(preview.value.url)
  preview.value = null
}
function deletePreviewed() {
  const [removed] = photos.value.splice(preview.value.index, 1)
  if (removed.thumb.startsWith('blob:')) URL.revokeObjectURL(removed.thumb)
  closePreview()
}

// --- invio ------------------------------------------------------------------------

async function submit() {
  if (!photos.value.length || uploading.value) return
  submitError.value = ''
  uploading.value = true
  progress.value = 0
  const form = new FormData()
  form.append('notes', notes.value)
  photos.value.forEach((p, i) => {
    const ext = p.blob.type === 'image/png' ? 'png' : 'jpg'
    form.append('photos', p.blob, `foto-${String(i + 1).padStart(2, '0')}.${ext}`)
  })
  try {
    const book = await uploadForm('/api/admin/books', form, (p) => (progress.value = p))
    submitted = true
    stopCamera()
    router.push({ name: 'book', params: { id: book.id } })
  } catch (e) {
    submitError.value = e.message
  } finally {
    uploading.value = false
  }
}

// --- ciclo di vita ------------------------------------------------------------------

const hasUnsaved = () => photos.value.length > 0 && !submitted
const beforeUnload = (e) => {
  if (hasUnsaved()) e.preventDefault()
}

onMounted(() => {
  startCamera()
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  stopCamera()
  window.removeEventListener('beforeunload', beforeUnload)
})
onBeforeRouteLeave(() => {
  if (hasUnsaved() && !confirm('Le foto scattate non sono state salvate. Uscire comunque?')) return false
})
</script>

<template>
  <div class="mx-auto" style="max-width: 44rem">
    <div class="d-flex align-items-center gap-2 mb-3">
      <RouterLink :to="{ name: 'books' }" class="btn btn-sm btn-light"><i class="bi bi-arrow-left"></i></RouterLink>
      <h1 class="h4 mb-0">Nuovo libro</h1>
    </div>

    <!-- Fotocamera -->
    <div class="camera rounded-3 overflow-hidden bg-dark position-relative mb-2">
      <video v-show="stream" ref="video" autoplay playsinline muted class="w-100 d-block"></video>
      <div v-if="flash" class="camera-flash"></div>
      <div v-if="!stream" class="text-center text-white-50 p-5">
        <div v-if="starting" class="spinner-border mb-2"></div>
        <template v-else>
          <i class="bi bi-camera-video-off display-5 d-block mb-2"></i>
          <p class="small mb-3">{{ cameraError || 'Fotocamera spenta' }}</p>
          <button class="btn btn-outline-light btn-sm" @click="startCamera">Riprova</button>
        </template>
      </div>
      <span v-if="stream && resolution" class="badge text-bg-dark position-absolute top-0 start-0 m-2 opacity-75">
        {{ resolution }}
      </span>
      <span class="badge text-bg-light position-absolute top-0 end-0 m-2 fs-6">
        <i class="bi bi-images me-1"></i>{{ photos.length }}
      </span>
    </div>

    <div class="d-flex align-items-center justify-content-between gap-2 mb-3">
      <select
        v-if="devices.length > 1"
        v-model="deviceId"
        class="form-select form-select-sm w-auto"
        style="max-width: 40%"
        aria-label="Fotocamera"
        @change="startCamera"
      >
        <option v-for="(d, i) in devices" :key="d.deviceId" :value="d.deviceId">
          {{ d.label || `Fotocamera ${i + 1}` }}
        </option>
      </select>
      <span v-else style="width: 40%"></span>

      <button
        class="btn btn-primary shutter"
        :disabled="!stream || capturing"
        aria-label="Scatta foto"
        @click="capture"
      >
        <i class="bi bi-camera-fill"></i>
      </button>

      <label class="btn btn-sm btn-light text-nowrap" style="width: 40%; max-width: 10rem">
        <i class="bi bi-upload me-1"></i>Da file
        <input type="file" accept="image/*" multiple class="d-none" @change="onFiles" />
      </label>
    </div>

    <!-- Miniature in ordine di scatto -->
    <div v-if="photos.length" ref="strip" class="d-flex gap-2 overflow-auto pb-2 mb-1">
      <button
        v-for="(p, i) in photos"
        :key="p.key"
        type="button"
        class="btn p-0 border-0 position-relative flex-shrink-0"
        :title="`Foto ${i + 1}: tocca per vedere o eliminare`"
        @click="openPreview(i)"
      >
        <img :src="p.thumb" class="thumb shadow-sm" style="width: 72px; height: 72px" alt="" />
        <span class="badge rounded-pill text-bg-dark position-absolute bottom-0 start-0 m-1" style="top: auto">{{ i + 1 }}</span>
      </button>
    </div>
    <p class="small text-secondary mb-3">
      <template v-if="photos.length">
        {{ photos.length }} foto · {{ fmtBytes(totalSize) }} · tocca una miniatura per vederla o eliminarla
      </template>
      <template v-else>
        Fotografa copertina, retro, costa, frontespizio, colophon ed eventuali difetti.
      </template>
    </p>

    <div class="mb-3">
      <label for="notes" class="form-label fw-semibold">Note</label>
      <textarea
        id="notes"
        v-model="notes"
        class="form-control"
        rows="3"
        placeholder="Informazioni che non si vedono dalle foto (es. pagine sottolineate, dedica, provenienza…)"
      ></textarea>
    </div>

    <div v-if="submitError" class="alert alert-danger small">{{ submitError }}</div>
    <div v-if="uploading" class="progress mb-2" role="progressbar" style="height: 0.5rem">
      <div class="progress-bar bg-danger" :style="{ width: `${Math.round(progress * 100)}%` }"></div>
    </div>
    <div class="d-grid mb-5">
      <button class="btn btn-primary btn-lg" :disabled="!photos.length || uploading" @click="submit">
        <span v-if="uploading" class="spinner-border spinner-border-sm me-2"></span>
        <i v-else class="bi bi-check2-circle me-2"></i>
        {{ uploading ? `Invio… ${Math.round(progress * 100)}%` : `Inserisci libro (${photos.length} foto)` }}
      </button>
    </div>

    <BaseModal v-if="preview" :title="`Foto ${preview.index + 1} di ${photos.length}`" size="lg" @close="closePreview">
      <img :src="preview.url" class="img-fluid rounded mx-auto d-block" style="max-height: 65vh" alt="" />
      <p v-if="photos[preview.index]?.width" class="small text-secondary text-center mt-2 mb-0">
        {{ photos[preview.index].width }}×{{ photos[preview.index].height }} ·
        {{ fmtBytes(photos[preview.index].blob.size) }}
      </p>
      <template #footer>
        <button class="btn btn-light" @click="closePreview">Tieni</button>
        <button class="btn btn-danger" @click="deletePreviewed"><i class="bi bi-trash me-1"></i>Elimina foto</button>
      </template>
    </BaseModal>
  </div>
</template>

<style scoped>
.camera video {
  max-height: 60vh;
  object-fit: contain;
  background: #000;
}
.camera-flash {
  position: absolute;
  inset: 0;
  background: #fff;
  opacity: 0.7;
  pointer-events: none;
}
.shutter {
  width: 4.5rem;
  height: 4.5rem;
  border-radius: 50%;
  font-size: 1.75rem;
  border: 4px solid #fff;
  box-shadow: 0 0 0 2px var(--lc-red);
  flex-shrink: 0;
}
</style>
