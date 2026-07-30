"""编辑器模块级辅助函数与常量（从 views/editor.py 顶层抽取）。

包含：
- _make_stable_cb：稳定回调包装器（配合 use_memo([]) 避免 memo 误判）
- _noop：空操作占位
- _build_diff_gap：diff 对齐间隙容器
- _inline_content：取行内内容源码（去块级前缀）
- _next_line_raw：回车续行 raw 计算
- _make_code_line：通过 parse_markdown 构造可靠 CODE 行（围栏合并）
- 模块级预编译正则 + 列表/引用常量

依赖项：
- flet（ft.Control / ft.Container / ft.Ref）
- models（BlockType / Line / SegType）
- utils.segment_helpers（is_fence / line_raw）
"""

import re

import flet as ft

import parser
from models import BlockType, Line, SegType


def _make_stable_cb(ref: ft.Ref, key: str):
    """创建稳定回调包装器：从 ref.current[key] 读取最新闭包并调用。

    配合 ft.use_callback([], []) 使用：首次渲染产出包装器，后续渲染返回缓存引用。
    包装器捕获 ref（use_ref 稳定对象），每次调用读取 ref.current[key] 即最新闭包。
    避免 editor 每次重渲染时闭包重建导致 ft.memo 误判所有 LineView 变化。
    """

    def wrapper(*args, **kwargs):
        cb = ref.current.get(key) if ref.current else None
        if cb is not None:
            return cb(*args, **kwargs)

    return wrapper


def _noop() -> None:
    pass


def _build_diff_gap(height: float, c) -> ft.Control:
    """构建 diff 对齐间隙容器：空行占位，背景色标识对侧增删。

    用于 diff 对比模式中，当一侧有行而另一侧没有时，在缺失侧插入等高间隙，
    保持左右视觉行对齐（VSCode diff editor 风格）。
    """
    return ft.Container(
        height=height,
        width=float("inf"),
        bgcolor=c.diff_gap_del_bg,
        margin=ft.Margin.all(0),
        padding=ft.Padding.all(0),
    )


# 围栏块：自管理独立岛屿，不参与光标导航/合并（统一来源 utils.segment_helpers）
# 列表/任务标记正则：模块级预编译，避免热路径重复 re.match 编译开销
_RE_TASK_MARKER = re.compile(r"^([-*+])\s+\[[ xX]\]\s+")  # 任务项前缀 - [ ] / - [x]
_RE_UO_MARKER = re.compile(r"^([-*+])\s+")  # 无序列表标记 - / * / +
_RE_O_MARKER = re.compile(r"^(\d+)\.\s+")  # 有序列表标记 N.
_RE_O_PREFIX = re.compile(r"^\d+\.$")  # 有序列表纯前缀 N.（续行判定）

# 列表缩进单位（空格数）：与 list_color_level 色阶（indent // 2 + 1）一致，
# 每 Tab 一级 = 2 空格 = 1 色阶，视觉与配色同步变化。
_LIST_INDENT_UNIT = 2
# 列表最大缩进空格数：对应 6 级色阶（0,2,4,6,8,10），防止无限嵌套。
_LIST_MAX_SPACES = 10
# 引用最大嵌套层级：对应 6 级彩色边框（heading_colors 红→紫）。
_QUOTE_MAX_LEVEL = 6

# Typora 式代码块触发：```[lang]（3+ 反引号 + 可选语言标识）独占一行 + 回车 → 代码块。
# lang 字符集覆盖常见语言名（python / c++ / f# / obj-c 等）。
_RE_FENCE_TRIGGER = re.compile(r"^`{3,}\s*([A-Za-z0-9_+#.-]*)$")


def _make_code_line(lang: str, content: str) -> Line:
    """通过 parse_markdown 构造可靠的 CODE 行（围栏合并为单编辑单元）。

    `_reparse_atomic` 无法把段落行转为 CODE 块：其普通块分支调用 `_build_line` →
    `_detect_block`，而后者不识别围栏（围栏仅在 `parse_markdown` 全量解析时合并）。
    故代码块创建统一走 `parse_markdown` 全量解析取首行，保证 block_type=CODE、
    lang 与 body 正确（含空内容 + lang 的情形）。

    被 set_block(CODE) 与 on_submit 的 ```+Enter 触发共用。
    """
    raw = f"```{lang}\n{content}\n```"
    return parser.parse_markdown(raw).lines[0]


def _inline_content(line: Line) -> str:
    """取一行的"行内内容"源码（去掉块级前缀），用于块类型切换。"""
    if line.block_type in (BlockType.CODE, BlockType.MATH):
        return line.segments[0].text if line.segments else ""
    if line.block_type == BlockType.HR:
        return ""
    return "".join(
        s.raw
        for s in line.segments
        if s.seg_type
        not in (SegType.HEADING_PREFIX, SegType.LIST_PREFIX, SegType.QUOTE_PREFIX)
    )


def _next_line_raw(line: Line) -> str:
    """回车续行：列表续列表（含任务/有序递增），否则空段落。"""
    if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O):
        indent_sp = " " * (line.level or 0)
        prefix = line.segments[0].raw if line.segments else "- "
        body = prefix.lstrip()
        if m := _RE_TASK_MARKER.match(body):
            return f"{indent_sp}{m.group(1)} [ ] "
        if m := _RE_UO_MARKER.match(body):
            return f"{indent_sp}{m.group(1)} "
        if m := _RE_O_MARKER.match(body):
            return f"{indent_sp}{int(m.group(1)) + 1}. "
        return f"{indent_sp}- "
    if line.block_type == BlockType.QUOTE:
        return "> " * (line.level or 1)
    return ""
