import { http } from '@/utils/request'
import type { HotspotItem, PoiDetail, RecommendItem, RecommendMeta } from '@/types'

export interface RecommendParams {
  city: string
  preferences?: string
  user_location?: string | null
  budget?: string
  limit?: number
  radius?: number
  nearby_only?: boolean
  coord_type?: 'wgs84' | 'gcj02'
}

export function getRecommend(params: RecommendParams) {
  return http.get<{ success: boolean; items: RecommendItem[]; meta: RecommendMeta }>(
    '/api/v1/recommend',
    params as Record<string, unknown>,
  )
}

export function getHotspots(limit?: number) {
  return http.get<{ success: boolean; items: HotspotItem[]; count: number }>(
    '/api/v1/hotspots',
    { limit: limit ?? 150 },
  )
}

export function getPoiDetail(id: string) {
  return http.get<{ success: boolean; detail: PoiDetail | null }>(
    '/api/v1/poi/detail',
    { id },
  )
}

const suggestCache = new Map<string, { items: { id: string; name: string; address: string }[]; ts: number }>()
const SUGGEST_CACHE_TTL = 5 * 60 * 1000
const SUGGEST_CACHE_MAX = 200

export function suggestCity(q: string) {
  const key = q.trim()
  const cached = suggestCache.get(key)
  if (cached && Date.now() - cached.ts < SUGGEST_CACHE_TTL) {
    return Promise.resolve(cached.items)
  }
  return http
    .get<{ status: string; tips: { name: string; display: string; district: string; location: string; id: string }[] }>(
      '/api/v1/city/suggest',
      { q: key },
    )
    .then((r) => {
      const items = (r.tips || []).map((t) => ({
        id: t.id || '',
        name: t.name,
        address: t.district || t.display || '',
      }))
      if (suggestCache.size >= SUGGEST_CACHE_MAX) {
        const firstKey = suggestCache.keys().next().value
        if (firstKey) suggestCache.delete(firstKey)
      }
      suggestCache.set(key, { items, ts: Date.now() })
      return items
    })
}