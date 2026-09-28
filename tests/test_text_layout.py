"""utils/text_layout 单元测试。

覆盖 measure_text_width / measure_text_offsets 缓存与单调性、
image_fit_size 缩放与缓存、clear_text_layout_cache。
HarfBuzz 需字体文件存在；缺失时跳过相关断言。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from utils.text_layout import (  # noqa: E402
    FONT_MAIN,
    FONT_MONO,
    _FLET_DEFAULT_LETTER_SPACING,
    _FONT_FILES,
    _IMG_MAX,
    _font_covers_cjk,
    _hb_shape_width,
    clear_text_layout_cache,
    image_fit_size,
    measure_text_offsets,
    measure_text_width,
    resolve_image_src,
)


# 字体文件是否可用（决定能否做正值断言）
_FONT_AVAILABLE = os.path.exists(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts", "AlibabaPuHuiTi-3-55-Regular.otf",
    )
)
# FONT_MONO 也改成随包分发的字体文件，需单独判定
_MONO_FONT_AVAILABLE = os.path.exists(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts", "NotoSansMonoCJKsc-Regular.otf",
    )
)
skip_no_font = pytest.mark.skipif(not _FONT_AVAILABLE, reason="Alibaba 字体文件缺失")
skip_no_mono_font = pytest.mark.skipif(
    not _MONO_FONT_AVAILABLE, reason="NotoSansMonoCJKsc 字体文件缺失"
)


# ---------------- measure_text_width ----------------
def test_measure_empty_text_zero():
    assert measure_text_width("", FONT_MAIN, 16) == 0.0


@skip_no_font
def test_measure_single_char_positive():
    assert measure_text_width("a", FONT_MAIN, 16) > 0.0


@skip_no_font
def test_measure_single_char_cached():
    """单字符宽度缓存：重复调用返回同值。"""
    clear_text_layout_cache()
    w1 = measure_text_width("x", FONT_MAIN, 16)
    w2 = measure_text_width("x", FONT_MAIN, 16)
    assert w1 == w2


@skip_no_font
def test_measure_multi_char_positive():
    assert measure_text_width("hello", FONT_MAIN, 16) > 0.0


@skip_no_font
def test_measure_multi_char_cached():
    clear_text_layout_cache()
    w1 = measure_text_width("hello", FONT_MAIN, 16)
    w2 = measure_text_width("hello", FONT_MAIN, 16)
    assert w1 == w2


@skip_no_font
def test_measure_cjk_positive():
    assert measure_text_width("你好", FONT_MAIN, 16) > 0.0


@skip_no_mono_font
def test_measure_mono_font_positive():
    """FONT_MONO（NotoSansMonoCJKsc，随包分发）测量拉丁字符。"""
    assert measure_text_width("abc", FONT_MONO, 14) > 0.0


@skip_no_mono_font
def test_measure_mono_is_strictly_halfwidth_for_latin():
    """FONT_MONO 的拉丁字宽严格等于 0.5em —— 等宽栅格的基石。

    容差取 1e-4：`_hb_shape_width` 对「设计单位 × size」的大整数求和后再除以 upem，
    浮点除法会带出 ~1e-4 量级的末位误差（实测 10 字符 80.00125 vs 80.0），
    与字体设计值无关，不影响像素级光标对齐。容差再收紧就会变成脆断言。

    代码块的列对齐、行号列宽估算、不换行模式的宽度撑开都依赖该比例。
    """
    for size in (12, 16, 20):
        expected = size * 10 * 0.5 + 10 * 0.25  # 10 字形 × 0.5em + 字距补偿
        assert measure_text_width("abcdefghij", FONT_MONO, size) == pytest.approx(
            expected, abs=1e-4
        ), "拉丁字宽不是 0.5em，等宽栅格被破坏"


@skip_no_mono_font
def test_measure_mono_cjk_is_fullwidth_and_has_glyphs():
    """FONT_MONO 自带 CJK 字形且为整字宽（1.0em）—— 中文注释不再触发字体回退。

    这正是把 FONT_MONO 从 Consolas 换成 NotoSansMonoCJKsc 的目的：Consolas 无
    CJK 字形，中文会走 Skia 回退链落到一个非等宽字体上，代码块里的中文注释既不
    对齐、行盒也更高（25px vs 24px，会让叠层编辑框的光标逐行漂移）。

    期望值含 `_FLET_DEFAULT_LETTER_SPACING`（0.25/字形）：测量端要补偿 Flet
    `TextStyle.letter_spacing` 的默认值才能和 Skia 实际渲染宽度对齐。
    """
    ls = _FLET_DEFAULT_LETTER_SPACING
    # 2 个汉字 = 2 × 1.0em + 2 × 字距
    assert measure_text_width("中文", FONT_MONO, 16) == pytest.approx(
        16 * 2 + 2 * ls, abs=1e-3
    )
    cjk = measure_text_width("汉", FONT_MONO, 16)
    latin = measure_text_width("a", FONT_MONO, 16)
    assert cjk > latin * 1.9, "CJK 字形疑似缺失（回退到 .notdef 宽度）"
    # 全角 : 半角 = 2:1 —— 中英混排因此仍落在同一等宽栅格上
    assert measure_text_width("中文ab", FONT_MONO, 16) == pytest.approx(
        16 * 2 + 8 * 2 + 4 * ls, abs=1e-3
    ), "中英混排宽度不等于逐字求和（栅格被破坏）"


@skip_no_mono_font
def test_mono_cjk_is_not_routed_to_proportional_main_font():
    """自带 CJK 字形的字体族**不得**被送进 CJK 回退分支（否则测量比渲染宽）。

    这是本轮换字体时踩到的真实陷阱：回退判据原先是 `font_family == FONT_MAIN`，
    于是中文一律改用 FONT_MAIN（Alibaba，比例字体：中文 15.744px / 拉丁 9.792px @16）
    测量，而实际渲染用的是 NotoSansMonoCJKsc（中文 16.0 / 拉丁 8.0）——
    测量比渲染**宽 0.256px/汉字**，代码块光标随中文注释长度累积右偏，
    且等宽栅格被破坏。判据改为逐字体探测 CJK 覆盖率后两者一致。
    """
    assert _font_covers_cjk(FONT_MONO), "NotoSansMonoCJKsc 应被探测为覆盖 CJK"
    assert _font_covers_cjk(FONT_MAIN), "Alibaba 应被探测为覆盖 CJK"

    # 自带 CJK 的字体：测量 = 直接整形（不切段、不换字体）
    direct = _hb_shape_width("中文ab", FONT_MONO, 16)
    assert measure_text_width("中文ab", FONT_MONO, 16) == pytest.approx(direct, abs=1e-6)
    # 逐字符字距也必须是整字宽（1.0em / 0.5em），不得出现 Alibaba 的 15.744 / 9.792
    offsets = measure_text_offsets("中文ab", FONT_MONO, 16)
    assert offsets[1] - offsets[0] == pytest.approx(16 + _FLET_DEFAULT_LETTER_SPACING, abs=1e-3)
    assert offsets[3] - offsets[2] == pytest.approx(8 + _FLET_DEFAULT_LETTER_SPACING, abs=1e-3)


@skip_no_font
def test_measure_clear_cache_then_remeasure_same():
    clear_text_layout_cache()
    w1 = measure_text_width("clear test", FONT_MAIN, 16)
    clear_text_layout_cache()
    w2 = measure_text_width("clear test", FONT_MAIN, 16)
    assert w1 == w2  # 清缓存后重测应一致


# ---------------- 字体注册表一致性 ----------------
def test_styles_and_text_layout_font_constants_agree():
    """`styles.FONT_*` 与 `utils.text_layout.FONT_*` 必须逐字相同。

    两者是各自独立声明的（为避免循环依赖，text_layout 不 import styles）。
    一旦漂移，渲染层用 styles 的族名、测量层查 text_layout 的路径表，
    `_FONT_FILES` 就会查不到该族 → 测量静默返回 0.0，光标 X 全错。
    """
    import styles

    assert styles.FONT_MAIN == FONT_MAIN
    assert styles.FONT_MONO == FONT_MONO


def test_every_registered_font_family_has_a_measurable_file():
    """`main.page.fonts` 注册的每个族都要能在 `_FONT_FILES` 里找到**存在的文件**。

    注册（渲染侧）与路径表（测量侧）是两处独立维护的清单，漏一处即静默退化：
    - 只在 page.fonts 注册、_FONT_FILES 没有 → 测量拿不到 Face，返回 0.0；
    - 只在 _FONT_FILES 有、page.fonts 没注册 → Flet 回退到系统默认字体，
      渲染宽度与测量宽度不再一致。
    字体文件本身缺失（打包漏带）也由本测试当场拦住。
    """
    import ast
    import inspect
    import re

    import main as entry

    src = inspect.getsource(entry.main)
    m = re.search(r"page\.fonts\s*=\s*\{(.*?)\}", src, re.S)
    assert m, "main.py 里找不到 page.fonts 赋值"
    registered = [k.value for k in ast.parse("{" + m.group(1) + "}", mode="eval").body.keys]

    for family in registered:
        assert family in _FONT_FILES, f"{family} 未登记进 utils.text_layout._FONT_FILES"
        assert os.path.exists(_FONT_FILES[family]), (
            f"{family} 的字体文件不存在：{_FONT_FILES[family]}"
        )


def test_mono_font_file_is_not_a_system_font():
    """FONT_MONO 必须指向**随包分发**的字体文件，不得依赖系统字体。

    代码块用系统等宽字体（原为 `C:\\Windows\\Fonts\\consola.ttf`）有两个真实问题：
    1. 换平台/精简系统上文件可能不存在，测量静默归零；
    2. 系统等宽字体普遍不含 CJK，中文注释走 Skia 回退链落到非等宽字体上，
       列对齐被破坏。
    """
    path = _FONT_FILES[FONT_MONO]
    assert "assets" in path, f"FONT_MONO 应指向随包字体，实际是 {path}"
    assert not path.lower().startswith(("c:\\windows", "/usr/", "/system")), (
        f"FONT_MONO 仍指向系统字体：{path}"
    )


@skip_no_font
def test_measure_longer_text_wider():
    a = measure_text_width("a", FONT_MAIN, 16)
    ab = measure_text_width("ab", FONT_MAIN, 16)
    assert ab >= a  # 多字符不窄于单字符（含 letter_spacing 补偿）


# ---------------- measure_text_offsets ----------------
def test_offsets_empty_text():
    assert measure_text_offsets("", FONT_MAIN, 16) == [0.0]


def test_offsets_single_char_two_entries():
    """单字符返回 [0.0, width] 两个偏移。"""
    offsets = measure_text_offsets("a", FONT_MAIN, 16)
    assert len(offsets) == 2
    assert offsets[0] == 0.0


@skip_no_font
def test_offsets_length_n_plus_1():
    text = "hello"
    offsets = measure_text_offsets(text, FONT_MAIN, 16)
    assert len(offsets) == len(text) + 1


@skip_no_font
def test_offsets_monotonic_non_decreasing():
    """偏移序列单调非递减（光标 X 不回退）。"""
    offsets = measure_text_offsets("hello world", FONT_MAIN, 16)
    for i in range(len(offsets) - 1):
        assert offsets[i] <= offsets[i + 1]


@skip_no_font
def test_offsets_first_zero_last_equals_width():
    text = "measure"
    offsets = measure_text_offsets(text, FONT_MAIN, 16)
    width = measure_text_width(text, FONT_MAIN, 16)
    assert offsets[0] == 0.0
    assert offsets[-1] == width


@skip_no_font
def test_offsets_cached_returns_copy():
    """缓存命中返回副本，外部篡改不影响缓存。"""
    clear_text_layout_cache()
    o1 = measure_text_offsets("cache", FONT_MAIN, 16)
    o1.append(999.0)
    o2 = measure_text_offsets("cache", FONT_MAIN, 16)
    assert 999.0 not in o2


# ---------------- resolve_image_src ----------------
def test_resolve_image_src_url_passthrough():
    """URL / data URI / file:// 原样返回，不基于文档目录解析。"""
    assert resolve_image_src("https://a.com/b.png", "C:/docs/note.md") == "https://a.com/b.png"
    assert resolve_image_src("http://a.com/b.png", "C:/docs/note.md") == "http://a.com/b.png"
    assert resolve_image_src("data:image/png;base64,xxxx", "C:/docs/note.md") == "data:image/png;base64,xxxx"
    assert resolve_image_src("file:///C:/docs/b.png", "C:/docs/note.md") == "file:///C:/docs/b.png"


def test_resolve_image_src_absolute_path_passthrough(tmp_path):
    """绝对路径原样返回。"""
    abs_p = str(tmp_path / "img.png")
    assert resolve_image_src(abs_p, "C:/docs/note.md") == abs_p


def test_resolve_image_src_relative_resolves_against_doc_dir(tmp_path):
    """相对路径基于文档所在目录解析为绝对路径。"""
    doc = tmp_path / "note.md"
    doc.write_text("x")
    resolved = resolve_image_src("assets/image-1.png", str(doc))
    expected = os.path.normpath(os.path.join(str(tmp_path), "assets", "image-1.png"))
    assert resolved == expected


def test_resolve_image_src_relative_subdir(tmp_path):
    """相对路径含子目录时正确拼接。"""
    doc = tmp_path / "note.md"
    doc.write_text("x")
    resolved = resolve_image_src("assets/sub/img.png", str(doc))
    expected = os.path.normpath(os.path.join(str(tmp_path), "assets", "sub", "img.png"))
    assert resolved == expected


def test_resolve_image_src_no_file_path_passthrough():
    """file_path 为 None（未保存文档）→ 相对路径原样返回（向后兼容）。"""
    assert resolve_image_src("assets/image-1.png", None) == "assets/image-1.png"
    assert resolve_image_src("assets/image-1.png", "") == "assets/image-1.png"


def test_resolve_image_src_empty_url():
    """空 url 原样返回。"""
    assert resolve_image_src("", "C:/docs/note.md") == ""


def test_resolve_image_src_normalizes_path(tmp_path):
    """解析结果经过 normpath，消除 ./ ../ 等冗余。"""
    doc = tmp_path / "note.md"
    doc.write_text("x")
    resolved = resolve_image_src("./assets/../assets/img.png", str(doc))
    expected = os.path.normpath(os.path.join(str(tmp_path), "assets", "img.png"))
    assert resolved == expected


# ---------------- image_fit_size ----------------
def test_image_fit_size_invalid_src_returns_none(tmp_path):
    w, h = image_fit_size(str(tmp_path / "nope.png"))
    assert (w, h) == (None, None)


def test_image_fit_size_small_image_keeps_original(tmp_path):
    """小图（<=max_size）保持原尺寸。"""
    try:
        from PIL import Image as PILImage
    except ImportError:
        pytest.skip("Pillow 缺失")
    p = tmp_path / "small.png"
    PILImage.new("RGB", (100, 80), "red").save(p)
    w, h = image_fit_size(str(p), max_size=500)
    assert (w, h) == (100, 80)


def test_image_fit_size_large_image_scaled(tmp_path):
    """大图等比缩放到 max_size。"""
    try:
        from PIL import Image as PILImage
    except ImportError:
        pytest.skip("Pillow 缺失")
    p = tmp_path / "large.png"
    PILImage.new("RGB", (1000, 500), "blue").save(p)
    w, h = image_fit_size(str(p), max_size=500)
    assert w == 500  # 宽边缩到 500
    assert h == 250  # 高度等比


def test_image_fit_size_tall_image_scaled(tmp_path):
    """高图（高 > 宽）缩放：高度边到 max_size。"""
    try:
        from PIL import Image as PILImage
    except ImportError:
        pytest.skip("Pillow 缺失")
    p = tmp_path / "tall.png"
    PILImage.new("RGB", (400, 800), "green").save(p)
    w, h = image_fit_size(str(p), max_size=400)
    assert h == 400
    assert w == 200


def test_image_fit_size_cached(tmp_path):
    """同 src 二次调用命中缓存（不重复 IO）。"""
    try:
        from PIL import Image as PILImage
    except ImportError:
        pytest.skip("Pillow 缺失")
    p = tmp_path / "cached.png"
    PILImage.new("RGB", (50, 50), "yellow").save(p)
    r1 = image_fit_size(str(p))
    # 删除源文件后仍能返回缓存值
    p.unlink()
    r2 = image_fit_size(str(p))
    assert r1 == r2


def test_image_max_default():
    assert _IMG_MAX == 500


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
