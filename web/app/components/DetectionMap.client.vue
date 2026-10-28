<script setup lang="ts">
import {
  mdiAlertCircleOutline,
  mdiCloudUploadOutline,
  mdiFilterOffOutline,
  mdiMapMarkerOffOutline,
  mdiRefresh,
} from '@mdi/js'
import type { GeoJSONSource, MapGeoJSONFeature, MapLayerMouseEvent } from 'maplibre-gl'
import {
  AttributionControl,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
} from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useTheme } from 'vuetify'
import { DISTRESS_CLASSES, type DistressClass } from '~/constants/distress'
import type { components } from '~/types/api'

type MapDetectionFeature = components['schemas']['MapDetectionFeature']
type GeoJsonSourceData = Parameters<GeoJSONSource['setData']>[0]
type RawClusterLeaf = Awaited<ReturnType<GeoJSONSource['getClusterLeaves']>>[number]

const SOURCE_ID = 'detections'
const SELECTED_SOURCE_ID = 'selected-detection'
const CLUSTERS_LAYER_ID = 'clusters'
const CLUSTER_COUNT_LAYER_ID = 'cluster-count'
const UNCLUSTERED_LAYER_ID = 'unclustered-point'
const SELECTED_RING_LAYER_ID = 'selected-point-ring'
const CLUSTER_MAX_ZOOM = 14

const props = defineProps<{
  features: MapDetectionFeature[]
  selectedDetectionId: string | null
  hasActiveFilters: boolean
  isLoading: boolean
  error: string | null
}>()

const emit = defineEmits<{
  retry: []
  openUploads: []
  resetFilters: []
  selectClusterLeaves: [leaves: MapDetectionFeature[]]
  selectDetection: [detectionId: string]
}>()

const config = useRuntimeConfig()
const theme = useTheme()

const distressClassList = Object.values(DISTRESS_CLASSES)

const mapContainer = ref<HTMLElement | null>(null)
const webglSupported = ref<boolean>(true)
const mapTileError = ref<string | null>(null)
const isMapReady = ref<boolean>(false)

let mapInstance: MapLibreMap | null = null
let resizeObserver: ResizeObserver | null = null

const defaultCenter = computed<[number, number]>(() => {
  const raw = String(config.public.mapDefaultCenter || '107.6035,-6.9425')
  const parts = raw.split(',').map(part => Number.parseFloat(part.trim()))
  if (
    parts.length === 2
    && Number.isFinite(parts[0])
    && Number.isFinite(parts[1])
  ) {
    return [parts[0] as number, parts[1] as number]
  }
  return [107.6035, -6.9425]
})

const defaultZoom = computed<number>(() => {
  const parsed = Number.parseFloat(String(config.public.mapDefaultZoom || '14'))
  return Number.isFinite(parsed) ? parsed : 14
})

const styleUrl = computed<string>(() => {
  return (
    String(config.public.mapStyleUrl || '').trim()
    || 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
  )
})

function getThemeColor(token: string): string {
  const colors = theme.current.value.colors as Record<string, string | undefined>
  return colors[token] || ''
}

function buildGeoJsonData(features: MapDetectionFeature[]): GeoJsonSourceData {
  return {
    type: 'FeatureCollection',
    features: features.map(f => ({
      type: 'Feature',
      geometry: {
        type: 'Point',
        coordinates: [f.geometry.coordinates[0], f.geometry.coordinates[1]],
      },
      properties: {
        id: f.properties.id,
        class: f.properties.class,
        confidence: f.properties.confidence,
        upload_id: f.properties.upload_id,
      },
    })),
  }
}

function buildSelectedGeoJsonData(
  features: MapDetectionFeature[],
  selectedId: string | null,
): GeoJsonSourceData {
  if (!selectedId) {
    return {
      type: 'FeatureCollection',
      features: [],
    }
  }
  const match = features.find(f => f.properties.id === selectedId)
  return buildGeoJsonData(match ? [match] : [])
}

function checkWebglSupport(): boolean {
  try {
    const canvas = document.createElement('canvas')
    const gl
      = canvas.getContext('webgl2')
        || canvas.getContext('webgl')
        || canvas.getContext('experimental-webgl')
    return Boolean(gl)
  }
  catch {
    return false
  }
}

function areCoordinatesIdentical(features: MapDetectionFeature[]): boolean {
  if (features.length <= 1) {
    return true
  }
  const first = features[0]?.geometry.coordinates
  if (!first) {
    return true
  }
  return features.every(
    f =>
      Math.abs(f.geometry.coordinates[0] - first[0]) < 1e-5
      && Math.abs(f.geometry.coordinates[1] - first[1]) < 1e-5,
  )
}

function fitToFeatures(features: MapDetectionFeature[]): void {
  if (!mapInstance) {
    return
  }

  if (features.length === 0) {
    mapInstance.jumpTo({
      center: defaultCenter.value,
      zoom: defaultZoom.value,
    })
    return
  }

  const firstCoords = features[0]?.geometry.coordinates
  if (!firstCoords) {
    return
  }

  // If there is only 1 detection, or multiple detections at the exact same spot,
  // cap zoom at CLUSTER_MAX_ZOOM so multi-detection clusters remain visible and clickable.
  if (areCoordinatesIdentical(features)) {
    const targetZoom = features.length === 1 ? 16 : CLUSTER_MAX_ZOOM
    mapInstance.flyTo({
      center: [firstCoords[0], firstCoords[1]],
      zoom: targetZoom,
      duration: 600,
    })
    return
  }

  const bounds = new LngLatBounds(
    [firstCoords[0], firstCoords[1]],
    [firstCoords[0], firstCoords[1]],
  )
  for (const feature of features) {
    const [lon, lat] = feature.geometry.coordinates
    bounds.extend([lon, lat])
  }

  mapInstance.fitBounds(bounds, {
    padding: 64,
    maxZoom: 16,
    duration: 600,
  })
}

function convertRawLeafToFeature(leaf: RawClusterLeaf): MapDetectionFeature | null {
  if (leaf.geometry.type !== 'Point' || !leaf.properties) {
    return null
  }
  const coords = leaf.geometry.coordinates
  const props = leaf.properties as Record<string, unknown>
  if (
    typeof coords[0] !== 'number'
    || typeof coords[1] !== 'number'
    || typeof props.id !== 'string'
    || typeof props.class !== 'string'
    || typeof props.confidence !== 'number'
    || typeof props.upload_id !== 'string'
  ) {
    return null
  }
  return {
    type: 'Feature',
    geometry: {
      type: 'Point',
      coordinates: [coords[0], coords[1]],
    },
    properties: {
      id: props.id,
      class: props.class as DistressClass,
      confidence: props.confidence,
      upload_id: props.upload_id,
    },
  }
}

async function handleClusterClick(event: MapLayerMouseEvent): Promise<void> {
  if (!mapInstance) {
    return
  }
  const rendered: MapGeoJSONFeature[] = mapInstance.queryRenderedFeatures(event.point, {
    layers: [CLUSTERS_LAYER_ID],
  })
  const clusterFeature = rendered[0]
  if (!clusterFeature || clusterFeature.geometry.type !== 'Point') {
    return
  }

  const clusterId = clusterFeature.properties?.cluster_id
  if (typeof clusterId !== 'number') {
    return
  }

  const source = mapInstance.getSource(SOURCE_ID) as GeoJSONSource | undefined
  if (!source) {
    return
  }

  const [lon, lat] = clusterFeature.geometry.coordinates as [number, number]
  try {
    const [expansionZoom, rawLeaves] = await Promise.all([
      source.getClusterExpansionZoom(clusterId),
      source.getClusterLeaves(clusterId, 100, 0),
    ])

    const leaves = rawLeaves
      .map(convertRawLeafToFeature)
      .filter((item): item is MapDetectionFeature => item !== null)

    const currentZoom = mapInstance.getZoom()
    const sameSpot = areCoordinatesIdentical(leaves)
    const cannotSplitWithinClusterZoom = expansionZoom > CLUSTER_MAX_ZOOM

    if (!cannotSplitWithinClusterZoom && currentZoom < expansionZoom - 0.05) {
      mapInstance.easeTo({
        center: [lon, lat],
        zoom: expansionZoom,
        duration: 500,
      })
      return
    }

    if (cannotSplitWithinClusterZoom && !sameSpot && currentZoom < expansionZoom - 0.05) {
      mapInstance.easeTo({
        center: [lon, lat],
        zoom: Math.min(expansionZoom, 16),
        duration: 500,
      })
      return
    }

    if (sameSpot && currentZoom < CLUSTER_MAX_ZOOM - 0.1) {
      mapInstance.easeTo({
        center: [lon, lat],
        zoom: CLUSTER_MAX_ZOOM,
        duration: 500,
      })
      return
    }

    emit('selectClusterLeaves', leaves)
  }
  catch {
    // Ignore transient cluster lookup failure
  }
}

function handleUnclusteredPointClick(event: MapLayerMouseEvent): void {
  if (!mapInstance) {
    return
  }
  const rendered: MapGeoJSONFeature[] = mapInstance.queryRenderedFeatures(event.point, {
    layers: [UNCLUSTERED_LAYER_ID],
  })
  const topFeature = rendered[0]
  const id = topFeature?.properties?.id
  if (typeof id === 'string') {
    emit('selectDetection', id)
  }
}

function syncSelectedSource(): void {
  if (!mapInstance) {
    return
  }
  const selectedSource = mapInstance.getSource(SELECTED_SOURCE_ID) as GeoJSONSource | undefined
  if (selectedSource) {
    selectedSource.setData(
      buildSelectedGeoJsonData(props.features, props.selectedDetectionId),
    )
  }
}

function setupDetectionsLayers(): void {
  if (!mapInstance) {
    return
  }

  const existingSource = mapInstance.getSource(SOURCE_ID) as GeoJSONSource | undefined
  if (existingSource) {
    existingSource.setData(buildGeoJsonData(props.features))
    syncSelectedSource()
    return
  }

  mapInstance.addSource(SOURCE_ID, {
    type: 'geojson',
    data: buildGeoJsonData(props.features),
    cluster: true,
    clusterRadius: 50,
    clusterMaxZoom: CLUSTER_MAX_ZOOM,
  })

  mapInstance.addSource(SELECTED_SOURCE_ID, {
    type: 'geojson',
    data: buildSelectedGeoJsonData(props.features, props.selectedDetectionId),
  })

  mapInstance.addLayer({
    id: CLUSTERS_LAYER_ID,
    type: 'circle',
    source: SOURCE_ID,
    filter: ['has', 'point_count'],
    paint: {
      'circle-color': getThemeColor('primary'),
      'circle-radius': [
        'step',
        ['get', 'point_count'],
        18,
        10,
        22,
        50,
        26,
      ],
      'circle-stroke-width': 2,
      'circle-stroke-color': getThemeColor('surface'),
    },
  })

  mapInstance.addLayer({
    id: CLUSTER_COUNT_LAYER_ID,
    type: 'symbol',
    source: SOURCE_ID,
    filter: ['has', 'point_count'],
    layout: {
      'text-field': ['get', 'point_count_abbreviated'],
      'text-size': 13,
    },
    paint: {
      'text-color': getThemeColor('on-primary'),
    },
  })

  mapInstance.addLayer({
    id: UNCLUSTERED_LAYER_ID,
    type: 'circle',
    source: SOURCE_ID,
    filter: ['!', ['has', 'point_count']],
    paint: {
      'circle-color': [
        'match',
        ['get', 'class'],
        'transverse_cracking',
        getThemeColor(DISTRESS_CLASSES.transverse_cracking.color),
        'longitudinal_cracking',
        getThemeColor(DISTRESS_CLASSES.longitudinal_cracking.color),
        'alligator_cracking',
        getThemeColor(DISTRESS_CLASSES.alligator_cracking.color),
        'patch',
        getThemeColor(DISTRESS_CLASSES.patch.color),
        'pothole',
        getThemeColor(DISTRESS_CLASSES.pothole.color),
        getThemeColor('primary'),
      ],
      'circle-radius': 8,
      'circle-stroke-width': 2,
      'circle-stroke-color': getThemeColor('background'),
    },
  })

  // Highlight ring around the currently selected detection
  mapInstance.addLayer({
    id: SELECTED_RING_LAYER_ID,
    type: 'circle',
    source: SELECTED_SOURCE_ID,
    paint: {
      'circle-radius': 14,
      'circle-opacity': 0,
      'circle-stroke-width': 3,
      'circle-stroke-color': getThemeColor('primary'),
    },
  })

  mapInstance.on('click', CLUSTERS_LAYER_ID, (event) => {
    void handleClusterClick(event)
  })

  mapInstance.on('click', UNCLUSTERED_LAYER_ID, (event) => {
    handleUnclusteredPointClick(event)
  })

  for (const layerId of [CLUSTERS_LAYER_ID, UNCLUSTERED_LAYER_ID]) {
    mapInstance.on('mouseenter', layerId, () => {
      if (mapInstance) {
        mapInstance.getCanvas().style.cursor = 'pointer'
      }
    })
    mapInstance.on('mouseleave', layerId, () => {
      if (mapInstance) {
        mapInstance.getCanvas().style.cursor = ''
      }
    })
  }
}

function handleRetry(): void {
  if (mapTileError.value) {
    mapTileError.value = null
    mapInstance?.setStyle(styleUrl.value)
  }
  emit('retry')
}

watch(
  () => props.features,
  (nextFeatures) => {
    if (!isMapReady.value || !mapInstance) {
      return
    }
    const source = mapInstance.getSource(SOURCE_ID) as GeoJSONSource | undefined
    if (source) {
      source.setData(buildGeoJsonData(nextFeatures))
    }
    syncSelectedSource()
    fitToFeatures(nextFeatures)
  },
)

watch(
  () => props.selectedDetectionId,
  () => {
    if (!isMapReady.value || !mapInstance) {
      return
    }
    syncSelectedSource()
  },
)

onMounted(() => {
  if (!checkWebglSupport()) {
    webglSupported.value = false
    return
  }

  if (!mapContainer.value) {
    return
  }

  try {
    setWorkerUrl(maplibreWorkerUrl)
    mapInstance = new MapLibreMap({
      container: mapContainer.value,
      style: styleUrl.value,
      center: defaultCenter.value,
      zoom: defaultZoom.value,
      attributionControl: false,
    })

    // Place controls at the top so they stay clear of the bottom bar and bottom sheet on phones
    mapInstance.addControl(
      new AttributionControl({ compact: false }),
      'top-left',
    )
    mapInstance.addControl(
      new NavigationControl({ showCompass: true }),
      'top-right',
    )

    mapInstance.on('load', () => {
      isMapReady.value = true
      mapTileError.value = null
      setupDetectionsLayers()
      if (props.features.length > 0) {
        fitToFeatures(props.features)
      }
    })

    mapInstance.on('error', (event: { error?: { message?: string } }) => {
      if (!isMapReady.value && event.error?.message) {
        mapTileError.value = `Failed to load basemap: ${event.error.message}`
      }
    })

    resizeObserver = new ResizeObserver(() => {
      mapInstance?.resize()
    })
    resizeObserver.observe(mapContainer.value)
  }
  catch {
    webglSupported.value = false
  }
})

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (mapInstance) {
    mapInstance.remove()
    mapInstance = null
  }
})
</script>

<template>
  <div class="w-100 h-100 position-relative bg-background">
    <div
      v-if="webglSupported"
      ref="mapContainer"
      class="w-100 h-100"
    />

    <!-- WebGL unavailable state -->
    <div
      v-else
      class="w-100 h-100 d-flex align-center justify-center pa-4"
    >
      <v-card
        color="surface"
        max-width="420"
        class="pa-6 d-flex flex-column align-center text-center ga-3"
      >
        <v-icon :icon="mdiAlertCircleOutline" color="error" size="x-large" />
        <div class="text-h6">
          WebGL Unavailable
        </div>
        <div class="text-body-2 text-medium-emphasis">
          The interactive map requires WebGL, which is disabled or unsupported in this browser.
        </div>
      </v-card>
    </div>

    <!-- Loading state overlay -->
    <div
      v-if="webglSupported && isLoading"
      class="position-absolute top-0 left-0 right-0 bottom-0 d-flex align-center justify-center pe-none pa-4"
    >
      <v-card
        color="surface"
        elevation="4"
        class="pa-4 d-flex align-center ga-3"
      >
        <v-progress-circular
          color="primary"
          indeterminate
          size="24"
          width="3"
        />
        <span class="text-body-2">Loading map detections…</span>
      </v-card>
    </div>

    <!-- Error state overlay -->
    <div
      v-else-if="webglSupported && (error || mapTileError)"
      class="position-absolute top-0 left-0 right-0 bottom-0 d-flex align-center justify-center pe-none pa-4"
    >
      <v-card
        color="surface"
        elevation="6"
        max-width="420"
        class="pa-6 d-flex flex-column align-center text-center ga-3 pe-auto"
      >
        <v-icon :icon="mdiAlertCircleOutline" color="error" size="large" />
        <div class="text-subtitle-1 font-weight-medium">
          Unable to load map data
        </div>
        <div class="text-body-2 text-medium-emphasis">
          {{ error || mapTileError }}
        </div>
        <v-btn
          color="primary"
          variant="flat"
          :prepend-icon="mdiRefresh"
          @click="handleRetry"
        >
          Retry
        </v-btn>
      </v-card>
    </div>

    <!-- Empty / No filter match state overlay -->
    <div
      v-else-if="webglSupported && !isLoading && features.length === 0"
      class="position-absolute top-0 left-0 right-0 bottom-0 d-flex align-center justify-center pe-none pa-4"
    >
      <v-card
        v-if="hasActiveFilters"
        color="surface"
        elevation="6"
        max-width="420"
        class="pa-6 d-flex flex-column align-center text-center ga-3 pe-auto"
      >
        <v-icon :icon="mdiFilterOffOutline" color="primary" size="large" />
        <div class="text-subtitle-1 font-weight-medium">
          No detections match
        </div>
        <div class="text-body-2 text-medium-emphasis">
          No geolocated road distress detections match your current filters.
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
        v-else
        color="surface"
        elevation="6"
        max-width="420"
        class="pa-6 d-flex flex-column align-center text-center ga-3 pe-auto"
      >
        <v-icon :icon="mdiMapMarkerOffOutline" color="primary" size="large" />
        <div class="text-subtitle-1 font-weight-medium">
          No geolocated detections yet
        </div>
        <div class="text-body-2 text-medium-emphasis">
          Upload a drone video with telemetry or geotagged road photos to see road distress detections on the map.
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
    </div>

    <!-- Legend (bottom-left of map canvas, clear of top controls and bottom nav bar) -->
    <v-card
      v-if="webglSupported"
      color="surface"
      elevation="4"
      class="position-absolute bottom-0 left-0 ma-3 pa-3 d-flex flex-column ga-2"
    >
      <div class="text-caption font-weight-bold text-uppercase text-medium-emphasis">
        Distress Classes
      </div>
      <div class="d-flex flex-column ga-1">
        <div
          v-for="meta in distressClassList"
          :key="meta.key"
          class="d-flex align-center ga-2"
        >
          <v-avatar :color="meta.color" size="10" />
          <v-icon :icon="meta.icon" :color="meta.color" size="small" />
          <span class="text-caption">{{ meta.label }}</span>
        </div>
      </div>
    </v-card>
  </div>
</template>
