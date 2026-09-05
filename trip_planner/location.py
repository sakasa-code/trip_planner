"""地理位置：基于高德 IP 定位与逆地理编码 API。"""
import httpx

from env_utils import AMAP_API_KEY
from trip_planner.cache import CacheService
from trip_planner.recommend import REGEO_CACHE_TTL

AMAP_IP_URL = "https://restapi.amap.com/v3/ip"
AMAP_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"

# 逆地理编码缓存：键为坐标簇（~111m 网格），同一片区只查一次，配额友好
_regeo_cache: dict[str, dict | None] = {}
_REGEO_CACHE_MAX = 1000
REGEO_KEY_PREFIX = "regeo:"


async def locate_by_ip(ip: str | None = None) -> dict:
    """根据 IP 返回城市级定位。ip 为空时高德定位调用方 IP。

    返回: {"province": "...", "city": "...", "adcode": "...", "source": "amap_ip"}
    失败时返回 {"province": "", "city": "", "adcode": "", "source": "fallback"}
    """
    if not AMAP_API_KEY:
        return {"province": "", "city": "", "adcode": "", "source": "no_key"}
    params = {"key": AMAP_API_KEY}
    if ip:
        params["ip"] = ip
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(AMAP_IP_URL, params=params)
            data = resp.json()
        if str(data.get("status")) == "1":
            city = data.get("city") or ""
            # 直辖市 city 可能等于 province，做归一
            province = data.get("province") or ""
            if city and city == province:
                city = city.replace("市", "") if city.endswith("市") else city
            return {
                "province": province,
                "city": city,
                "adcode": data.get("adcode") or "",
                "source": "amap_ip",
            }
    except Exception:
        pass
    return {"province": "", "city": "", "adcode": "", "source": "fallback"}


async def reverse_geocode(lat: float, lng: float) -> dict | None:
    """逆地理编码：GCJ-02 坐标 → 省/市/区县（用于定位反馈展示）。

    坐标须为 GCJ-02（调用方先用 wgs84_to_gcj02 转换）。
    返回 {"province", "city", "district", "adcode", "label"}；失败返回 None。
    label 为可直接展示的"省市区"串，直辖市自动去重（北京市朝阳区而非北京市北京市朝阳区）。

    缓存层：Redis regeo:{lat_lng} → 进程内 _regeo_cache → 上游 API
    同区域（round(lat/lng, 3) ≈ 111m 网格）的重复请求直接命中。
    """
    key = f"{round(lat, 3)},{round(lng, 3)}"
    cache = CacheService.get_instance()

    # 1. Redis / 内存缓存查询
    cached = await cache.get(f"{REGEO_KEY_PREFIX}{key}")
    if cached is not None:
        return cached
    if key in _regeo_cache:
        return _regeo_cache[key]

    result = None
    if AMAP_API_KEY:
        params = {"key": AMAP_API_KEY, "location": f"{lng},{lat}", "extensions": "base"}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(AMAP_REGEO_URL, params=params)
                data = resp.json()
            if str(data.get("status")) == "1":
                comp = (data.get("regeocode") or {}).get("addressComponent") or {}
                province = comp.get("province") or ""
                city = comp.get("city") or ""
                district = comp.get("district") or ""
                # 直辖市/部分行政区 city 与 district 可能是空数组
                if isinstance(city, list) or not city:
                    city = province
                if isinstance(district, list):
                    district = ""
                if city and city == province:
                    label = f"{city}{district}"
                else:
                    label = f"{province}{city}{district}"
                if label:
                    result = {
                        "province": province,
                        "city": city,
                        "district": district,
                        "adcode": comp.get("adcode") or "",
                        "label": label,
                    }
        except Exception:
            result = None

    # 仅缓存成功结果：暂时性网络失败下次可重试
    if result is not None:
        await cache.set(f"{REGEO_KEY_PREFIX}{key}", result, REGEO_CACHE_TTL)
        if len(_regeo_cache) >= _REGEO_CACHE_MAX:
            _regeo_cache.clear()
        _regeo_cache[key] = result
    return result
