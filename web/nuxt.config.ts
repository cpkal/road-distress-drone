// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  modules: ['vuetify-nuxt-module', '@nuxt/eslint'],
  css: ['maplibre-gl/dist/maplibre-gl.css'],
  vite: {
    optimizeDeps: {
      exclude: ['maplibre-gl'],
    },
  },
  runtimeConfig: {
    public: {
      // Overridden by NUXT_PUBLIC_API_BASE / NUXT_PUBLIC_MAP_*
      apiBase: 'http://localhost:8000',
      mapStyleUrl: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
      mapDefaultCenter: '100.5018,13.7563',
      mapDefaultZoom: '12',
    },
  },
})
