"""混合格式（加粗+斜体+删除线+高亮等嵌套）toggle 测试。

参考 Typora 行为，验证 outward 选区按快捷键 toggle 行内格式时：
- 单一格式 toggle（wrap / unwrap）
- 混合格式嵌套（如 ***加粗斜体***）逐层 unwrap
- 子串冲突场景（`*` vs `**`、`~` vs `~~`）不误判
- 三重嵌套（==**~~三重~~**==）逐层移除
- 选区保持在内容上（不含标记）

Bug 根因（已修复）：旧 _apply_outward_wrap 用 raw 字符串匹配外侧/内侧标记，
对 `*`（斜体）与 `**`（加粗）、`~`（下标）与 `~~`（删除线）的子串关系误判：
在 `**加粗**` 上按 Ctrl+I，旧逻辑见 raw[a_off-1:a_off]=='*' 即误判为已包裹
斜体，unwrap 成 `*加粗*`（丢失加粗）。修复改为基于 Segment.marks 判断。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from models import SegType  # noqa: E402
from parser import parse_markdown  # noqa: E402
from utils.segment_helpers import WRAP_SYNTAX  # noqa: E402
from views.editor._inline_format import _compute_wrap_toggle  # noqa: E402


def _toggle(raw: str, a_off: int, b_off: int, fmt: str) -> tuple[str, tuple]:
    """便捷封装：解析 raw → 调用 _compute_wrap_toggle → 返回 (new_raw, new_sel)。"""
    seg_type = {
        "bold": SegType.STRONG,
        "italic": SegType.EMPHASIS,
        "highlight": SegType.HIGHLIGHT,
        "strike": SegType.STRIKE,
        "subscript": SegType.SUBSCRIPT,
    }[fmt]
    wo, wc = WRAP_SYNTAX[seg_type]
    doc = parse_markdown(raw)
    line = doc.lines[0]
    return _compute_wrap_toggle(line, a_off, b_off, seg_type, wo, wc)


# ================ 子串冲突修复（核心 Bug） ================

def test_bold_then_italic_adds_layer():
    """`**加粗**` 选中内容按 Ctrl+I → `***加粗***`（加粗+斜体，不丢失加粗）。

    旧 Bug：`*` 是 `**` 子串，raw[a_off-1:a_off]=='*' 误判已包裹斜体，
    unwrap 成 `*加粗*`（丢失加粗）。
    """
    new_raw, _ = _toggle("**加粗**", 2, 4, "italic")
    assert new_raw == "***加粗***"


def test_italic_then_bold_adds_layer():
    """`*斜体*` 选中内容按 Ctrl+B → `***斜体***`（斜体+加粗）。"""
    new_raw, _ = _toggle("*斜体*", 1, 3, "bold")
    assert new_raw == "***斜体***"


def test_strike_then_subscript_adds_layer():
    """`~~删除线~~` 选中内容按下标 → `~~~删除线~~~`（删除线+下标）。

    旧 Bug：`~` 是 `~~` 子串，误判已包裹下标，unwrap 成 `~删除线~`。
    """
    new_raw, _ = _toggle("~~删除线~~", 2, 5, "subscript")
    assert new_raw == "~~~删除线~~~"


# ================ 单一格式 toggle ================

def test_single_bold_unwrap():
    """`**加粗**` 选中内容按 Ctrl+B → `加粗`（取消加粗）。"""
    new_raw, _ = _toggle("**加粗**", 2, 4, "bold")
    assert new_raw == "加粗"


def test_single_italic_unwrap():
    """`*斜体*` 选中内容按 Ctrl+I → `斜体`（取消斜体）。"""
    new_raw, _ = _toggle("*斜体*", 1, 3, "italic")
    assert new_raw == "斜体"


def test_single_highlight_unwrap():
    """`==高亮==` 选中内容按 Ctrl+H → `高亮`（取消高亮）。"""
    new_raw, _ = _toggle("==高亮==", 2, 4, "highlight")
    assert new_raw == "高亮"


def test_single_strike_unwrap():
    """`~~删除线~~` 选中内容按 Ctrl+S → `删除线`（取消删除线）。"""
    new_raw, _ = _toggle("~~删除线~~", 2, 5, "strike")
    assert new_raw == "删除线"


def test_plain_text_wrap_bold():
    """普通文本按 Ctrl+B → `**普通文本**`（添加加粗）。"""
    new_raw, _ = _toggle("普通文本", 0, 4, "bold")
    assert new_raw == "**普通文本**"


# ================ 混合格式逐层 unwrap ================

def test_bold_italic_unwrap_italic():
    """`***加粗斜体***` 按 Ctrl+I → `**加粗斜体**`（移除斜体层，保留加粗）。"""
    new_raw, _ = _toggle("***加粗斜体***", 3, 7, "italic")
    assert new_raw == "**加粗斜体**"


def test_bold_italic_unwrap_bold():
    """`***加粗斜体***` 按 Ctrl+B → `*加粗斜体*`（移除加粗层，保留斜体）。"""
    new_raw, _ = _toggle("***加粗斜体***", 3, 7, "bold")
    assert new_raw == "*加粗斜体*"


def test_highlight_bold_unwrap_bold():
    """`==**高亮加粗**==` 按 Ctrl+B → `==高亮加粗==`（移除加粗层，保留高亮）。"""
    new_raw, _ = _toggle("==**高亮加粗**==", 4, 8, "bold")
    assert new_raw == "==高亮加粗=="


def test_bold_strike_unwrap_strike():
    """`**~~加粗删除~~**` 按 Ctrl+S → `**加粗删除**`（移除删除线层，保留加粗）。"""
    new_raw, _ = _toggle("**~~加粗删除~~**", 4, 8, "strike")
    assert new_raw == "**加粗删除**"


def test_italic_highlight_unwrap_italic():
    """`*==斜体高亮==*` 按 Ctrl+I → `==斜体高亮==`（移除斜体层，保留高亮）。"""
    new_raw, _ = _toggle("*==斜体高亮==*", 3, 7, "italic")
    assert new_raw == "==斜体高亮=="


# ================ 三重嵌套逐层 unwrap ================

def test_triple_unwrap_strike():
    """`==**~~三重~~**==` 按 Ctrl+S → `==**三重**==`（移除删除线层）。"""
    new_raw, _ = _toggle("==**~~三重~~**==", 6, 8, "strike")
    assert new_raw == "==**三重**=="


def test_triple_unwrap_bold():
    """`==**~~三重~~**==` 按 Ctrl+B → `==~~三重~~==`（移除加粗层）。"""
    new_raw, _ = _toggle("==**~~三重~~**==", 6, 8, "bold")
    assert new_raw == "==~~三重~~=="


def test_triple_unwrap_highlight():
    """`==**~~三重~~**==` 按 Ctrl+H → `**~~三重~~**`（移除高亮层）。"""
    new_raw, _ = _toggle("==**~~三重~~**==", 6, 8, "highlight")
    assert new_raw == "**~~三重~~**"


# ================ 选区保持 ================

def test_wrap_selection_stays_on_content():
    """wrap 后选区保持在内容上（不含标记）。"""
    new_raw, new_sel = _toggle("普通文本", 0, 4, "bold")
    # new_raw = `**普通文本**`，选区应在 `普通文本` 上，即 [2, 6)
    assert new_sel == (0, 2, 0, 6)


def test_unwrap_selection_stays_on_content():
    """unwrap 后选区保持在内容上（与 wrap 一致）。"""
    new_raw, new_sel = _toggle("***加粗斜体***", 3, 7, "italic")
    # new_raw = `**加粗斜体**`，选区应在 `加粗斜体` 上，即 [2, 6)
    assert new_sel == (0, 2, 0, 6)


def test_triple_unwrap_selection_stays_on_content():
    """三重嵌套 unwrap 后选区保持在内容上。"""
    new_raw, new_sel = _toggle("==**~~三重~~**==", 6, 8, "strike")
    # new_raw = `==**三重**==`，内容 `三重` 在 [4, 6)
    assert new_sel == (0, 4, 0, 6)


# ================ 句中混合格式 ================

def test_mixed_in_sentence_wrap():
    """句中混合格式 wrap：`前文**加粗**后文` 选中加粗内容按 Ctrl+I。"""
    new_raw, _ = _toggle("前文**加粗**后文", 4, 6, "italic")
    assert new_raw == "前文***加粗***后文"


def test_mixed_in_sentence_unwrap():
    """句中混合格式 unwrap：`前文***加粗斜体***后文` 选中内容按 Ctrl+B。"""
    new_raw, _ = _toggle("前文***加粗斜体***后文", 5, 9, "bold")
    assert new_raw == "前文*加粗斜体*后文"


# ================ 连续 toggle 流程（Typora 式） ================

def test_consecutive_toggle_bold_then_italic():
    """连续 toggle：选中文本 → Ctrl+B → Ctrl+I 产生加粗+斜体。

    Typora 式工作流：选中 `加粗`，Ctrl+B 变 `**加粗**`（选区在内容），
    再 Ctrl+I 应在加粗段上添加斜体层 → `***加粗***`。
    """
    # 第一次 Ctrl+B
    new_raw1, sel1 = _toggle("加粗", 0, 2, "bold")
    assert new_raw1 == "**加粗**"
    # 模拟 reparse 后再次 toggle（选区平移到新 raw 中的内容位置）
    doc2 = parse_markdown(new_raw1)
    line2 = doc2.lines[0]
    # sel1 = (0, 2, 0, 4)，在 `**加粗**` 中内容 `加粗` 在 [2, 4)
    new_raw2, _ = _compute_wrap_toggle(
        line2, sel1[1], sel1[3], SegType.EMPHASIS, "*", "*",
    )
    assert new_raw2 == "***加粗***"


def test_consecutive_toggle_unwrap_both_layers():
    """连续 unwrap：`***加粗斜体***` → Ctrl+I → Ctrl+B → 纯文本。"""
    # 第一次 Ctrl+I（移除斜体）
    new_raw1, sel1 = _toggle("***加粗斜体***", 3, 7, "italic")
    assert new_raw1 == "**加粗斜体**"
    # 第二次 Ctrl+B（移除加粗）
    doc2 = parse_markdown(new_raw1)
    line2 = doc2.lines[0]
    new_raw2, _ = _compute_wrap_toggle(
        line2, sel1[1], sel1[3], SegType.STRONG, "**", "**",
    )
    assert new_raw2 == "加粗斜体"
