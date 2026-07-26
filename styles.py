"""共享样式常量与段→TextStyle 映射。

职责：
- 主题配色（亮/暗两套 Colors、get_colors / _current_colors）
- 块级排版（block_text_size / block_weight / list_color_level）
- 段样式映射（segment_style / prefix_style）
- 通用 Border 工具（only_border）

依赖项：models（BlockType / SegType / Segment）、flet。
对外接口：见每条 def 与模块顶层常量 FONT_MAIN / FONT_MONO。

文本测量与图片尺寸相关功能已迁移至 utils/text_layout.py；
本模块仅保留样式与配色职责，避免职责重叠。
"""

from dataclasses import dataclass, field

import flet as ft

from models import (
    BlockType,
    SegType,
    Segment,
)

# 字体族
FONT_MAIN = "Alibaba"
FONT_MONO = "Consolas"  # 代码块的等宽回退，提升可读性


# ---------------------------------------------------------------------------
# 主题配色：亮/暗两套，科学、有序、清爽、科技、专业
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Colors:
    """主题颜色集合。"""

    bg: str
    surface: str
    text: str
    muted: str
    link: str
    code_bg: str  # 行内代码背景
    code_fg: str  # 行内代码文字
    strike: str
    math_fg: str
    math_bg: str
    quote_fg: str
    quote_bar: str
    code_block_bg: str
    code_block_fg: str
    hover: str
    active_bg: str  # 正在编辑的段淡黄底
    toolbar_bg: str
    border: str
    highlight_bg: str = "#FFF3BF"  # ==高亮== 背景
    supsub_fg: str = "#8A919E"  # 上下标文字色
    heading_colors: dict[int, str] = field(default_factory=dict)


# 亮色：清爽白底，Material 700 标题色阶
_LIGHT = Colors(
    bg="#FFFFFF",
    surface="#FFFFFF",
    text="#1F2329",
    muted="#8A919E",
    link="#1677FF",
    code_bg="#F2F3F5",
    code_fg="#C7254E",
    strike="#8A919E",
    math_fg="#C41E7A",
    math_bg="#FAF0F5",
    quote_fg="#595959",
    quote_bar="#D9D9D9",
    code_block_bg="#F6F8FA",
    code_block_fg="#1F2329",
    hover="#F0F7FF",
    active_bg="#FFFBEA",
    toolbar_bg="#FAFBFC",
    border="#E5E6EB",
    heading_colors={
        1: "#D32F2F",  # 红
        2: "#E65100",  # 橙
        3: "#388E3C",  # 绿
        4: "#0097A7",  # 青
        5: "#1976D2",  # 蓝
        6: "#7B1FA2",  # 紫
    },
)

# 暗色：GitHub Dark 基底，科技深邃，标题色提亮保证对比度
_DARK = Colors(
    bg="#0D1117",
    surface="#161B22",
    text="#E6EDF3",
    muted="#7D8590",
    link="#58A6FF",
    code_bg="#21262D",
    code_fg="#FF7B72",
    strike="#7D8590",
    math_fg="#FF7EB6",
    math_bg="#2D1B2E",
    quote_fg="#B0B8C1",
    quote_bar="#30363D",
    code_block_bg="#161B22",
    code_block_fg="#E6EDF3",
    hover="#1C2128",
    active_bg="#3A2F1A",  # 暗琥珀
    toolbar_bg="#161B22",
    border="#30363D",
    highlight_bg="#4D3E00",  # 暗琥珀高亮
    supsub_fg="#B0B8C1",
    heading_colors={
        1: "#FF6B6B",  # 亮红
        2: "#FFA94D",  # 亮橙
        3: "#51CF66",  # 亮绿
        4: "#22D3EE",  # 亮青
        5: "#5C9CFF",  # 亮蓝
        6: "#C77DFF",  # 亮紫
    },
)


def get_colors(mode: ft.ThemeMode | str | None) -> Colors:
    """根据主题模式返回颜色集合。"""
    if mode == ft.ThemeMode.DARK:
        return _DARK
    return _LIGHT


def _current_colors() -> Colors:
    """读取当前 page.theme_mode 取色；非渲染上下文回退亮色。"""
    try:
        page = ft.context.page
        if page is not None:
            return get_colors(page.theme_mode)
    except Exception:
        pass
    return _LIGHT


def block_text_size(block_type: BlockType, level: int = 0) -> int:
    """块级正文基础字号。"""
    if block_type == BlockType.HEADING:
        return {1: 30, 2: 24, 3: 20, 4: 18, 5: 16, 6: 16}.get(level, 16)
    if block_type == BlockType.CODE:
        return 14
    return 16


_HEADING_WEIGHTS: dict[int, ft.FontWeight] = {
    1: ft.FontWeight.W_800,
    2: ft.FontWeight.W_700,
    3: ft.FontWeight.W_600,
    4: ft.FontWeight.W_500,
    5: ft.FontWeight.W_500,
    6: ft.FontWeight.W_500,
}


def block_weight(block_type: BlockType, level: int = 0) -> ft.FontWeight:
    if block_type == BlockType.HEADING:
        return _HEADING_WEIGHTS.get(level, ft.FontWeight.NORMAL)
    return ft.FontWeight.NORMAL


def list_color_level(indent: int) -> int:
    """列表缩进（空格数）→ 1..6 色阶，与 heading_colors 共用。"""
    return min(max(indent // 2 + 1, 1), 6)


def segment_style(seg: Segment, base_size: int = 16) -> ft.TextStyle:
    """把段类型映射为 TextStyle（渲染态）。

    支持组合格式：seg.marks 记录作用于整段的所有包裹器（如 (EMPHASIS, STRONG)
    对应 ***加粗斜体***），按 marks 累加效果。单一类型沿用原样式。
    """
    c = _current_colors()
    t = seg.seg_type

    # 原子类型（不参与组合）
    if t == SegType.INLINE_MATH:
        return ft.TextStyle(
            size=base_size - 1,
            color=c.math_fg,
            bgcolor=c.math_bg,
            font_family=FONT_MONO,
            italic=True,
        )
    if t == SegType.CODESPAN:
        return ft.TextStyle(
            size=base_size - 1,
            color=c.code_fg,
            bgcolor=c.code_bg,
            font_family=FONT_MONO,
        )
    if t == SegType.LINK:
        return ft.TextStyle(
            size=base_size, color=c.link, decoration=ft.TextDecoration.UNDERLINE
        )
    if t == SegType.IMAGE:
        return ft.TextStyle(size=base_size, color=c.link, italic=True)

    # 包裹器类型：单一沿用原样式，组合按 marks 累加
    marks = seg.marks
    if not marks or len(marks) == 1:
        single = marks[0] if marks else t
        if single == SegType.STRONG:
            return ft.TextStyle(size=base_size, weight=ft.FontWeight.BOLD, color=c.text)
        if single == SegType.EMPHASIS:
            return ft.TextStyle(size=base_size, italic=True, color=c.text)
        if single == SegType.STRIKE:
            return ft.TextStyle(
                size=base_size, color=c.strike, decoration=ft.TextDecoration.LINE_THROUGH
            )
        if single == SegType.HIGHLIGHT:
            return ft.TextStyle(size=base_size, color=c.text, bgcolor=c.highlight_bg)
        if single == SegType.SUPERSCRIPT:
            return ft.TextStyle(size=max(base_size - 4, 10), color=c.supsub_fg)
        if single == SegType.SUBSCRIPT:
            return ft.TextStyle(size=max(base_size - 4, 10), color=c.supsub_fg)
        # TEXT / 其它
        return ft.TextStyle(size=base_size, color=c.text)

    # 组合格式：累加各 mark 效果
    weight = ft.FontWeight.NORMAL
    italic = False
    decoration = ft.TextDecoration.NONE
    color = c.text
    bgcolor = None
    size = base_size
    for m in marks:
        if m == SegType.STRONG:
            weight = ft.FontWeight.BOLD
        elif m == SegType.EMPHASIS:
            italic = True
        elif m == SegType.STRIKE:
            decoration = ft.TextDecoration.LINE_THROUGH
            color = c.strike
        elif m == SegType.HIGHLIGHT:
            bgcolor = c.highlight_bg
        elif m in (SegType.SUPERSCRIPT, SegType.SUBSCRIPT):
            size = max(base_size - 4, 10)
    kwargs: dict = {"size": size, "weight": weight, "color": color}
    if italic:
        kwargs["italic"] = True
    if decoration != ft.TextDecoration.NONE:
        kwargs["decoration"] = decoration
    if bgcolor is not None:
        kwargs["bgcolor"] = bgcolor
    return ft.TextStyle(**kwargs)


def prefix_style(seg: Segment, base_size: int = 16) -> ft.TextStyle:
    """块级前缀段（# - >）的样式：弱化显示。"""
    return ft.TextStyle(
        size=base_size, color=_current_colors().muted, weight=ft.FontWeight.BOLD
    )


_NO_BORDER = ft.BorderSide.none()


def only_border(
    *,
    top: ft.BorderSide | None = None,
    bottom: ft.BorderSide | None = None,
    left: ft.BorderSide | None = None,
    right: ft.BorderSide | None = None,
) -> ft.Border:
    """便捷构造单边 Border。"""
    return ft.Border(
        top=top or _NO_BORDER,
        right=right or _NO_BORDER,
        bottom=bottom or _NO_BORDER,
        left=left or _NO_BORDER,
    )


# ---------------------------------------------------------------------------
# Design Token：间距 / 圆角 / 海拔 / 阴影的统一来源
# ---------------------------------------------------------------------------
# 设计原则：
# - 4px 基准网格，2px 半步长用于紧凑控件
# - 圆角递进 4-6-8-12-16-18，嵌套时外层 = 内层 + padding
# - 海拔用 BoxShadow 表达（不用 Container.elevation，Material 风格过重）
# - 亮/暗主题共享 token，颜色差异由 Colors 处理
# 符合「科学、有序、清爽、科技、专业」的视觉偏好。


class Spacing:
    """间距 token（4px 基准网格）。"""

    XS = 2     # 紧凑控件内间距（toolbar 按钮 padding、TOC 列表 spacing）
    SM = 4     # 标准控件内间距（图标按钮 padding）
    MD = 6     # 中等间距（Row spacing、紧凑容器 padding）
    LG = 8     # 标准垂直间距（块级容器 vertical padding）
    XL = 12    # 标准水平间距（块级容器 horizontal padding、引用缩进）
    XXL = 16   # 大间距（对话框 padding、TOC 缩进）
    XXXL = 24  # 超大间距（对话框外 padding、章节间距）


class Radius:
    """圆角 token（4-6-8-12-16-18 递进，嵌套时外层 = 内层 + padding）。"""

    SM = 4     # 小圆角（行内代码、行内公式）
    MD = 6     # 中圆角（代码块、表格单元格、TOC 块）
    LG = 8     # 大圆角（按钮、当前行高亮、侧边栏列表项）
    XL = 12    # 超大圆角（DataTable、设置面板卡片）
    XXL = 16   # 容器圆角（表格容器、代码块外层）
    XXXL = 18  # 对话框圆角


class Elevation:
    """海拔 token（BoxShadow 配置预设）。"""

    NONE = 0
    LOW = 1      # 代码块、表格（微妙层次）
    MEDIUM = 2   # 浮动工具栏、弹出菜单
    HIGH = 4     # 对话框
    DIALOG = 8   # 模态对话框（强层次）


def card_shadow(elevation: int = Elevation.LOW, is_dark: bool = False) -> list[ft.BoxShadow]:
    """按海拔返回阴影列表。

    亮色：低 opacity（0.06）淡黑阴影，微妙层次感
    暗色：高 opacity（0.30）深黑阴影，暗背景下需更强对比才可见
    """
    if elevation == Elevation.NONE:
        return []
    if elevation == Elevation.LOW:
        opacity = 0.30 if is_dark else 0.06
        blur = 8 if is_dark else 6
        return [ft.BoxShadow(
            spread_radius=0, blur_radius=blur,
            color=ft.Colors.with_opacity(opacity, ft.Colors.BLACK),
            offset=ft.Offset(0, 1),
        )]
    if elevation == Elevation.MEDIUM:
        opacity = 0.40 if is_dark else 0.10
        blur = 12 if is_dark else 10
        return [ft.BoxShadow(
            spread_radius=0, blur_radius=blur,
            color=ft.Colors.with_opacity(opacity, ft.Colors.BLACK),
            offset=ft.Offset(0, 2),
        )]
    # HIGH / DIALOG
    opacity = 0.50 if is_dark else 0.18
    blur = 24 if is_dark else 20
    return [ft.BoxShadow(
        spread_radius=0, blur_radius=blur,
        color=ft.Colors.with_opacity(opacity, ft.Colors.BLACK),
        offset=ft.Offset(0, 8),
    )]


def hairline_border(color: str, opacity: float = 1.0) -> ft.BorderSide:
    """1px 细线边框（表格分割线、弱化边框）。"""
    return ft.BorderSide(1, ft.Colors.with_opacity(opacity, color))


def accent_border(color: str, width: int = 2) -> ft.BorderSide:
    """强调边框（当前行/激活态）。"""
    return ft.BorderSide(width, color)
