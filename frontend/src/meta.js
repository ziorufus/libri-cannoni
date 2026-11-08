// Metadati delle fasi e degli stati, letti una volta dal backend.
import { reactive } from 'vue'
import { api } from './api'

export const meta = reactive({ phases: [], statuses: {}, loaded: false })

let pending = null
export function loadMeta() {
  if (meta.loaded) return Promise.resolve(meta)
  pending ??= api('/api/admin/meta')
    .then((m) => {
      Object.assign(meta, m, { loaded: true })
      return meta
    })
    .finally(() => (pending = null))
  return pending
}

export const phaseLabel = (n) => meta.phases.find((p) => p.number === n)?.label ?? `Fase ${n}`
export const statusLabel = (s) => meta.statuses[s] ?? s
