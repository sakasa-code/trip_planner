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

    async def plan_trip(self, request: TripRequest) -> TripPlan:
        """
        使用多智能体进行旅行规划

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        try:
            print(f"\n{'=' * 60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"目的地: {request.city}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'=' * 60}\n")

            # 每次请求建立独立 MCP 会话：请求内所有工具调用复用同一条连接，
            # 请求结束自动关闭；下次请求重新建立，天然自愈，不依赖远端连接存活。
            # 即使会话被远端断开，也只影响当前这一次请求（报错后下次请求自动恢复）。
            print("🔌 建立本次请求的 MCP 会话...")
            async with self.amap_tool.session("amap-amap-sse") as session:
                weather_agent, attraction_agent, hotel_agent = await self._build_agents(session)

                print("📍 步骤1: 搜索景点...")
                attraction_query = self._build_attraction_query(request)
                attraction_text = await self._run_agent_with_retry(
                    attraction_agent, attraction_query, "景点搜索")
                print(f"景点搜索结果: {attraction_text[:200]}...\n")

                print("🌤️  步骤2: 查询天气...")
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
                print(f"天气查询结果: {weather_text[:200]}...\n")

                print("🏨 步骤3: 搜索酒店...")
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
                print(f"酒店搜索结果: {hotel_text[:200]}...\n")

            print("📋 步骤4: 生成行程计划...")
            planner_query = self._build_planner_query(request, attraction_text, weather_text, hotel_text)
            planner_messages = [
                ("system", PLANNER_AGENT_PROMPT),
                ("user", planner_query),
            ]

            trip_plan = None
            for attempt in range(3):
                planner_response = await self.planner_llm.ainvoke(planner_messages)
                planner_text = self._extract_text(planner_response)
                print(f"行程规划结果: {planner_text[:800]}...\n")

                try:
                    trip_plan = self._parse_response(planner_text, request)
                    break
                except Exception as e:
                    print(f"⚠️ 规划结果解析失败（第{attempt + 1}次）: {e}")
                    if attempt == 2:
                        raise
                    # 追加纠正指令后重试：要求只输出一个完整的 JSON 对象
                    planner_messages = planner_messages + [
                        ("user",
                         "\n\n**重要：** 你上一次的输出为空或不是合法 JSON，无法解析。"
                         "请只输出一个完整的、可直接 json.loads 的 JSON 对象，"
                         "不要任何解释，不要代码块标记（不要```json），不要额外文字。")
                    ]

            assert trip_plan is not None

            print(f"{'=' * 60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'=' * 60}\n")

            return trip_plan

        except Exception as e:
            print(f"❌ 旅行规划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

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
