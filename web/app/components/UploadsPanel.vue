<script setup lang="ts">
import {
  mdiAlertOutline,
  mdiCloudUploadOutline,
  mdiImageMultipleOutline,
  mdiRefresh,
  mdiVideoOutline,
} from '@mdi/js'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { components } from '~/types/api'

type UploadSummary = components['schemas']['UploadSummary']
type UploadDetail = components['schemas']['UploadDetail']
type UploadCreateResponse = components['schemas']['UploadCreateResponse']

const config = useRuntimeConfig()
const apiBase = computed(() => config.public.apiBase || 'http://localhost:8000')

const uploads = ref<UploadSummary[]>([])
const failedErrors = ref<Record<string, string>>({})
const isLoading = ref<boolean>(true)
const listError = ref<string | null>(null)

const selectedFiles = ref<File[]>([])
const isUploading = ref<boolean>(false)
const uploadError = ref<string | null>(null)

let pollTimer: ReturnType<typeof setInterval> | null = null

const hasActiveJobs = computed(() =>
  uploads.value.some(u => u.status === 'queued' || u.status === 'processing'),
)

async function fetchFailedDetail(uploadId: string): Promise<void> {
  if (failedErrors.value[uploadId]) {
    return
  }
  try {
    const detail = await $fetch<UploadDetail>(`${apiBase.value}/uploads/${uploadId}`)
    if (detail.error) {
      failedErrors.value[uploadId] = detail.error
    }
  }
  catch {
    // Ignore secondary detail lookup failure
  }
}

async function fetchUploads(showSpinner = false): Promise<void> {
  if (showSpinner) {
    isLoading.value = true
  }
  listError.value = null
  try {
    const data = await $fetch<UploadSummary[]>(`${apiBase.value}/uploads`)
    uploads.value = data
    for (const item of data) {
      if (item.status === 'failed') {
        void fetchFailedDetail(item.id)
      }
    }
  }
  catch (err: unknown) {
    listError.value = err instanceof Error ? err.message : 'Failed to load uploads.'
  }
  finally {
    isLoading.value = false
  }
}

function extractErrorMessage(err: unknown): string {
  if (err && typeof err === 'object' && 'data' in err) {
    const data = (err as { data?: unknown }).data
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail?: unknown }).detail
      if (typeof detail === 'string') {
        return detail
      }
    }
  }
  return err instanceof Error ? err.message : 'Upload failed.'
}

async function submitUpload(): Promise<void> {
  if (selectedFiles.value.length === 0 || isUploading.value) {
    return
  }
  isUploading.value = true
  uploadError.value = null

  const formData = new FormData()
  for (const file of selectedFiles.value) {
    formData.append('files', file)
  }

  try {
    await $fetch<UploadCreateResponse>(`${apiBase.value}/uploads`, {
      method: 'POST',
      body: formData,
    })
    selectedFiles.value = []
    await fetchUploads(false)
  }
  catch (err: unknown) {
    uploadError.value = extractErrorMessage(err)
  }
  finally {
    isUploading.value = false
  }
}

function formatDate(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return iso
  }
  return date.toLocaleString()
}

onMounted(async () => {
  await fetchUploads(true)
  pollTimer = setInterval(() => {
    if (hasActiveJobs.value) {
      void fetchUploads(false)
    }
  }, 2000)
})

onBeforeUnmount(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<template>
  <div class="d-flex flex-column ga-4 pa-4">
    <div class="d-flex align-center justify-space-between flex-wrap ga-2">
      <div>
        <h1 class="text-h5 font-weight-medium">
          Uploads
        </h1>
        <p class="text-body-2 text-medium-emphasis">
          Upload a DJI drone video (with optional .SRT) or drone photos with GPS metadata.
        </p>
      </div>
      <v-btn
        variant="tonal"
        :prepend-icon="mdiRefresh"
        :loading="isLoading"
        @click="fetchUploads(true)"
      >
        Refresh
      </v-btn>
    </div>

    <v-card color="surface" class="pa-4">
      <div class="d-flex flex-column ga-3">
        <v-file-input
          v-model="selectedFiles"
          label="Select video (.mp4, .mov + optional .srt) or drone photos (.jpg, .jpeg)"
          accept=".mp4,.mov,.srt,.jpg,.jpeg"
          multiple
          chips
          show-size
          hide-details="auto"
          :disabled="isUploading"
        />

        <v-progress-linear
          v-if="isUploading"
          color="primary"
          indeterminate
          rounded
        />

        <v-alert
          v-if="uploadError"
          type="error"
          variant="tonal"
          density="comfortable"
          closable
          @click:close="uploadError = null"
        >
          {{ uploadError }}
        </v-alert>

        <div class="d-flex justify-end">
          <v-btn
            color="primary"
            variant="flat"
            :prepend-icon="mdiCloudUploadOutline"
            :loading="isUploading"
            :disabled="selectedFiles.length === 0"
            @click="submitUpload"
          >
            Upload media
          </v-btn>
        </div>
      </div>
    </v-card>

    <v-alert
      v-if="listError"
      type="error"
      variant="tonal"
      class="pa-4"
    >
      <div class="d-flex align-center justify-space-between flex-wrap ga-2">
        <span>{{ listError }}</span>
        <v-btn
          variant="tonal"
          size="small"
          @click="fetchUploads(true)"
        >
          Retry
        </v-btn>
      </div>
    </v-alert>

    <v-card
      v-else-if="isLoading && uploads.length === 0"
      color="surface"
      class="pa-6 d-flex flex-column align-center justify-center ga-3"
    >
      <v-progress-circular color="primary" indeterminate />
      <span class="text-body-2 text-medium-emphasis">Loading uploads…</span>
    </v-card>

    <v-card
      v-else-if="uploads.length === 0"
      color="surface"
      class="pa-6 d-flex flex-column align-center justify-center ga-2 text-center"
    >
      <v-icon :icon="mdiCloudUploadOutline" size="large" color="primary" />
      <div class="text-subtitle-1 font-weight-medium">
        No uploads yet
      </div>
      <div class="text-body-2 text-medium-emphasis">
        Select a drone video or photos above to start road distress detection.
      </div>
    </v-card>

    <div v-else class="d-flex flex-column ga-3">
      <v-card
        v-for="item in uploads"
        :key="item.id"
        color="surface"
        class="pa-4"
      >
        <div class="d-flex flex-column ga-3">
          <div class="d-flex align-center justify-space-between flex-wrap ga-2">
            <div class="d-flex align-center ga-2">
              <v-icon
                :icon="item.kind === 'video' ? mdiVideoOutline : mdiImageMultipleOutline"
                color="primary"
              />
              <span class="text-subtitle-1 font-weight-medium text-capitalize">
                {{ item.kind }} upload
              </span>
              <span class="text-caption text-medium-emphasis">
                #{{ item.id.slice(0, 8) }}
              </span>
            </div>

            <JobStatusChip :status="item.status" />
          </div>

          <div class="d-flex align-center ga-3">
            <v-progress-linear
              :model-value="item.progress"
              :color="item.status === 'failed' ? 'error' : item.status === 'done' ? 'success' : 'info'"
              :indeterminate="item.status === 'processing' && item.progress < 10"
              height="8"
              rounded
            />
            <span class="text-caption text-medium-emphasis">
              {{ item.progress }}%
            </span>
          </div>

          <div class="d-flex align-center justify-space-between flex-wrap ga-2 text-body-2">
            <div class="d-flex align-center ga-4">
              <span>Frames: <strong>{{ item.frame_count }}</strong></span>
              <span>Detections: <strong>{{ item.detection_count }}</strong></span>
            </div>
            <span class="text-caption text-medium-emphasis">
              {{ formatDate(item.created_at) }}
            </span>
          </div>

          <v-alert
            v-if="item.status === 'failed' && failedErrors[item.id]"
            type="error"
            variant="tonal"
            density="compact"
          >
            {{ failedErrors[item.id] }}
          </v-alert>

          <v-alert
            v-for="(warning, wIdx) in item.warnings ?? []"
            :key="wIdx"
            type="warning"
            variant="tonal"
            density="compact"
            :icon="mdiAlertOutline"
          >
            {{ warning }}
          </v-alert>
        </div>
      </v-card>
    </div>
  </div>
</template>

