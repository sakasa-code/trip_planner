import { http } from '@/utils/request'
import type { LocationInfo } from '@/types'

export function getLocationByIp() {
  return http.get<{ success: boolean; location: LocationInfo }>('/api/v1/location/me')
}

export function reverseGeocode(lat: number, lng: number) {
  return http.get<{ success: boolean; location: LocationInfo }>(
    '/api/v1/location/regeo',
    { lat, lng },
  )
}