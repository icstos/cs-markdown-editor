"""文本像素测量与图片尺寸读取。

依赖项：Pillow（PIL.Image / PIL.ImageFont）、标准库 io/os/re/urllib。
对外接口：
- measure_text_width(text, font_family, size) -> float：文本像素宽度
- image_fit_size(src, max_size=500) -> tuple[int|None, int|None]：图片显示尺寸
- FONT_FILES：dict[str, str]，字体文件路径表（供外部扩展）

设计要点：
- Pillow FreeType getlength 返回 advance 宽度，精度远高于「字符数 × 平均字宽」
- CJK 回退：主字体不含 CJK 字形时按 CJK 边界切分，CJK 片段改用主中文字体测量，
  贴合 Skia 渲染回退行为
- 图片尺寸按 src 缓存，避免重复 IO / 网络请求

从 styles.py 拆出：原 styles.py 混合了主题颜色、排版样式、文本测量、图片尺寸
四个职责，此处将后两者（测量相关）独立到 utils/text_layout.py。
"""

import io
import os
import re
import urllib.request

from PIL import Image as _PILImage
from PIL import ImageFont as _PILImageFont

# 字体族常量（与 styles.py 保持一致，避免循环依赖）
FONT_MAIN = "Alibaba"
FONT_MONO = "Consolas"

_FONT_FILES: dict[str, str] = {
    FONT_MAIN: os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "assets", "fonts", "AlibabaPuHuiTi-3-55-Regular.otf",
    ),
    FONT_MONO: r"C:\Windows\Fonts\consola.ttf",
}

_font_cache: dict[tuple[str, int], _PILImageFont.FreeTypeFont] = {}

# CJK 与全角字符范围：主字体（如 Consolas）不含这些字形时需回退到主中文字体
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\ufeff\uff00-\uffef]"
)


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

    字体回退处理：当请求字体（如 Consolas 等宽体）不含 CJK/全角字形时，
    Pillow getlength 报告的 advance 严重偏小，而 Flutter/Skia 渲染会回退到
    系统 CJK 字体（宽度约 1em/字）。此处将文本按 CJK 边界切分，CJK 片段改用
    主中文字体测量，非 CJK 片段仍用原字体，二者求和贴合 Skia 实际渲染宽度。
    """
    if not text:
        return 0.0
    if font_family == FONT_MAIN or not _CJK_RE.search(text):
        return _get_font(font_family, size).getlength(text)

    total = 0.0
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            total += _get_font(font_family, size).getlength(text[pos:m.start()])
        total += _get_font(FONT_MAIN, size).getlength(m.group())
        pos = m.end()
    if pos < len(text):
        total += _get_font(font_family, size).getlength(text[pos:])
    return total


# ---------------------------------------------------------------------------
# 图片尺寸读取与缩放
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
