import sys
from pathlib import Path

# Windows 中文环境默认 GBK 编码，打印 emoji 会抛 UnicodeEncodeError 并掩盖真实错误；
# 统一改用 UTF-8 输出。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 允许直接以 `python trip_planner/trip_planner_agent.py` 运行（而非仅 -m 方式）
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import json

from langchain.agents import create_agent
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from env_utils import AMAP_API_KEY
from my_llm import llm1, llm_planner
from trip_planner.prompts import (
    WEATHER_AGENT_PROMPT,
    ATTRACTION_AGENT_PROMPT,
    HOTEL_AGENT_PROMPT,
    PLANNER_AGENT_PROMPT,
)
from trip_planner.schemas import TripRequest, TripPlan


def _user_message(content: str) -> dict:
    """构造 create_agent 接受的输入消息（文档示例为 dict 格式，元组可能校验失败）。"""
    return {"role": "user", "content": content}


# 每个 Agent 单次任务允许的最大工具调用次数（防止 LLM 循环重复调用同一高德 API）。
# exit_behavior="continue"：超限的工具调用会被拦截并提示模型"不要再调用"，其余照常执行，
# 模型基于已拿到的数据完成回答，功能不受影响。
# - 天气：1 次即可拿到多日预报；放宽到 2 以防个别边界情况
# - 景点/酒店：合并关键词后通常 1 次，最多 2 次
TOOL_CALL_LIMITS = {
    "weather": ToolCallLimitMiddleware(run_limit=2, exit_behavior="continue"),
    "poi": ToolCallLimitMiddleware(run_limit=2, exit_behavior="continue"),
}

# 检测 Agent 反问/空输出（deepseek-reasoner 偶发不调用工具而是反问用户）
CLARIFICATION_MARKERS = (
    "请您", "请告诉我", "请提供", "请补充", "无法确定", "无法查询",
    "请问", "缺少关键信息", "需要补充",
)

RETRY_INSTRUCTION = (
    "注意：所有必要信息（城市、日期、偏好）已经在对话中完整提供，"
    "绝对不要再询问用户或要求补充信息！请直接调用可用工具获取真实数据，然后输出最终结果。"
)


def _normalize_city(city: str) -> str:
    """城市名归一：去首尾空白、去「市」后缀，用于比较模型输出与请求是否一致。"""
    return (city or "").strip().rstrip("市")


class _CityMismatch(Exception):
    """规划模型输出的城市与用户请求不一致（幻觉城市）。"""

    def __init__(self, output: str, requested: str):
        self.output = output
        self.requested = requested
        super().__init__(f"模型输出城市 {output!r} 与请求城市 {requested!r} 不一致")


class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    def __init__(self):
        self.llm = llm1
        self.amap_tool = None
        # 规划步骤无工具，直接使用 LLM（deepseek-chat + JSON 模式），不走 agent 框架
        self.planner_llm = llm_planner
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self):
        """初始化（幂等）：只创建 MCP 客户端配置对象，不做网络连接。
        MCP 会话改为每次请求独立创建（见 plan_trip），避免共享会话被远端
        断开后无法恢复、后续工具调用挂起的问题。"""
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            print("初始化多智能体旅行规划系统...")
            try:
                print("  - 创建 MCP 客户端（会话在每次请求时建立）...")
                self.amap_tool = MultiServerMCPClient(
                    {
                        "amap-amap-sse": {
                            "url": "https://mcp.amap.com/sse?key={}".format(AMAP_API_KEY),
                            "transport": "sse",
                        }
                    }
                )
                self._initialized = True
                print("✅ 多智能体系统初始化成功（每次请求独立建立 MCP 会话）")

            except Exception as e:
                print(f"❌ 多智能体系统初始化失败: {str(e)}")
                import traceback
                traceback.print_exc()
                raise

    async def aclose(self):
        """释放资源（幂等）：无长期持有的会话，仅重置状态。"""
        self.amap_tool = None
        self._initialized = False

    async def _build_agents(self, session):
        """基于给定 MCP 会话加载工具并创建三个工具 Agent。
        请求级会话模式下，每次请求都会调用本方法（Agent 是内存中的无状态图，创建开销极小）。
        """
        tools = await load_mcp_tools(session)

        # 按用途拆分工具：天气类工具只给天气 Agent，其余 POI 搜索工具给景点/酒店 Agent，
        # 避免天气 Agent 误调用景点搜索工具（或反之）。
        weather_tools = [t for t in tools if "weather" in t.name.lower()]
        poi_tools = [t for t in tools if "weather" not in t.name.lower()]
        if not weather_tools:
            print("  ⚠ 未识别到天气类工具，天气 Agent 将使用全部工具")
            weather_tools = tools
        if not poi_tools:
            poi_tools = tools

        weather_agent = create_agent(
            self.llm,
            weather_tools,
            system_prompt=WEATHER_AGENT_PROMPT,
            middleware=[TOOL_CALL_LIMITS["weather"]]
        )
        attraction_agent = create_agent(
            self.llm,
            poi_tools,
            system_prompt=ATTRACTION_AGENT_PROMPT,
            middleware=[TOOL_CALL_LIMITS["poi"]]
        )
        hotel_agent = create_agent(
            self.llm,
            poi_tools,
            system_prompt=HOTEL_AGENT_PROMPT,
            middleware=[TOOL_CALL_LIMITS["poi"]]
        )
        print(f"   本次请求工具数量: {len(tools)} 个 | "
              f"天气: {[t.name for t in weather_tools]} | "
              f"POI: {[t.name for t in poi_tools]}")
        return weather_agent, attraction_agent, hotel_agent

    async def plan_trip_stream(self, request: TripRequest):
        """流式旅行规划：异步生成器，逐步 yield 进度事件，最终 yield 结果或错误。

        事件格式（dict）：
          {"type": "step", "step": "<step_id>", "status": "start"|"done", "message": "..."}
          {"type": "result", "data": <TripPlan 的 dict>}
          {"type": "error", "error_code": "<code>", "message": "..."}

        step_id: attractions | weather | hotels | planning
        error_code: amap_error | llm_parse_error | network_error | unknown
        """
        attraction_text = weather_text = hotel_text = ""
        current_step = "init"
        try:
            yield {"type": "step", "step": "init", "status": "done",
                   "message": f"正在为「{request.city}」规划 {request.travel_days} 天行程"}

            # 每次请求建立独立 MCP 会话
            current_step = "attractions"
            yield {"type": "step", "step": "attractions", "status": "start", "message": "正在检索景点…"}
            async with self.amap_tool.session("amap-amap-sse") as session:
                weather_agent, attraction_agent, hotel_agent = await self._build_agents(session)

                attraction_query = self._build_attraction_query(request)
                attraction_text = await self._run_agent_with_retry(
                    attraction_agent, attraction_query, "景点搜索")
                yield {"type": "step", "step": "attractions", "status": "done", "message": "景点检索完成"}

                current_step = "weather"
                yield {"type": "step", "step": "weather", "status": "start", "message": "正在查询天气…"}
                weather_query = {
                    "messages": [
                        _user_message(
                            f"请查询{request.city}从{request.start_date}到{request.end_date}的天气预报，"
                            f"包括每天白天/夜间温度和天气状况。"
                        )
                    ]
                }
                weather_text = await self._run_agent_with_retry(
                    weather_agent, weather_query, "天气查询")
                yield {"type": "step", "step": "weather", "status": "done", "message": "天气查询完成"}

                current_step = "hotels"
                yield {"type": "step", "step": "hotels", "status": "start", "message": "正在搜索酒店…"}
                hotel_query = {
                    "messages": [
                        _user_message(
                            f"请在{request.city}搜索{request.accommodation}，推荐6-10家位置方便、"
                            f"价格适中的酒店，包括名称、地址、大致价格、评分等信息。"
                        )
                    ]
                }
                hotel_text = await self._run_agent_with_retry(
                    hotel_agent, hotel_query, "酒店搜索")
                yield {"type": "step", "step": "hotels", "status": "done", "message": "酒店搜索完成"}

            current_step = "planning"
            yield {"type": "step", "step": "planning", "status": "start", "message": "正在生成行程计划…"}
            planner_query = self._build_planner_query(request, attraction_text, weather_text, hotel_text)
            planner_messages = [
                ("system", PLANNER_AGENT_PROMPT),
                ("user", planner_query),
            ]

            trip_plan = None
            for attempt in range(3):
                planner_response = await self.planner_llm.ainvoke(planner_messages)
                planner_text = self._extract_text(planner_response)
                try:
                    trip_plan = self._parse_response(planner_text, request)
                    # 关键防线：模型偶尔会「幻觉」出别的城市（日志实证：广州被输出成北京），
                    # 此处校验输出城市与请求一致，不一致则追加强硬修正指令重试。
                    if _normalize_city(trip_plan.city) != _normalize_city(request.city):
                        raise _CityMismatch(trip_plan.city, request.city)
                    break
                except _CityMismatch as e:
                    if attempt == 2:
                        raise
                    planner_messages = planner_messages + [
                        ("user",
                         f"\n\n**重要修正：** 你输出的城市是「{e.output}」，但用户要求的是「{e.requested}」。"
                         "请把 JSON 中的 city 字段改为用户要求的城市，其余内容可保留，"
                         "重新输出完整 JSON，不要任何解释或代码块标记。")
                    ]
                except Exception as e:
                    if attempt == 2:
                        raise
                    planner_messages = planner_messages + [
                        ("user",
                         "\n\n**重要：** 你上一次的输出为空或不是合法 JSON，无法解析。"
                         "请只输出一个完整的、可直接 json.loads 的 JSON 对象，"
                         "不要任何解释，不要代码块标记（不要```json），不要额外文字。")
                    ]

            assert trip_plan is not None
            yield {"type": "step", "step": "planning", "status": "done", "message": "行程生成完成"}
            yield {"type": "result", "data": trip_plan.model_dump()}

        except Exception as e:
            error_code = self._classify_error(e, current_step)
            yield {"type": "error", "error_code": error_code, "message": str(e)[:300]}

    async def plan_trip(self, request: TripRequest) -> TripPlan:
        """兼容旧调用：消费 plan_trip_stream 并返回 TripPlan，出错时抛出。"""
        result = None
        async for event in self.plan_trip_stream(request):
            if event["type"] == "result":
                result = event["data"]
            elif event["type"] == "error":
                raise RuntimeError(f"[{event['error_code']}] {event['message']}")
        if result is None:
            raise RuntimeError("[unknown] 规划未返回结果")
        return TripPlan(**result)

    @staticmethod
    def _classify_error(exc: Exception, step: str) -> str:
        """根据异常类型与发生步骤归类错误码，供前端做定向重试。"""
        msg = str(exc).lower()
        if step in ("attractions", "weather", "hotels"):
            return "amap_error"
        if step == "planning":
            return "llm_parse_error"
        if any(k in msg for k in ("timeout", "timed out", "connection", "network", "refused")):
            return "network_error"
        return "unknown"

    async def _run_agent_with_retry(self, agent, query: dict, step_name: str) -> str:
        """调用 Agent 并校验输出：若模型反问用户或输出为空，追加强硬指令重试一次。

        防止 deepseek-reasoner 偶发"不调用工具、反问用户"导致该步骤无数据。
        """
        response = await agent.ainvoke(query)
        text = self._extract_text(response)
        if self._looks_like_clarification(text):
            print(f"⚠ {step_name} Agent 输出疑似反问/空结果，追加指令重试一次...")
            retry_query = {
                "messages": query["messages"] + [_user_message(RETRY_INSTRUCTION)]
            }
            response = await agent.ainvoke(retry_query)
            text = self._extract_text(response)
        return text

    @staticmethod
    def _looks_like_clarification(text: str) -> bool:
        """判断 Agent 输出是否为反问/空结果（没有实际数据）。
        只检查开头 120 字，避免误伤正常输出中顺带出现的提示语。"""
        if not text or not text.strip():
            return True
        head = text[:120]
        return any(marker in head for marker in CLARIFICATION_MARKERS)

    def _build_attraction_query(self, request: TripRequest) -> dict:
        """构建景点搜索查询"""
        preferences = ', '.join(request.preferences) if request.preferences else "经典景点"
        return {
            "messages": [
                _user_message(
                    f"请搜索{request.city}适合{request.travel_days}天游玩的{preferences}，"
                    f"推荐8-12个热门景点，包括名称、地址、简介、门票价格等信息。"
                )
            ]
        }

    @staticmethod
    def _truncate(text: str, limit: int = 1500) -> str:
        """截断传给规划 Agent 的中间结果，控制提示词体积以加快生成速度。"""
        text = text or ""
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n…（内容较长，已截断，共 {len(text)} 字）"

    def _build_planner_query(self, request: TripRequest, attractions: str, weather: str, hotels: str = "") -> str:
        """构建行程规划查询"""
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{self._truncate(attractions)}

**天气信息:**
{self._truncate(weather)}

**酒店信息:**
{self._truncate(hotels)}

**要求:**
1. 每天安排2-3个景点
2. 每天必须包含早中晚三餐
3. 每天推荐一个具体的酒店(从酒店信息中选择)
4. 考虑景点之间的距离和交通方式
5. 返回完整的JSON格式数据
6. 景点的经纬度坐标要真实准确
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        return query

    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """
        解析Agent响应

        Args:
            response: Agent响应文本
            request: 原始请求

        Returns:
            旅行计划
        """
        json_str = ""
        try:
            # 尝试从响应中提取JSON
            # 查找JSON代码块
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                parts = response.split("```")
                if len(parts) >= 3:
                    json_str = parts[1].strip()
                else:
                    json_str = parts[-1].strip()
            else:
                json_str = self._extract_outermost_json(response)

            # 解析JSON
            json_str = json_str.strip()
            print(f"提取到的JSON:\n{json_str[:500]}...")

            data = json.loads(json_str)

            # 转换为TripPlan对象
            trip_plan = TripPlan(**data)

            return trip_plan

        except json.JSONDecodeError as e:
            print(f"JSON 解析错误: {e}")
            print(f"问题JSON内容:\n{json_str[:1000]}")
            raise
        except Exception as e:
            print(f"提取JSON失败: {e}")
            print(f"原始响应:\n{response[:1000]}")
            raise

    @staticmethod
    def _extract_outermost_json(response: str) -> str:
        """
        从响应中提取最外层完整 JSON 对象。
        从第一个 { 开始按括号配对扫描，自动跳过字符串内的花括号；
        若候选无法解析为合法 JSON，则继续尝试后续的 { 位置。
        """
        start = response.find("{")
        while start != -1:
            bracket_count = 0
            in_string = False
            escape = False
            for i in range(start, len(response)):
                ch = response[i]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == '{':
                        bracket_count += 1
                    elif ch == '}':
                        bracket_count -= 1
                        if bracket_count == 0:
                            candidate = response[start:i + 1]
                            try:
                                json.loads(candidate)
                                return candidate
                            except json.JSONDecodeError:
                                break  # 候选无效，尝试下一个 {
            start = response.find("{", start + 1)
        raise ValueError("未找到完整 JSON 对象")

    def _extract_text(self, response) -> str:
        """从 Agent 响应中提取可读的文本内容"""
        if isinstance(response, str):
            return response

        def _content_to_text(content) -> str:
            """把消息 content（字符串或 content block 列表）转为纯文本"""
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict):
                        if c.get("type") == "text" and c.get("text"):
                            parts.append(c["text"])
                    elif hasattr(c, "type") and getattr(c, "type") == "text":
                        text = getattr(c, "text", None)
                        if text:
                            parts.append(text)
                return "".join(parts)
            return str(content)

        if isinstance(response, dict):
            # create_agent 输出：{"messages": [...], ...}；部分版本可能有 "output" 键
            if "messages" in response:
                messages = response["messages"]
                if messages:
                    last_msg = messages[-1]
                    if hasattr(last_msg, "content"):
                        return _content_to_text(last_msg.content)
            if "output" in response:
                return _content_to_text(response["output"])
            # 备用：直接 str 整个 dict（调试用）
            return str(response)[:500]
        if hasattr(response, "content"):
            # 直接 LLM 调用返回的 AIMessage 等消息对象
            return _content_to_text(response.content)
        else:
            return str(response)[:500]


_multi_agent_planner = None


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """获取多智能体系统实例（进程级单例）"""
    global _multi_agent_planner
    if _multi_agent_planner is None:
        _multi_agent_planner = MultiAgentTripPlanner()
    return _multi_agent_planner


async def main():
    """主入口函数（测试用）"""
    planner = MultiAgentTripPlanner()
    await planner.initialize()
    print("✅ 系统初始化成功")
    request = TripRequest(
        city="北京",
        start_date="2025-12-16",
        end_date="2025-12-18",
        travel_days=3,
        transportation="公共公交",
        accommodation="经济型酒店",
        preferences=["历史文化", "美食"],
        free_text_input="多安排博物馆，避免拥挤景点"
    )
    try:
        trip_plan = await planner.plan_trip(request)
        print("\n✅ 生成的旅行计划：")
        print(trip_plan.model_dump_json(indent=2))  # 漂亮打印JSON
    except Exception as e:
        print(f"规划失败: {e}")


if __name__ == "__main__":
    """测试多智能体系统：可运行 `python -m trip_planner.trip_planner_agent` 或直接运行本文件"""
    asyncio.run(main())
