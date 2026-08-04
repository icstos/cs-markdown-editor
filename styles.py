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
@dataclass(frozen=True, slots=True)
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
    highlight_bg: str = "#FBF2A8"  # ==高亮== 背景（Typora 式柔和荧光笔黄）
    supsub_fg: str = "#8A919E"  # 上下标文字色
    # 搜索匹配段高亮（区别于 ==高亮== 语法，独立 token 便于单独调色）
    search_match_bg: str = "#FFE082"  # 搜索匹配段背景（亮黄）
    search_match_fg: str = "#1F2329"  # 搜索匹配段文字
    heading_colors: dict[int, str] = field(default_factory=dict)
    # diff 对比配色（GitHub 风格绿增红删）
    diff_add_bg: str = "#e6ffed"  # 新增行背景
    diff_del_bg: str = "#ffeef0"  # 删除行背景
    diff_gap_add_bg: str = "#f0fff4"  # 新增侧间隙背景（更浅）
    diff_gap_del_bg: str = "#fff5f5"  # 删除侧间隙背景（更浅）


# 亮色：浅色商务科技配色，简洁专业，适配白天办公场景
# - bg 采用极浅冷调米白（FAFBFC），降低纯白刺眼感，长时间阅读更舒适
# - surface 纯白保留，与 bg 形成微妙层次，强化"卡片浮起"语义
# - 标题色采用商务科技色阶：海军蓝→亮蓝→深青→雅紫→焦糖橙→石板灰
#   每级色相相邻但有明显明度差，层级递进清晰，规避高饱和彩虹色
# - 边框/引用条统一偏冷中性灰，视觉干净克制
_LIGHT = Colors(
    bg="#FAFBFC",           # 极浅冷调米白，缓解纯白刺眼
    surface="#FFFFFF",       # 纯白表面，与 bg 微妙分层
    text="#1F2329",          # 深炭黑，高对比锐利
    muted="#8A919E",         # 中性灰，弱化辅助信息
    link="#1677FF",          # Ant Design 蓝，专业商务
    code_bg="#F2F3F5",       # 浅灰底，行内代码
    code_fg="#C7254E",       # 经典红，代码字符
    strike="#8A919E",
    math_fg="#C41E7A",       # 深紫红，公式
    math_bg="#FAF0F5",       # 浅紫粉底
    quote_fg="#595959",
    quote_bar="#DDE0E6",     # 偏冷中性灰，去黄感
    code_block_bg="#F6F8FA", # GitHub 风格浅灰
    code_block_fg="#1F2329",
    hover="#F0F7FF",         # 浅蓝 hover
    active_bg="#FFFBEA",     # 淡黄激活态
    toolbar_bg="#F1F3F7",    # 工具栏底，与 bg 明显分层
    border="#E5E8ED",        # 偏冷边框灰
    heading_colors={
        1: "#1A4480",  # 深海军蓝 - 最高层级，权威
        2: "#2C7BE5",  # 亮蓝 - 主结构
        3: "#0E7C66",  # 深青绿 - 章节分隔
        4: "#6B5B95",  # 雅致紫 - 子小节
        5: "#B54708",  # 焦糖橙 - 次级
        6: "#5C6573",  # 石板灰 - 最低级
    },
)

# 暗色：暗色护眼科技配色，降低蓝光与饱和度
# - bg 由 GitHub #0D1117 (B=23 最高) 调整为 #14161A (R=G=B 平衡)，
#   降低蓝色通道占比，减少蓝光刺激；色温偏中性暖，长时间阅读更护眼
# - 标题色统一降饱和（原高饱和亮色 → 柔粉/雾感色），保留色相区分层级，
#   但去掉刺眼感，符合"护眼 + 科技 + 简洁专业"诉求
# - 行内代码/公式/链接等强调色同步降饱和，避免暗背景下高饱和闪烁
# - 边框/引用条/工具栏底统一中性偏暖灰，避免冷蓝漂移
_DARK = Colors(
    bg="#14161A",            # 中性偏暖深色，降蓝光
    surface="#1A1D22",       # 表面色，与 bg 分层
    text="#E6EDF3",          # 高对比浅文本
    muted="#8B939E",         # 略亮中性灰，弱化信息更易辨识
    link="#6BA0F5",          # 柔化蓝，降低饱和度
    code_bg="#1F242B",       # 行内代码底
    code_fg="#E88989",       # 柔粉红，替代高饱和珊瑚红
    strike="#8B939E",
    math_fg="#E891B5",       # 柔粉，替代高饱和粉
    math_bg="#281D24",       # 偏暖暗紫底
    quote_fg="#B0B8C1",
    quote_bar="#353B43",     # 偏暖中性灰
    code_block_bg="#1A1D22", # 代码块底，与 surface 一致
    code_block_fg="#E6EDF3",
    hover="#1F242B",         # 偏暖 hover
    active_bg="#3A2F1A",     # 暗琥珀激活态
    toolbar_bg="#1A1D22",    # 工具栏底
    border="#2F353D",        # 偏暖边框
    highlight_bg="#5D4E1A",  # 暗琥珀高亮（Typora 式，与 search_match_bg 一致更鲜明）
    search_match_bg="#5D4E1A",  # 暗琥珀搜索匹配
    search_match_fg="#E6EDF3",
    supsub_fg="#B0B8C1",
    diff_add_bg="#1a2e22",   # 暗色新增行背景
    diff_del_bg="#2e1a1d",   # 暗色删除行背景
    diff_gap_add_bg="#16201a",  # 暗色新增侧间隙
    diff_gap_del_bg="#1f1618",   # 暗色删除侧间隙
    heading_colors={
        1: "#75A4F0",  # 柔雾蓝 - 最高层级
        2: "#65C292",  # 柔薄荷绿
        3: "#4FB8C9",  # 柔青蓝
        4: "#B08FD8",  # 柔丁香紫
        5: "#DD9658",  # 柔琥珀橙
        6: "#C78787",  # 柔珊瑚红
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
    """块级正文基础字号。

    标题字号递进 30→24→20→18→16→15：每级 -2~-6px，H6 略小于正文（16）
    以"小一号+加粗"区分；正文 16，代码 14（紧凑可读）。
    """
    if block_type == BlockType.HEADING:
        return {1: 30, 2: 24, 3: 20, 4: 18, 5: 16, 6: 15}.get(level, 16)
    if block_type == BlockType.CODE:
        return 14
    return 16


# 字重递进：W_800→W_700→W_700→W_600→W_600→W_500
# 相邻级别差异 ≥100，避免 H4/H5/H6 同字重导致层级模糊
_HEADING_WEIGHTS: dict[int, ft.FontWeight] = {
    1: ft.FontWeight.W_800,
    2: ft.FontWeight.W_700,
    3: ft.FontWeight.W_700,
    4: ft.FontWeight.W_600,
    5: ft.FontWeight.W_600,
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
                size=base_size,
                color=c.strike,
                decoration=ft.TextDecoration.LINE_THROUGH,
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

    XS = 2  # 紧凑控件内间距（toolbar 按钮 padding、TOC 列表 spacing）
    SM = 4  # 标准控件内间距（图标按钮 padding）
    MD = 6  # 中等间距（Row spacing、紧凑容器 padding）
    LG = 8  # 标准垂直间距（块级容器 vertical padding）
    XL = 12  # 标准水平间距（块级容器 horizontal padding、引用缩进）
    XXL = 16  # 大间距（对话框 padding、TOC 缩进）
    XXXL = 24  # 超大间距（对话框外 padding、章节间距）


class Radius:
    """圆角 token（4-6-8-12-16-18 递进，嵌套时外层 = 内层 + padding）。"""

    SM = 4  # 小圆角（行内代码、行内公式）
    MD = 6  # 中圆角（代码块、表格单元格、TOC 块）
    LG = 8  # 大圆角（按钮、当前行高亮、侧边栏列表项）
    XL = 12  # 超大圆角（DataTable、设置面板卡片）
    XXL = 16  # 容器圆角（表格容器、代码块外层）
    XXXL = 18  # 对话框圆角


class Elevation:
    """海拔 token（BoxShadow 配置预设）。"""

    NONE = 0
    LOW = 1  # 代码块、表格（微妙层次）
    MEDIUM = 2  # 浮动工具栏、弹出菜单
    HIGH = 4  # 对话框
    DIALOG = 8  # 模态对话框（强层次）


def card_shadow(
    elevation: int = Elevation.LOW, is_dark: bool = False
) -> list[ft.BoxShadow]:
    """按海拔返回阴影列表。

    亮色：低 opacity（0.06）淡黑阴影，微妙层次感
    暗色：高 opacity（0.30）深黑阴影，暗背景下需更强对比才可见
    """
    if elevation == Elevation.NONE:
        return []
    if elevation == Elevation.LOW:
        opacity = 0.30 if is_dark else 0.06
        blur = 8 if is_dark else 6
        return [
            ft.BoxShadow(
                spread_radius=0,
                blur_radius=blur,
                color=ft.Colors.with_opacity(opacity, ft.Colors.BLACK),
                offset=ft.Offset(0, 1),
            )
        ]
    if elevation == Elevation.MEDIUM:
        opacity = 0.40 if is_dark else 0.10
        blur = 12 if is_dark else 10
        return [
            ft.BoxShadow(
                spread_radius=0,
                blur_radius=blur,
                color=ft.Colors.with_opacity(opacity, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            )
        ]
    # HIGH / DIALOG
    opacity = 0.50 if is_dark else 0.18
    blur = 24 if is_dark else 20
    return [
        ft.BoxShadow(
            spread_radius=0,
            blur_radius=blur,
            color=ft.Colors.with_opacity(opacity, ft.Colors.BLACK),
            offset=ft.Offset(0, 8),
        )
    ]


def hairline_border(color: str, opacity: float = 1.0) -> ft.BorderSide:
    """1px 细线边框（表格分割线、弱化边框）。"""
    return ft.BorderSide(1, ft.Colors.with_opacity(opacity, color))


def accent_border(color: str, width: int = 2) -> ft.BorderSide:
    """强调边框（当前行/激活态）。"""
    return ft.BorderSide(width, color)
