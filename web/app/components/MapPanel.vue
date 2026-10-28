<script setup lang="ts">
import {
  mdiAlertOutline,
  mdiClose,
  mdiCloudUploadOutline,
  mdiFilterOffOutline,
  mdiMapMarkerOffOutline,
  mdiMapMarkerRadiusOutline,
  mdiRefresh,
} from '@mdi/js'
import { computed } from 'vue'
import { DISTRESS_CLASSES, type DistressClass } from '~/constants/distress'
import type { components } from '~/types/api'

type MapDetectionFeature = components['schemas']['MapDetectionFeature']
type UploadSummary = components['schemas']['UploadSummary']

const props = defineProps<{
  features: MapDetectionFeature[]
  clusterLeaves: MapDetectionFeature[]
  uploads: UploadSummary[]
  selectedClasses: DistressClass[]
  selectedUploadId: string | null
  selectedDetectionId: string | null
  truncated: boolean
  isLoading: boolean
  error: string | null
}>()

const emit = defineEmits<{
  retry: []
  openUploads: []
  clearClusterLeaves: []
  selectDetection: [detectionId: string]
  clearSelection: []
  updateClasses: [classes: DistressClass[]]
  updateUploadId: [uploadId: string | null]
  resetFilters: []
}>()

const distressClassList = Object.values(DISTRESS_CLASSES)

const hasActiveFilters = computed<boolean>(
  () => props.selectedClasses.length > 0 || props.selectedUploadId !== null,
)

const uploadOptions = computed(() =>
  props.uploads.map(u => ({
    title: `${u.kind === 'video' ? 'Video' : 'Photos'} upload (#${u.id.slice(0, 8)})`,
    value: u.id,
  })),
)

function formatConfidence(conf: number): string {
  return `${Math.round(conf * 100)}%`
}

function formatCoords(coords: [number, number]): string {
  const [lon, lat] = coords
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`
}
</script>

<template>
  <div class="d-flex flex-column ga-4 pa-4">
    <div class="d-flex align-center justify-space-between flex-wrap ga-2">
      <div>
        <h1 class="text-h6 font-weight-medium">
          Road Distress Map
        </h1>
        <p class="text-body-2 text-medium-emphasis">
          Inspect geolocated road distress detections across your survey area.
        </p>
      </div>
      <v-btn
        variant="tonal"
        size="small"
        :prepend-icon="mdiRefresh"
        :loading="isLoading"
        @click="emit('retry')"
      >
        Refresh
      </v-btn>
    </div>

    <!-- Selected detection detail view -->
    <DetectionDetailCard
      v-if="selectedDetectionId"
      :detection-id="selectedDetectionId"
      @close="emit('clearSelection')"
    />

    <template v-else>
      <!-- Filters Card -->
      <v-card color="background" class="pa-4 d-flex flex-column ga-3">
        <div class="d-flex align-center justify-space-between ga-2">
          <span class="text-subtitle-2 font-weight-medium">Filters</span>
          <v-btn
            variant="text"
            size="small"
            :prepend-icon="mdiFilterOffOutline"
            :disabled="!hasActiveFilters"
            @click="emit('resetFilters')"
          >
            Reset filters
          </v-btn>
        </div>

        <div>
          <div class="text-caption text-medium-emphasis mb-1">
            Distress class
          </div>
          <v-chip-group
            :model-value="selectedClasses"
            multiple
            column
            @update:model-value="emit('updateClasses', $event as DistressClass[])"
          >
            <v-chip
              v-for="meta in distressClassList"
              :key="meta.key"
              :value="meta.key"
              :color="meta.color"
              :prepend-icon="meta.icon"
              filter
              size="small"
              variant="tonal"
            >
              {{ meta.label }}
            </v-chip>
          </v-chip-group>
        </div>

        <v-select
          :model-value="selectedUploadId"
          :items="uploadOptions"
          item-title="title"
          item-value="value"
          label="Upload"
          placeholder="All uploads"
          clearable
          density="comfortable"
          hide-details="auto"
          @update:model-value="emit('updateUploadId', $event ?? null)"
        />
      </v-card>

      <!-- Error state -->
      <v-alert
        v-if="error"
        type="error"
        variant="tonal"
        class="pa-4"
      >
        <div class="d-flex align-center justify-space-between flex-wrap ga-2">
          <span>{{ error }}</span>
          <v-btn
            variant="tonal"
            size="small"
            @click="emit('retry')"
          >
            Retry
          </v-btn>
        </div>
      </v-alert>

      <!-- Loading state -->
      <v-card
        v-else-if="isLoading && features.length === 0"
        color="background"
        class="pa-6 d-flex flex-column align-center justify-center ga-3"
      >
        <v-progress-circular color="primary" indeterminate />
        <span class="text-body-2 text-medium-emphasis">Loading detections…</span>
      </v-card>

      <!-- Empty / No filter match state -->
      <v-card
        v-else-if="features.length === 0 && hasActiveFilters"
        color="background"
        class="pa-6 d-flex flex-column align-center justify-center ga-3 text-center"
      >
        <v-icon :icon="mdiFilterOffOutline" size="large" color="primary" />
        <div class="text-subtitle-1 font-weight-medium">
          No detections match
        </div>
        <div class="text-body-2 text-medium-emphasis">
          Try clearing or adjusting your class or upload filters.
        </div>
        <v-btn
          color="primary"
          variant="flat"
          :prepend-icon="mdiFilterOffOutline"
          @click="emit('resetFilters')"
        >
          Reset filters
        </v-btn>
      </v-card>

      <v-card
        v-else-if="features.length === 0"
        color="background"
        class="pa-6 d-flex flex-column align-center justify-center ga-3 text-center"
      >
        <v-icon :icon="mdiMapMarkerOffOutline" size="large" color="primary" />
        <div class="text-subtitle-1 font-weight-medium">
          No geolocated detections
        </div>
        <div class="text-body-2 text-medium-emphasis">
          Upload drone media with GPS metadata to populate the map.
        </div>
        <v-btn
          color="primary"
          variant="flat"
          :prepend-icon="mdiCloudUploadOutline"
          @click="emit('openUploads')"
        >
          Go to Uploads
        </v-btn>
      </v-card>

      <template v-else>
        <!-- 10,000 cap warning -->
        <v-alert
          v-if="truncated"
          type="warning"
          variant="tonal"
          density="compact"
          :icon="mdiAlertOutline"
        >
          Showing the first 10,000 detections. Use the filters above to narrow down results.
        </v-alert>

        <v-card
          color="background"
          class="pa-4 d-flex align-center ga-3"
        >
          <v-icon :icon="mdiMapMarkerRadiusOutline" color="primary" />
          <div class="text-body-2">
            Showing <strong>{{ features.length }}</strong> geolocated
            {{ features.length === 1 ? 'detection' : 'detections' }} on the map.
          </div>
        </v-card>

        <!-- Cluster detections list fallback when cluster cannot zoom further -->
        <v-card
          v-if="clusterLeaves.length > 0"
          color="background"
          class="pa-4 d-flex flex-column ga-3"
        >
          <div class="d-flex align-center justify-space-between ga-2">
            <div class="text-subtitle-2 font-weight-medium">
              Detections in cluster ({{ clusterLeaves.length }})
            </div>
            <v-btn
              variant="text"
              size="small"
              :prepend-icon="mdiClose"
              @click="emit('clearClusterLeaves')"
            >
              Clear
            </v-btn>
          </div>

          <v-list bg-color="transparent" class="pa-0 d-flex flex-column ga-2">
            <v-list-item
              v-for="leaf in clusterLeaves"
              :key="leaf.properties.id"
              rounded="lg"
              class="bg-surface pa-3"
              @click="emit('selectDetection', leaf.properties.id)"
            >
              <div class="d-flex align-center justify-space-between flex-wrap ga-2">
                <v-chip
                  :color="DISTRESS_CLASSES[leaf.properties.class].color"
                  :prepend-icon="DISTRESS_CLASSES[leaf.properties.class].icon"
                  size="small"
                  variant="tonal"
                >
                  {{ DISTRESS_CLASSES[leaf.properties.class].label }}
                </v-chip>
                <span class="text-body-2 font-weight-medium">
                  {{ formatConfidence(leaf.properties.confidence) }}
                </span>
              </div>
              <div class="text-caption text-medium-emphasis mt-1">
                {{ formatCoords(leaf.geometry.coordinates) }}
              </div>
            </v-list-item>
          </v-list>
        </v-card>
      </template>
    </template>
  </div>
</template>
