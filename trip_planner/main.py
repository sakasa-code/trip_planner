import sys

# Windows 中文环境默认 GBK 编码，打印 emoji 会抛 UnicodeEncodeError 并掩盖真实错误；
# 统一改用 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging
import os
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from trip_planner.schemas import TripRequest
from trip_planner.trip_planner_agent import get_trip_planner_agent
from trip_planner import models, auth, location, recommend
from trip_planner.cache import CacheService
from env_utils import AMAP_API_KEY, REDIS_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("trip_planner")

# 开发期可设置 DEBUG_ERRORS=true，让前端看到真实错误信息便于排查；
# 默认关闭，避免把内部异常（如带 API Key 的 MCP 地址）泄露给前端。
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "").lower() in ("1", "true", "yes")


def _public_error_message(message: str) -> str:
    """调试模式才透出内部异常细节，否则返回通用提示，避免泄露内部信息（如含 Key 的地址）。"""
    return message if DEBUG_ERRORS else "服务暂时不可用，请稍后重试"


planner = get_trip_planner_agent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化缓存层（优先 Redis，不可用时降级内存）
    cache = CacheService.get_instance()
    try:
        await cache.init(REDIS_URL)
        logger.info("缓存服务已就绪，后端=%s", cache.backend)
    except Exception:
        logger.warning("缓存初始化异常，降级内存", exc_info=True)

    # 服务启动时尝试一次性初始化智能体（幂等）。
    # 注意：初始化会联网连接高德 MCP，失败时只告警、不阻断服务启动，
    # 后续每个请求仍会惰性重试并返回可读错误。
    try:
        await planner.initialize()
    except Exception:
        logger.warning("启动时初始化智能体失败，将在首次请求时重试", exc_info=True)
    yield
    await planner.aclose()


app = FastAPI(title="AI旅行助手", version="1.1", lifespan=lifespan)

# 明确列出允许的来源，不再使用通配符 + 凭据的不安全组合；
# 前端既可从本服务(8000)直接访问，也可通过 Live Server(5500) 访问。
FRONTEND_ORIGINS = [
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,  # 纯 JSON API，无 Cookie 会话，无需凭据
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """全局兜底：未捕获异常一律返回结构化 JSON，绝不把纯文本/HTML 的 500 页抛给前端——
    否则前端 r.json() 会二次报 "Unexpected token 'I' ... Internal Server Error"，
    用解析异常掩盖真实错误。真实堆栈只落服务端日志，细节仅 DEBUG_ERRORS 时透出。"""
    logger.error("未处理异常 %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "internal_error",
            "message": _public_error_message(f"服务器内部错误：{exc}"),
        },
    )


@app.get("/", include_in_schema=False)
async def root():
    """根路径直接提供前端页面，访问 http://127.0.0.1:8000 即可使用"""
    index_file = Path(__file__).parent / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"message": "AI旅行规划系统已启动！前端 index.html 缺失"})


@app.get("/api/health")
async def health():
    return {"status": "ok", "message": "AI旅行规划系统已启动", "frontend": "http://127.0.0.1:8000"}


@app.get("/api/v1/city/suggest")
async def city_suggest(q: str = Query(..., min_length=1, max_length=30)):
    """城市/地点输入联想：代理高德「输入提示」接口，避免前端暴露 Key。

    缓存：suggest:{q}，30 分钟 TTL。高频搜索词（如"北京"）直接命中，
    避免每次按键都触发上游 API。
    """
    from trip_planner.recommend import SUGGEST_CACHE_TTL
    cache = CacheService.get_instance()
    cache_key = f"suggest:{q}"
    cached = await cache.get(cache_key)
    if cached is not None:
        return cached

    if not AMAP_API_KEY:
        return JSONResponse({"status": "0", "tips": []})
    params = {
        "key": AMAP_API_KEY,
        "keywords": q,
        "city": q,
        "citylimit": "false",
        "datatype": "poi",
        "offset": "8",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://restapi.amap.com/v3/assistant/inputtips", params=params
            )
            data = resp.json()
        tips = []
        for t in data.get("tips", []) or []:
            name = t.get("name") or ""
            district = t.get("district") or ""
            if not name:
                continue
            display = name
            if district and district != name:
                display = f"{name}（{district}）"
            # POI 类提示自带坐标（"lng,lat"），城市/区划类为空——
            # 前端拿到坐标可直接飞行定位，不必再走命中率低的 Geocoder
            loc = t.get("location")
            location = loc if isinstance(loc, str) and "," in loc else ""
            # POI 的 id 用于后续 place/detail 拉取介绍（图片/评分/人均/电话/营业时间）
            poi_id = t.get("id") or ""
            tips.append({"name": name, "display": display,
                         "district": district, "location": location, "id": poi_id})
        # 把名称精确匹配搜索词的主 POI 排到最前：
        # 高德 inputtips 常把"XX(西入口)""XX广场"等子地点排在主景点前面，
        # 而子 POI 在 place/detail 里查不到，导致介绍窗信息缺失。
        tips.sort(key=lambda t: 0 if t["name"] == q else 1)
        result = {"status": "1", "tips": tips}
        await cache.set(cache_key, result, SUGGEST_CACHE_TTL)
        return result
    except Exception as e:
        logger.warning("城市联想接口调用失败: %s", e)
        return {"status": "0", "tips": []}


@app.post("/api/v1/trip/plan")
async def plan_trip(request: TripRequest):
    """流式返回规划进度与结果（SSE 格式）。

    事件格式：每行 `data: <JSON>\\n\\n`
    - step 事件: {"type":"step","step":"<id>","status":"start|done","message":"..."}
    - result 事件: {"type":"result","data":<TripPlan dict>}
    - error 事件: {"type":"error","error_code":"<code>","message":"..."}
    """
    async def event_generator():
        try:
            await planner.initialize()
            async for event in planner.plan_trip_stream(request):
                # 错误事件统一在 API 边界做信息脱敏（DEBUG_ERRORS=false 时不透出内部异常细节）
                if event.get("type") == "error":
                    event = {**event, "message": _public_error_message(str(event.get("message", "")))}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("旅行规划失败: city=%s", getattr(request, "city", "?"))
            err = {"type": "error", "error_code": "unknown",
                   "message": _public_error_message(str(e)[:300])}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ===================== 用户认证 =====================
# register 端点移至下方企业端模块（含企业资料自动创建）

@app.post("/api/v1/auth/login")
async def login(body: dict):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    user = models.get_user_by_username(username)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return JSONResponse({"success": False, "message": "用户名或密码错误"}, status_code=401)
    token = auth.create_token(user["id"], user["username"], user["role"])
    return {"success": True, "token": token,
            "user": {"id": user["id"], "username": user["username"], "role": user["role"]}}


@app.get("/api/v1/auth/me")
async def auth_me(request: Request):
    auth_header = request.headers.get("authorization")
    user = auth.get_current_user(auth_header)
    if not user:
        return JSONResponse({"success": False, "message": "未登录"}, status_code=401)
    return {"success": True, "user": user}


# ===================== 地理位置 =====================
@app.get("/api/v1/location/me")
async def location_me(request: Request):
    """IP 定位返回城市。优先用 X-Forwarded-For，回退到 client.host。"""
    xff = request.headers.get("x-forwarded-for")
    ip = xff.split(",")[0].strip() if xff else request.client.host if request.client else None
    # 本地回环地址高德无法定位，传空让高德用服务端出口 IP（开发期近似）
    if ip in ("127.0.0.1", "localhost", "::1"):
        ip = None
    loc = await location.locate_by_ip(ip)
    return {"success": True, "location": loc}


@app.get("/api/v1/location/regeo")
async def location_regeo(
    lat: float = Query(..., ge=-90, le=90, description="纬度，WGS-84"),
    lng: float = Query(..., ge=-180, le=180, description="经度，WGS-84"),
):
    """逆地理编码：浏览器坐标 → 可读的省/市/区县，用于定位反馈。"""
    # 坐标合法性由 Query 约束兜底；浏览器 WGS-84 需转 GCJ-02 再查高德
    gcj_lat, gcj_lng = recommend.wgs84_to_gcj02(lat, lng)
    info = await location.reverse_geocode(gcj_lat, gcj_lng)
    if info is None:
        return {"success": False, "message": "无法解析所在城市，建议手动选择城市"}
    return {"success": True, "location": info}


# ===================== 推荐引擎 =====================
@app.get("/api/v1/config/map")
async def map_config():
    """前端地图能力开关与高德 JS Key。

    JS Key 本就设计为浏览器端公开凭据（防盗用靠高德后台的域名白名单），
    这里集中下发以便无 Key 时前端整体降级隐藏地图入口。
    """
    from env_utils import AMAP_JS_KEY, AMAP_JS_SECURITY_CODE
    enabled = bool(AMAP_JS_KEY)
    return {"enabled": enabled, "js_key": AMAP_JS_KEY or "", "security_code": AMAP_JS_SECURITY_CODE or ""}


@app.get("/api/v1/hotspots")
async def hotspots_api(limit: int = Query(default=150, ge=1, le=500)):
    """全国热门 POI：地图探索页首屏数据源。按种子城市 × 类别搜索后取评分×热度 top limit，
    1 小时内存缓存，无 Key 时返回空列表。"""
    items = await recommend.fetch_hotspots(limit=limit)
    return {"success": True, "items": items, "count": len(items)}


@app.get("/api/v1/poi/detail")
async def poi_detail_api(id: str = Query(..., min_length=1)):
    """单个 POI 详情（地图搜索景点后弹出的介绍窗口数据源）。
    代理高德 place/detail：返回图片/评分/人均/电话/营业时间/地址等，
    高德无文字简介字段，介绍界面以图片+关键信息呈现。无 Key 或查不到时返回空。"""
    detail = await recommend._fetch_poi_detail(id)
    return {"success": detail is not None, "detail": detail}


@app.get("/api/v1/recommend")
async def recommend_api(
    city: str = Query(..., min_length=1),
    preferences: str = Query(default=""),
    user_location: str | None = Query(default=None, description="lat,lng，纬度在前；坐标系由 coord_type 决定"),
    budget: str | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=30),
    radius: int | None = Query(default=None, ge=100, le=100000,
                               description="附近半径（米），提供精确坐标时生效，自动吸附到档位"),
    nearby_only: bool = Query(default=True,
                              description="仅看附近：true 半径硬过滤+距离加权；false 允许评分≥4.5 的略超范围结果"),
    coord_type: str = Query(default="wgs84", pattern="^(wgs84|gcj02)$",
                            description="user_location 坐标系：wgs84=浏览器 GPS；gcj02=地图视野中心"),
):
    """基于规则的推荐：有坐标走周边搜索（半径硬过滤），无坐标走全市文本搜索。"""
    # 坐标在边界处校验：非法直接拒绝并给出明确错误，绝不静默算出错误距离
    if user_location and recommend.parse_user_location(user_location) is None:
        return JSONResponse(
            {"success": False,
             "message": "user_location 非法：应为 'lat,lng'（纬度在前），纬度 [-90,90]，经度 [-180,180]"},
            status_code=400,
        )
    prefs = [p.strip() for p in preferences.split(",") if p.strip()]
    data = await recommend.recommend(
        city=city,
        preferences=prefs,
        user_location=user_location,
        budget=budget,
        limit=limit,
        radius_m=radius,
        nearby_only=nearby_only,
        coord_type=coord_type,
    )
    return {"success": True, "items": data["items"], "meta": data["meta"]}


# ===================== 企业端 API =====================

# 注册 merchant 时自动创建企业资料
@app.post("/api/v1/auth/register")
async def register(body: dict):
    # 检查是否已有企业资料（幂等创建）
    import trip_planner.models as _m
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role") or "personal"
    if len(username) < 2:
        return JSONResponse({"success": False, "message": "用户名至少 2 个字符"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"success": False, "message": "密码至少 6 位"}, status_code=400)
    if role not in ("personal", "merchant"):
        return JSONResponse({"success": False, "message": "角色无效"}, status_code=400)
    try:
        user = _m.create_user(username, auth.hash_password(password), role)
    except ValueError:
        return JSONResponse({"success": False, "message": "用户名已存在"}, status_code=409)
    # merchant 角色自动创建企业资料
    if role == "merchant":
        ent_name = body.get("enterprise_name") or username
        _m.create_enterprise(user["id"], ent_name,
                             address=body.get("address"),
                             contact_name=body.get("contact_name"),
                             contact_phone=body.get("contact_phone"),
                             license_no=body.get("license_no"))
    token = auth.create_token(user["id"], user["username"], user["role"])
    return {"success": True, "token": token, "user": user}


# —— 企业资料 ——

@app.get("/api/v1/me/enterprise")
async def me_enterprise(request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    ent = models.get_enterprise(user["id"])
    if not ent:
        # 兜底：如果没有企业资料，自动创建一份
        ent = models.create_enterprise(user["id"], user["username"])
    return {"success": True, "enterprise": ent}


@app.put("/api/v1/me/enterprise")
async def update_me_enterprise(body: dict, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    ent = models.update_enterprise(user["id"], **body)
    return {"success": True, "enterprise": ent}


# —— 企业产品 CRUD ——

@app.get("/api/v1/me/products")
async def list_products(request: Request, status: int | None = None,
                         search: str | None = None, category: str | None = None):
    user = auth.require_merchant(request.headers.get("authorization"))
    items = models.list_products(user["id"], status=status, search=search, category=category)
    return {"success": True, "items": items}


@app.post("/api/v1/me/products")
async def create_product(body: dict, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"success": False, "message": "产品名称不能为空"}, status_code=400)
    # 坐标验证
    lng = body.get("lng"); lat = body.get("lat")
    if lng and lat:
        try:
            lng_f, lat_f = float(lng), float(lat)
            if not (-180 <= lng_f <= 180 and -90 <= lat_f <= 90):
                return JSONResponse({"success": False, "message": "坐标超出合法范围"}, status_code=400)
        except ValueError:
            return JSONResponse({"success": False, "message": "坐标格式无效"}, status_code=400)
    # 从 body 中移除 name（已单独提取），避免 create_product 重复参数
    rest = {k: v for k, v in body.items() if k != "name"}
    product = models.create_product(user["id"], name, **rest)
    return {"success": True, "product": product}


@app.put("/api/v1/me/products/{product_id}")
async def update_product(product_id: int, body: dict, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    product = models.update_product(product_id, user["id"], **body)
    if not product:
        return JSONResponse({"success": False, "message": "产品不存在或无权操作"}, status_code=404)
    return {"success": True, "product": product}


@app.delete("/api/v1/me/products/{product_id}")
async def delete_product(product_id: int, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    product = models.toggle_product_status(product_id, user["id"])
    if not product:
        return JSONResponse({"success": False, "message": "产品不存在或无权操作"}, status_code=404)
    return {"success": True, "product": product, "note": "已下架（软删）"}


@app.post("/api/v1/me/products/{product_id}/toggle")
async def toggle_product(product_id: int, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    product = models.toggle_product_status(product_id, user["id"])
    if not product:
        return JSONResponse({"success": False, "message": "产品不存在或无权操作"}, status_code=404)
    return {"success": True, "product": product}


# —— 数据看板 ——

@app.get("/api/v1/me/stats/overview")
async def stats_overview(request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    overview = models.get_enterprise_overview(user["id"])
    return {"success": True, "data": overview}


@app.get("/api/v1/me/stats/trend")
async def stats_trend(request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    trend = models.get_enterprise_week_trend(user["id"])
    return {"success": True, "data": trend}


@app.get("/api/v1/me/stats/products")
async def stats_products(request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    stats = models.get_product_stats(user["id"])
    return {"success": True, "items": stats}


# —— 推广设置 ——

@app.get("/api/v1/me/promotions")
async def list_promotions(request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    items = models.list_promotions(user["id"])
    return {"success": True, "items": items}


@app.post("/api/v1/me/promotions")
async def create_promotion(body: dict, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    promo = models.create_promotion(user["id"], **body)
    return {"success": True, "promotion": promo}


@app.put("/api/v1/me/promotions/{promotion_id}")
async def update_promotion(promotion_id: int, body: dict, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    promo = models.update_promotion(promotion_id, user["id"], **body)
    if not promo:
        return JSONResponse({"success": False, "message": "推广不存在或无权操作"}, status_code=404)
    return {"success": True, "promotion": promo}


@app.post("/api/v1/me/promotions/topup")
async def topup_promotion(body: dict, request: Request):
    user = auth.require_merchant(request.headers.get("authorization"))
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"success": False, "message": "充值金额格式无效"}, status_code=400)
    if amount <= 0:
        return JSONResponse({"success": False, "message": "充值金额必须大于 0"}, status_code=400)
    promo_id = body.get("promotion_id")
    balance = models.recharge_promotion(user["id"], promo_id, amount)
    return {"success": True, "balance": balance}


# —— 企业端静态页面 ——

@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    dashboard_file = Path(__file__).parent / "dashboard.html"
    if dashboard_file.exists():
        return FileResponse(dashboard_file)
    return JSONResponse({"success": False, "message": "企业控制台页面暂未部署"}, status_code=404)


if __name__ == "__main__":
    uvicorn.run("trip_planner.main:app", host="127.0.0.1", port=8000, reload=True)
