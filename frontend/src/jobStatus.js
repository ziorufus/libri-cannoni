// Stato dei job periodici (sessione Vinted, uso di Claude), condiviso tra la
// barra dell'app e la pagina Job. Aggiornato ogni minuto mentre la pagina è visibile.
import { reactive } from 'vue'
import { api } from './api'

export const jobStatus = reactive({ jobs: [], auto_pauses: [], loaded: false, error: '' })

let timer = null

export async function refreshJobs() {
  try {
    Object.assign(jobStatus, await api('/api/admin/jobs'), { loaded: true, error: '' })
  } catch (e) {
    jobStatus.error = e.message
  }
}

export function setJobs(data) {
  Object.assign(jobStatus, data, { loaded: true, error: '' })
}

export function startJobsPolling() {
  if (timer) return
  refreshJobs()
  timer = setInterval(() => document.visibilityState === 'visible' && refreshJobs(), 60000)
}

export function stopJobsPolling() {
  clearInterval(timer)
  timer = null
}

export const jobByName = (name) => jobStatus.jobs.find((j) => j.name === name)
