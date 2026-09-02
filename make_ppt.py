# -*- coding: utf-8 -*-
"""生成 trip_planner 项目总结汇报 PPT"""
import sys

sys.path.insert(0, r"D:\Projects\测试\.venv\Lib\site-packages")

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

# ---------- 主题色（与项目前端一致） ----------
PRIMARY = RGBColor(0x25, 0x63, 0xEB)      # 蓝
SECONDARY = RGBColor(0x10, 0xB9, 0x81)    # 绿
DARK = RGBColor(0x1F, 0x29, 0x37)         # 深灰文字
GRAY = RGBColor(0x6B, 0x72, 0x80)
LIGHT_BG = RGBColor(0xEF, 0xF4, 0xFF)     # 浅蓝底
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xDC, 0x26, 0x26)
AMBER = RGBColor(0xD9, 0x77, 0x06)

FONT = "Microsoft YaHei"

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def set_font(run, size=18, bold=False, color=DARK, name=FONT):
    f = run.font
    f.name = name
    f.size = Pt(size)
    f.bold = bold
    f.color.rgb = color
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = rPr.makeelement(qn("a:ea"), {})
        rPr.append(ea)
    ea.set("typeface", name)


def add_text(slide, x, y, w, h, items, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
             line_spacing=1.0, space_after=6):
    """items: list of (text, size, bold, color) or list of list for paragraphs"""
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        p.space_after = Pt(space_after)
        text, size, bold, color = item
        run = p.add_run()
        run.text = text
        set_font(run, size=size, bold=bold, color=color)
    return box


def add_rect(slide, x, y, w, h, fill, line=None, shape=MSO_SHAPE.RECTANGLE):
    sp = slide.shapes.add_shape(shape, x, y, w, h)
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(1)
    sp.shadow.inherit = False
    return sp


def add_round_rect(slide, x, y, w, h, fill, text="", text_color=WHITE, size=16, bold=True, line=None):
    sp = add_rect(slide, x, y, w, h, fill, line=line, shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = Inches(0.08)
    tf.margin_right = Inches(0.08)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    set_font(run, size=size, bold=bold, color=text_color)
    return sp


def add_slide_title(slide, section, title, subtitle=None):
    add_rect(slide, 0, 0, prs.slide_width, Inches(1.0), PRIMARY)
    add_rect(slide, 0, Inches(1.0), prs.slide_width, Inches(0.06), SECONDARY)
    add_text(slide, Inches(0.6), Inches(0.16), Inches(11.5), Inches(0.7),
             [(title, 30, True, WHITE)])
    if subtitle:
        add_text(slide, Inches(0.6), Inches(1.16), Inches(12), Inches(0.4),
                 [(subtitle, 14, False, GRAY)])


def add_footer(slide, page):
    add_text(slide, Inches(12.3), Inches(7.08), Inches(0.8), Inches(0.35),
             [(str(page), 12, False, GRAY)], align=PP_ALIGN.RIGHT)
    add_text(slide, Inches(0.6), Inches(7.08), Inches(6), Inches(0.35),
             [("AI 旅行规划系统（trip_planner）项目总结", 11, False, GRAY)])


def bullet_items(lines, size=16, color=DARK, marker="▪ "):
    return [(marker + t, size, False, color) for t in lines]


# ============ 1. 封面 ============
s = prs.slides.add_slide(BLANK)
add_rect(s, 0, 0, prs.slide_width, prs.slide_height, RGBColor(0x0F, 0x2A, 0x5C))
add_rect(s, 0, Inches(4.9), prs.slide_width, Inches(0.08), SECONDARY)
add_text(s, Inches(1.0), Inches(1.5), Inches(11.3), Inches(1.2),
         [("AI 旅行规划系统", 54, True, WHITE)])
add_text(s, Inches(1.0), Inches(2.7), Inches(11.3), Inches(0.8),
         [("trip_planner —— 多智能体协作的智能行程规划平台", 24, False, RGBColor(0xBF, 0xD3, 0xFF))])
add_text(s, Inches(1.0), Inches(5.3), Inches(11.3), Inches(1.4),
         [("项目总结汇报", 22, True, WHITE),
          ("汇报人：____________    日期：____________", 16, False, RGBColor(0x9F, 0xB8, 0xE8))],
         space_after=14)

# ============ 2. 目录 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "", "目录 CONTENTS")
toc = [
    ("01", "项目需求", "背景 · 目标 · 核心需求"),
    ("02", "项目环境", "开发环境 · 部署环境 · 工具链"),
    ("03", "核心技术", "技术栈 · 多智能体架构 · 关键方案"),
    ("04", "遇到的问题与解决方案", "四大难题 · 应对措施 · 最终成效"),
    ("05", "项目成果与总结", "成果数据 · 经验反思 · 优化建议"),
]
y = Inches(1.6)
for num, t, d in toc:
    add_round_rect(s, Inches(1.2), y, Inches(1.0), Inches(0.8), PRIMARY, num, size=24)
    add_text(s, Inches(2.5), y + Inches(0.02), Inches(9), Inches(0.8),
             [(t, 22, True, DARK), (d, 13, False, GRAY)], space_after=4)
    y += Inches(1.05)

# ============ 3. 01 项目需求 —— 背景 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "01", "项目需求 · 项目背景")
add_round_rect(s, Inches(0.7), Inches(1.7), Inches(5.9), Inches(4.9), LIGHT_BG, line=PRIMARY)
add_text(s, Inches(1.0), Inches(2.0), Inches(5.3), Inches(4.4),
         [("◆ 用户痛点", 20, True, PRIMARY),
          ("· 出行前做攻略费时费力：查景点、查天气、找酒店、排行程、算预算，信息分散在多平台", 16, False, DARK),
          ("· 人工规划难以兼顾时间、距离、预算与个人偏好", 16, False, DARK),
          ("· 通用 AI 助手缺乏实时地理数据，容易编造景点/酒店信息", 16, False, DARK)],
         space_after=10)
add_round_rect(s, Inches(6.9), Inches(1.7), Inches(5.7), Inches(4.9), RGBColor(0xE8, 0xF9, 0xF1), line=SECONDARY)
add_text(s, Inches(7.2), Inches(2.0), Inches(5.1), Inches(4.4),
         [("◆ 技术契机", 20, True, SECONDARY),
          ("· 大模型（DeepSeek）具备理解与生成能力，可承担规划与文案", 16, False, DARK),
          ("· MCP（模型上下文协议）让 AI 直接调用高德地图真实数据", 16, False, DARK),
          ("· 多智能体（Multi-Agent）协作可拆解复杂任务、各司其职", 16, False, DARK)],
         space_after=10)
add_footer(s, 3)

# ============ 4. 01 项目需求 —— 目标与核心需求 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "01", "项目需求 · 目标与核心需求")
add_round_rect(s, Inches(0.7), Inches(1.7), Inches(5.9), Inches(5.0), PRIMARY)
add_text(s, Inches(1.0), Inches(2.0), Inches(5.3), Inches(4.4),
         [("项目目标", 22, True, WHITE),
          ("构建一个基于多智能体协作的 AI 旅行规划系统：", 16, False, WHITE),
          ("输入目的地与偏好，一键生成包含景点、天气、酒店、", 16, False, WHITE),
          ("每日行程与预算的真实可执行旅行计划。", 16, False, WHITE),
          ("", 10, False, WHITE),
          ("· 数据真实：接入高德地图，不编造信息", 16, False, WHITE),
          ("· 输出结构化：JSON 行程可直接展示与复用", 16, False, WHITE),
          ("· 使用友好：Web 表单一键操作", 16, False, WHITE)],
         space_after=8)
add_text(s, Inches(7.0), Inches(1.85), Inches(5.7), Inches(0.5),
         [("核心功能需求", 22, True, PRIMARY)])
reqs = [
    ("行程规划", "按城市/日期/天数生成逐日行程，含交通与住宿安排"),
    ("景点推荐", "依据偏好搜索真实景点及地址、门票、游览时长"),
    ("天气查询", "获取旅行时段内逐日天气预报并纳入行程考量"),
    ("酒店推荐", "按住宿类型推荐真实酒店及价格、评分、位置"),
    ("预算估算", "汇总门票、酒店、餐饮、交通的预估总费用"),
    ("前端展示", "表单输入 + 行程卡片/天气/预算可视化展示"),
]
y = Inches(2.45)
for name, desc in reqs:
    add_round_rect(s, Inches(6.9), y, Inches(1.7), Inches(0.62), SECONDARY, name, size=15)
    add_text(s, Inches(8.75), y + Inches(0.02), Inches(4.0), Inches(0.7),
             [(desc, 13, False, DARK)])
    y += Inches(0.72)
add_footer(s, 4)

# ============ 5. 02 项目环境 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "02", "项目环境")
rows, cols = 3, 2
tbl = s.shapes.add_table(rows, cols, Inches(0.7), Inches(1.75), Inches(11.9), Inches(4.9)).table
tbl.columns[0].width = Inches(2.3)
tbl.columns[1].width = Inches(9.6)
env_data = [
    ("开发环境", "Windows · Python 3.14.3（稳定版）· 虚拟环境 .venv · PyCharm / PowerShell"),
    ("部署环境", "本机部署：FastAPI + uvicorn @ http://127.0.0.1:8000；前端由后端直接托管或 Live Server(5500)"),
    ("相关工具", "Git（待初始化仓库）· 高德开放平台(MCP 服务) · DeepSeek 开放平台 · pip 依赖管理"),
]
for r, (k, v) in enumerate(env_data):
    c0, c1 = tbl.cell(r, 0), tbl.cell(r, 1)
    c0.fill.solid(); c0.fill.fore_color.rgb = PRIMARY
    c1.fill.solid(); c1.fill.fore_color.rgb = LIGHT_BG if r % 2 == 0 else WHITE
    c0.vertical_anchor = MSO_ANCHOR.MIDDLE
    c1.vertical_anchor = MSO_ANCHOR.MIDDLE
    p0 = c0.text_frame.paragraphs[0]; p0.alignment = PP_ALIGN.CENTER
    r0 = p0.add_run(); r0.text = k; set_font(r0, 18, True, WHITE)
    p1 = c1.text_frame.paragraphs[0]
    r1 = p1.add_run(); r1.text = v; set_font(r1, 15, False, DARK)
add_text(s, Inches(0.7), Inches(6.85), Inches(12), Inches(0.5),
         [("主要依赖：fastapi · uvicorn · pydantic · langchain 1.x · langchain-deepseek · langchain-mcp-adapters · openai · python-dotenv", 13, False, GRAY)])
add_footer(s, 5)

# ============ 6. 03 核心技术 —— 技术栈与架构 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "03", "核心技术 · 技术栈与系统架构")
add_text(s, Inches(0.7), Inches(1.6), Inches(12), Inches(0.5),
         [("技术栈", 20, True, PRIMARY)])
add_text(s, Inches(0.7), Inches(2.1), Inches(12.0), Inches(1.4),
         [("后端：FastAPI + uvicorn（异步）  ·  数据模型：Pydantic v2（请求校验 + 响应建模）", 15, False, DARK),
          ("智能体：LangChain 1.x create_agent（多智能体）  ·  地图数据：MCP 协议接入高德开放平台（15 个工具）", 15, False, DARK),
          ("大模型：DeepSeek（deepseek-chat 工具调用 + JSON 结构化输出）  ·  前端：原生 HTML + Tailwind CDN + JavaScript", 15, False, DARK)],
         space_after=6)
add_rect(s, Inches(0.7), Inches(3.55), Inches(12.0), Inches(0.06), RGBColor(0xDD, 0xE5, 0xF5))
add_text(s, Inches(0.7), Inches(3.75), Inches(12), Inches(0.5),
         [("多智能体协作流水线（一次规划 = 四步）", 20, True, PRIMARY)])
flow = [
    ("用户请求", "城市/日期\n偏好/预算", LIGHT_BG, PRIMARY),
    ("① 景点搜索\nAgent", "高德 MCP\nmaps_text_search", LIGHT_BG, PRIMARY),
    ("② 天气查询\nAgent", "高德 MCP\nmaps_weather", LIGHT_BG, PRIMARY),
    ("③ 酒店推荐\nAgent", "高德 MCP\nmaps_text_search", LIGHT_BG, PRIMARY),
    ("④ 行程规划\nLLM(JSON)", "deepseek-chat\njson_object", RGBColor(0xE8, 0xF9, 0xF1), SECONDARY),
]
x = Inches(0.7)
for i, (t, d, bg, edge) in enumerate(flow):
    add_round_rect(s, x, Inches(4.35), Inches(2.15), Inches(1.5), bg, "", line=edge)
    tf = s.shapes[-1].text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p1 = tf.paragraphs[0]; p1.alignment = PP_ALIGN.CENTER
    r = p1.add_run(); r.text = t; set_font(r, 15, True, DARK)
    p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
    r = p2.add_run(); r.text = d; set_font(r, 11, False, GRAY)
    if i < len(flow) - 1:
        arrow = add_rect(s, x + Inches(2.2), Inches(4.95), Inches(0.5), Inches(0.35),
                         PRIMARY, shape=MSO_SHAPE.RIGHT_ARROW)
    x += Inches(2.72)
add_text(s, Inches(0.7), Inches(6.2), Inches(12), Inches(0.6),
         [("前 3 步经 MCP 获取高德真实数据（工具按用途拆分、限制调用次数），第 4 步用 JSON 模式生成结构化行程（景点/天气/酒店/预算）", 13, False, GRAY)])
add_footer(s, 6)

# ============ 7. 03 核心技术 —— 关键方案 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "03", "核心技术 · 关键技术方案")
techs = [
    ("MCP 地图接入", "通过 langchain-mcp-adapters 连接高德 MCP，15 个工具（搜索/天气/路线/地理编码）；工具按用途拆分：天气 Agent 仅持有 maps_weather，避免误调用"),
    ("结构化输出", "规划步骤使用 deepseek-chat + response_format=json_object 强制返回合法 JSON；配合代码块/裸 JSON 双解析与最多 3 次解析重试"),
    ("API 调用优化", "ToolCallLimitMiddleware 限制每个 Agent 最多调用 2 次工具；提示词要求合并关键词一次搜索；请求级 MCP 会话复用同一条连接"),
    ("可靠性保障", "LLM 调用设置 180s 超时；前端 5 分钟 AbortController 超时；Agent 输出反问检测 + 追加强硬指令重试；错误日志脱敏 + DEBUG_ERRORS 开关"),
]
y = Inches(1.55)
for name, desc in techs:
    add_round_rect(s, Inches(0.7), y, Inches(2.3), Inches(1.15), PRIMARY, name, size=16)
    add_text(s, Inches(3.2), y + Inches(0.03), Inches(9.5), Inches(1.2), [(desc, 13.5, False, DARK)])
    y += Inches(1.32)
add_footer(s, 7)

# ============ 8. 04 问题与解决(1) —— 三个大问题 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "04", "遇到的问题与解决方案 · 三大难题")
cards = [
    ("① 服务无法启动", RED,
     "虚拟环境基于 Python 3.14.0a5（alpha），依赖 jiter 等 cp314 扩展按稳定版 ABI 编译，导入即段错误(0xC0000005)，端口从未绑定 → 访问被拒绝",
     "用稳定版 Python 3.14.3 重建虚拟环境并重装依赖；服务正常启动"),
    ("② 页面一直显示「规划中」", AMBER,
     "LLM 调用默认无超时（底层傻等 10 分钟）；reasoner 推理 token 与答案共用 max_tokens 预算，长任务推理耗尽预算导致输出为空",
     "LLM 设 180s 超时 + max_tokens 上限；提示词瘦身（中间结果截断）；前端加 5 分钟超时提示"),
    ("③ 7 天行程 JSON 解析失败", PRIMARY,
     "「未找到完整 JSON 对象」：reasoner 复杂任务推理过长，最终内容为空；解析器拿不到任何数据",
     "规划改用 deepseek-chat + json_object 强制 JSON；解析失败自动重试（≤3 次）；实测 7 天行程 35s 成功返回"),
]
y = Inches(1.55)
for name, color, problem, fix in cards:
    add_round_rect(s, Inches(0.7), y, Inches(11.9), Inches(1.7), LIGHT_BG, line=color)
    add_round_rect(s, Inches(0.95), y + Inches(0.18), Inches(2.1), Inches(1.34), color, name, size=15)
    add_text(s, Inches(3.3), y + Inches(0.12), Inches(9.1), Inches(1.5),
             [("✗ " + problem, 12.5, False, DARK),
              ("✓ " + fix, 13.5, True, RGBColor(0x0F, 0x7A, 0x4D))],
             space_after=6)
    y += Inches(1.82)
add_footer(s, 8)

# ============ 9. 04 问题与解决(2) —— 表格 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "04", "遇到的问题与解决方案 · 其他问题与成效")
rows, cols = 5, 4
tbl = s.shapes.add_table(rows, cols, Inches(0.55), Inches(1.6), Inches(12.2), Inches(5.1)).table
tbl.columns[0].width = Inches(2.6)
tbl.columns[1].width = Inches(3.6)
tbl.columns[2].width = Inches(3.6)
tbl.columns[3].width = Inches(2.4)
head = ["问题", "原因", "解决方案", "成效"]
body = [
    ("WinError 10013 端口被占用", "后台残留服务占用 8000 端口", "停止残留进程，释放端口", "正常启动"),
    ("高德 API 被反复调用", "Agent 循环调用工具，每次调用新建 MCP 连接", "调用上限中间件 + 提示词合并关键词 + 请求级会话复用", "连接 N 条→1 条/请求，工具调用 ≤5 次/请求"),
    ("sse_reader ReadError", "共享 MCP 会话被服务端断开且 SDK 无重连", "改为请求级会话，用完即关、下次自愈", "报错消除，连续请求稳定"),
    ("前端显示乱码/城市异常", "测试工具(PowerShell)中文编码损坏请求体", "改用 UTF-8 请求体验证；排查确认非项目缺陷", "数据链路正常"),
]
for c, h in enumerate(head):
    cell = tbl.cell(0, c)
    cell.fill.solid(); cell.fill.fore_color.rgb = PRIMARY
    p = cell.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = h; set_font(r, 15, True, WHITE)
for r_i, row in enumerate(body, start=1):
    for c_i, v in enumerate(row):
        cell = tbl.cell(r_i, c_i)
        cell.fill.solid()
        cell.fill.fore_color.rgb = LIGHT_BG if r_i % 2 == 1 else WHITE
        p = cell.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.LEFT if c_i > 0 else PP_ALIGN.CENTER
        r = p.add_run(); r.text = v; set_font(r, 12.5, False, DARK)
add_footer(s, 9)

# ============ 10. 05 项目成果 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "05", "项目成果")
stats = [
    ("30~50s", "单次规划耗时\n（2 天/7 天行程实测）"),
    ("5 次内", "每次请求的高德工具调用数\n（此前无上限循环）"),
    ("1 条/请求", "MCP 连接数\n（此前每次调用 1 条）"),
    ("4 倍", "切换 deepseek-chat 后\n的响应提速"),
]
x = Inches(0.7)
for num, desc in stats:
    add_round_rect(s, x, Inches(1.6), Inches(2.85), Inches(1.9), PRIMARY, num, size=30)
    tf = s.shapes[-1].text_frame
    p2 = tf.add_paragraph(); p2.alignment = PP_ALIGN.CENTER
    r = p2.add_run(); r.text = desc; set_font(r, 12.5, False, WHITE)
    x += Inches(3.05)
add_text(s, Inches(0.7), Inches(3.75), Inches(12), Inches(0.5),
         [("已实现功能", 20, True, PRIMARY)])
feats = [
    ("行程规划", "逐日行程、交通与住宿安排，2-7 天实测可用"),
    ("真实数据", "高德 MCP 获取景点/天气/酒店，杜绝编造"),
    ("预算估算", "门票/酒店/餐饮/交通费用自动汇总"),
    ("Web 前端", "表单输入、行程折叠卡片、天气与预算展示"),
    ("健壮性", "超时保护、解析重试、会话自愈、日志脱敏"),
]
y = Inches(4.3)
for name, desc in feats:
    add_round_rect(s, Inches(0.7), y, Inches(2.0), Inches(0.55), SECONDARY, name, size=13)
    add_text(s, Inches(2.9), y + Inches(0.02), Inches(9.6), Inches(0.6), [(desc, 13, False, DARK)])
    y += Inches(0.63)
add_footer(s, 10)

# ============ 11. 05 经验反思与优化建议 ============
s = prs.slides.add_slide(BLANK)
add_slide_title(s, "05", "经验反思与后续优化")
add_text(s, Inches(0.7), Inches(1.55), Inches(12), Inches(0.5),
         [("经验反思", 20, True, PRIMARY)])
lessons = [
    "环境即基础设施：Python alpha 版本 ABI 不稳定，务必使用稳定版，避免踩坑",
    "外部依赖必须设边界：LLM/第三方 API 调用要有超时、重试与结果校验，防止无限挂起",
    "结构化输出用对工具：JSON 类任务优先 json_object 模式，而不是靠提示词碰运气",
    "测试工具也会引入 bug：PowerShell 中文编码曾把请求体损坏为「??」，需用真实场景验证",
    "长连接不等于可靠：共享连接要考虑服务端断连，请求级会话更稳",
]
for i, t in enumerate(lessons):
    add_text(s, Inches(0.9), Inches(2.1 + i * 0.52), Inches(11.6), Inches(0.5),
             [(f"{i + 1}. {t}", 14, False, DARK)])
add_rect(s, Inches(0.7), Inches(4.75), Inches(12.0), Inches(0.06), RGBColor(0xDD, 0xE5, 0xF5))
add_text(s, Inches(0.7), Inches(4.95), Inches(12), Inches(0.5),
         [("后续优化建议", 20, True, PRIMARY)])
opts = [
    "· 校验模型输出城市与请求一致；增加同参数结果缓存，进一步减少 API 调用",
    "· 支持并发请求与任务队列；前端升级：地图可视化、景点图片、天气图标增强",
    "· 配置化：模型/温度/超时可配，AGENT_MODEL 已支持 reasoner/chat 切换",
    "· 部署上云：Docker 容器化 + 域名/HTTPS；初始化 Git 仓库并妥善保管 API 密钥",
    "· 补充单元测试与 CI，沉淀可复用的提示词库",
]
for i, t in enumerate(opts):
    add_text(s, Inches(0.9), Inches(5.5 + i * 0.42), Inches(11.6), Inches(0.4),
             [(t, 13.5, False, GRAY)])
add_footer(s, 11)

# ============ 12. 结束页 ============
s = prs.slides.add_slide(BLANK)
add_rect(s, 0, 0, prs.slide_width, prs.slide_height, RGBColor(0x0F, 0x2A, 0x5C))
add_text(s, Inches(1.0), Inches(2.6), Inches(11.3), Inches(1.2),
         [("谢谢观看", 48, True, WHITE)])
add_text(s, Inches(1.0), Inches(4.0), Inches(11.3), Inches(0.8),
         [("欢迎批评指正 · Q&A", 20, False, RGBColor(0xBF, 0xD3, 0xFF))])

prs.core_properties.title = "AI 旅行规划系统（trip_planner）项目总结"
prs.core_properties.author = "trip_planner"

out = r"D:\Projects\测试\trip_planner_项目总结汇报.pptx"
prs.save(out)
print("SAVED:", out)
