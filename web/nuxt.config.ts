// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  modules: ['vuetify-nuxt-module', '@nuxt/eslint'],
  runtimeConfig: {
    public: {
      // Overridden by NUXT_PUBLIC_API_BASE / NUXT_PUBLIC_MAP_STYLE_URL
      apiBase: '',
      mapStyleUrl: '',
    },
  },
})
