import { http } from '@/utils/request'
import type {
  Enterprise,
  Product,
  Promotion,
  EnterpriseOverview,
  WeekTrend,
  ProductStats,
} from '@/types'

// ===================== 企业资料 =====================

export function getEnterprise() {
  return http.get<{ success: boolean; enterprise: Enterprise }>('/api/v1/me/enterprise')
}

export function updateEnterprise(data: Partial<Enterprise>) {
  return http.put<{ success: boolean; enterprise: Enterprise }>('/api/v1/me/enterprise', data)
}

// ===================== 产品管理 =====================

export function listProducts(params?: {
  status?: number
  search?: string
  category?: string
}) {
  return http.get<{ success: boolean; items: Product[] }>(
    '/api/v1/me/products',
    params as Record<string, unknown>,
  )
}

export function createProduct(data: {
  name: string
  category?: string
  price?: number
  description?: string
  images?: string[]
  lng?: number
  lat?: number
}) {
  return http.post<{ success: boolean; product: Product }>('/api/v1/me/products', data)
}

export function updateProduct(productId: number, data: Partial<Product>) {
  return http.put<{ success: boolean; product: Product }>(
    `/api/v1/me/products/${productId}`,
    data,
  )
}

export function toggleProduct(productId: number) {
  return http.post<{ success: boolean; product: Product }>(
    `/api/v1/me/products/${productId}/toggle`,
  )
}

// ===================== 数据看板 =====================

export function getStatsOverview() {
  return http.get<{ success: boolean; data: EnterpriseOverview }>('/api/v1/me/stats/overview')
}

export function getStatsTrend() {
  return http.get<{ success: boolean; data: WeekTrend[] }>('/api/v1/me/stats/trend')
}

export function getStatsProducts() {
  return http.get<{ success: boolean; items: ProductStats[] }>('/api/v1/me/stats/products')
}

// ===================== 推广管理 =====================

export function listPromotions() {
  return http.get<{ success: boolean; items: Promotion[] }>('/api/v1/me/promotions')
}

export function createPromotion(data: {
  name: string
  description?: string
  budget_daily?: number
  bid_per_1k?: number
}) {
  return http.post<{ success: boolean; promotion: Promotion }>('/api/v1/me/promotions', data)
}

export function updatePromotion(promotionId: number, data: Partial<Promotion>) {
  return http.put<{ success: boolean; promotion: Promotion }>(
    `/api/v1/me/promotions/${promotionId}`,
    data,
  )
}

export function topupPromotion(promotionId: number, amount: number) {
  return http.post<{ success: boolean; balance: number }>('/api/v1/me/promotions/topup', {
    promotion_id: promotionId,
    amount,
  })
}