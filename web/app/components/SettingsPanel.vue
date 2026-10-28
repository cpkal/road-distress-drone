<script setup lang="ts">
import { mdiCogOutline } from '@mdi/js'

const config = useRuntimeConfig()
</script>

<template>
  <div class="d-flex flex-column ga-4 pa-4">
    <div>
      <h1 class="text-h6 font-weight-medium">
        Settings
      </h1>
      <p class="text-body-2 text-medium-emphasis">
        Runtime configuration loaded from environment variables.
      </p>
    </div>

    <v-card color="background" class="pa-4 d-flex flex-column ga-3">
      <div class="d-flex align-center ga-2">
        <v-icon :icon="mdiCogOutline" color="primary" />
        <span class="text-subtitle-2 font-weight-medium">Environment Config</span>
      </div>

      <div class="d-flex flex-column ga-2 text-body-2">
        <div>
          <div class="text-caption text-medium-emphasis">
            API Base URL
          </div>
          <div class="text-truncate">
            {{ config.public.apiBase }}
          </div>
        </div>
        <div>
          <div class="text-caption text-medium-emphasis">
            Basemap Style URL
          </div>
          <div class="text-truncate">
            {{ config.public.mapStyleUrl }}
          </div>
        </div>
        <div>
          <div class="text-caption text-medium-emphasis">
            Default Center (lon, lat) &amp; Zoom
          </div>
          <div>
            {{ config.public.mapDefaultCenter }} (zoom {{ config.public.mapDefaultZoom }})
          </div>
        </div>
      </div>
    </v-card>
  </div>
</template>

