<script setup lang="ts">
import {
  mdiAlertCircleOutline,
  mdiCheckCircleOutline,
  mdiClockOutline,
  mdiProgressClock,
} from '@mdi/js'
import { computed } from 'vue'
import type { components } from '~/types/api'

type UploadStatus = components['schemas']['UploadStatus']

const props = defineProps<{
  status: UploadStatus
}>()

interface StatusMeta {
  label: string
  color: string | undefined
  icon: string
}

const STATUS_META: Record<UploadStatus, StatusMeta> = {
  queued: {
    label: 'Queued',
    color: undefined,
    icon: mdiClockOutline,
  },
  processing: {
    label: 'Processing',
    color: 'info',
    icon: mdiProgressClock,
  },
  done: {
    label: 'Done',
    color: 'success',
    icon: mdiCheckCircleOutline,
  },
  failed: {
    label: 'Failed',
    color: 'error',
    icon: mdiAlertCircleOutline,
  },
}

const meta = computed<StatusMeta>(() => STATUS_META[props.status])
</script>

<template>
  <v-chip
    :color="meta.color"
    :prepend-icon="meta.icon"
    size="small"
    variant="tonal"
    label
  >
    {{ meta.label }}
  </v-chip>
</template>

