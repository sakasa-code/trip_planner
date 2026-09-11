import { http } from '@/utils/request'
import type { ApiResponse, LoginPayload, RegisterPayload, UserInfo } from '@/types'

export function loginApi(data: LoginPayload) {
  return http.post<ApiResponse<{ token: string; user: UserInfo }>>('/api/v1/auth/login', data)
}

export function registerApi(data: RegisterPayload) {
  return http.post<ApiResponse<{ token: string; user: UserInfo }>>('/api/v1/auth/register', data)
}

export function getMeApi() {
  return http.get<ApiResponse<{ user: UserInfo }>>('/api/v1/auth/me')
}