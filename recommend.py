"""基于规则的推荐引擎：距离 + 评分 + 标签匹配，结果可解释。

输入：城市、偏好标签、可选用户经纬度、预算
输出：带分数与推荐理由的 POI 列表
"""
import asyncio
import hashlib
import json
import logging
import math
from typing import Optional

import httpx

from trip_planner.cache import CacheService
from trip_planner import models
from trip_planner.geo_utils import (
    haversine_km,
    valid_latlng,
    parse_user_location,
    parse_amap_location,
    wgs84_to_gcj02,
)
from trip_planner.amap_client import (
    search_pois,
    search_pois_around,
    fetch_poi_detail,
    fetch_hotspots,
    HOTSPOT_CACHE_TTL,
)

logger = logging.getLogger("trip_planner.recommend")

# 无偏好时的兜底搜索词
FALLBACK_KEYWORDS = ["景点", "美食", "购物"]

RECOMMEND_CACHE_TTL = 600
REGEO_CACHE_TTL = 7 * 86400
SUGGEST_CACHE_TTL = 1800

POI_DETAIL_KEY = "poi:detail:"

# 半径档位（米）
RADIUS_TIERS = [1000, 3000, 5000, 10000, 20000, 50000]
DEFAULT_RADIUS_M = 5000
MIN_RESULTS = 10

# 打分权重
W_RATING = 0.40
W_POPULARITY = 0.20
W_TAG = 0.20
W_DISTANCE = 0.20
W_RATING_STRICT = 0.35
W_DISTANCE_STRICT = 0.25
DETOUR_RATING = 4.5
RADIUS_BREACH = 1.2
MAX_DISTANCE_KM = 50.0
MAX_PHOTOS = 10


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


def snap_radius(radius_m: int | None) -> int:
    """把任意半径值吸附到最近的档位；未提供时用默认档。"""
    if radius_m is None:
        return DEFAULT_RADIUS_M
    return min(RADIUS_TIERS, key=lambda t: abs(t - radius_m))


def _score_poi(poi: dict, tag: str, user_lat_lng: Optional[tuple],
               strict: bool = False) -> tuple[float, str, Optional[int], float]:
    """计算单个 POI 的分数、推荐理由、距离（米）与评分。"""
    raw_rating = _raw_rating(poi)
    try:
        rating = float(raw_rating) if raw_rating is not None else 3.5
    except (TypeError, ValueError):
        rating = 3.5
    rating_norm = min(rating / 5.0, 1.0)

    photo_count = len(poi.get("photos") or [])
    popularity = min(photo_count / MAX_PHOTOS, 1.0)

    name = poi.get("name") or ""
    poi_type = poi.get("type") or ""
    if tag and (tag in name or tag in poi_type):
        tag_match = 1.0
    else:
        tag_match = 0.4

    distance_km = None
    if user_lat_lng:
        loc = parse_amap_location(poi.get("location") or "")
        if loc:
            distance_km = haversine_km(user_lat_lng[0], user_lat_lng[1], loc[0], loc[1])
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
    - "gcj02"：地图视图传来的视野中心（高德 JS API 即 GCJ-02），跳过转换

    返回: {"items": [...], "meta": {mode, city, radius_m, requested_radius_m,
          expanded, nearby_only}}
    """
    if not city:
        return {"items": [], "meta": {"mode": "city", "city": city, "radius_m": None,
                                      "requested_radius_m": None, "expanded": False,
                                      "nearby_only": nearby_only}}

    cache_key = f"recommend:{hashlib.md5(json.dumps({
        'city': city, 'prefs': sorted(preferences), 'radius': radius_m,
        'coord': coord_type, 'nearby': nearby_only, 'limit': limit,
        'loc': user_location,
    }, ensure_ascii=False).encode()).hexdigest()}"
    cache = CacheService.get_instance()
    cached_result = await cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        _ent_products_pre = models.get_active_enterprise_products(city)
    except Exception:
        _ent_products_pre = []

    user_lat_lng = None
    if user_location:
        parsed = parse_user_location(user_location)
        if parsed:
            if coord_type == "gcj02":
                user_lat_lng = parsed
            else:
                user_lat_lng = wgs84_to_gcj02(*parsed)

    nearby = user_lat_lng is not None
    strict = nearby and nearby_only
    requested_radius = snap_radius(radius_m) if nearby else None
    radius = requested_radius
    keywords = preferences if preferences else FALLBACK_KEYWORDS

    while True:
        all_pois: list[dict] = []
        async with httpx.AsyncClient(timeout=8) as http_client:
            if nearby:
                search_radius = radius if strict else int(radius * RADIUS_BREACH)
                loc_str = f"{user_lat_lng[1]},{user_lat_lng[0]}"
                tasks = [search_pois_around(kw, loc_str, search_radius, limit=25, client=http_client)
                         for kw in keywords]
                batches = await asyncio.gather(*tasks)
                for kw, batch in zip(keywords, batches):
                    for p in batch:
                        p["_matched_tag"] = kw
                        all_pois.append(p)
            else:
                tasks = [search_pois(kw, city, limit=20, client=http_client)
                         for kw in keywords]
                batches = await asyncio.gather(*tasks)
                for kw, batch in zip(keywords, batches):
                    for p in batch:
                        p["_matched_tag"] = kw
                        all_pois.append(p)

        seen = set()
        unique = []
        for p in all_pois:
            name = p.get("name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            unique.append(p)

        scored = []
        for p in unique:
            score, reason, distance_m, rating = _score_poi(
                p, p.get("_matched_tag", ""), user_lat_lng, strict=strict)
            r = _raw_rating(p)
            scored.append({
                "id": p.get("id") or "",
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

        if nearby and strict:
            scored = [it for it in scored
                      if it["distance_m"] is None or it["distance_m"] <= radius]

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

    _ENT_PROMO_CAP = 0.10
    _ENT_IDEAL_BID = 200
    ent_products = _ent_products_pre

    for ep in ent_products:
        if ep.get("lng") is None or ep.get("lat") is None:
            continue
        try:
            lng_f = float(ep["lng"])
            lat_f = float(ep["lat"])
            if not valid_latlng(lat_f, lng_f):
                continue
        except (ValueError, TypeError):
            continue

        fake_poi = {
            "name": ep.get("name") or "",
            "type": ep.get("category") or "",
            "address": ep.get("address") or "",
            "location": f"{lng_f},{lat_f}",
            "rating": str(ep.get("rating") or 3.5),
            "photos": [],
            "biz_ext": {},
        }
        tag_for_match = (preferences[0] if preferences else "") or ep.get("category") or ""
        score, reason, distance_m, rating = _score_poi(
            fake_poi, tag_for_match, user_lat_lng, strict=strict)

        if nearby and strict:
            if distance_m is not None and distance_m > radius:
                continue

        bid = ep.get("bid_per_1k") or 0
        promo_status = ep.get("promotion_status") or 0
        promo_bonus = 0.0
        if promo_status and bid > 0:
            promo_bonus = min(bid / _ENT_IDEAL_BID * _ENT_PROMO_CAP, _ENT_PROMO_CAP)

        ent_score = score + promo_bonus

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

    seen_names: dict[str, dict] = {}
    for item in scored:
        nm = item["name"]
        if nm in seen_names:
            if item["score"] > seen_names[nm]["score"]:
                seen_names[nm] = item
        else:
            seen_names[nm] = item
    scored = list(seen_names.values())

    diversity_cap = 10 if len(preferences) <= 1 else 5
    scored.sort(key=lambda x: x["score"], reverse=True)
    category_count: dict[str, int] = {}
    result = []
    ent_hit_ids: list[int] = []
    for item in scored:
        cat = (item["category"] or "").split(";")[0]
        if category_count.get(cat, 0) >= diversity_cap:
            continue
        category_count[cat] = category_count.get(cat, 0) + 1
        item.pop("rating_value", None)
        if item.get("_is_enterprise"):
            eid = item.pop("_enterprise_id", None)
            if eid:
                ent_hit_ids.append(int(eid))
            item.pop("_promotion_bonus", None)
        raw_photos = item.pop("_raw_photos", None) or []
        item["photos"] = [{"url": p.get("url", ""), "title": p.get("title", "")}
                          for p in raw_photos[:5] if isinstance(p, dict) and p.get("url")]
        result.append(item)
        if len(result) >= limit:
            break

    meta["enterprise_hit"] = len(ent_hit_ids)

    final = {"items": result, "meta": meta}

    if ent_hit_ids:
        async def _bump_stats():
            try:
                for pid in ent_hit_ids:
                    await asyncio.to_thread(models.increment_product_stats, pid, "recommend")
            except Exception:
                logger.warning("企业产品推荐统计更新失败，ids=%s", ent_hit_ids, exc_info=True)
        asyncio.create_task(_bump_stats())

    await cache.set(cache_key, final, RECOMMEND_CACHE_TTL)
    return final