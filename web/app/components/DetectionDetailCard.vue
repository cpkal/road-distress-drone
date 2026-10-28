<script setup lang="ts">
import {
  mdiAlertOutline,
  mdiArrowLeft,
  mdiClockOutline,
  mdiCrosshairsGps,
  mdiFileVideoOutline,
  mdiImageOffOutline,
  mdiRefresh,
} from '@mdi/js'
import { computed, ref, watch } from 'vue'
import { useTheme } from 'vuetify'
import { DISTRESS_CLASSES } from '~/constants/distress'
import type { components } from '~/types/api'

type DetectionDetail = components['schemas']['DetectionDetail']

const props = defineProps<{
  detectionId: string
}>()

const emit = defineEmits<{
  close: []
}>()

const config = useRuntimeConfig()
const theme = useTheme()
const apiBase = computed(() => config.public.apiBase || 'http://localhost:8000')

const detail = ref<DetectionDetail | null>(null)
const isLoading = ref<boolean>(true)
const error = ref<string | null>(null)

const imageLoaded = ref<boolean>(false)
const imageFailed = ref<boolean>(false)

const classMeta = computed(() => {
  if (!detail.value) {
    return null
  }
  return DISTRESS_CLASSES[detail.value.class]
})

const bboxStrokeColor = computed<string>(() => {
  if (!classMeta.value) {
    return ''
  }
  const colors = theme.current.value.colors as Record<string, string | undefined>
  return colors[classMeta.value.color] || colors.primary || ''
})

const bboxRect = computed(() => {
  const b = detail.value?.bbox
  if (!b || b.length < 4) {
    return null
  }
  const xMin = Math.max(0, Math.min(1, b[0] ?? 0))
  const yMin = Math.max(0, Math.min(1, b[1] ?? 0))
  const xMax = Math.max(xMin, Math.min(1, b[2] ?? 1))
  const yMax = Math.max(yMin, Math.min(1, b[3] ?? 1))
  return {
    x: xMin * 1000,
    y: yMin * 1000,
    width: Math.max(1, (xMax - xMin) * 1000),
    height: Math.max(1, (yMax - yMin) * 1000),
  }
})

const fullImageUrl = computed<string>(() => {
  if (!detail.value) {
    return ''
  }
  return `${apiBase.value}${detail.value.image_url}`
})

const formattedConfidence = computed<string>(() => {
  if (!detail.value) {
    return ''
  }
  return `${Math.round(detail.value.confidence * 100)}%`
})

const formattedCoordinates = computed<string>(() => {
  const coords = detail.value?.location?.coordinates
  if (!coords) {
    return 'Unavailable'
  }
  const [lon, lat] = coords
  return `${lat.toFixed(6)}, ${lon.toFixed(6)}`
})

const formattedCaptureTime = computed<string>(() => {
  if (!detail.value) {
    return ''
  }
  const ts = detail.value.timestamp
  if (detail.value.upload_kind === 'photos' && ts > 1_000_000_000) {
    return new Date(ts * 1000).toLocaleString()
  }
  if (detail.value.upload_kind === 'video') {
    const totalSeconds = Math.max(0, Math.floor(ts))
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')} video offset`
  }
  return new Date(detail.value.upload_created_at).toLocaleString()
})

const locationNote = computed<string | null>(() => {
  if (!detail.value) {
    return null
  }
  if (detail.value.oblique) {
    return 'Angled photo, lower accuracy'
  }
  if (detail.value.upload_kind === 'video') {
    return 'Approximate location'
  }
  return null
})

async function fetchDetail(): Promise<void> {
  isLoading.value = true
  error.value = null
  imageLoaded.value = false
  imageFailed.value = false

  try {
    const data = await $fetch<DetectionDetail>(
      `${apiBase.value}/detections/${props.detectionId}`,
    )
    detail.value = data
  }
  catch (err: unknown) {
    error.value
      = err instanceof Error ? err.message : 'Failed to load detection details.'
  }
  finally {
    isLoading.value = false
  }
}

watch(
  () => props.detectionId,
  () => {
    void fetchDetail()
  },
  { immediate: true },
)
</script>

<template>
  <v-card color="background" class="pa-4 d-flex flex-column ga-3">
    <div class="d-flex align-center justify-space-between ga-2">
      <v-btn
        variant="text"
        size="small"
        :prepend-icon="mdiArrowLeft"
        @click="emit('close')"
      >
        Back to map overview
      </v-btn>
      <span class="text-caption text-medium-emphasis">
        #{{ detectionId.slice(0, 8) }}
      </span>
    </div>

    <v-alert
      v-if="error"
      type="error"
      variant="tonal"
      class="pa-3"
    >
      <div class="d-flex align-center justify-space-between flex-wrap ga-2">
        <span>{{ error }}</span>
        <v-btn
          variant="tonal"
          size="small"
          :prepend-icon="mdiRefresh"
          @click="fetchDetail"
        >
          Retry
        </v-btn>
      </div>
    </v-alert>

    <div
      v-else-if="isLoading"
      class="pa-6 d-flex flex-column align-center justify-center ga-3"
    >
      <v-progress-circular color="primary" indeterminate size="28" />
      <span class="text-body-2 text-medium-emphasis">Loading detection details…</span>
    </div>

    <template v-else-if="detail && classMeta">
      <!-- Frame image with normalized bounding box overlay -->
      <div class="position-relative w-100 rounded-lg overflow-hidden bg-surface">
        <img
          v-if="!imageFailed"
          :src="fullImageUrl"
          :alt="`${classMeta.label} detection frame`"
          class="w-100 d-block"
          @load="imageLoaded = true"
          @error="imageFailed = true"
        >

        <svg
          v-if="imageLoaded && !imageFailed && bboxRect"
          viewBox="0 0 1000 1000"
          preserveAspectRatio="none"
          class="position-absolute top-0 left-0 w-100 h-100 pe-none"
        >
          <rect
            :x="bboxRect.x"
            :y="bboxRect.y"
            :width="bboxRect.width"
            :height="bboxRect.height"
            fill="none"
            :stroke="bboxStrokeColor"
            stroke-width="3"
            vector-effect="non-scaling-stroke"
          />
        </svg>

        <!-- Missing / failed image placeholder -->
        <div
          v-if="imageFailed"
          class="pa-6 d-flex flex-column align-center justify-center text-center ga-2"
        >
          <v-icon :icon="mdiImageOffOutline" color="primary" size="large" />
          <div class="text-body-2 font-weight-medium">
            Frame image unavailable
          </div>
          <div class="text-caption text-medium-emphasis">
            The source frame image could not be loaded.
          </div>
        </div>
      </div>

      <!-- Class chip + confidence -->
      <div class="d-flex align-center justify-space-between flex-wrap ga-2">
        <v-chip
          :color="classMeta.color"
          :prepend-icon="classMeta.icon"
          variant="tonal"
        >
          {{ classMeta.label }}
        </v-chip>
        <div class="text-body-2">
          Confidence: <strong>{{ formattedConfidence }}</strong>
        </div>
      </div>

      <!-- Location note alert -->
      <v-alert
        v-if="locationNote"
        type="warning"
        variant="tonal"
        density="compact"
        :icon="mdiAlertOutline"
      >
        {{ locationNote }}
        <span v-if="detail.location_accuracy_m !== null && detail.location_accuracy_m !== undefined">
          (±{{ detail.location_accuracy_m }}m)
        </span>
      </v-alert>

      <!-- Metadata rows -->
      <div class="d-flex flex-column ga-2 text-body-2 bg-surface pa-3 rounded-lg">
        <div class="d-flex align-center ga-2">
          <v-icon :icon="mdiCrosshairsGps" size="small" color="primary" />
          <div>
            <div class="text-caption text-medium-emphasis">
              Coordinates (lat, lon)
            </div>
            <div>
              {{ formattedCoordinates }}
            </div>
          </div>
        </div>

        <div class="d-flex align-center ga-2">
          <v-icon :icon="mdiClockOutline" size="small" color="primary" />
          <div>
            <div class="text-caption text-medium-emphasis">
              Capture time
            </div>
            <div>
              {{ formattedCaptureTime }}
            </div>
          </div>
        </div>

        <div class="d-flex align-center ga-2">
          <v-icon :icon="mdiFileVideoOutline" size="small" color="primary" />
          <div>
            <div class="text-caption text-medium-emphasis">
              Source upload file
            </div>
            <div class="text-Break">
              {{ detail.source_file }}
            </div>
          </div>
        </div>
      </div>
    </template>
  </v-card>
</template>

