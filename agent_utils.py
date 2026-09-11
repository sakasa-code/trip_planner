"""Trip Planner Agent 工具函数：文本提取、JSON 解析、城市校验等。"""

import json


CLARIFICATION_MARKERS = (
    "请您", "请告诉我", "请提供", "请补充", "无法确定", "无法查询",
    "请问", "缺少关键信息", "需要补充",
)

RETRY_INSTRUCTION = (
    "注意：所有必要信息（城市、日期、偏好）已经在对话中完整提供，"
    "绝对不要再询问用户或要求补充信息！请直接调用可用工具获取真实数据，然后输出最终结果。"
)


def _user_message(content: str) -> dict:
    """构造 create_agent 接受的输入消息（文档示例为 dict 格式，元组可能校验失败）。"""
    return {"role": "user", "content": content}


def _normalize_city(city: str) -> str:
    """城市名归一：去首尾空白、去「市」后缀，用于比较模型输出与请求是否一致。"""
    return (city or "").strip().rstrip("市")


class CityMismatch(Exception):
    """规划模型输出的城市与用户请求不一致（幻觉城市）。"""

    def __init__(self, output: str, requested: str):
        self.output = output
        self.requested = requested
        super().__init__(f"模型输出城市 {output!r} 与请求城市 {requested!r} 不一致")


def looks_like_clarification(text: str) -> bool:
    """判断 Agent 输出是否为反问/空结果（没有实际数据）。
    只检查开头 120 字，避免误伤正常输出中顺带出现的提示语。"""
    if not text or not text.strip():
        return True
    head = text[:120]
    return any(marker in head for marker in CLARIFICATION_MARKERS)


def extract_text(response) -> str:
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
        if "messages" in response:
            messages = response["messages"]
            if messages:
                last_msg = messages[-1]
                if hasattr(last_msg, "content"):
                    return _content_to_text(last_msg.content)
        if "output" in response:
            return _content_to_text(response["output"])
        return str(response)[:500]
    if hasattr(response, "content"):
        return _content_to_text(response.content)
    else:
        return str(response)[:500]


def extract_outermost_json(response: str) -> str:
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
                            break
        start = response.find("{", start + 1)
    raise ValueError("未找到完整 JSON 对象")


def truncate(text: str, limit: int = 1500) -> str:
    """截断传给规划 Agent 的中间结果，控制提示词体积以加快生成速度。"""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（内容较长，已截断，共 {len(text)} 字）"