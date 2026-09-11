import { http } from '@/utils/request'
import type { MapConfig } from '@/types'

export function getMapConfig() {
  return http.get<MapConfig>('/api/v1/config/map')
}