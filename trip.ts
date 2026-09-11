import { http } from '@/utils/request'
import type { ApiResponse, TripRequest, TripPlan } from '@/types'

export function planTripApi(data: TripRequest) {
  return http.post<ApiResponse<TripPlan>>('/api/v1/trip/plan', data)
}