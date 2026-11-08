import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  // host: true => raggiungibile dal telefono nella stessa rete (per provare la fotocamera)
  server: { host: true, port: 5173 },
})
