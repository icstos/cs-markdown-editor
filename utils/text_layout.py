"""文本像素测量与图片尺寸读取。

依赖项：uharfbuzz（文本整形测量）、Pillow（PIL.Image 图片尺寸）、标准库 io/os/re/urllib。
对外接口：
- measure_text_width(text, font_family, size) -> float：文本像素宽度
- image_fit_size(src, max_size=500) -> tuple[int|None, int|None]：图片显示尺寸
- FONT_FILES：dict[str, str]，字体文件路径表（供外部扩展）

设计要点：
- 文本测量使用 HarfBuzz（与 Skia/Flutter 渲染同一引擎）整形后取 advance 之和，
  保证光标像素坐标与渲染层 TextSpan 像素级对齐。Pillow 的 getlength 会截断
  字形 advance（如 575/1000×16=9.2 截成 9.0），数字每字偏差 0.2px，多字累积
  导致光标与文字重叠——改用 HarfBuzz 后彻底消除该偏差。
- Flet 0.86 letter_spacing 补偿：Flet 的 Text 控件默认 TextStyle.letter_spacing=0.25
  （非 Flutter 标准 0.0），Skia 渲染时每个字形 advance 都加 0.25px（含末字形）。
  HarfBuzz 整形返回的是字体原始 advance，需按字形数补偿 _FLET_DEFAULT_LETTER_SPACING，
  否则光标偏移随字符数线性累积（10 位数字累积 2.5px）。渲染层未显式设置 letter_spacing，
  全部使用默认值 0.25，故测量端统一按 0.25/字形补偿。
- CJK 回退：主字体不含 CJK 字形时按 CJK 边界切分，CJK 片段改用主中文字体测量，
  贴合 Skia 渲染回退行为。
- 单字符宽度缓存（_char_width_cache）：行内 X 偏移逐字符累加时高频调用，缓存后
  首次测量外均为 O(1) 查表。
- 图片尺寸按 src 缓存，避免重复 IO / 网络请求。

从 styles.py 拆出：原 styles.py 混合了主题颜色、排版样式、文本测量、图片尺寸
四个职责，此处将后两者（测量相关）独立到 utils/text_layout.py。
"""

import io
import os
import re
import urllib.request

import uharfbuzz as _hb
from PIL import Image as _PILImage

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

# CJK 与全角字符范围：主字体（如 Consolas）不含这些字形时需回退到主中文字体
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\ufeff\uff00-\uffef]"
)

# Flet 0.86 的 Text 控件默认 TextStyle.letter_spacing=0.25（非 Flutter 标准 0.0）。
# Skia 渲染时对每个字形（含末字形）的 advance 都加 0.25px，故 HarfBuzz 原始 advance
# 需按字形数补偿，否则光标偏移随字符数线性累积（10 位数字累积 2.5px 偏差）。
# 渲染层（styles.segment_style / prefix_style / rendered_line._line_style 等）均未
# 显式设置 letter_spacing，统一使用该默认值，故测量端按 0.25/字形补偿即可对齐。
_FLET_DEFAULT_LETTER_SPACING = 0.25


# ---------------------------------------------------------------------------
# HarfBuzz 文本整形测量（与 Skia 渲染对齐）
# ---------------------------------------------------------------------------
# Face 缓存：字体族 -> 已解析 Face（文件只解析一次）
_hb_faces: dict[str, _hb.Face] = {}
# Font 缓存：(字体族, 字号) -> (Font, upem)（按字号缩放后的实例）
_hb_fonts: dict[tuple[str, int], tuple[_hb.Font, int]] = {}
# 单字符宽度缓存：(字符, 字体族, 字号) -> 像素宽度（行内逐字符累加高频调用）
_char_width_cache: dict[tuple[str, str, int], float] = {}


def _get_hb_font(font_family: str, size: int) -> tuple[_hb.Font, int] | None:
    """按 (字体族, 字号) 缓存加载 HarfBuzz Font；Face 仅解析一次。

    scale = size * upem 使 advance 为整数（避免 26.6 定点舍入误差）；
    ot_font_set_funcs 启用 GSUB/GPOS 整形（kern/liga/calt 等），与 Skia 一致。
    返回 (Font, upem) 或 None（字体文件缺失时）。
    """
    key = (font_family, size)
    cached = _hb_fonts.get(key)
    if cached is not None:
        return cached
    path = _FONT_FILES.get(font_family)
    if not path or not os.path.exists(path):
        return None
    face = _hb_faces.get(font_family)
    if face is None:
        blob = _hb.Blob.from_file_path(path)
        face = _hb.Face(blob)
        _hb_faces[font_family] = face
    font = _hb.Font(face)
    upem = face.upem
    font.scale = (size * upem, size * upem)
    _hb.ot_font_set_funcs(font)
    _hb_fonts[key] = (font, upem)
    return (font, upem)


def _hb_shape_width(text: str, font_family: str, size: int) -> float:
    """用 HarfBuzz 整形 text，返回像素 advance 总宽（含 Flet letter_spacing 补偿）。

    scale = size*upem 下 x_advance 为设计单位 × size（整数），除以 upem 得像素宽度，
    与 Skia 在相同字号下的渲染 advance 一致；再按字形数加 _FLET_DEFAULT_LETTER_SPACING
    补偿 Flet Text 默认 letter_spacing=0.25（每字形含末字形都加 0.25px）。
    """
    pair = _get_hb_font(font_family, size)
    if pair is None:
        return 0.0
    font, upem = pair
    buf = _hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    _hb.shape(font, buf)
    positions = buf.glyph_positions
    raw_advance = sum(p.x_advance for p in positions) / upem
    # 每个字形（含末字形）都加 0.25px letter_spacing，与 Flet/Skia 渲染对齐
    return raw_advance + len(positions) * _FLET_DEFAULT_LETTER_SPACING


def _measure_uncached(text: str, font_family: str, size: int) -> float:
    """无缓存测量：FONT_MAIN 或无 CJK 时直接整形；否则按 CJK 边界切分回退主中文字体。

    CJK 回退与原 Pillow 实现一致：请求字体（如 Consolas）不含 CJK 字形时，
    HarfBuzz 报告 .notdef（advance=0），而 Skia 渲染回退到系统 CJK 字体（约 1em/字）。
    此处将 CJK 片段改用 FONT_MAIN 测量，贴合 Skia 实际渲染宽度。
    """
    if font_family == FONT_MAIN or not _CJK_RE.search(text):
        return _hb_shape_width(text, font_family, size)
    total = 0.0
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            total += _hb_shape_width(text[pos:m.start()], font_family, size)
        total += _hb_shape_width(m.group(), FONT_MAIN, size)
        pos = m.end()
    if pos < len(text):
        total += _hb_shape_width(text[pos:], font_family, size)
    return total


def measure_text_width(text: str, font_family: str, size: int) -> float:
    """测量文本在指定字体/字号下的像素宽度（与 Skia 渲染一致）。

    返回值为 Flet 逻辑像素宽度。使用 HarfBuzz 整形（与 Flutter/Skia 同引擎），
    保证光标定位与渲染层 TextSpan 像素级对齐——修复 Pillow getlength 截断 advance
    导致数字行光标偏移（每数字 0.2px 累积）的问题。

    单字符结果缓存于 _char_width_cache，行内逐字符累加场景下首次外为 O(1) 查表。
    需要逐字符光标偏移时优先用 measure_text_offsets（cluster 级，含 kerning）。
    """
    if not text:
        return 0.0
    if len(text) == 1:
        key = (text, font_family, size)
        cached = _char_width_cache.get(key)
        if cached is not None:
            return cached
        width = _measure_uncached(text, font_family, size)
        _char_width_cache[key] = width
        return width
    return _measure_uncached(text, font_family, size)


def measure_text_offsets(text: str, font_family: str, size: int) -> list[float]:
    """整形 text 返回逐字符光标 X 偏移（len(text)+1 个，与 Skia 渲染像素级一致）。

    返回 offsets[i] = 光标在字符偏移 i 处的 X（相对文本起点，含 kerning/连字）。
    比「逐字符 measure_text_width 累加」更准：后者不捕获字符间 kerning（如 "AV"
    整形宽度比 A+V 单字宽度和小 1.12px），导致光标在含 kerning 的文本上偏移。

    实现：HarfBuzz 整形整段文本后，按 glyph cluster（字符→字形映射）二分查找
    每个字符偏移对应的字形边界，取其前累计 advance。连字内部光标吸附到连字边界
    （标准行为）。RTL 文本假设簇单调（本编辑器以 LTR/CJK 为主）。
    """
    if not text:
        return [0.0]
    if len(text) == 1:
        # 单字符：直接查宽度缓存，offsets = [0, width]
        return [0.0, measure_text_width(text, font_family, size)]
    # CJK 回退：FONT_MAIN 直接整形；其它字体按 CJK 边界切分，CJK 片段换主中文字体
    if font_family == FONT_MAIN or not _CJK_RE.search(text):
        return _hb_cluster_offsets(text, font_family, size)
    # 混合 CJK：逐段整形后按段拼接偏移（CJK 段用 FONT_MAIN，非 CJK 段用原字体）
    offsets: list[float] = [0.0]
    acc = 0.0
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            sub = text[pos:m.start()]
            sub_off = _hb_cluster_offsets(sub, font_family, size)
            for o in sub_off[1:]:
                offsets.append(acc + o)
            acc += sub_off[-1]
        sub = m.group()
        sub_off = _hb_cluster_offsets(sub, FONT_MAIN, size)
        for o in sub_off[1:]:
            offsets.append(acc + o)
        acc += sub_off[-1]
        pos = m.end()
    if pos < len(text):
        sub = text[pos:]
        sub_off = _hb_cluster_offsets(sub, font_family, size)
        for o in sub_off[1:]:
            offsets.append(acc + o)
        acc += sub_off[-1]
    return offsets


def _hb_cluster_offsets(text: str, font_family: str, size: int) -> list[float]:
    """HarfBuzz 整形单段文本，返回 cluster 级光标偏移（不含 CJK 回退切分）。

    每个字形 advance 加 _FLET_DEFAULT_LETTER_SPACING（0.25px）补偿 Flet Text 默认
    letter_spacing=0.25——Skia 渲染时每个字形（含末字形）都加 0.25px，与字符 advance
    线性叠加，故按字形数补偿即可对齐。
    """
    pair = _get_hb_font(font_family, size)
    if pair is None:
        return [0.0] * (len(text) + 1)
    font, upem = pair
    buf = _hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    _hb.shape(font, buf)
    infos = buf.glyph_infos
    positions = buf.glyph_positions
    # 累计 advance：glyph_cum[i] = 第 i 个字形前的累计 X（含 letter_spacing 补偿）
    glyph_cum = [0.0]
    acc = 0.0
    for p in positions:
        acc += p.x_advance / upem + _FLET_DEFAULT_LETTER_SPACING
        glyph_cum.append(acc)
    clusters = [info.cluster for info in infos]
    n = len(text)
    # 对每个字符偏移 i，二分找首个 cluster >= i 的字形，光标 X = 其前累计 advance
    offsets = [0.0] * (n + 1)
    for char_off in range(n + 1):
        lo, hi = 0, len(clusters)
        while lo < hi:
            mid = (lo + hi) // 2
            if clusters[mid] < char_off:
                lo = mid + 1
            else:
                hi = mid
        offsets[char_off] = glyph_cum[lo]
    return offsets


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
