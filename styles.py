"""共享样式常量与段→TextStyle 映射。

集中管理颜色与排版，保证视图层声明式、无散落的魔法数字。
支持亮/暗双主题：get_colors(mode) 返回对应 Colors，_current_colors() 读取
当前 page.theme_mode 自动取色，供渲染态函数与视图层使用。
"""

import io
import os
import urllib.request
from dataclasses import dataclass, field

import flet as ft
from PIL import Image as _PILImage
from PIL import ImageFont as _PILImageFont

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


def block_weight(block_type: BlockType, level: int = 0) -> ft.FontWeight:
    if block_type == BlockType.HEADING:
        return ft.FontWeight.BOLD
    return ft.FontWeight.NORMAL


def segment_style(seg: Segment, base_size: int = 16) -> ft.TextStyle:
    """把段类型映射为 TextStyle（渲染态）。"""
    c = _current_colors()
    t = seg.seg_type
    if t == SegType.STRONG:
        return ft.TextStyle(size=base_size, weight=ft.FontWeight.BOLD, color=c.text)
    if t == SegType.EMPHASIS:
        return ft.TextStyle(size=base_size, italic=True, color=c.text)
    if t == SegType.STRIKE:
        return ft.TextStyle(
            size=base_size, color=c.strike, decoration=ft.TextDecoration.LINE_THROUGH
        )
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
    return ft.TextStyle(size=base_size, color=c.text)


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
# 文本宽度测量：基于本地字体精确计算像素宽度，用于编辑块宽度自适应。
# 用 Pillow 的 FreeType 渲染器加载 .otf/.ttf，getlength 返回文本 advance 宽度，
# 精度远高于"字符数 × 平均字宽"估算，能贴合 Flet 渲染逻辑像素。
# ---------------------------------------------------------------------------

_FONT_FILES = {
    FONT_MAIN: os.path.join(
        os.path.dirname(__file__), "assets", "fonts", "AlibabaPuHuiTi-3-55-Regular.otf"
    ),
    FONT_MONO: r"C:\Windows\Fonts\consola.ttf",  # 代码段等宽回退字体
}
_font_cache: dict[tuple[str, int], _PILImageFont.FreeTypeFont] = {}


def _get_font(font_family: str, size: int) -> _PILImageFont.FreeTypeFont:
    """按 (字体族, 字号) 缓存加载 ImageFont，避免重复磁盘 IO。"""
    key = (font_family, size)
    f = _font_cache.get(key)
    if f is None:
        path = _FONT_FILES.get(font_family)
        try:
            f = (
                _PILImageFont.truetype(path, size)
                if path
                else _PILImageFont.load_default()
            )
        except OSError:
            f = _PILImageFont.load_default()
        _font_cache[key] = f
    return f


def measure_text_width(text: str, font_family: str, size: int) -> float:
    """测量文本在指定字体/字号下的像素宽度。

    返回值约为 Flet 逻辑像素宽度（桌面端 1.0 缩放下与渲染一致）。
    """
    if not text:
        return 0.0
    # Pillow getlength 逐字形累加 advance，最接近实际渲染宽度
    return _get_font(font_family, size).getlength(text)


# ---------------------------------------------------------------------------
# 图片尺寸读取与缩放：用 Pillow 读取图片真实像素尺寸，按最大边等比例缩放。
# - 大图（宽或高 > max_size）：缩放到最大边 = max_size
# - 小图：返回实际尺寸
# - 无法读取：返回 (None, None)，交由 ft.Image 自适应
# 结果按 src 缓存，避免每次渲染重复 IO / 网络请求。
# ---------------------------------------------------------------------------

_IMG_MAX = 500  # 图片最大边长（像素）
_img_size_cache: dict[str, tuple[int, int] | None] = {}


def _read_image_size(src: str) -> tuple[int, int] | None:
    """读取图片真实 (width, height)。本地路径直接打开；URL 下载后解析。"""
    try:
        if src.startswith(("http://", "https://")):
            with urllib.request.urlopen(src, timeout=5) as resp:
                data = resp.read()
            img = _PILImage.open(io.BytesIO(data))
        else:
            img = _PILImage.open(src)
        return img.size
    except Exception:
        return None


def image_fit_size(src: str, max_size: int = _IMG_MAX) -> tuple[int | None, int | None]:
    """返回图片在 UI 中应使用的 (width, height)。

    大图等比例缩放到最大边 = max_size；小图保持原尺寸；读取失败返回 (None, None)。
    """
    if src not in _img_size_cache:
        _img_size_cache[src] = _read_image_size(src)
    size = _img_size_cache[src]
    if size is None:
        return None, None
    w, h = size
    if w <= max_size and h <= max_size:
        return w, h
    if w >= h:
        ratio = max_size / w
        return max_size, round(h * ratio)
    ratio = max_size / h
    return round(w * ratio), max_size
