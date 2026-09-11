"""地理坐标工具：距离计算、坐标系转换（WGS-84 ↔ GCJ-02）、坐标校验。"""

import math

_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def valid_latlng(lat: float, lng: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def parse_user_location(loc_str: str):
    """解析用户坐标 'lat,lng'（W3C Geolocation 顺序：纬度在前）。

    返回 (lat, lng)；格式错误或超出合法范围返回 None。
    """
    try:
        lat_s, lng_s = loc_str.split(",")
        lat, lng = float(lat_s), float(lng_s)
    except (ValueError, AttributeError):
        return None
    if not valid_latlng(lat, lng):
        return None
    return lat, lng


def parse_amap_location(loc_str: str):
    """解析高德 POI 坐标 'lng,lat'（经度在前），返回 (lat, lng)；非法返回 None。"""
    try:
        lng_s, lat_s = loc_str.split(",")
        lat, lng = float(lat_s), float(lng_s)
    except (ValueError, AttributeError):
        return None
    if not valid_latlng(lat, lng):
        return None
    return lat, lng


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