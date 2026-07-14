import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vite'

// Flask serves the production build from static/dist (committed); in dev, Vite serves
// from the root and proxies API/tile requests to a locally running Flask.
// On macOS, Flask can't use :5000 (AirPlay) — run it elsewhere and set VITE_API_TARGET.
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://localhost:5000'

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/dist/' : '/',
  plugins: [svelte()],
  build: {
    outDir: '../static/dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': API_TARGET,
      '/tiles': API_TARGET,
      '/static': API_TARGET,
    },
  },
}))
