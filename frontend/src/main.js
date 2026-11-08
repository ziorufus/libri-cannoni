import { createApp } from 'vue'
import 'bootstrap/dist/css/bootstrap.min.css'
import 'bootstrap-icons/font/bootstrap-icons.css'
import './styles.css'

import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api'
import { auth, loadUser, logout } from './auth'

setUnauthorizedHandler((status) => {
  if (status === 401) {
    logout()
    const here = router.currentRoute.value
    if (here.matched.some((r) => r.meta.requires)) {
      router.push({ name: 'login', query: { next: here.fullPath, error: 'expired' } })
    }
  }
})

if (!auth.loaded) loadUser()

createApp(App).use(router).mount('#app')
