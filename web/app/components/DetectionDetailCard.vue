<script setup lang="ts">
import {
  mdiAlertOutline,
  mdiArrowExpandAll,
  mdiArrowLeft,
  mdiClockOutline,
  mdiClose,
  mdiCrosshairs,
  mdiCrosshairsGps,
  mdiEyeOffOutline,
  mdiEyeOutline,
  mdiFileVideoOutline,
  mdiImageOffOutline,
  mdiMagnifyMinusOutline,
  mdiMagnifyPlusOutline,
  mdiRefresh,
  mdiRestore,
} from '@mdi/js'
import { computed, ref, watch } from 'vue'
import { useTheme } from 'vuetify'
import { DISTRESS_CLASSES, type DistressClass } from '~/constants/distress'
import type { components } from '~/types/api'

type DetectionDetail = components['schemas']['DetectionDetail']
type FrameDetectionItem = components['schemas']['FrameDetectionItem']

interface RenderedBox {
  id: string
  classKey: DistressClass
  label: string
  confidence: number
  x: number
  y: number
  width: number
  height: number
  strokeColor: string
  isSelected: boolean
}

const MIN_ZOOM = 1
const MAX_ZOOM = 6
const ZOOM_STEP = 0.5

const props = defineProps<{
  detectionId: string
}>()

const emit = defineEmits<{
  close: []
  selectDetection: [detectionId: string]
}>()

const config = useRuntimeConfig()
const theme = useTheme()
const apiBase = computed(() => config.public.apiBase || 'http://localhost:8000')

const detail = ref<DetectionDetail | null>(null)
const isLoading = ref<boolean>(true)
const error = ref<string | null>(null)

const imageLoaded = ref<boolean>(false)
const imageFailed = ref<boolean>(false)
const naturalWidth = ref<number>(1000)
const naturalHeight = ref<number>(750)

// Multi-bounding-box visibility state (supports all, selected-only, or custom subset)
const visibleDetectionIds = ref<string[]>([])

// Zoom & pan state (normalized center in 0..1)
const zoomLevel = ref<number>(1)
const panCenterX = ref<number>(0.5)
const panCenterY = ref<number>(0.5)
const isDragging = ref<boolean>(false)
const dragStartClient = ref<{ x: number, y: number, centerX: number, centerY: number } | null>(null)
const expandedDialogOpen = ref<boolean>(false)

const classMeta = computed(() => {
  if (!detail.value) {
    return null
  }
  return DISTRESS_CLASSES[detail.value.class]
})

const allFrameDetections = computed<FrameDetectionItem[]>(() => {
  if (!detail.value) {
    return []
  }
  if (detail.value.frame_detections && detail.value.frame_detections.length > 0) {
    return detail.value.frame_detections
  }
  return [
    {
      id: detail.value.id,
      class: detail.value.class,
      confidence: detail.value.confidence,
      bbox: detail.value.bbox,
    },
  ]
})

function strokeColorForClass(cls: DistressClass): string {
  const meta = DISTRESS_CLASSES[cls]
  const colors = theme.current.value.colors as Record<string, string | undefined>
  return colors[meta.color] || colors.primary || ''
}

function normalizeBbox(bbox: number[]): { xMin: number, yMin: number, xMax: number, yMax: number } | null {
  if (bbox.length < 4) {
    return null
  }
  const xMin = Math.max(0, Math.min(1, bbox[0] ?? 0))
  const yMin = Math.max(0, Math.min(1, bbox[1] ?? 0))
  const xMax = Math.max(xMin, Math.min(1, bbox[2] ?? 1))
  const yMax = Math.max(yMin, Math.min(1, bbox[3] ?? 1))
  return { xMin, yMin, xMax, yMax }
}

const renderedBoxes = computed<RenderedBox[]>(() => {
  const visibleSet = new Set(visibleDetectionIds.value)
  const w = naturalWidth.value
  const h = naturalHeight.value
  const boxes: RenderedBox[] = []

  for (const item of allFrameDetections.value) {
    if (!visibleSet.has(item.id)) {
      continue
    }
    const norm = normalizeBbox(item.bbox)
    if (!norm) {
      continue
    }
    const meta = DISTRESS_CLASSES[item.class]
    boxes.push({
      id: item.id,
      classKey: item.class,
      label: meta.label,
      confidence: item.confidence,
      x: norm.xMin * w,
      y: norm.yMin * h,
      width: Math.max(2, (norm.xMax - norm.xMin) * w),
      height: Math.max(2, (norm.yMax - norm.yMin) * h),
      strokeColor: strokeColorForClass(item.class),
      isSelected: item.id === detail.value?.id,
    })
  }

  // Draw non-selected boxes first so the active detection box is always rendered on top
  boxes.sort((a, b) => Number(a.isSelected) - Number(b.isSelected))
  return boxes
})

const isAllVisible = computed<boolean>(
  () =>
    allFrameDetections.value.length > 0
    && visibleDetectionIds.value.length === allFrameDetections.value.length,
)

const isSelectedOnlyVisible = computed<boolean>(
  () =>
    Boolean(detail.value)
    && visibleDetectionIds.value.length === 1
    && visibleDetectionIds.value[0] === detail.value?.id,
)

const svgViewBox = computed<string>(() => {
  const w = naturalWidth.value
  const h = naturalHeight.value
  const z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoomLevel.value))
  const viewW = w / z
  const viewH = h / z
  const halfW = 0.5 / z
  const halfH = 0.5 / z
  const clampedCX = Math.max(halfW, Math.min(1 - halfW, panCenterX.value))
  const clampedCY = Math.max(halfH, Math.min(1 - halfH, panCenterY.value))
  const vx = (clampedCX - halfW) * w
  const vy = (clampedCY - halfH) * h
  return `${vx} ${vy} ${viewW} ${viewH}`
})

const zoomPercentLabel = computed<string>(() => `${Math.round(zoomLevel.value * 100)}%`)

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

function formatItemConfidence(conf: number): string {
  return `${Math.round(conf * 100)}%`
}

function isDetectionVisible(id: string): boolean {
  return visibleDetectionIds.value.includes(id)
}

function toggleDetectionVisibility(id: string): void {
  if (visibleDetectionIds.value.includes(id)) {
    visibleDetectionIds.value = visibleDetectionIds.value.filter(item => item !== id)
  }
  else {
    visibleDetectionIds.value = [...visibleDetectionIds.value, id]
  }
}

function showAllDetections(): void {
  visibleDetectionIds.value = allFrameDetections.value.map(d => d.id)
}

function showSelectedOnly(): void {
  if (!detail.value) {
    return
  }
  visibleDetectionIds.value = [detail.value.id]
}

function hideAllDetections(): void {
  visibleDetectionIds.value = []
}

function handleSelectSibling(item: FrameDetectionItem): void {
  if (!visibleDetectionIds.value.includes(item.id)) {
    visibleDetectionIds.value = [...visibleDetectionIds.value, item.id]
  }
  if (detail.value) {
    detail.value = {
      ...detail.value,
      id: item.id,
      class: item.class,
      confidence: item.confidence,
      bbox: item.bbox,
    }
  }
  emit('selectDetection', item.id)
}

function clampPan(nextX: number, nextY: number, z: number): { x: number, y: number } {
  const half = 0.5 / Math.max(MIN_ZOOM, z)
  return {
    x: Math.max(half, Math.min(1 - half, nextX)),
    y: Math.max(half, Math.min(1 - half, nextY)),
  }
}

function setZoom(nextZoom: number): void {
  const clamped = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Number(nextZoom.toFixed(2))))
  zoomLevel.value = clamped
  const p = clampPan(panCenterX.value, panCenterY.value, clamped)
  panCenterX.value = p.x
  panCenterY.value = p.y
}

function zoomIn(): void {
  setZoom(zoomLevel.value + ZOOM_STEP)
}

function zoomOut(): void {
  setZoom(zoomLevel.value - ZOOM_STEP)
}

function resetZoom(): void {
  zoomLevel.value = 1
  panCenterX.value = 0.5
  panCenterY.value = 0.5
}

function focusSelectedBox(): void {
  if (!detail.value) {
    return
  }
  const norm = normalizeBbox(detail.value.bbox)
  if (!norm) {
    return
  }
  const targetCenterX = (norm.xMin + norm.xMax) / 2
  const targetCenterY = (norm.yMin + norm.yMax) / 2
  const boxSpan = Math.max(norm.xMax - norm.xMin, norm.yMax - norm.yMin, 0.15)
  const targetZoom = Math.max(2, Math.min(MAX_ZOOM, Number((0.6 / boxSpan).toFixed(1))))
  zoomLevel.value = targetZoom
  const p = clampPan(targetCenterX, targetCenterY, targetZoom)
  panCenterX.value = p.x
  panCenterY.value = p.y
  if (!visibleDetectionIds.value.includes(detail.value.id)) {
    visibleDetectionIds.value = [...visibleDetectionIds.value, detail.value.id]
  }
}

function onWheelZoom(event: WheelEvent): void {
  const delta = event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP
  setZoom(zoomLevel.value + delta)
}

function onPointerDown(event: PointerEvent): void {
  if (zoomLevel.value <= 1) {
    return
  }
  const target = event.currentTarget as HTMLElement | null
  target?.setPointerCapture?.(event.pointerId)
  isDragging.value = true
  dragStartClient.value = {
    x: event.clientX,
    y: event.clientY,
    centerX: panCenterX.value,
    centerY: panCenterY.value,
  }
}

function onPointerMove(event: PointerEvent): void {
  if (!isDragging.value || !dragStartClient.value || zoomLevel.value <= 1) {
    return
  }
  const target = event.currentTarget as HTMLElement | null
  const rect = target?.getBoundingClientRect()
  if (!rect || rect.width <= 0 || rect.height <= 0) {
    return
  }
  const dx = (event.clientX - dragStartClient.value.x) / (rect.width * zoomLevel.value)
  const dy = (event.clientY - dragStartClient.value.y) / (rect.height * zoomLevel.value)
  const p = clampPan(
    dragStartClient.value.centerX - dx,
    dragStartClient.value.centerY - dy,
    zoomLevel.value,
  )
  panCenterX.value = p.x
  panCenterY.value = p.y
}

function onPointerUp(): void {
  isDragging.value = false
  dragStartClient.value = null
}

function onImageLoad(event: Event): void {
  const img = event.target as HTMLImageElement | null
  if (img && img.naturalWidth > 0 && img.naturalHeight > 0) {
    naturalWidth.value = img.naturalWidth
    naturalHeight.value = img.naturalHeight
  }
  imageLoaded.value = true
}

async function fetchDetail(): Promise<void> {
  // If switching between detections on the same already-loaded frame, avoid reloading the image
  if (
    detail.value
    && detail.value.frame_detections?.some(d => d.id === props.detectionId)
  ) {
    const match = detail.value.frame_detections.find(d => d.id === props.detectionId)
    if (match) {
      detail.value = {
        ...detail.value,
        id: match.id,
        class: match.class,
        confidence: match.confidence,
        bbox: match.bbox,
      }
      if (!visibleDetectionIds.value.includes(match.id)) {
        visibleDetectionIds.value = [...visibleDetectionIds.value, match.id]
      }
      return
    }
  }

  isLoading.value = true
  error.value = null
  imageLoaded.value = false
  imageFailed.value = false
  resetZoom()

  try {
    const data = await $fetch<DetectionDetail>(
      `${apiBase.value}/detections/${props.detectionId}`,
    )
    detail.value = data
    const frameIds = data.frame_detections?.length
      ? data.frame_detections.map(d => d.id)
      : [data.id]
    visibleDetectionIds.value = frameIds
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
      <!-- Hidden probe image to read natural dimensions and load status -->
      <img
        v-if="!imageFailed"
        :src="fullImageUrl"
        alt=""
        class="d-none"
        @load="onImageLoad"
        @error="imageFailed = true"
      >

      <!-- Zoomable frame image with multi-bounding-box SVG overlay -->
      <div class="d-flex flex-column ga-2">
        <div
          class="position-relative w-100 rounded-lg overflow-hidden bg-surface"
          @wheel.prevent="onWheelZoom"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointercancel="onPointerUp"
        >
          <svg
            v-if="imageLoaded && !imageFailed"
            :viewBox="svgViewBox"
            class="w-100 d-block"
            role="img"
            :aria-label="`${classMeta.label} detection frame with ${renderedBoxes.length} bounding boxes`"
          >
            <image
              :href="fullImageUrl"
              x="0"
              y="0"
              :width="naturalWidth"
              :height="naturalHeight"
              preserveAspectRatio="none"
            />
            <rect
              v-for="box in renderedBoxes"
              :key="box.id"
              :x="box.x"
              :y="box.y"
              :width="box.width"
              :height="box.height"
              fill="none"
              :stroke="box.strokeColor"
              :stroke-width="box.isSelected ? 3.5 : 2"
              :stroke-dasharray="box.isSelected ? undefined : '6 3'"
              vector-effect="non-scaling-stroke"
            />
          </svg>

          <div
            v-else-if="!imageFailed"
            class="pa-6 d-flex align-center justify-center"
          >
            <v-progress-circular color="primary" indeterminate size="24" />
          </div>

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

        <!-- Zoom & Expand toolbar -->
        <div
          v-if="imageLoaded && !imageFailed"
          class="d-flex align-center justify-space-between flex-wrap ga-1 bg-surface px-2 py-1 rounded-lg"
        >
          <div class="d-flex align-center ga-1">
            <v-btn
              variant="text"
              size="x-small"
              :icon="mdiMagnifyMinusOutline"
              :disabled="zoomLevel <= MIN_ZOOM"
              aria-label="Zoom out"
              @click="zoomOut"
            />
            <span class="text-caption font-weight-medium px-1">
              {{ zoomPercentLabel }}
            </span>
            <v-btn
              variant="text"
              size="x-small"
              :icon="mdiMagnifyPlusOutline"
              :disabled="zoomLevel >= MAX_ZOOM"
              aria-label="Zoom in"
              @click="zoomIn"
            />
            <v-btn
              variant="tonal"
              size="x-small"
              :prepend-icon="mdiCrosshairs"
              @click="focusSelectedBox"
            >
              Focus box
            </v-btn>
            <v-btn
              v-if="zoomLevel > 1"
              variant="text"
              size="x-small"
              :prepend-icon="mdiRestore"
              @click="resetZoom"
            >
              Reset
            </v-btn>
          </div>

          <v-btn
            variant="text"
            size="x-small"
            :prepend-icon="mdiArrowExpandAll"
            @click="expandedDialogOpen = true"
          >
            Expand
          </v-btn>
        </div>
      </div>

      <!-- Active Class chip + confidence -->
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

      <!-- Detections in this frame (multi-box controls: show all, selected only, or toggle individual boxes) -->
      <div class="d-flex flex-column ga-2 bg-surface pa-3 rounded-lg">
        <div class="d-flex align-center justify-space-between flex-wrap ga-2">
          <span class="text-caption font-weight-medium text-medium-emphasis">
            Bounding boxes in frame ({{ visibleDetectionIds.length }}/{{ allFrameDetections.length }})
          </span>
          <div class="d-flex align-center ga-1">
            <v-btn
              variant="tonal"
              size="x-small"
              :color="isAllVisible ? 'primary' : undefined"
              @click="showAllDetections"
            >
              Show all
            </v-btn>
            <v-btn
              v-if="allFrameDetections.length > 1"
              variant="tonal"
              size="x-small"
              :color="isSelectedOnlyVisible ? 'primary' : undefined"
              @click="showSelectedOnly"
            >
              Selected only
            </v-btn>
            <v-btn
              v-if="visibleDetectionIds.length > 0"
              variant="text"
              size="x-small"
              @click="hideAllDetections"
            >
              Hide
            </v-btn>
          </div>
        </div>

        <v-list bg-color="transparent" density="compact" class="pa-0 d-flex flex-column ga-1">
          <v-list-item
            v-for="item in allFrameDetections"
            :key="item.id"
            rounded="lg"
            :active="item.id === detail.id"
            color="primary"
            class="px-2 py-1"
            @click="handleSelectSibling(item)"
          >
            <div class="d-flex align-center justify-space-between ga-2 w-100">
              <div class="d-flex align-center ga-2 flex-wrap">
                <v-btn
                  variant="text"
                  size="x-small"
                  :icon="isDetectionVisible(item.id) ? mdiEyeOutline : mdiEyeOffOutline"
                  :color="isDetectionVisible(item.id) ? 'primary' : undefined"
                  :aria-label="isDetectionVisible(item.id) ? 'Hide bounding box' : 'Show bounding box'"
                  @click.stop="toggleDetectionVisibility(item.id)"
                />
                <v-chip
                  :color="DISTRESS_CLASSES[item.class].color"
                  :prepend-icon="DISTRESS_CLASSES[item.class].icon"
                  size="x-small"
                  variant="tonal"
                >
                  {{ DISTRESS_CLASSES[item.class].label }}
                </v-chip>
              </div>
              <span class="text-caption font-weight-medium">
                {{ formatItemConfidence(item.confidence) }}
              </span>
            </div>
          </v-list-item>
        </v-list>
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
            <div class="text-break">
              {{ detail.source_file }}
            </div>
          </div>
        </div>
      </div>

      <!-- Expandable high-resolution frame & bounding box inspector dialog -->
      <v-dialog
        v-model="expandedDialogOpen"
        max-width="1080"
      >
        <v-card color="surface" class="pa-4 d-flex flex-column ga-3">
          <div class="d-flex align-center justify-space-between flex-wrap ga-2">
            <div class="d-flex align-center ga-2 flex-wrap">
              <v-chip
                :color="classMeta.color"
                :prepend-icon="classMeta.icon"
                variant="tonal"
                size="small"
              >
                {{ classMeta.label }} ({{ formattedConfidence }})
              </v-chip>
              <span class="text-body-2 text-medium-emphasis">
                {{ detail.source_file }} — {{ visibleDetectionIds.length }}/{{ allFrameDetections.length }} boxes shown
              </span>
            </div>

            <div class="d-flex align-center ga-1 flex-wrap">
              <v-btn
                variant="tonal"
                size="small"
                :color="isAllVisible ? 'primary' : undefined"
                @click="showAllDetections"
              >
                Show all ({{ allFrameDetections.length }})
              </v-btn>
              <v-btn
                v-if="allFrameDetections.length > 1"
                variant="tonal"
                size="small"
                :color="isSelectedOnlyVisible ? 'primary' : undefined"
                @click="showSelectedOnly"
              >
                Selected only
              </v-btn>
              <v-btn
                variant="tonal"
                size="small"
                :icon="mdiMagnifyMinusOutline"
                :disabled="zoomLevel <= MIN_ZOOM"
                aria-label="Zoom out"
                @click="zoomOut"
              />
              <span class="text-body-2 font-weight-medium px-2">
                {{ zoomPercentLabel }}
              </span>
              <v-btn
                variant="tonal"
                size="small"
                :icon="mdiMagnifyPlusOutline"
                :disabled="zoomLevel >= MAX_ZOOM"
                aria-label="Zoom in"
                @click="zoomIn"
              />
              <v-btn
                variant="tonal"
                size="small"
                :prepend-icon="mdiCrosshairs"
                @click="focusSelectedBox"
              >
                Focus box
              </v-btn>
              <v-btn
                v-if="zoomLevel > 1"
                variant="text"
                size="small"
                :prepend-icon="mdiRestore"
                @click="resetZoom"
              >
                Reset
              </v-btn>
              <v-btn
                variant="text"
                size="small"
                :icon="mdiClose"
                aria-label="Close expanded view"
                @click="expandedDialogOpen = false"
              />
            </div>
          </div>

          <div
            class="position-relative w-100 rounded-lg overflow-hidden bg-background"
            @wheel.prevent="onWheelZoom"
            @pointerdown="onPointerDown"
            @pointermove="onPointerMove"
            @pointerup="onPointerUp"
            @pointercancel="onPointerUp"
          >
            <svg
              v-if="imageLoaded && !imageFailed"
              :viewBox="svgViewBox"
              class="w-100 d-block"
              role="img"
              :aria-label="`${classMeta.label} expanded detection frame`"
            >
              <image
                :href="fullImageUrl"
                x="0"
                y="0"
                :width="naturalWidth"
                :height="naturalHeight"
                preserveAspectRatio="none"
              />
              <rect
                v-for="box in renderedBoxes"
                :key="box.id"
                :x="box.x"
                :y="box.y"
                :width="box.width"
                :height="box.height"
                fill="none"
                :stroke="box.strokeColor"
                :stroke-width="box.isSelected ? 3.5 : 2.5"
                :stroke-dasharray="box.isSelected ? undefined : '6 3'"
                vector-effect="non-scaling-stroke"
              />
            </svg>
          </div>

          <div class="d-flex align-center flex-wrap ga-2">
            <v-chip
              v-for="item in allFrameDetections"
              :key="item.id"
              :color="DISTRESS_CLASSES[item.class].color"
              :prepend-icon="DISTRESS_CLASSES[item.class].icon"
              :variant="item.id === detail.id ? 'flat' : 'tonal'"
              size="small"
              @click="handleSelectSibling(item)"
            >
              {{ DISTRESS_CLASSES[item.class].label }} ({{ formatItemConfidence(item.confidence) }})
              <template #append>
                <v-icon
                  :icon="isDetectionVisible(item.id) ? mdiEyeOutline : mdiEyeOffOutline"
                  size="small"
                  class="ms-1"
                  @click.stop="toggleDetectionVisibility(item.id)"
                />
              </template>
            </v-chip>
          </div>
        </v-card>
      </v-dialog>
    </template>
  </v-card>
</template>
