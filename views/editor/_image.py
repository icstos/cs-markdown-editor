"""图片操作工厂：渲染态图片右键菜单触发的图片级操作。

参考 Typora 图片交互：
- 左键点击图片 → 进入图片 Markdown 编辑（由 rendered_line 的 on_tap 接入，
  定位光标到图片段 raw 偏移，激活行渲染源码 ![alt](url)）
- 右键图片 → 上下文菜单：拷贝 Markdown / 拷贝图片 / 另存为 / 删除

闭包组：on_image_action（统一分发入口，同步签名，async 操作用 run_task 调度）

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history / mark_dirty（history / 共享组）
- set_cursor / set_cursor_line / set_nav_seq（cursor 组）
- clipboard_ref / picker_ref（props）
- document（props）

依赖项：
- parser（reparse_line_atomic）
- utils.segment_helpers（is_fence）
- flet（ft.context.page.run_task 调度 async 剪贴板/文件操作）
"""

import base64
import contextlib
import os
import urllib.request

import flet as ft

import parser
from utils.segment_helpers import is_fence as _is_fence

# 高频编辑路径用原子化重解析（仅触发 1 次 observable 通知）
_reparse_atomic = parser.reparse_line_atomic

# 图片允许的保存扩展名
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg")


def _fetch_image_bytes(url: str) -> bytes | None:
    """统一获取图片二进制：支持本地路径 / http(s) URL / data URI。

    失败返回 None。复用 text_layout._read_image_size 的三类分支策略，
    但返回原始 bytes（供剪贴板 set_image / 文件写入）。
    """
    src = (url or "").strip()
    if not src:
        return None
    try:
        if src.startswith(("http://", "https://")):
            with urllib.request.urlopen(src, timeout=10) as resp:
                return resp.read()
        if src.startswith("data:"):
            # data:image/png;base64,XXXX
            _, _, b64 = src.partition(",")
            return base64.b64decode(b64) if b64 else None
        # 本地路径（相对路径基于 cwd 解析，与 image_fit_size 一致）
        with open(src, "rb") as f:
            return f.read()
    except Exception:
        return None


def _image_extension(url: str) -> str:
    """从 url 推断图片扩展名（默认 .png）。"""
    src = (url or "").strip()
    if src.startswith("data:image/"):
        fmt = src.split(";")[0].split("/")[-1].lower()
        return f".{fmt}" if fmt else ".png"
    _, ext = os.path.splitext(src.split("?")[0])
    ext = ext.lower()
    return ext if ext in _IMG_EXTS else ".png"


def _sanitize_filename(name: str) -> str:
    """清理文件名非法字符，截断至 40 字符。空则返回 "image"。"""
    cleaned = "".join(c for c in (name or "") if c not in '\\/:*?"<>|\t\r\n')
    cleaned = cleaned.strip().strip(".")[:40]
    return cleaned or "image"


def build_image(ctx):
    """构造图片操作闭包组。

    返回 dict[str, Callable]：on_image_action
    on_image_action(action, line_idx, seg_idx, url, alt) -> None（同步签名）
      action ∈ {"copy_md", "copy_image", "save_as", "delete"}
    """

    def _run(coro_fn, *args, **kwargs):
        """在 page 事件循环调度 async 操作（剪贴板/文件 IO）。

        page.run_task 期望协程函数 + 参数（内部调用 coro_fn(*args)），
        故传入函数引用而非已调用的协程对象。
        """
        page = ft.context.page
        if page is not None:
            page.run_task(coro_fn, *args, **kwargs)

    def _seg_raw_off(li: int, seg_idx: int) -> int:
        """计算 (li, seg_idx) 段起始 raw 偏移。"""
        line = ctx.document.lines[li]
        return sum(len(s.raw) for s in line.segments[:seg_idx])

    # ============ 删除图片 ============
    def _delete_image(li: int, seg_idx: int):
        """删除图片段：从行 raw 中移除该段 raw，reparse 行。

        - 单图片行（仅该图片 + 空白）：清空行内容
        - 混合行：移除图片段，保留其余文本
        与 cut_current_line 一致走 push_history + reparse_atomic 原子路径。
        """
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        seg = line.segments[seg_idx]
        off = _seg_raw_off(li, seg_idx)
        new_raw = line.raw[:off] + line.raw[off + len(seg.raw):]
        ctx.push_history()
        ctx.undo_push_pending.current = True
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()
        # 光标复位到删除位置（Typora 式：删除后光标停在原位置）
        ctx.set_cursor(li, min(off, len(new_raw)))
        ctx.set_cursor_line(li)
        ctx.set_nav_seq(ctx.nav_seq + 1)

    # ============ 拷贝图片 Markdown ============
    async def _copy_image_md(url: str, alt: str):
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is None:
            return
        md = f"![{alt}]({url})"
        with contextlib.suppress(Exception):
            await clipboard.set(md)

    # ============ 拷贝图片（二进制写入系统剪贴板）============
    async def _copy_image(url: str):
        data = _fetch_image_bytes(url)
        if data is None:
            return
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is None:
            return
        with contextlib.suppress(Exception):
            await clipboard.set_image(data)

    # ============ 将图像另存为 ============
    async def _save_image_as(url: str, alt: str):
        picker = ctx.picker_ref.current if ctx.picker_ref is not None else None
        if picker is None:
            return
        data = _fetch_image_bytes(url)
        if data is None:
            return
        ext = _image_extension(url)
        default_name = _sanitize_filename(alt) + ext
        try:
            path = await picker.save_file(
                dialog_title="图像另存为",
                file_name=default_name,
                allowed_extensions=["png", "jpg", "jpeg", "gif", "bmp", "webp"],
                file_type=ft.FilePickerFileType.CUSTOM,
            )
        except Exception:
            return
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(data)
        except Exception:
            pass

    # ============ 统一分发入口 ============
    def on_image_action(action: str, li: int, seg_idx: int, url: str, alt: str):
        if not (0 <= li < len(ctx.document.lines)):
            return
        if action == "delete":
            _delete_image(li, seg_idx)
        elif action == "copy_md":
            _run(_copy_image_md, url, alt)
        elif action == "copy_image":
            _run(_copy_image, url)
        elif action == "save_as":
            _run(_save_image_as, url, alt)

    return {"on_image_action": on_image_action}
