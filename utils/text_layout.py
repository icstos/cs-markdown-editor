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
from collections import OrderedDict

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

# 多字符文本测量 LRU 缓存：(text, font_family, size) -> 测量结果
# width_cache 缓存 float；offsets_cache 缓存 tuple[float, ...]
# 两者分开存储：同一 (text, font, size) 可能被 measure_text_width 和
# measure_text_offsets 同时查询，但返回类型不同，混用会类型冲突。
# 激活行重渲染时非激活段文本不变，命中缓存跳过 HarfBuzz 整形（O(1) 查表）。
# 单字符仍走 _char_width_cache（独立小缓存，命中率更高，避免被多字符 LRU 淘汰）。
_MEASURE_CACHE_MAXLEN = 4096          # 条目数上限（~3MB 内存）
_MEASURE_CACHE_MAX_TEXT_LEN = 256     # 单条文本长度上限（超长段落不缓存）
_width_cache: OrderedDict[tuple[str, str, int], float] = OrderedDict()
_offsets_cache: OrderedDict[tuple[str, str, int], tuple[float, ...]] = OrderedDict()


def _width_cache_get(key: tuple[str, str, int]) -> float | None:
    val = _width_cache.get(key)
    if val is not None:
        _width_cache.move_to_end(key)
    return val


def _width_cache_put(key: tuple[str, str, int], val: float) -> None:
    _width_cache[key] = val
    if len(_width_cache) > _MEASURE_CACHE_MAXLEN:
        _width_cache.popitem(last=False)


def _offsets_cache_get(key: tuple[str, str, int]) -> tuple[float, ...] | None:
    val = _offsets_cache.get(key)
    if val is not None:
        _offsets_cache.move_to_end(key)
    return val


def _offsets_cache_put(key: tuple[str, str, int], val: tuple[float, ...]) -> None:
    _offsets_cache[key] = val
    if len(_offsets_cache) > _MEASURE_CACHE_MAXLEN:
        _offsets_cache.popitem(last=False)


def clear_text_layout_cache() -> None:
    """清空所有文本测量缓存（字体文件变更、主题切换时调用）。"""
    _char_width_cache.clear()
    _width_cache.clear()
    _offsets_cache.clear()


def _get_hb_font(font_family: str, size: int) -> tuple[_hb.Font, int] | None:
    """按 (字体族, 字号) 缓存加载 HarfBuzz Font；Face 仅解析一次。

    核心算法原理：
    - scale = size * upem：HarfBuzz 内部用 26.6 定点（int32 << 6）存储 advance，
      字体原始 advance 单位为「设计单位 / upem」。若 scale = size，x_advance 会
      经过 /64 二次舍入（如 575 设计单位 × 16 / 64 = 143.75 → 截断 143）；
      改用 scale = size × upem 后，x_advance = 设计单位 × size（upem 倍整数），
      消除 26.6 定点舍入误差，与 Skia 的 FreeType+HarfBuzz 计算路径完全一致。
    - ot_font_set_funcs：默认 Font 未绑定 OpenType 字形数据回调，shape 只能产
      .notdef（advance=0）。该函数注入 hmtx/glyf/CFF 读取器，使 shape 能查
      hmtx（横向 advance）、GSUB（连字替换如 fi→ﬁ）、GPOS（kerning 位置调整），
      与 Skia 在 Flutter TextPainter 中的整形配置一致。
    - guess_segment_properties 由调用方在 Buffer 上设置（推断 script/direction），
      否则 GSUB/GPOS 不应用（缺脚本标签无法查表）。

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
    # scale = size × upem：使 x_advance 为「设计单位 × size」整数，消除 26.6 定点舍入
    font.scale = (size * upem, size * upem)
    # 注入 OpenType 字形读取器：启用 hmtx/GSUB/GPOS 整形（kern/liga/calt）
    _hb.ot_font_set_funcs(font)
    _hb_fonts[key] = (font, upem)
    return (font, upem)


def _hb_shape_width(text: str, font_family: str, size: int) -> float:
    """用 HarfBuzz 整形 text，返回像素 advance 总宽（含 Flet letter_spacing 补偿）。

    核心算法原理：
    - 单位还原：scale = size × upem 下 x_advance 单位为「设计单位 × size」（upem 倍
      整数），除以 upem 还原为「设计单位 × size / upem」= 像素宽度（因为字体设计
      单位 = 1/upem em，size px = 1 em，故 设计单位 × size / upem = 像素）。
      与 Skia 在同字号下渲染 advance 一致（同走 FreeType+HarfBuzz）。
    - letter_spacing 末字形补偿：Flutter TextPainter 对每个字形（含末字形）的
      advance 都加 letterSpacing（text width = Σ(advance + letterSpacing)）。
      实验验证：'1' 单字符 Flet 渲染 9.45 = HB advance 9.20 + 0.25（末字形也加）。
      故补偿项为 len(positions) × 0.25，而非 (len-1) × 0.25。
    - 连字影响：'fi' 整形为单字形时 positions 长度为 1，比逐字符测量少加 0.25px，
      这与 Skia 渲染一致（连字整体作为一个 advance 单元）。
    """
    pair = _get_hb_font(font_family, size)
    if pair is None:
        return 0.0
    font, upem = pair
    buf = _hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()  # 推断 script/direction/language，启用 GSUB/GPOS
    _hb.shape(font, buf)  # 整形：应用 GSUB 替换 + GPOS 位置调整，输出字形序列
    positions = buf.glyph_positions
    # x_advance 单位还原：设计单位 × size → 像素（除以 upem）
    raw_advance = sum(p.x_advance for p in positions) / upem
    # 末字形也加 0.25px：Flutter TextPainter 对所有字形 advance 加 letterSpacing
    return raw_advance + len(positions) * _FLET_DEFAULT_LETTER_SPACING


def _measure_uncached(text: str, font_family: str, size: int) -> float:
    """无缓存测量：FONT_MAIN 或无 CJK 时直接整形；否则按 CJK 边界切分回退主中文字体。

    核心算法原理（CJK 回退）：
    - 缺字现象：请求字体（如 Consolas）不含 CJK 字形时，HarfBuzz 输出 .notdef
      （glyph id=0），其 advance 通常为字体 cmap 中 .notdef 的宽度（多为 0 或
      一固定值），与实际渲染宽度严重不符。
    - Skia 渲染回退：Flutter 的 TextPainter 在 FontCollection 中查找字形时，若
      主字体缺失会按 fallback 链回退到系统 CJK 字体（Microsoft YaHei 等），
      CJK 字符实际渲染宽度约 1em（size px）。
    - 切分策略：按 _CJK_RE 边界将文本切为「非 CJK 段 + CJK 段」交替序列，
      非 CJK 段用请求字体整形（捕获拉丁字符间 kerning），CJK 段统一用 FONT_MAIN
      （Alibaba，含完整 CJK 字形）测量，模拟 Skia 回退行为。
    - 段间 kerning 损失：CJK 与拉丁字符之间通常无 GPOS kerning 表，实验验证
      '中1文2' HB 切分累加 = Flet 渲染（差 4×0.25 letter_spacing 补偿后归零）。
    """
    if font_family == FONT_MAIN or not _CJK_RE.search(text):
        return _hb_shape_width(text, font_family, size)
    total = 0.0
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            # 非 CJK 段：用请求字体整形（保留拉丁字符间 kerning）
            total += _hb_shape_width(text[pos:m.start()], font_family, size)
        # CJK 段：换主中文字体测量（模拟 Skia 字体回退）
        total += _hb_shape_width(m.group(), FONT_MAIN, size)
        pos = m.end()
    if pos < len(text):
        # 尾部非 CJK 段
        total += _hb_shape_width(text[pos:], font_family, size)
    return total


def measure_text_width(text: str, font_family: str, size: int) -> float:
    """测量文本在指定字体/字号下的像素宽度（与 Skia 渲染一致）。

    返回值为 Flet 逻辑像素宽度。使用 HarfBuzz 整形（与 Flutter/Skia 同引擎），
    保证光标定位与渲染层 TextSpan 像素级对齐——修复 Pillow getlength 截断 advance
    导致数字行光标偏移（每数字 0.2px 累积）的问题。

    缓存策略：
    - 单字符：_char_width_cache（独立小缓存，逐字符累加高频场景命中率高）
    - 多字符（≤256 字符）：_measure_cache LRU（激活行重渲染时非激活段文本不变，
      命中缓存跳过 HarfBuzz 整形，O(1) 查表）
    - 超长文本（>256 字符）：不缓存（避免单条占用过大内存；代码块走 CodeEditor
      独立路径，不经过此函数）
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
    # 多字符：LRU 缓存（文本长度上限 _MEASURE_CACHE_MAX_TEXT_LEN）
    if len(text) <= _MEASURE_CACHE_MAX_TEXT_LEN:
        key = (text, font_family, size)
        cached = _width_cache_get(key)
        if cached is not None:
            return cached
        width = _measure_uncached(text, font_family, size)
        _width_cache_put(key, width)
        return width
    return _measure_uncached(text, font_family, size)


def measure_text_offsets(text: str, font_family: str, size: int) -> list[float]:
    """整形 text 返回逐字符光标 X 偏移（len(text)+1 个，与 Skia 渲染像素级一致）。

    核心算法原理：
    - offsets[i] = 光标在字符偏移 i 处的 X（相对文本起点，含 kerning/连字）。
    - 为什么不能用「逐字符 measure_text_width 累加」：后者对每个字符单独整形，
      丢失字符间 GPOS kerning（如 "AV" 整形宽度 18.30 比 A 单字 9.55 + V 单字
      8.75 = 18.30 → 实际有 kerning 调整 -0.0；但 "AVAIL" 中 V→A 有 kerning
      +10.67-8.75=+1.92，逐字符累加会偏 1.92px）。cluster 级一次整形整段文本，
      完整捕获所有 kerning/连字位置调整。
    - cluster 概念：HarfBuzz 整形后每个字形记录 info.cluster = 该字形覆盖的
      起始字符偏移。1 字符 → 1 字形时 cluster 即字符索引；连字（如 "fi"→ﬁ）
      多字符 → 1 字形时 cluster = 起始字符索引；变音符号组合时多字形共享 cluster。
    - 二分查找：对字符偏移 i，找「首个 cluster >= i 的字形位置 lo」，光标 X =
      glyph_cum[lo]（该字形前的累计 advance）。详见 _hb_cluster_offsets。
    - 连字光标吸附：'fi' 整形为 1 字形（cluster=0）时，char_off=0/1 都映射到
      glyph_cum[0]=0，char_off=2 映射到 glyph_cum[1]。光标在 'fi' 中间无法停留
      （标准行为：连字内部不可插入光标，左右键跳过整个连字）。
    - RTL 假设簇单调递增：本编辑器以 LTR/CJK 为主，未处理 RTL 复杂情况。
    """
    if not text:
        return [0.0]
    if len(text) == 1:
        # 单字符：直接查宽度缓存，offsets = [0, width]
        return [0.0, measure_text_width(text, font_family, size)]
    # 多字符：LRU 缓存（缓存 tuple 不可变，返回 list 副本防外部篡改）
    if len(text) <= _MEASURE_CACHE_MAX_TEXT_LEN:
        key = (text, font_family, size)
        cached = _offsets_cache_get(key)
        if cached is not None:
            return list(cached)
        offsets_tuple = tuple(_compute_offsets(text, font_family, size))
        _offsets_cache_put(key, offsets_tuple)
        return list(offsets_tuple)
    return _compute_offsets(text, font_family, size)


def _compute_offsets(text: str, font_family: str, size: int) -> list[float]:
    """多字符 offsets 计算内部函数（CJK 回退切分 + _hb_cluster_offsets 拼接）。

    被 measure_text_offsets 调用，独立出来便于 LRU 缓存包裹。
    """
    # CJK 回退：FONT_MAIN 直接整形；其它字体按 CJK 边界切分，CJK 片段换主中文字体
    if font_family == FONT_MAIN or not _CJK_RE.search(text):
        return _hb_cluster_offsets(text, font_family, size)
    # 混合 CJK：逐段整形后按段拼接偏移（CJK 段用 FONT_MAIN，非 CJK 段用原字体）
    # 各段独立整形，段间累计 acc 拼接，模拟 Skia 分段回退渲染
    offsets: list[float] = [0.0]
    acc = 0.0  # 段间累计 X 偏移
    pos = 0
    for m in _CJK_RE.finditer(text):
        if m.start() > pos:
            # 非 CJK 段：原字体整形，偏移叠加到 acc 上
            sub = text[pos:m.start()]
            sub_off = _hb_cluster_offsets(sub, font_family, size)
            for o in sub_off[1:]:  # 跳过 sub_off[0]=0（已含在 acc 中）
                offsets.append(acc + o)
            acc += sub_off[-1]
        # CJK 段：换主中文字体整形
        sub = m.group()
        sub_off = _hb_cluster_offsets(sub, FONT_MAIN, size)
        for o in sub_off[1:]:
            offsets.append(acc + o)
        acc += sub_off[-1]
        pos = m.end()
    if pos < len(text):
        # 尾部非 CJK 段
        sub = text[pos:]
        sub_off = _hb_cluster_offsets(sub, font_family, size)
        for o in sub_off[1:]:
            offsets.append(acc + o)
        acc += sub_off[-1]
    return offsets


def _hb_cluster_offsets(text: str, font_family: str, size: int) -> list[float]:
    """HarfBuzz 整形单段文本，返回 cluster 级光标偏移（不含 CJK 回退切分）。

    核心算法原理（cluster 二分查找）：
    - cluster 是 HarfBuzz 标记的「字符→字形」映射索引。整形后每个字形 info.cluster
      记录该字形覆盖的起始字符偏移（UTF-8 字节偏移，但 uharfbuzz 的 add_str 已转
      为码点偏移）。例：
        * "abc" 1:1 整形：clusters=[0,1,2]，3 字形各覆盖 1 字符
        * "fi" 连字（GSUB liga）整为 1 字形：clusters=[0]，1 字形覆盖 2 字符
        * "á" 组合（a + U+0301）整为 2 字形共享 cluster：clusters=[0,0]
    - glyph_cum[i] = 第 i 个字形前的累计 advance（含 letter_spacing 补偿）。
      i 范围 0..len(positions)，glyph_cum[0]=0，glyph_cum[-1]=文本总宽。
    - 二分查找语义：对字符偏移 i，找「首个 cluster >= i 的字形位置 lo」，
      光标 X = glyph_cum[lo]。原理：cluster[j] >= i 表示字形 j 覆盖的字符范围
      起点在 i 处或之后，即字形 j 是「光标在 i 处时遇到的第一个字形」，光标应
      位于该字形之前（其前累计 advance）。
    - 边界处理：
        * i=0：lo=0（首字形前），光标在文本起点
        * i=len(text)：lo=len(clusters)（所有字形之后），光标在文本末尾
        * 连字内部：'fi' clusters=[0]，i=1 时找 cluster>=1 → lo=1（=len），
          glyph_cum[1]=连字总宽，光标在 'fi' 末尾（中间不可停留）
    - letter_spacing 补偿：每个字形 advance 加 0.25px（含末字形），与 _hb_shape_width
      保持一致，否则 cluster 偏移与总宽不匹配。
    """
    pair = _get_hb_font(font_family, size)
    if pair is None:
        return [0.0] * (len(text) + 1)
    font, upem = pair
    buf = _hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()  # 推断 script/direction/language
    _hb.shape(font, buf)  # 整形：输出 glyph_infos（含 cluster）+ glyph_positions（含 x_advance）
    infos = buf.glyph_infos
    positions = buf.glyph_positions
    # 累计 advance：glyph_cum[i] = 第 i 个字形前的累计 X（含 letter_spacing 补偿）
    # 注意：每个字形 advance 加 0.25px（Flutter TextPainter 行为，含末字形）
    glyph_cum = [0.0]
    acc = 0.0
    for p in positions:
        acc += p.x_advance / upem + _FLET_DEFAULT_LETTER_SPACING
        glyph_cum.append(acc)
    # clusters[j] = 字形 j 覆盖的起始字符偏移（单调非递减，因为 LTR/CJK 整形）
    clusters = [info.cluster for info in infos]
    n = len(text)
    # 对每个字符偏移 i，二分找首个 cluster >= i 的字形位置 lo，光标 X = glyph_cum[lo]
    offsets = [0.0] * (n + 1)
    for char_off in range(n + 1):
        # 标准库 bisect_left 等价实现：在 clusters 中找首个 >= char_off 的位置
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
