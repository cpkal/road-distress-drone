import {
  mdiAlertOctagonOutline,
  mdiCallSplit,
  mdiGrid,
  mdiRoadVariant,
  mdiTextureBox,
} from '@mdi/js'
import type { components } from '~/types/api'

export type DistressClass = components['schemas']['DistressClass']

export interface DistressClassMeta {
  key: DistressClass
  label: string
  icon: string
  color: 'primary' | 'info' | 'warning' | 'success' | 'error'
}

// Single source of truth for distress classes (matches api/app/domain/distress.py).
// Detections are object detection bounding boxes only (no severity levels).
export const DISTRESS_CLASSES: Record<DistressClass, DistressClassMeta> = {
  transverse_cracking: {
    key: 'transverse_cracking',
    label: 'Transverse cracking',
    icon: mdiCallSplit,
    color: 'info',
  },
  longitudinal_cracking: {
    key: 'longitudinal_cracking',
    label: 'Longitudinal cracking',
    icon: mdiRoadVariant,
    color: 'primary',
  },
  alligator_cracking: {
    key: 'alligator_cracking',
    label: 'Alligator cracking',
    icon: mdiGrid,
    color: 'warning',
  },
  patch: {
    key: 'patch',
    label: 'Patch',
    icon: mdiTextureBox,
    color: 'success',
  },
  pothole: {
    key: 'pothole',
    label: 'Pothole',
    icon: mdiAlertOctagonOutline,
    color: 'error',
  },
}
