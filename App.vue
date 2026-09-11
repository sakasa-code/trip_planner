<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAppStore } from '@/stores/app'
import { getLocationByIp } from '@/api/location'
import type { LocationInfo } from '@/types'

const appStore = useAppStore()
const LOCATION_CACHE_KEY = 'trip_planner_ip_location'
const LOCATION_CACHE_TTL = 10 * 60 * 1000

onMounted(async () => {
  try {
    const raw = sessionStorage.getItem(LOCATION_CACHE_KEY)
    if (raw) {
      const cached = JSON.parse(raw) as { location: LocationInfo; ts: number }
      if (Date.now() - cached.ts < LOCATION_CACHE_TTL) {
        appStore.setCity(cached.location.city || '未知城市')
        appStore.setLocationInfo(cached.location)
        return
      }
    }

    const res = await getLocationByIp()
    if (res.success && res.location?.city) {
      appStore.setCity(res.location.city)
      appStore.setLocationInfo(res.location)
      sessionStorage.setItem(LOCATION_CACHE_KEY, JSON.stringify({ location: res.location, ts: Date.now() }))
    } else {
      appStore.setCity('未知城市')
    }
  } catch {
    appStore.setCity('定位失败')
  }
})
</script>