"""基于规则的推荐引擎：距离 + 评分 + 标签匹配，结果可解释。

输入：城市、偏好标签、可选用户经纬度、预算
输出：带分数与推荐理由的 POI 列表
"""
import asyncio
import hashlib
import json
import logging
import math
import time
from typing import Optional

import httpx

from env_utils import AMAP_API_KEY
from trip_planner.cache import CacheService
from trip_planner import models

logger = logging.getLogger("trip_planner.recommend")

AMAP_TEXT_SEARCH = "https://restapi.amap.com/v3/place/text"
AMAP_AROUND_SEARCH = "https://restapi.amap.com/v3/place/around"
AMAP_DETAIL = "https://restapi.amap.com/v3/place/detail"
# 无偏好时的兜底搜索词
FALLBACK_KEYWORDS = ["景点", "美食", "购物"]
# 全国热门景点搜索的类别词 + 城市种子（覆盖一二线 + 热门旅游城市）
HOTSPOT_CATEGORIES = ["风景名胜", "博物馆", "美食", "酒店", "娱乐"]
HOTSPOT_CITIES = ["北京", "上海", "广州", "深圳", "成都", "西安", "杭州", "重庆",
                  "苏州", "南京", "武汉", "长沙", "厦门", "青岛", "大理", "丽江"]
HOTSPOT_CACHE_TTL = 3600  # 秒：全国热点每小时刷新一次
RECOMMEND_CACHE_TTL = 600  # 秒：推荐结果 10 分钟（城市+偏好+半径 组合稳定）
REGEO_CACHE_TTL = 7 * 86400  # 秒：逆地理编码 7 天（地理位置几乎不变）
SUGGEST_CACHE_TTL = 1800  # 秒：城市联想 30 分钟

# 缓存 key 前缀
HOTSPOT_KEY = "hotspots:top200"
HOTSPOT_GEO_KEY = "geo:hotspots"
POI_DETAIL_KEY = "poi:detail:"

# 缓存熔断：连续熔断阈值 + 过期缓存兜底
API_FAIL_THRESHOLD = 5
_api_fail_count = 0
_last_good_hotspots: list[dict] = []  # 熔断时返回的兜底数据

# 半径档位（米）：附近模式的可选范围，默认 5km，最多扩到 50km
RADIUS_TIERS = [1000, 3000, 5000, 10000, 20000, 50000]
DEFAULT_RADIUS_M = 5000
MIN_RESULTS = 5  # 附近结果少于此数时自动扩一档半径

# 打分权重（可配置）
W_RATING = 0.40
W_POPULARITY = 0.20
W_TAG = 0.20
W_DISTANCE = 0.20
# "仅看附近"严格模式：距离权重上调、评分下调（总权重仍为 1），
# 让"近"成为更强的排序信号
W_RATING_STRICT = 0.35
W_DISTANCE_STRICT = 0.25
# "值得绕路"白名单（宽松模式）：评分达标的 POI 可突破半径 20% 进入结果
DETOUR_RATING = 4.5
RADIUS_BREACH = 1.2
MAX_DISTANCE_KM = 50.0  # 超过该距离距离分得 0
MAX_PHOTOS = 10  # 照片数归一上限


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _valid_latlng(lat: float, lng: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _raw_rating(poi: dict):
    """POI 评分的原始值：优先 biz_ext.rating，回退顶层 rating；缺失/空返回 None。"""
    biz = poi.get("biz_ext") or {}
    value = biz.get("rating") if isinstance(biz, dict) else None
    if value in (None, "", []):
        value = poi.get("rating")
    return value if value not in (None, "", []) else None


def _raw_cost(poi: dict) -> str:
    """人均消费：高德原始 POI 在 biz_ext.cost（缺失时为 []），
    企业产品等归一化数据用顶层 cost；统一取字符串，缺失返回 ''。"""
    biz = poi.get("biz_ext")
    value = biz.get("cost") if isinstance(biz, dict) else None
    if value in (None, "", []):
        value = poi.get("cost")
    return str(value) if value not in (None, "", []) else ""


def parse_user_location(loc_str: str):
    """解析用户坐标 'lat,lng'（W3C Geolocation 顺序：纬度在前）。

    返回 (lat, lng)；格式错误或超出合法范围返回 None。
    """
    try:
        lat_s, lng_s = loc_str.split(",")
        lat, lng = float(lat_s), float(lng_s)
    except (ValueError, AttributeError):
        return None
    if not _valid_latlng(lat, lng):
        return None
    return lat, lng


def _parse_amap_location(loc_str: str):
    """解析高德 POI 坐标 'lng,lat'（经度在前），返回 (lat, lng)；非法返回 None。"""
    try:
        lng_s, lat_s = loc_str.split(",")
        lat, lng = float(lat_s), float(lng_s)
    except (ValueError, AttributeError):
        return None
    if not _valid_latlng(lat, lng):
        return None
    return lat, lng


# ---------- WGS-84 → GCJ-02 转换 ----------
# 浏览器 Geolocation 返回 WGS-84 坐标，高德 POI 使用 GCJ-02（火星坐标系），
# 在中国境内两者相差约 300–700 米，距离计算前必须统一到 GCJ-02。
_PI = math.pi
_A = 6378245.0  # 克拉索夫斯基椭球长半轴
_EE = 0.00669342162296594323  # 第一偏心率平方


def _out_of_china(lat: float, lng: float) -> bool:
    return not (0.8293 <= lat <= 55.8271 and 72.004 <= lng <= 137.8347)


def _transform_lat(x: float, y: float) -> float:
    ret = (-100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
           + 0.2 * math.sqrt(abs(x)))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = (300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
           + 0.1 * math.sqrt(abs(x)))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """WGS-84 转 GCJ-02。中国境外无偏移，原样返回。"""
    if _out_of_china(lat, lng):
        return lat, lng
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * _PI
    magic = 1 - _EE * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * _PI)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * _PI)
    return lat + dlat, lng + dlng


async def _search_pois(keyword: str, city: str, limit: int = 25) -> list[dict]:
    """调用高德文本搜索，返回 POI 列表。"""
    if not AMAP_API_KEY:
        return []
    params = {
        "key": AMAP_API_KEY,
        "keywords": keyword,
        "city": city,
        "citylimit": "true",
        "extensions": "all",
        "offset": str(limit),
        "page": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(AMAP_TEXT_SEARCH, params=params)
            data = resp.json()
        if str(data.get("status")) != "1":
            return []
        return data.get("pois") or []
    except Exception:
        return []


async def _search_pois_around(keyword: str, location_lnglat: str, radius_m: int, limit: int = 25) -> list[dict]:
    """高德周边搜索：location 为 GCJ-02 'lng,lat'（经度在前），服务端按半径过滤。

    结果天然落在 radius_m 内，从数据源层面保证"附近"承诺。
    """
    if not AMAP_API_KEY:
        return []
    params = {
        "key": AMAP_API_KEY,
        "location": location_lnglat,
        "radius": str(radius_m),
        "keywords": keyword,
        "extensions": "all",
        "offset": str(limit),
        "page": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(AMAP_AROUND_SEARCH, params=params)
            data = resp.json()
        if str(data.get("status")) != "1":
            return []
        return data.get("pois") or []
    except Exception:
        return []


async def _fetch_poi_detail(poi_id: str, max_photos: int = 5) -> dict | None:
    """从 place/detail 拉取 POI 完整信息，供地图搜索后的介绍窗口展示。

    返回精简结构：{name, category, address, tel, rating, cost, open_hours,
    province, city, district, photos:[{url,title}], location}
    高德 POI 不提供文字简介，介绍界面以图片+关键信息呈现。
    """
    if not AMAP_API_KEY or not poi_id:
        return None
    params = {"key": AMAP_API_KEY, "id": poi_id, "extensions": "all"}
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            resp = await client.get(AMAP_DETAIL, params=params)
            data = resp.json()
        if str(data.get("status")) != "1":
            return None
        pois = data.get("pois") or []
        if not pois:
            return None
        p = pois[0]
        biz = p.get("biz_ext") or {}
        # biz_ext.cost 在无数据时可能为 []，统一用防御函数
        cost_val = biz.get("cost") if isinstance(biz, dict) else None
        cost = str(cost_val) if cost_val not in (None, "", []) else ""
        rating = biz.get("rating") if isinstance(biz, dict) else None
        rating = str(rating) if rating not in (None, "", []) else (p.get("rating") or "")
        # 营业时间在 biz_ext.open_hours，部分 POI 缺失
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


# —— 全国热门 POI 缓存：避免每次进地图都触发几十次 API 调用 ——
_hotspots_cache: dict = {"items": [], "ts": 0}


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
    3. 上游 API → 并发 15 的 asyncio.gather，冷启动 1.6s

    熔断保护：连续 API_FAIL_THRESHOLD 次 API 失败后，返回上次成功的结果（带 stale 标记），
    避免高德限流时用户白屏。
    """
    global _api_fail_count, _last_good_hotspots
    cache = CacheService.get_instance()

    # 1. 尝试 Redis / 内存缓存
    cached = await cache.get(HOTSPOT_KEY)
    if cached is None:
        cached = _hotspots_cache["items"] if _hotspots_cache["items"] and \
                 (time.time() - _hotspots_cache["ts"]) < HOTSPOT_CACHE_TTL else None

    if cached:
        # 缓存命中，重置熔断计数
        _api_fail_count = 0
        return cached[:limit]

    # 2. 缓存未命中 → 检查熔断状态
    if _api_fail_count >= API_FAIL_THRESHOLD and _last_good_hotspots:
        # 熔断中：返回上次成功的结果
        logger.warning("[熔断] API 连续失败 %d 次，返回过期热点缓存", _api_fail_count)
        return _last_good_hotspots[:limit]

    # 3. 拉取上游
    if not AMAP_API_KEY:
        return []

    sem = asyncio.Semaphore(15)  # 高德免费 QPS 约 20，留 5 给其他模块
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

    # 全部失败 → 累计熔断
    if not gathered:
        _api_fail_count += 1
        if _api_fail_count >= API_FAIL_THRESHOLD and _last_good_hotspots:
            logger.warning("[熔断触发] API 连续失败 %d 次，启用熔断保护", _api_fail_count)
        return _last_good_hotspots[:limit] if _last_good_hotspots else []

    # 有成功结果 → 重置熔断计数 + 保存兜底
    _api_fail_count = 0
    _last_good_hotspots = gathered

    # 按热度分降序取 top limit
    gathered.sort(key=lambda p: p.get("hot_score", 0), reverse=True)
    result = gathered[:limit]

    # 4. 写入 Redis + GEO 索引（异步，不阻塞响应）
    await cache.set(HOTSPOT_KEY, result, HOTSPOT_CACHE_TTL)
    # 重建 GEO：先删旧 key 再逐个写入
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

    # 5. 同步更新进程内缓存（Redis 未部署时的双重保险）
    _hotspots_cache["items"] = result
    _hotspots_cache["ts"] = time.time()

    return result


def snap_radius(radius_m: int | None) -> int:
    """把任意半径值吸附到最近的档位；未提供时用默认档。"""
    if radius_m is None:
        return DEFAULT_RADIUS_M
    return min(RADIUS_TIERS, key=lambda t: abs(t - radius_m))


def _score_poi(poi: dict, tag: str, user_lat_lng: Optional[tuple],
               strict: bool = False) -> tuple[float, str, Optional[int], float]:
    """计算单个 POI 的分数、推荐理由、距离（米）与评分。

    strict: "仅看附近"严格模式，距离权重 0.25 / 评分权重 0.35。
    """
    # 评分：优先 biz_ext.rating，回退顶层 rating；缺失给中性 3.5
    raw_rating = _raw_rating(poi)
    try:
        rating = float(raw_rating) if raw_rating is not None else 3.5
    except (TypeError, ValueError):
        rating = 3.5
    rating_norm = min(rating / 5.0, 1.0)

    # 热度：照片数归一（有照片说明运营完善、关注度高）
    photo_count = len(poi.get("photos") or [])
    popularity = min(photo_count / MAX_PHOTOS, 1.0)

    # 标签匹配：偏好词出现在名称或类别中；未命中给 0.4（因来自该关键词搜索，仍有相关性）
    name = poi.get("name") or ""
    poi_type = poi.get("type") or ""
    if tag and (tag in name or tag in poi_type):
        tag_match = 1.0
    else:
        tag_match = 0.4

    # 距离分
    distance_km = None
    if user_lat_lng:
        loc = _parse_amap_location(poi.get("location") or "")
        if loc:
            distance_km = _haversine_km(user_lat_lng[0], user_lat_lng[1], loc[0], loc[1])
            distance_score = max(0.0, 1.0 - distance_km / MAX_DISTANCE_KM)
        else:
            distance_score = 0.3
    else:
        distance_score = 0.3

    w_rating = W_RATING_STRICT if strict else W_RATING
    w_distance = W_DISTANCE_STRICT if strict else W_DISTANCE
    score = (w_rating * rating_norm + W_POPULARITY * popularity
             + W_TAG * tag_match + w_distance * distance_score)

    distance_m = int(distance_km * 1000) if distance_km is not None else None

    # 理由（可解释）：不足 1km 用米展示，更符合步行直觉
    parts = [f"评分 {rating:.1f}"]
    if photo_count:
        parts.append(f"{photo_count} 张实拍图")
    if distance_m is not None:
        if distance_m < 1000:
            parts.append(f"距你 {distance_m}m")
        else:
            parts.append(f"距你 {distance_km:.1f}km")
    if tag_match >= 1.0:
        parts.append(f"匹配「{tag}」")
    reason = "，".join(parts)
    return round(score, 3), reason, distance_m, rating


async def recommend(
    city: str,
    preferences: list[str],
    user_location: Optional[str] = None,
    budget: Optional[str] = None,
    limit: int = 12,
    radius_m: Optional[int] = None,
    nearby_only: bool = True,
    coord_type: str = "wgs84",
) -> dict:
    """主推荐入口。

    user_location: 'lat,lng' 字符串，纬度在前。coord_type 决定坐标系：
    - "wgs84"（默认）：浏览器 Geolocation 原始输出，内部转换为高德 GCJ-02
    - "gcj02"：地图视图传来的视野中心（高德 JS API 即 GCJ-02），跳过转换，
      否则会二次偏移数百米，导致"地图上指着 A，推荐却在 B"

    数据源分流（地理邻近性的根本保证）：
    - 有坐标 → 高德周边搜索（place/around），服务端按半径过滤，结果天然在附近
    - 无坐标 → 高德文本搜索（place/text），全市范围，距离仅作打分不作承诺

    nearby_only（"仅看附近"开关，仅有坐标时生效）：
    - True（严格）：半径硬过滤 + 距离权重上调，"近"是强承诺
    - False（宽松）：检索半径放宽 20%，评分 ≥ DETOUR_RATING 的高分 POI
      可略超半径进入，标记 out_of_range 并注明"略超范围但值得"

    渐进扩半径：附近结果少于 MIN_RESULTS 时自动扩一档，最多到 50km，
    并在 meta 中标记 expanded，供前端向用户说明。

    返回: {"items": [...], "meta": {mode, city, radius_m, requested_radius_m,
          expanded, nearby_only}}
    """
    if not city:
        return {"items": [], "meta": {"mode": "city", "city": city, "radius_m": None,
                                      "requested_radius_m": None, "expanded": False,
                                      "nearby_only": nearby_only}}

    # 企业产品快照（同步拉取，极快；纳入 cache_key 让企业产品变更自动触发缓存刷新）
    try:
        _ent_products_pre = models.get_active_enterprise_products(city)
    except Exception:
        _ent_products_pre = []
    _ent_snap_for_key = json.dumps(
        [(ep["id"], ep.get("status"), ep.get("bid_per_1k")) for ep in _ent_products_pre],
        ensure_ascii=False, sort_keys=True,
    )

    # 缓存查询：相同 city+preferences+radius+coord_type+nearby_only+limit+企业快照 → 复用
    cache_key = f"recommend:{hashlib.md5(json.dumps({
        'city': city, 'prefs': sorted(preferences), 'radius': radius_m,
        'coord': coord_type, 'nearby': nearby_only, 'limit': limit,
        'loc': user_location, 'ent': _ent_snap_for_key,
    }, ensure_ascii=False).encode()).hexdigest()}"
    cache = CacheService.get_instance()
    cached_result = await cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    user_lat_lng = None
    if user_location:
        parsed = parse_user_location(user_location)
        if parsed:
            if coord_type == "gcj02":
                # 地图视野中心已是高德坐标系，直接使用
                user_lat_lng = parsed
            else:
                # 浏览器 GPS 为 WGS-84，高德 POI 为 GCJ-02，先转换统一坐标系
                user_lat_lng = wgs84_to_gcj02(*parsed)

    nearby = user_lat_lng is not None
    strict = nearby and nearby_only
    requested_radius = snap_radius(radius_m) if nearby else None
    radius = requested_radius
    keywords = preferences if preferences else FALLBACK_KEYWORDS

    while True:
        # 按偏好检索（周边搜索 / 文本搜索两种数据源）
        all_pois: list[dict] = []
        if nearby:
            # 宽松模式检索半径放宽 20%，给"值得绕路"的高分 POI 留出缓冲带；
            # 全市模式无半径概念，search_radius 不参与文本搜索
            search_radius = radius if strict else int(radius * RADIUS_BREACH)
            loc_str = f"{user_lat_lng[1]},{user_lat_lng[0]}"  # 高德要求 'lng,lat'
            for kw in keywords:
                for p in await _search_pois_around(kw, loc_str, search_radius, limit=25):
                    p["_matched_tag"] = kw
                    all_pois.append(p)
        else:
            for kw in keywords:
                for p in await _search_pois(kw, city, limit=20):
                    p["_matched_tag"] = kw
                    all_pois.append(p)

        # 去重（按名称）
        seen = set()
        unique = []
        for p in all_pois:
            name = p.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            unique.append(p)

        # 打分
        scored = []
        for p in unique:
            score, reason, distance_m, rating = _score_poi(
                p, p.get("_matched_tag", ""), user_lat_lng, strict=strict)
            # 展示用评分取原始值（缺失时不展示），内部过滤/排序用中性默认值
            r = _raw_rating(p)
            scored.append({
                "name": p.get("name"),
                "address": p.get("address") or "",
                "category": p.get("type") or "",
                "rating": str(r) if r is not None else "",
                "cost": _raw_cost(p),
                "location": p.get("location") or "",
                "score": score,
                "reason": reason,
                "distance_m": distance_m,
                "rating_value": rating,
                "out_of_range": False,
                "_raw_photos": p.get("photos") or [],
            })

        # 宽松模式过滤：半径内全保留；半径外仅留评分达标的"值得绕路"，并明确标注
        if nearby and not strict:
            filtered = []
            for it in scored:
                if it["distance_m"] is None or it["distance_m"] <= radius:
                    filtered.append(it)
                elif it["rating_value"] >= DETOUR_RATING:
                    it["out_of_range"] = True
                    it["reason"] += "，略超范围但值得"
                    filtered.append(it)
            scored = filtered

        # 严格模式：应用层兜底一次硬过滤。"仅看附近"是产品承诺，
        # 不应完全依赖上游周边搜索 API 的服务端过滤行为
        if nearby and strict:
            scored = [it for it in scored
                      if it["distance_m"] is None or it["distance_m"] <= radius]

        # 渐进扩半径：结果太少就扩大一档再搜一次，避免"附近没结果"的冷场
        if not nearby or len(scored) >= MIN_RESULTS or radius >= RADIUS_TIERS[-1]:
            break
        radius = RADIUS_TIERS[RADIUS_TIERS.index(radius) + 1]

    meta = {
        "mode": "nearby" if nearby else "city",
        "city": city,
        "radius_m": radius,
        "requested_radius_m": requested_radius,
        "expanded": nearby and radius != requested_radius,
        "nearby_only": nearby_only if nearby else None,
    }

    # ============ 企业产品合并 ============
    # 规则分始终 ≥ 90%，推广加权 ≤ 10%（bid_per_1k 越高权重越高，封顶 0.10）
    _ENT_PROMO_CAP = 0.10
    _ENT_IDEAL_BID = 200  # ¥200/千次 → 拿满 10% 加权
    # 复用前面为 cache_key 预拉的快照
    ent_products = _ent_products_pre

    for ep in ent_products:
        # 跳过无坐标产品（无法参与附近推荐，全市推荐时也没有距离分）
        if ep.get("lng") is None or ep.get("lat") is None:
            continue
        try:
            lng_f = float(ep["lng"])
            lat_f = float(ep["lat"])
            if not _valid_latlng(lat_f, lng_f):
                continue
        except (ValueError, TypeError):
            continue

        # 转为高德 POI 兼容格式，复用 _score_poi 统一打分
        fake_poi = {
            "name": ep.get("name") or "",
            "type": ep.get("category") or "",
            "address": ep.get("address") or "",
            "location": f"{lng_f},{lat_f}",  # 高德格式 lng,lat
            "rating": str(ep.get("rating") or 3.5),
            "photos": [],  # 企业产品暂无照片
            "biz_ext": {},
        }
        # 标签匹配：用第一个偏好词，无偏好时用分类
        tag_for_match = (preferences[0] if preferences else "") or ep.get("category") or ""
        score, reason, distance_m, rating = _score_poi(
            fake_poi, tag_for_match, user_lat_lng, strict=strict)

        # 附近模式：距离硬过滤
        if nearby and strict:
            if distance_m is not None and distance_m > radius:
                continue

        # 推广加权：bid_per_1k 越高加权越高，封顶 _ENT_PROMO_CAP
        bid = ep.get("bid_per_1k") or 0
        promo_status = ep.get("promotion_status") or 0
        promo_bonus = 0.0
        if promo_status and bid > 0:
            promo_bonus = min(bid / _ENT_IDEAL_BID * _ENT_PROMO_CAP, _ENT_PROMO_CAP)

        ent_score = score + promo_bonus

        # 合并进 scored 列表，标记来源
        scored.append({
            "name": ep.get("name"),
            "address": ep.get("address") or "",
            "category": ep.get("category") or "",
            "rating": str(ep.get("rating") or "") if ep.get("rating") not in (None, "", []) else "",
            "cost": "",
            "location": f"{lng_f},{lat_f}",
            "score": ent_score,
            "reason": reason + ("，商家推荐" if promo_bonus > 0 else ""),
            "distance_m": distance_m,
            "rating_value": rating,
            "out_of_range": False,
            "_raw_photos": [],
            "_is_enterprise": True,
            "_enterprise_id": ep["id"],
            "_enterprise_name": ep.get("enterprise_name"),
            "_promotion_bonus": promo_bonus,
        })

    # 名称去重：企业产品和同名高德 POI 取高分者
    seen_names: dict[str, dict] = {}
    for item in scored:
        nm = item["name"]
        if nm in seen_names:
            if item["score"] > seen_names[nm]["score"]:
                seen_names[nm] = item
        else:
            seen_names[nm] = item
    scored = list(seen_names.values())

    # 多样性：同类目最多 3 个
    scored.sort(key=lambda x: x["score"], reverse=True)
    category_count: dict[str, int] = {}
    result = []
    ent_hit_ids: list[int] = []  # 本轮推荐中命中的企业产品 id，用于异步统计
    for item in scored:
        cat = (item["category"] or "").split(";")[0]
        if category_count.get(cat, 0) >= 3:
            continue
        category_count[cat] = category_count.get(cat, 0) + 1
        item.pop("rating_value", None)  # 内部排序字段，不外露
        # 企业内部字段：保留 _is_enterprise / _enterprise_name 供前端展示，其余清理
        if item.get("_is_enterprise"):
            eid = item.pop("_enterprise_id", None)
            if eid:
                ent_hit_ids.append(int(eid))
            item.pop("_promotion_bonus", None)
        # photos：extensions=all 已返回 poi 级 photos，直接映射到响应结构
        raw_photos = item.pop("_raw_photos", None) or []
        item["photos"] = [{"url": p.get("url", ""), "title": p.get("title", "")}
                          for p in raw_photos[:5] if isinstance(p, dict) and p.get("url")]
        result.append(item)
        if len(result) >= limit:
            break

    # meta 附带企业命中数（前端可识别是否有企业产品）
    meta["enterprise_hit"] = len(ent_hit_ids)

    final = {"items": result, "meta": meta}

    # 异步递增企业产品推荐统计（fire-and-forget，不阻塞响应）
    if ent_hit_ids:
        async def _bump_stats():
            try:
                for pid in ent_hit_ids:
                    await asyncio.to_thread(models.increment_product_stats, pid, "recommend")
            except Exception:
                pass  # 统计失败不影响主流程
        asyncio.create_task(_bump_stats())

    # 结果写入缓存（推荐结果 10min 稳定，企业快照已在 cache_key 中，变更自动刷新）
    await cache.set(cache_key, final, RECOMMEND_CACHE_TTL)
    return final
