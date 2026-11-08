// Client per il backend FastAPI. L'indirizzo arriva da VITE_API_URL.

export const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/+$/, '')

const TOKEN_KEY = 'lc_token'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* modalità privata: la sessione dura finché la pagina resta aperta */
  }
  memoryToken = token
}

export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignora */
  }
  memoryToken = null
}

let memoryToken = null
const currentToken = () => getToken() || memoryToken

let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

function errorMessage(status, data) {
  if (data && typeof data.detail === 'string') return data.detail
  if (data && Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join('; ')
  return `Errore ${status}`
}

function authHeaders() {
  const token = currentToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function api(path, { method = 'GET', body } = {}) {
  const headers = authHeaders()
  let payload
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(API_URL + path, { method, headers, body: payload })
  } catch {
    throw new ApiError(0, 'Impossibile contattare il server')
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) onUnauthorized(res.status)
    throw new ApiError(res.status, errorMessage(res.status, data))
  }
  return data
}

// Upload multipart con avanzamento (fetch non espone il progresso dell'upload).
export function uploadForm(path, formData, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', API_URL + path)
    const token = currentToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onerror = () => reject(new ApiError(0, 'Impossibile contattare il server'))
    xhr.onload = () => {
      let data = null
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        /* risposta non JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else {
        if (xhr.status === 401) onUnauthorized(401)
        reject(new ApiError(xhr.status, errorMessage(xhr.status, data)))
      }
    }
    xhr.send(formData)
  })
}

export function loginUrl(provider, next = '/') {
  return `${API_URL}/api/auth/login/${provider}?next=${encodeURIComponent(next)}`
}
