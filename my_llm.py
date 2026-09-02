from env_utils import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_API_URL,
    DASHSCOPE_API_KEY,
    DASHSCOPE_API_URL,
    ZHIPUAI_API_KEY,
    ZHIPUAI_API_URL,
)
from langchain_deepseek import ChatDeepSeek

# 明确校验凭据，缺失时给出可读的错误，避免运行时才报出难懂的异常
if not DEEPSEEK_API_KEY:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在项目根目录的 .env 文件中配置后再运行。")

# 当前使用的模型：DeepSeek 对话模型（工具调用场景默认用 deepseek-chat，更稳定、更快、更便宜）
# 说明：deepseek-reasoner 在多次实测中偶发"不调用工具、把城市名渲染成 ??/幻觉"，
#       且推理阶段导致单次规划耗时 2-4 倍，故默认改用 deepseek-chat。
#       如需切回推理模型，设置环境变量 AGENT_MODEL=deepseek-reasoner 即可。
# request_timeout=180：单次 LLM 请求最长等 180 秒，超时立即抛错（默认 None 会傻等 10 分钟）
# max_tokens=8192：限制生成长度，避免超长输出拖慢整体响应
import os as _os

AGENT_MODEL = _os.getenv("AGENT_MODEL", "deepseek-chat")

llm1 = ChatDeepSeek(
    model=AGENT_MODEL,
    temperature=0.5,
    api_key=DEEPSEEK_API_KEY,
    api_base=DEEPSEEK_API_URL,  # langchain-deepseek 中 api_base 是 base_url 的别名
    request_timeout=180,
    max_tokens=8192,
)

# 行程规划 Agent 专用模型：deepseek-chat + 强制 JSON 输出
# - response_format={"type": "json_object"}：强制返回合法 JSON，从根源消除"解析不到 JSON"的失败
#   （注意：json_object 模式要求消息中包含 "json" 字样，PLANNER_AGENT_PROMPT 已包含）
# - 不用 deepseek-reasoner：推理模型会在推理阶段消耗大量 token，复杂任务（如 7 天行程）
#   可能在输出答案前就耗尽 max_tokens 预算，导致最终内容为空
# - max_tokens=8192：deepseek-chat 单次输出上限，长行程 JSON 足够
llm_planner = ChatDeepSeek(
    model="deepseek-chat",
    temperature=0.5,
    api_key=DEEPSEEK_API_KEY,
    api_base=DEEPSEEK_API_URL,
    request_timeout=180,
    max_tokens=8192,
).bind(response_format={"type": "json_object"})

# 以下模型当前未被使用；缺少有效凭据会在导入时报错，暂时禁用
# llm2 = init_chat_model(model="deepseek-chat",
#                        temperature=0.5,
#                        model_provider="deepseek",  # 或者使用 openai：等于方法1；使用 deepseek 等于方法2
#                        api_key=DEEPSEEK_API_KEY,
#                        base_url=DEEPSEEK_API_URL
#                        )

# llm3 = ChatOpenAI(model="qwen-max",
#                   temperature=0.5,
#                   extra_body={"enable_search": True},
#                   api_key=DASHSCOPE_API_KEY,
#                   base_url=DASHSCOPE_API_URL
# )

# llm4 = init_chat_model(model="glm-4.5",  # 原注释拼写为 gml-4.5，已修正为 glm-4.5
#                        temperature=0.5,
#                        model_provider="openai",
#                        api_key=ZHIPUAI_API_KEY,
#                        base_url=ZHIPUAI_API_URL
#                        )
