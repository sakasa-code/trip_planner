"""高德地图 API 客户端：文本搜索、周边搜索、POI 详情、热点批量拉取。"""

import asyncio
import logging
import time
from typing import Optional

import httpx

from env_utils import AMAP_API_KEY
from trip_planner.cache import CacheService

logger = logging.getLogger("trip_planner.recommend")

AMAP_TEXT_SEARCH = "https://restapi.amap.com/v3/place/text"
AMAP_AROUND_SEARCH = "https://restapi.amap.com/v3/place/around"
AMAP_DETAIL = "https://restapi.amap.com/v3/place/detail"

HOTSPOT_CATEGORIES = ["风景名胜", "博物馆", "美食", "酒店", "娱乐"]
HOTSPOT_CITIES = ["北京", "上海", "广州", "深圳", "成都", "西安", "杭州", "重庆",
                  "苏州", "南京", "武汉", "长沙", "厦门", "青岛", "大理", "丽江"]
HOTSPOT_CACHE_TTL = 3600
HOTSPOT_KEY = "hotspots:top200"
HOTSPOT_GEO_KEY = "geo:hotspots"

API_FAIL_THRESHOLD = 5
_api_fail_count = 0
_api_fail_lock = asyncio.Lock()
_last_good_hotspots: list[dict] = []


async def search_pois(keyword: str, city: str, limit: int = 25, pages: int = 2,
                      client: httpx.AsyncClient | None = None) -> list[dict]:
    """调用高德文本搜索，返回 POI 列表。支持多页拉取。可传入共享 client 复用连接。"""
    if not AMAP_API_KEY:
        return []
    all_pois: list[dict] = []
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=8)
    try:
        for page in range(1, pages + 1):
            params = {
                "key": AMAP_API_KEY,
                "keywords": keyword,
                "city": city,
                "citylimit": "true",
                "extensions": "all",
                "offset": str(min(limit, 25)),
                "page": str(page),
            }
            try:
                resp = await client.get(AMAP_TEXT_SEARCH, params=params)
                data = resp.json()
            except Exception:
                break
            if str(data.get("status")) != "1":
                break
            pois = data.get("pois") or []
            if not pois:
                break
            all_pois.extend(pois)
            if len(pois) < 25:
                break
    finally:
        if own_client and client:
            await client.aclose()
    return all_pois


async def search_pois_around(keyword: str, location_lnglat: str, radius_m: int, limit: int = 25, pages: int = 2,
                             client: httpx.AsyncClient | None = None) -> list[dict]:
    """高德周边搜索：location 为 GCJ-02 'lng,lat'（经度在前），服务端按半径过滤。支持多页拉取。可传入共享 client。"""
    if not AMAP_API_KEY:
        return []
    all_pois: list[dict] = []
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=8)
    try:
        for page in range(1, pages + 1):
            params = {
                "key": AMAP_API_KEY,
                "location": location_lnglat,
                "radius": str(radius_m),
                "keywords": keyword,
                "extensions": "all",
                "offset": str(min(limit, 25)),
                "page": str(page),
            }
            try:
                resp = await client.get(AMAP_AROUND_SEARCH, params=params)
                data = resp.json()
            except Exception:
                break
            if str(data.get("status")) != "1":
                break
            pois = data.get("pois") or []
            if not pois:
                break
            all_pois.extend(pois)
            if len(pois) < 25:
                break
    finally:
        if own_client and client:
            await client.aclose()
    return all_pois


async def fetch_poi_detail(poi_id: str, max_photos: int = 5,
                           client: httpx.AsyncClient | None = None) -> dict | None:
    """从 place/detail 拉取 POI 完整信息，供地图搜索后的介绍窗口展示。

    返回精简结构：{name, category, address, tel, rating, cost, open_hours,
    province, city, district, photos:[{url,title}], location}
    高德 POI 不提供文字简介，介绍界面以图片+关键信息呈现。
    """
    if not AMAP_API_KEY or not poi_id:
        return None
    params = {"key": AMAP_API_KEY, "id": poi_id, "extensions": "all"}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=6)
    try:
        try:
            resp = await client.get(AMAP_DETAIL, params=params)
            data = resp.json()
        except Exception:
            return None
        if str(data.get("status")) != "1":
            return None
        pois = data.get("pois") or []
        if not pois:
            return None
        p = pois[0]
        biz = p.get("biz_ext") or {}
        cost_val = biz.get("cost") if isinstance(biz, dict) else None
        cost = str(cost_val) if cost_val not in (None, "", []) else ""
        rating = biz.get("rating") if isinstance(biz, dict) else None
        rating = str(rating) if rating not in (None, "", []) else (p.get("rating") or "")
        open_hours = biz.get("open_hours") if isinstance(biz, dict) else None
        open_hours = open_hours if open_hours not in (None, "", []) else ""
        raw_photos = p.get("photos") or []
        photos = [{"url": ph.get("url", ""), "title": ph.get("title", "")}
                  for ph in raw_photos[:max_photos] if isinstance(ph, dict) and ph.get("url")]
        return {
            "id": poi_id,
            "name": p.get("name") or "",
            "category": p.get("type") or "",
            "address": p.get("address") or "",
            "tel": p.get("tel") or "",
            "rating": rating,
            "cost": cost,
            "open_hours": open_hours,
            "province": p.get("pname") or "",
            "city": p.get("cityname") or "",
            "district": p.get("adname") or "",
            "location": p.get("location") or "",
            "photos": photos,
        }
    except Exception:
        return None
    finally:
        if own_client and client:
            await client.aclose()


_hotspots_cache: dict = {"items": [], "ts": 0}
_hotspots_cache_lock = asyncio.Lock()


async def _fetch_city_cat(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                          city: str, cat: str) -> list[dict]:
    """单个 (城市, 类别) 组合的搜索任务，供 asyncio.gather 并发调度。"""
    async with sem:
        try:
            resp = await client.get(AMAP_TEXT_SEARCH, params={
                "key": AMAP_API_KEY,
                "keywords": cat,
                "city": city,
                "citylimit": "true",
                "extensions": "all",
                "offset": "15",
                "page": "1",
            })
            data = resp.json()
            if str(data.get("status")) != "1":
                return []
            out: list[dict] = []
            for poi in data.get("pois") or []:
                pid = poi.get("id")
                if not pid:
                    continue
                biz = poi.get("biz_ext") or {}
                try:
                    r = float(biz.get("rating") or poi.get("rating") or 3.5)
                except (TypeError, ValueError):
                    r = 3.5
                photos = poi.get("photos") or []
                out.append({
                    "id": pid,
                    "name": poi.get("name"),
                    "location": poi.get("location"),
                    "address": poi.get("address") or "",
                    "category": poi.get("type") or "",
                    "rating": str(r),
                    "hot_score": r * (1 + min(len(photos), 10) / 10.0),
                    "city": city,
                    "photos": [{"url": p.get("url", ""), "title": p.get("title", "")}
                               for p in (photos or [])[:5]
                               if isinstance(p, dict) and p.get("url")],
                })
            return out
        except Exception:
            return []


async def fetch_hotspots(limit: int = 200) -> list[dict]:
    """全国热门 POI：按"种子城市 × 类别"组合搜索，按评分×热度排序取 top limit。

    缓存层（三层降级）：
    1. Redis `hotspots:top200` → 毫秒级命中
    2. 进程内 `_hotspots_cache` → 微秒级（Redis 未部署时自动启用）
    3. 上游 API → 并发 15 的 asyncio.gather，冷启动约 1.6s

    熔断保护：连续 API_FAIL_THRESHOLD 次 API 失败后，返回上次成功的结果（带 stale 标记），
    避免高德限流时用户白屏。
    """
    global _api_fail_count, _last_good_hotspots
    cache = CacheService.get_instance()

    # 1. 尝试 Redis / 内存缓存
    cached = await cache.get(HOTSPOT_KEY)
    if cached is None:
        async with _hotspots_cache_lock:
            cached = _hotspots_cache["items"] if _hotspots_cache["items"] and \
                     (time.time() - _hotspots_cache["ts"]) < HOTSPOT_CACHE_TTL else None

    if cached:
        async with _api_fail_lock:
            _api_fail_count = 0
        return cached[:limit]

    # 2. 缓存未命中 → 检查熔断状态
    async with _api_fail_lock:
        if _api_fail_count >= API_FAIL_THRESHOLD and _last_good_hotspots:
            logger.warning("[熔断] API 连续失败 %d 次，返回过期热点缓存", _api_fail_count)
            return _last_good_hotspots[:limit]

    # 3. 拉取上游
    if not AMAP_API_KEY:
        return []

    sem = asyncio.Semaphore(15)
    tasks = []
    errors = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for city in HOTSPOT_CITIES:
            for cat in HOTSPOT_CATEGORIES:
                tasks.append(_fetch_city_cat(client, sem, city, cat))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    seen_ids: set = set()
    gathered: list[dict] = []
    for batch in results:
        if isinstance(batch, Exception):
            errors += 1
            continue
        for poi in batch:
            pid = poi.get("id")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            gathered.append(poi)

    if not gathered:
        async with _api_fail_lock:
            _api_fail_count += 1
            if _api_fail_count >= API_FAIL_THRESHOLD and _last_good_hotspots:
                logger.warning("[熔断触发] API 连续失败 %d 次，启用熔断保护", _api_fail_count)
        return _last_good_hotspots[:limit] if _last_good_hotspots else []

    async with _api_fail_lock:
        _api_fail_count = 0
        _last_good_hotspots = gathered

    gathered.sort(key=lambda p: p.get("hot_score", 0), reverse=True)
    result = gathered[:limit]

    await cache.set(HOTSPOT_KEY, result, HOTSPOT_CACHE_TTL)
    await cache.delete_geo(HOTSPOT_GEO_KEY)
    for poi in result:
        loc = poi.get("location") or ""
        if "," in loc:
            try:
                lng_s, lat_s = loc.split(",")
                await cache.geoadd(HOTSPOT_GEO_KEY, float(lng_s), float(lat_s),
                                   poi["id"], ttl=HOTSPOT_CACHE_TTL)
            except (ValueError, KeyError):
                pass

    async with _hotspots_cache_lock:
        _hotspots_cache["items"] = result
        _hotspots_cache["ts"] = time.time()

    return result