import { reactive } from 'vue'
import { api, clearToken, getToken } from './api'

export const auth = reactive({
  user: null, // { email, name, role }
  loaded: false,
})

let pending = null
export function loadUser() {
  pending ??= fetchUser().finally(() => (pending = null))
  return pending
}

async function fetchUser() {
  if (!getToken()) {
    auth.user = null
  } else {
    try {
      auth.user = await api('/api/auth/me')
    } catch (e) {
      auth.user = null
      // Il token si butta solo se il server lo rifiuta (non se è irraggiungibile)
      if (e.status === 401 || e.status === 403) clearToken()
    }
  }
  auth.loaded = true
  return auth.user
}

export function logout() {
  clearToken()
  auth.user = null
}

export const isAdmin = () => auth.user?.role === 'admin'
