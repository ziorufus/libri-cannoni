import { createRouter, createWebHistory } from 'vue-router'
import { auth, loadUser } from './auth'

import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import AuthCallback from './views/AuthCallback.vue'

const routes = [
  // Parte pubblica
  { path: '/', name: 'home', component: HomeView },
  { path: '/login', name: 'login', component: LoginView },
  { path: '/auth/callback', name: 'auth-callback', component: AuthCallback },

  // Parte semi-pubblica: utenti abilitati
  {
    path: '/area',
    name: 'area',
    component: () => import('./views/AreaView.vue'),
    meta: { requires: 'user' },
  },

  // Amministrazione
  {
    path: '/admin',
    component: () => import('./views/admin/AdminLayout.vue'),
    meta: { requires: 'admin' },
    children: [
      { path: '', redirect: { name: 'books' } },
      { path: 'libri', name: 'books', component: () => import('./views/admin/BooksList.vue') },
      { path: 'libri/nuovo', name: 'book-new', component: () => import('./views/admin/BookNew.vue') },
      {
        path: 'libri/:id(\\d+)',
        name: 'book',
        component: () => import('./views/admin/BookDetail.vue'),
        props: (r) => ({ id: Number(r.params.id) }),
      },
      { path: 'accessi', name: 'access', component: () => import('./views/admin/AccessView.vue') },
      { path: 'job', name: 'jobs', component: () => import('./views/admin/JobsView.vue') },
      { path: 'vinted', name: 'vinted', component: () => import('./views/admin/VintedView.vue') },
      { path: 'impostazioni', name: 'settings', component: () => import('./views/admin/SettingsView.vue') },
    ],
  },

  { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('./views/NotFound.vue') },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const requires = to.matched.map((r) => r.meta.requires).find(Boolean)
  if (!requires) return true
  if (!auth.loaded) await loadUser()
  if (!auth.user) return { name: 'login', query: { next: to.fullPath } }
  if (requires === 'admin' && auth.user.role !== 'admin') {
    return { name: 'login', query: { error: 'forbidden', next: to.fullPath } }
  }
  return true
})

export default router
