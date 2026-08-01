"""图片操作工厂 _image.py 单元测试。

覆盖不依赖 UI 的纯逻辑：
- _fetch_image_bytes：本地路径 / data URI / 无效 src
- _image_extension：URL / data URI / 无扩展名回退
- _sanitize_filename：非法字符清理 / 截断 / 空回退
- build_image.delete：删除图片段后文档 raw 与段状态正确

依赖项：parser、models、views.editor._image。
不依赖 UI 层（ctx 用 stub 注入）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parse_markdown, reparse_line_atomic
from views.editor._image import (
    _fetch_image_bytes,
    _image_extension,
    _sanitize_filename,
    build_image,
)


# ---------------- _fetch_image_bytes ----------------
def test_fetch_image_bytes_local_path(tmp_path):
    p = tmp_path / "t.png"
    payload = b"\x89PNG\r\n\x1a\nfake-png-data"
    p.write_bytes(payload)
    assert _fetch_image_bytes(str(p)) == payload


def test_fetch_image_bytes_data_uri():
    import base64
    raw = b"hello-image"
    b64 = base64.b64encode(raw).decode()
    uri = f"data:image/png;base64,{b64}"
    assert _fetch_image_bytes(uri) == raw


def test_fetch_image_bytes_empty_and_invalid():
    assert _fetch_image_bytes("") is None
    assert _fetch_image_bytes("   ") is None
    assert _fetch_image_bytes("/no/such/file.png") is None


# ---------------- _image_extension ----------------
def test_image_extension_url():
    assert _image_extension("https://a.b/c.png") == ".png"
    assert _image_extension("/local/img.JPG") == ".jpg"
    assert _image_extension("http://x/y/z.jpeg?q=1") == ".jpeg"


def test_image_extension_data_uri():
    assert _image_extension("data:image/png;base64,xxx") == ".png"
    assert _image_extension("data:image/webp;base64,xxx") == ".webp"


def test_image_extension_fallback():
    assert _image_extension("https://a.b/no-ext") == ".png"
    assert _image_extension("https://a.b/file.txt") == ".png"


# ---------------- _sanitize_filename ----------------
def test_sanitize_filename_illegal_chars():
    assert _sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"


def test_sanitize_filename_truncation():
    assert len(_sanitize_filename("x" * 100)) == 40


def test_sanitize_filename_empty_fallback():
    assert _sanitize_filename("") == "image"
    assert _sanitize_filename("///") == "image"
    assert _sanitize_filename("   ") == "image"


# ---------------- build_image.delete（文档行为）----------------
class _StubRef:
    """最小 Ref 替身，current 可读写。"""

    def __init__(self, value=None):
        self.current = value


def _make_ctx(lines):
    """构造仅含 delete 所需装配槽的 stub ctx。"""
    ctx = types.SimpleNamespace()
    ctx.document = types.SimpleNamespace(lines=lines)
    ctx.clipboard_ref = None
    ctx.picker_ref = None
    ctx.viewport_w = 1000.0
    ctx.viewport_h_ref = _StubRef(800.0)
    # delete 调用记录
    state = {"pushed": 0, "dirtied": 0, "nav": 0}

    ctx.push_history = lambda: state.__setitem__("pushed", state["pushed"] + 1)
    ctx.undo_push_pending = _StubRef(False)
    ctx.mark_dirty = lambda: state.__setitem__("dirtied", state["dirtied"] + 1)
    ctx.set_cursor = lambda li, off: None
    ctx.set_cursor_line = lambda li: None

    def _set_nav_seq(v):
        ctx.nav_seq = v
        state["nav"] = v
    ctx.set_nav_seq = _set_nav_seq
    ctx.nav_seq = 0
    return ctx, state


def _image_line_state(line):
    """提取行可比较状态。"""
    return (line.raw, [(str(s.seg_type), s.raw, s.text, s.url) for s in line.segments])


def test_delete_image_single_image_line():
    """单图片行删除：清空行内容。"""
    doc = parse_markdown("![alt](img.png)")
    line = doc.lines[0]
    ctx, state = _make_ctx(doc.lines)
    cbs = build_image(ctx)
    cbs["on_image_action"]("delete", 0, 0, "img.png", "alt")
    assert line.raw == ""
    assert state["pushed"] == 1
    assert state["dirtied"] == 1


def test_delete_image_mixed_line_preserves_text():
    """混合行删除图片段：保留其余文本（注意 _image_seg_indices 仅纯图片行进入
    图片渲染分支，但 delete 操作本身不依赖该判定，直接按 seg_idx 删段 raw）。"""
    # 行内含图片 + 文本：![a](b.png) 文字
    doc = parse_markdown("![a](b.png) 文字")
    line = doc.lines[0]
    # 定位 IMAGE 段索引
    from models import SegType
    img_idx = next(i for i, s in enumerate(line.segments)
                   if s.seg_type == SegType.IMAGE)
    ctx, _state = _make_ctx(doc.lines)
    cbs = build_image(ctx)
    seg = line.segments[img_idx]
    cbs["on_image_action"]("delete", 0, img_idx, seg.url, seg.text)
    # 删除后行 raw 不再含图片段 raw
    assert seg.raw not in line.raw
    assert "文字" in line.raw


def test_delete_image_out_of_range_noop():
    """越界 line_idx 安全返回（不抛异常）。"""
    doc = parse_markdown("![a](b.png)")
    ctx, _ = _make_ctx(doc.lines)
    cbs = build_image(ctx)
    cbs["on_image_action"]("delete", 99, 0, "b.png", "a")  # 不抛异常
    assert doc.lines[0].raw == "![a](b.png)"


def test_on_image_action_unknown_action_noop():
    """未知 action 安全返回。"""
    doc = parse_markdown("![a](b.png)")
    ctx, _ = _make_ctx(doc.lines)
    cbs = build_image(ctx)
    cbs["on_image_action"]("bogus", 0, 0, "b.png", "a")
    assert doc.lines[0].raw == "![a](b.png)"


def test_delete_image_equivalent_to_manual_reparse():
    """删除图片段与手动 reparse_line_atomic(raw 去掉图片段) 结果一致。"""
    md = "前文 ![alt](img.png) 后文"
    doc1 = parse_markdown(md)
    doc2 = parse_markdown(md)
    line1, line2 = doc1.lines[0], doc2.lines[0]
    from models import SegType
    img_idx = next(i for i, s in enumerate(line1.segments)
                   if s.seg_type == SegType.IMAGE)
    seg = line1.segments[img_idx]
    # 手动基线：从 raw 移除图片段
    off = sum(len(s.raw) for s in line1.segments[:img_idx])
    manual_raw = line2.raw[:off] + line2.raw[off + len(seg.raw):]
    reparse_line_atomic(line2, manual_raw)
    # 走 build_image
    ctx, _ = _make_ctx(doc1.lines)
    cbs = build_image(ctx)
    cbs["on_image_action"]("delete", 0, img_idx, seg.url, seg.text)
    assert _image_line_state(line1) == _image_line_state(line2)
