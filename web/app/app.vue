<script setup lang="ts">
import {
  mdiClose,
  mdiCogOutline,
  mdiFolderUploadOutline,
  mdiMapOutline,
} from '@mdi/js'
import { computed, onMounted, ref } from 'vue'
import { useDisplay } from 'vuetify'
import type { DistressClass } from '~/constants/distress'
import type { components } from '~/types/api'

type NavSection = 'map' | 'uploads' | 'settings'
type MapDetectionFeature = components['schemas']['MapDetectionFeature']
type MapDetectionFeatureCollection = components['schemas']['MapDetectionFeatureCollection']
type UploadSummary = components['schemas']['UploadSummary']

const config = useRuntimeConfig()
const apiBase = computed(() => config.public.apiBase || 'http://localhost:8000')

const { mdAndUp } = useDisplay()
const activeSection = ref<NavSection>('map')
const mobileSheetOpen = ref<boolean>(false)

const features = ref<MapDetectionFeature[]>([])
const truncated = ref<boolean>(false)
const uploads = ref<UploadSummary[]>([])
const clusterLeaves = ref<MapDetectionFeature[]>([])
const selectedDetectionId = ref<string | null>(null)
const selectedClasses = ref<DistressClass[]>([])
const selectedUploadId = ref<string | null>(null)
const isLoadingDetections = ref<boolean>(true)
const detectionsError = ref<string | null>(null)

const hasActiveFilters = computed<boolean>(
  () => selectedClasses.value.length > 0 || selectedUploadId.value !== null,
)

function selectSection(section: NavSection): void {
  const changed = activeSection.value !== section
  activeSection.value = section
  if (!mdAndUp.value) {
    mobileSheetOpen.value = true
  }
  if (changed && section === 'map') {
    void refreshMapData()
  }
}

function handleSelectClusterLeaves(leaves: MapDetectionFeature[]): void {
  clusterLeaves.value = leaves
  selectedDetectionId.value = null
  activeSection.value = 'map'
  if (!mdAndUp.value) {
    mobileSheetOpen.value = true
  }
}

function handleSelectDetection(detectionId: string): void {
  selectedDetectionId.value = detectionId
  activeSection.value = 'map'
  if (!mdAndUp.value) {
    mobileSheetOpen.value = true
  }
}

function handleUpdateClasses(nextClasses: DistressClass[]): void {
  selectedClasses.value = nextClasses
  void fetchDetections()
}

function handleUpdateUploadId(nextUploadId: string | null): void {
  selectedUploadId.value = nextUploadId
  void fetchDetections()
}

function handleResetFilters(): void {
  selectedClasses.value = []
  selectedUploadId.value = null
  void fetchDetections()
}

async function fetchUploadsList(): Promise<void> {
  try {
    const data = await $fetch<UploadSummary[]>(`${apiBase.value}/uploads`)
    uploads.value = data
  }
  catch {
    // Keep existing upload options if secondary lookup fails
  }
}

async function fetchDetections(): Promise<void> {
  isLoadingDetections.value = true
  detectionsError.value = null
  try {
    const params = new URLSearchParams()
    for (const cls of selectedClasses.value) {
      params.append('class', cls)
    }
    if (selectedUploadId.value) {
      params.set('upload_id', selectedUploadId.value)
    }
    const queryString = params.toString()
    const url = queryString
      ? `${apiBase.value}/detections?${queryString}`
      : `${apiBase.value}/detections`

    const data = await $fetch<MapDetectionFeatureCollection>(url)
    features.value = data.features
    truncated.value = Boolean(data.truncated)
    clusterLeaves.value = []
    if (
      selectedDetectionId.value
      && !data.features.some(f => f.properties.id === selectedDetectionId.value)
    ) {
      selectedDetectionId.value = null
    }
  }
  catch (err: unknown) {
    detectionsError.value
      = err instanceof Error ? err.message : 'Failed to load detections.'
  }
  finally {
    isLoadingDetections.value = false
  }
}

async function refreshMapData(): Promise<void> {
  await Promise.all([fetchUploadsList(), fetchDetections()])
}

onMounted(() => {
  void refreshMapData()
})
</script>

<template>
  <v-app>
    <!-- Desktop navigation rail -->
    <v-navigation-drawer
      v-if="mdAndUp"
      permanent
      rail
      color="surface"
    >
      <v-list density="comfortable" nav>
        <v-list-item
          :prepend-icon="mdiMapOutline"
          title="Map"
          value="map"
          :active="activeSection === 'map'"
          @click="selectSection('map')"
        />
        <v-list-item
          :prepend-icon="mdiFolderUploadOutline"
          title="Uploads"
          value="uploads"
          :active="activeSection === 'uploads'"
          @click="selectSection('uploads')"
        />
        <v-list-item
          :prepend-icon="mdiCogOutline"
          title="Settings"
          value="settings"
          :active="activeSection === 'settings'"
          @click="selectSection('settings')"
        />
      </v-list>
    </v-navigation-drawer>

    <!-- Desktop side panel -->
    <v-navigation-drawer
      v-if="mdAndUp"
      permanent
      width="380"
      color="surface"
    >
      <MapPanel
        v-if="activeSection === 'map'"
        :features="features"
        :cluster-leaves="clusterLeaves"
        :uploads="uploads"
        :selected-classes="selectedClasses"
        :selected-upload-id="selectedUploadId"
        :selected-detection-id="selectedDetectionId"
        :truncated="truncated"
        :is-loading="isLoadingDetections"
        :error="detectionsError"
        @retry="refreshMapData"
        @open-uploads="selectSection('uploads')"
        @clear-cluster-leaves="clusterLeaves = []"
        @select-detection="handleSelectDetection"
        @clear-selection="selectedDetectionId = null"
        @update-classes="handleUpdateClasses"
        @update-upload-id="handleUpdateUploadId"
        @reset-filters="handleResetFilters"
      />
      <UploadsPanel v-else-if="activeSection === 'uploads'" />
      <SettingsPanel v-else />
    </v-navigation-drawer>

    <!-- Persistent map canvas -->
    <v-main class="h-screen">
      <ClientOnly>
        <DetectionMap
          :features="features"
          :selected-detection-id="selectedDetectionId"
          :has-active-filters="hasActiveFilters"
          :is-loading="isLoadingDetections"
          :error="detectionsError"
          @retry="refreshMapData"
          @open-uploads="selectSection('uploads')"
          @reset-filters="handleResetFilters"
          @select-cluster-leaves="handleSelectClusterLeaves"
          @select-detection="handleSelectDetection"
        />
        <template #fallback>
          <div class="w-100 h-100 d-flex align-center justify-center bg-background">
            <v-progress-circular color="primary" indeterminate />
          </div>
        </template>
      </ClientOnly>
    </v-main>

    <!-- Phone bottom sheet -->
    <v-bottom-sheet
      v-if="!mdAndUp"
      v-model="mobileSheetOpen"
      scrollable
      max-height="75vh"
    >
      <v-card color="surface">
        <div class="d-flex align-center justify-end px-2 pt-2">
          <v-btn
            variant="text"
            :icon="mdiClose"
            aria-label="Close panel"
            @click="mobileSheetOpen = false"
          />
        </div>
        <v-card-text class="pa-0">
          <MapPanel
            v-if="activeSection === 'map'"
            :features="features"
            :cluster-leaves="clusterLeaves"
            :uploads="uploads"
            :selected-classes="selectedClasses"
            :selected-upload-id="selectedUploadId"
            :selected-detection-id="selectedDetectionId"
            :truncated="truncated"
            :is-loading="isLoadingDetections"
            :error="detectionsError"
            @retry="refreshMapData"
            @open-uploads="selectSection('uploads')"
            @clear-cluster-leaves="clusterLeaves = []"
            @select-detection="handleSelectDetection"
            @clear-selection="selectedDetectionId = null"
            @update-classes="handleUpdateClasses"
            @update-upload-id="handleUpdateUploadId"
            @reset-filters="handleResetFilters"
          />
          <UploadsPanel v-else-if="activeSection === 'uploads'" />
          <SettingsPanel v-else />
        </v-card-text>
      </v-card>
    </v-bottom-sheet>

    <!-- Phone bottom navigation -->
    <v-bottom-navigation
      v-if="!mdAndUp"
      :model-value="activeSection"
      bg-color="surface"
      color="primary"
      grow
    >
      <v-btn value="map" @click="selectSection('map')">
        <v-icon :icon="mdiMapOutline" />
        <span>Map</span>
      </v-btn>
      <v-btn value="uploads" @click="selectSection('uploads')">
        <v-icon :icon="mdiFolderUploadOutline" />
        <span>Uploads</span>
      </v-btn>
      <v-btn value="settings" @click="selectSection('settings')">
        <v-icon :icon="mdiCogOutline" />
        <span>Settings</span>
      </v-btn>
    </v-bottom-navigation>
  </v-app>
</template>
