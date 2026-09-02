import sys

# Windows 中文环境默认 GBK 编码，打印 emoji 会抛 UnicodeEncodeError 并掩盖真实错误；
# 统一改用 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from trip_planner.schemas import TripRequest, TripPlanResponse
from trip_planner.trip_planner_agent import get_trip_planner_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("trip_planner")

# 开发期可设置 DEBUG_ERRORS=true，让前端看到真实错误信息便于排查；
# 默认关闭，避免把内部异常（如带 API Key 的 MCP 地址）泄露给前端。
DEBUG_ERRORS = os.getenv("DEBUG_ERRORS", "").lower() in ("1", "true", "yes")

planner = get_trip_planner_agent()


@asynccontextmanager
async def lifespan(app: FastAPI):
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


@app.post("/api/v1/trip/plan", response_model=TripPlanResponse)
async def plan_trip(request: TripRequest):
    try:
        await planner.initialize()
        plan = await planner.plan_trip(request)
        return TripPlanResponse(success=True, message="规划成功", data=plan)
    except Exception as e:
        # 完整错误写入服务端日志；默认对客户端返回脱敏信息，
        # 设置 DEBUG_ERRORS=true 时返回真实错误（最多 500 字）便于开发排查。
        logger.exception("旅行规划失败: city=%s, dates=%s~%s", request.city, request.start_date, request.end_date)
        message = f"旅行规划失败: {str(e)[:500]}" if DEBUG_ERRORS else "旅行规划失败，请查看后端日志获取详细错误"
        return TripPlanResponse(success=False, message=message)


if __name__ == "__main__":
    uvicorn.run("trip_planner.main:app", host="127.0.0.1", port=8000, reload=True)
