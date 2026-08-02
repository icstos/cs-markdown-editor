"""图片粘贴 _image.py 单元测试。

覆盖不依赖 UI 的纯逻辑：
- 辅助函数：_is_image_path / _resolve_assets_dir / _unique_image_path /
  _unique_copy_path / _rel_url
- paste_image_from_clipboard：clipboard None / file_path None / 本地图片文件 /
  位图 / 剪贴板无图片 / 多图片文件
- _insert_image_md（经 paste_image_from_clipboard 间接调用）：编辑态光标定位 /
  浏览态行尾插入 / 围栏块跳过

不依赖 UI 渲染，ctx 用 SimpleNamespace 注入，async 协程用 asyncio.run
直接驱动（与 test_autosave / test_open_folder 一致，无 pytest-asyncio 依赖）。
"""

import asyncio
import io
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft
from PIL import Image as PILImage

from parser import parse_markdown
from views.editor._image import (
    _is_image_path,
    _rel_url,
    _resolve_assets_dir,
    _unique_copy_path,
    _unique_image_path,
    build_image,
)


# ---------------- 辅助函数 ----------------

def test_is_image_path():
    assert _is_image_path("a.png") is True
    assert _is_image_path("/abs/file.JPG") is True
    assert _is_image_path("https://x/y/z.webp") is True
    assert _is_image_path("no_ext") is False
    assert _is_image_path("a.txt") is False
    assert _is_image_path("") is False


def test_resolve_assets_dir_none_when_no_file_path():
    assert _resolve_assets_dir(None) is None
    assert _resolve_assets_dir("") is None


def test_resolve_assets_dir_creates_dir(tmp_path):
    """file_path 有效时自动创建 assets 目录，返回 (abs, rel)。"""
    doc = tmp_path / "note.md"
    doc.write_text("# hi")  # 确保父目录存在
    result = _resolve_assets_dir(str(doc))
    assert result is not None
    abs_dir, rel_dir = result
    assert rel_dir == "assets"
    assert abs_dir == str(tmp_path / "assets")
    assert os.path.isdir(abs_dir)


def test_resolve_assets_dir_idempotent(tmp_path):
    """assets 已存在时不报错（exist_ok=True）。"""
    (tmp_path / "assets").mkdir()
    doc = tmp_path / "note.md"
    doc.write_text("x")
    result = _resolve_assets_dir(str(doc))
    assert result is not None
    assert os.path.isdir(result[0])


def test_unique_image_path_increments(tmp_path):
    """Typora 风格 image-1.png / image-2.png 递增，跳过已存在文件。"""
    p1 = _unique_image_path(str(tmp_path), ".png")
    assert p1.endswith("image-1.png")
    open(p1, "wb").close()  # 创建占位
    p2 = _unique_image_path(str(tmp_path), ".png")
    assert p2.endswith("image-2.png")


def test_unique_copy_path_keeps_name(tmp_path):
    """本地文件复制保留原文件名（src 与 assets 分离避免与自身冲突）。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "photo.jpg"
    src.write_bytes(b"x")
    assets = tmp_path / "assets"
    assets.mkdir()
    target = _unique_copy_path(str(assets), str(src))
    assert os.path.basename(target) == "photo.jpg"


def test_unique_copy_path_conflict_adds_suffix(tmp_path):
    """目标已存在同名文件时加 _N 后缀。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "photo.jpg"
    src.write_bytes(b"x")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "photo.jpg").write_bytes(b"existing")  # 占位
    target = _unique_copy_path(str(assets), str(src))
    assert os.path.basename(target) == "photo_1.jpg"

    open(target, "wb").close()  # 再占位
    target2 = _unique_copy_path(str(assets), str(src))
    assert os.path.basename(target2) == "photo_2.jpg"


def test_rel_url_uses_forward_slash():
    assert _rel_url("assets", "/abs/path/image-1.png") == "assets/image-1.png"
    assert _rel_url("assets", "/abs/a b/图片.png") == "assets/图片.png"


# ---------------- paste_image_from_clipboard ----------------

class _StubRef:
    """最小 Ref 替身，current 可读写。"""

    def __init__(self, value=None):
        self.current = value


def _make_ctx(lines, *, file_path=None, clipboard=None, cursor_li=None, cursor_line=0):
    """构造 paste_image_from_clipboard 所需的 stub ctx。

    cursor_base 返回 0（行首）以简化断言；测试可通过 set_cursor 记录光标落点。
    """
    ctx = types.SimpleNamespace()
    ctx.document = types.SimpleNamespace(lines=lines)
    ctx.clipboard_ref = _StubRef(clipboard)
    ctx.picker_ref = None
    ctx.file_path = file_path
    ctx.cursor_li = cursor_li
    ctx.cursor_line = cursor_line
    ctx.nav_seq = 0

    state = {"pushed": 0, "dirtied": 0, "cursor": None, "cursor_line": None, "nav": 0}

    ctx.push_history = lambda: state.__setitem__("pushed", state["pushed"] + 1)
    ctx.undo_push_pending = _StubRef(False)
    ctx.mark_dirty = lambda: state.__setitem__("dirtied", state["dirtied"] + 1)

    def _set_cursor(li, off):
        state["cursor"] = (li, off)

    def _set_cursor_line(li):
        state["cursor_line"] = li

    def _set_nav_seq(v):
        ctx.nav_seq = v
        state["nav"] = v

    ctx.set_cursor = _set_cursor
    ctx.set_cursor_line = _set_cursor_line
    ctx.set_nav_seq = _set_nav_seq
    ctx.cursor_base = lambda raw_len=None: 0  # 行首偏移
    return ctx, state


def _make_png_bytes(size=(2, 2), color=(255, 0, 0)):
    """生成最小合法 PNG 字节流。"""
    buf = io.BytesIO()
    PILImage.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_paste_returns_false_when_clipboard_none():
    ctx, _ = _make_ctx([], clipboard=None)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is False


def test_paste_unsaved_doc_returns_true_and_hints(tmp_path):
    """file_path 为 None（未保存文档）→ 返回 True 并 SnackBar 提示先保存。

    生产代码中 ft.context.page 在 Flet 上下文内可弹 SnackBar；测试无 Flet 上下文，
    paste 函数防御性捕获 RuntimeError 静默降级，仍返回 True（阻止回退到文本粘贴）。
    """
    doc = parse_markdown("正文")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, state = _make_ctx(doc.lines, file_path=None, clipboard=fake_clip)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # 文档未被修改
    assert state["pushed"] == 0
    assert doc.lines[0].raw == "正文"


def test_paste_unsaved_doc_snackbar_called_when_page_available(tmp_path):
    """file_path 为 None 且 page 可用时 → _show_snack 被调用提示先保存。

    用 PropertyMock patch ft.context.page property，模拟 Flet 上下文可用。
    """
    from unittest.mock import PropertyMock
    doc = parse_markdown("正文")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, _ = _make_ctx(doc.lines, file_path=None, clipboard=fake_clip)
    cbs = build_image(ctx)
    snack_calls: list[str] = []
    fake_page = MagicMock()
    with patch("views.editor._image._show_snack", lambda page, msg: snack_calls.append(msg)), \
         patch.object(ft.context.__class__, "page", new_callable=PropertyMock,
                      return_value=fake_page):
        handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    assert snack_calls == ["请先保存文档后再粘贴图片"]


def test_paste_unsaved_doc_no_page_does_not_crash(tmp_path):
    """file_path 为 None 且无 Flet 上下文（page 访问抛 RuntimeError）→ 静默返回 True。"""
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, _ = _make_ctx(doc.lines, file_path=None, clipboard=fake_clip)
    cbs = build_image(ctx)
    # 无需 patch：ft.context.page 在测试环境天然抛 RuntimeError，paste 函数防御性捕获
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True


def test_paste_local_image_file_copies_and_inserts(tmp_path):
    """剪贴板含本地图片文件路径 → 复制到 assets/ 并插入 ![](assets/xxx)。"""
    # 准备源图片文件
    src = tmp_path / "source.png"
    src.write_bytes(_make_png_bytes())
    # 文档
    doc_path = tmp_path / "note.md"
    doc_path.write_text("正文")
    doc = parse_markdown("正文")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=[str(src)])
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, state = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                           cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # 文件已复制到 assets/
    copied = tmp_path / "assets" / "source.png"
    assert copied.exists()
    assert copied.read_bytes() == src.read_bytes()
    # 文档行 raw 含 Markdown 图片语法
    assert "![](assets/source.png)" in doc.lines[0].raw
    # 光标定位到 alt 位置（off=0 + 2 = 2，即 ![ 之后）
    assert state["cursor"] == (0, 2)
    assert state["pushed"] == 1
    assert state["dirtied"] == 1


def test_paste_local_image_file_conflict_adds_suffix(tmp_path):
    """assets/ 已有同名文件 → 复制时加 _1 后缀，Markdown URL 用新文件名。"""
    src = tmp_path / "photo.png"
    src.write_bytes(_make_png_bytes())
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    # 预占 assets/photo.png
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "photo.png").write_bytes(b"existing")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=[str(src)])
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, _ = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                       cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    assert (assets_dir / "photo_1.png").exists()
    assert "![](assets/photo_1.png)" in doc.lines[0].raw


def test_paste_skips_non_image_files_in_clipboard(tmp_path):
    """剪贴板含非图片文件（.txt）→ 跳过文件复制，回退到位图检测。"""
    src = tmp_path / "notes.txt"
    src.write_text("hello")
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=[str(src)])
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, _ = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                       cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    # 非图片文件 + 无位图 → 返回 False（继续走文本粘贴）
    assert handled is False
    assert not (tmp_path / "assets" / "notes.txt").exists()


def test_paste_multiple_image_files_all_inserted(tmp_path):
    """剪贴板含多个图片文件 → 全部复制并插入对应 Markdown 语法。"""
    src1 = tmp_path / "a.png"
    src1.write_bytes(_make_png_bytes(color=(255, 0, 0)))
    src2 = tmp_path / "b.jpg"
    src2.write_bytes(_make_png_bytes(color=(0, 255, 0)))
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=[str(src1), str(src2)])
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, _ = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                       cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    assert (tmp_path / "assets" / "a.png").exists()
    assert (tmp_path / "assets" / "b.jpg").exists()
    # 两段图片语法都在行 raw 中
    assert "![](assets/a.png)" in doc.lines[0].raw
    assert "![](assets/b.jpg)" in doc.lines[0].raw


def test_paste_bitmap_saves_as_png_and_inserts(tmp_path):
    """剪贴板含位图字节流 → PIL 标准化为 PNG 落盘，插入 ![](assets/image-N.png)。"""
    png_data = _make_png_bytes(size=(10, 8), color=(0, 128, 255))
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=png_data)
    ctx, state = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                           cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    saved = tmp_path / "assets" / "image-1.png"
    assert saved.exists()
    # 验证保存的 PNG 可被 PIL 重新打开且尺寸正确
    reloaded = PILImage.open(saved)
    assert reloaded.size == (10, 8)
    # 文档已插入图片语法
    assert "![](assets/image-1.png)" in doc.lines[0].raw
    assert state["pushed"] == 1
    assert state["cursor"] == (0, 2)


def test_paste_bitmap_invalid_data_returns_true_no_insert(tmp_path):
    """位图数据无效（非图片字节流）→ 返回 True（已尝试），不插入。"""
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=b"not-an-image-bytes")
    ctx, state = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                           cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    assert state["pushed"] == 0
    assert "![" not in doc.lines[0].raw
    # assets 目录可能已创建（_resolve_assets_dir 副作用），但无图片文件
    assets_dir = tmp_path / "assets"
    if assets_dir.exists():
        assert not any(assets_dir.iterdir())


def test_paste_empty_clipboard_returns_false(tmp_path):
    """剪贴板无文件也无位图 → 返回 False（继续走文本粘贴）。"""
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=[])
    fake_clip.get_image = AsyncMock(return_value=None)
    ctx, _ = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                       cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is False


def test_paste_local_image_priority_over_bitmap(tmp_path):
    """剪贴板同时含图片文件和位图 → 优先处理文件，不再处理位图。"""
    src = tmp_path / "file.png"
    src.write_bytes(_make_png_bytes(color=(1, 2, 3)))
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=[str(src)])
    # 位图也会被调用？不应被调用（文件优先返回 True）
    fake_clip.get_image = AsyncMock(return_value=_make_png_bytes(color=(9, 9, 9)))
    ctx, _ = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                       cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # 只复制了文件，没有 image-1.png（位图未被处理）
    assert (tmp_path / "assets" / "file.png").exists()
    assert not (tmp_path / "assets" / "image-1.png").exists()


# ---------------- _insert_image_md（间接测试）----------------

def test_paste_browse_mode_inserts_at_line_end(tmp_path):
    """浏览态（cursor_li=None）→ 在 cursor_line 行尾插入。"""
    doc_path = tmp_path / "note.md"
    doc_path.write_text("正文")
    doc = parse_markdown("正文")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=_make_png_bytes())
    # cursor_li=None, cursor_line=0
    ctx, state = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                           cursor_li=None, cursor_line=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # 行尾插入：raw = "正文" + "![](assets/image-1.png)"
    assert doc.lines[0].raw == "正文![](assets/image-1.png)"
    # 光标定位到 alt 位置：len("正文")=2 + 2 = 4
    assert state["cursor"] == (0, 4)
    assert state["cursor_line"] == 0


def test_paste_skips_fence_block(tmp_path):
    """光标在代码块（围栏块）→ 不插入图片语法（保护围栏语法）。

    parser 把多行代码块合并为单行 Line（raw 含换行符），_is_fence 判定为 True，
    _insert_image_md 早返回，文档不被修改。
    """
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    code_md = "```\ncode\n```"
    doc = parse_markdown(code_md)
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=_make_png_bytes())
    ctx, state = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                           cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # 围栏块 raw 保持原样（未被插入图片语法）
    assert doc.lines[0].raw == code_md
    assert "![" not in doc.lines[0].raw
    assert state["pushed"] == 0


def test_paste_edit_mode_inserts_at_cursor_offset(tmp_path):
    """编辑态光标在行中 → 在光标偏移处插入，光标定位到 off+2。

    cursor_base 返回 0（行首），故插入位置在行首。
    """
    doc_path = tmp_path / "note.md"
    doc_path.write_text("hello")
    doc = parse_markdown("hello")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=_make_png_bytes())
    ctx, state = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                           cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # cursor_base 返回 0 → 在行首插入
    assert doc.lines[0].raw == "![](assets/image-1.png)hello"
    # 光标在 alt 位置（off=0 + 2）
    assert state["cursor"] == (0, 2)
    # nav_seq 递增强制 TextField 重建
    assert state["nav"] == 1


def test_paste_assets_dir_already_exists(tmp_path):
    """assets 目录已存在（含其他文件）→ 复制不报错，新文件命名递增。"""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "image-1.png").write_bytes(b"existing")  # 占位
    doc_path = tmp_path / "note.md"
    doc_path.write_text("x")
    doc = parse_markdown("x")
    fake_clip = MagicMock()
    fake_clip.get_files = AsyncMock(return_value=None)
    fake_clip.get_image = AsyncMock(return_value=_make_png_bytes())
    ctx, _ = _make_ctx(doc.lines, file_path=str(doc_path), clipboard=fake_clip,
                       cursor_li=0)
    cbs = build_image(ctx)
    handled = asyncio.run(cbs["paste_image_from_clipboard"]())
    assert handled is True
    # image-1.png 已占用 → 新文件命名 image-2.png
    assert (assets_dir / "image-2.png").exists()
    assert "![](assets/image-2.png)" in doc.lines[0].raw
