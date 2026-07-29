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
    _IMG_MAX,
    clear_text_layout_cache,
    image_fit_size,
    measure_text_offsets,
    measure_text_width,
)


# 字体文件是否可用（决定能否做正值断言）
_FONT_AVAILABLE = os.path.exists(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets", "fonts", "AlibabaPuHuiTi-3-55-Regular.otf",
    )
)
skip_no_font = pytest.mark.skipif(not _FONT_AVAILABLE, reason="Alibaba 字体文件缺失")


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


@skip_no_font
def test_measure_mono_font_positive():
    """Consolas 字体（Windows）测量拉丁字符。"""
    consolas_exists = os.path.exists(r"C:\Windows\Fonts\consola.ttf")
    if not consolas_exists:
        pytest.skip("Consolas 字体缺失")
    assert measure_text_width("abc", FONT_MONO, 14) > 0.0


@skip_no_font
def test_measure_clear_cache_then_remeasure_same():
    clear_text_layout_cache()
    w1 = measure_text_width("clear test", FONT_MAIN, 16)
    clear_text_layout_cache()
    w2 = measure_text_width("clear test", FONT_MAIN, 16)
    assert w1 == w2  # 清缓存后重测应一致


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
