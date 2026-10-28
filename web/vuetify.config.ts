import { md3 } from 'vuetify/blueprints'
import { defineVuetifyConfiguration } from 'vuetify-nuxt-module/custom-configuration'

export default defineVuetifyConfiguration({
  blueprint: md3,
  icons: {
    defaultSet: 'mdi-svg',
  },
  theme: {
    defaultTheme: 'dark',
    themes: {
      dark: {
        dark: true,
        colors: {
          background: '#0E1415',
          surface: '#171D1E',
          primary: '#4FD8EB',
          'on-primary': '#00363D',
          error: '#FFB4AB',
          success: '#81C784',
          info: '#4FC3F7',
          warning: '#FFD54F',
        },
      },
    },
  },
})
