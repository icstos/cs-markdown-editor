"""图片操作工厂：渲染态图片右键菜单 + Ctrl+V 图片粘贴。

参考 Typora 图片交互：
- 左键点击图片 → 进入图片 Markdown 编辑（由 rendered_line 的 on_tap 接入，
  定位光标到图片段 raw 偏移，激活行渲染源码 ![alt](url)）
- 右键图片 → 上下文菜单：拷贝 Markdown / 拷贝图片 / 另存为 / 删除
- Ctrl+V 粘贴图片（Typora 式无感落盘）：
  * 剪贴板含本地图片文件（资源管理器复制）→ 复制文件到 ./assets/，插入 ![](assets/xxx)
  * 剪贴板含位图（截图工具 / 浏览器右键复制）→ PIL 标准化为 PNG 落盘 ./assets/，插入 ![](assets/xxx)
  * 文本路径不再额外转换（避免误判，与 Typora 默认行为一致）
  * 文档未保存时（file_path 为 None）→ SnackBar 提示先保存文档

闭包组：
- on_image_action（统一分发入口，同步签名，async 操作用 run_task 调度）
- paste_image_from_clipboard（async：返回 True 已处理图片，False 剪贴板无图片）

跨组依赖（通过 ctx 装配槽，调用时读取）：
- push_history / mark_dirty（history / 共享组）
- set_cursor / set_cursor_line / set_nav_seq / cursor_base（cursor 组）
- clipboard_ref / picker_ref / file_path（props）
- document（props）

依赖项：
- parser（reparse_line_atomic）
- utils.segment_helpers（is_fence / line_raw）
- services.ui_feedback（show_snack：编辑器 ctx 无 show_snack 装配槽，直接用 page）
- flet（ft.context.page.run_task 调度 async 剪贴板/文件操作）
- PIL.Image（位图字节流标准化为 PNG）
"""

import base64
import contextlib
import io
import os
import shutil
import urllib.request

import flet as ft
from PIL import Image as _PILImage

import parser
from services.ui_feedback import show_snack as _show_snack
from utils.segment_helpers import is_fence as _is_fence
from utils.segment_helpers import line_raw as _line_raw
from utils.text_layout import resolve_image_src as _resolve_image_src

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
        # 本地路径：调用方应先用 resolve_image_src 解析为绝对路径（基于文档目录），
        # 此处直接 open（兼容历史调用方传绝对路径或 URL）
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


# ============ 图片粘贴：assets 目录与文件名策略 ============

def _is_image_path(path: str) -> bool:
    """判断路径是否为图片（按扩展名）。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in _IMG_EXTS


def _resolve_assets_dir(file_path: str | None) -> tuple[str, str] | None:
    """解析文档同级的 assets 目录，返回 (abs_dir, rel_dir) | None。

    - file_path 为 None（未保存文档）：返回 None
    - 目录创建失败：返回 None
    - rel_dir 固定为 "assets"（Markdown 相对路径，正斜杠跨平台兼容）
    """
    if not file_path:
        return None
    doc_dir = os.path.dirname(os.path.abspath(file_path))
    assets_abs = os.path.join(doc_dir, "assets")
    try:
        os.makedirs(assets_abs, exist_ok=True)
    except OSError:
        return None
    return assets_abs, "assets"


def _unique_image_path(assets_dir: str, ext: str) -> str:
    """生成唯一的位图保存路径（Typora 风格 image-1.png / image-2.png 递增）。

    ext 必须含点号（如 ".png"）。返回绝对路径。
    """
    counter = 1
    while True:
        candidate = os.path.join(assets_dir, f"image-{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _unique_copy_path(assets_dir: str, src_path: str) -> str:
    """为本地文件复制生成目标路径，保留原文件名，冲突加 _N 后缀。"""
    name = os.path.basename(src_path)
    stem, ext = os.path.splitext(name)
    target = os.path.join(assets_dir, name)
    if not os.path.exists(target):
        return target
    counter = 1
    while True:
        candidate = os.path.join(assets_dir, f"{stem}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _rel_url(assets_rel: str, abs_path: str) -> str:
    """构造 Markdown 中的相对 URL：assets/文件名（正斜杠）。"""
    return f"{assets_rel}/{os.path.basename(abs_path)}"


def build_image(ctx):
    """构造图片操作闭包组。

    返回 dict[str, Callable]：
    - on_image_action(action, line_idx, seg_idx, url, alt) -> None（同步签名）
      action ∈ {"copy_md", "copy_image", "save_as", "delete"}
    - paste_image_from_clipboard() -> bool（async：True 已处理图片，False 无图片）
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
        # 相对路径基于文档目录解析（与渲染层 image_fit_size 一致），否则 cwd
        # 非文档目录时本地图片读取失败
        data = _fetch_image_bytes(_resolve_image_src(url, ctx.file_path))
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
        data = _fetch_image_bytes(_resolve_image_src(url, ctx.file_path))
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

    # ============ 图片粘贴：在光标处插入 ![](url) ============
    def _insert_image_md(url: str, alt: str = ""):
        """在当前光标位置插入 ![alt](url)，光标定位到 alt 位置（Typora 式）。

        - 编辑态（cursor_li is not None）：在行内光标偏移处插入
        - 浏览态（cursor_li is None）：在 cursor_line 行尾插入并激活
        - 围栏块（CODE/MATH/TABLE/HR/TOC）：跳过，避免破坏语法

        插入后光标停在 `![` 之后（即 `[]` 之间），用户可立即输入 alt 文本，
        与 Typora 行为一致。reparse_atomic 仅触发 1 次 observable 通知。
        """
        li = ctx.cursor_li if ctx.cursor_li is not None else ctx.cursor_line
        if li is None or not (0 <= li < len(ctx.document.lines)):
            return
        line = ctx.document.lines[li]
        if _is_fence(line):
            return
        raw = _line_raw(line)
        if ctx.cursor_li is not None:
            off = ctx.cursor_base(len(raw))
        else:
            off = len(raw)  # 浏览态：行尾插入
        md = f"![{alt}]({url})"
        new_raw = raw[:off] + md + raw[off:]
        ctx.push_history()
        ctx.undo_push_pending.current = True
        _reparse_atomic(line, new_raw)
        ctx.mark_dirty()
        # 光标定位到 alt 位置（![ 之后，偏移 = off + 2），递增 nav_seq 强制
        # TextField 重建以刷新光标（与 _delete_image 一致的模式）
        ctx.set_cursor(li, off + 2)
        ctx.set_cursor_line(li)
        ctx.set_nav_seq(ctx.nav_seq + 1)

    # ============ Ctrl+V 图片粘贴主入口 ============
    async def paste_image_from_clipboard() -> bool:
        """检测剪贴板图片，落盘到 ./assets/ 并插入 Markdown 语法。

        返回 True 表示已处理图片粘贴（调用方跳过文本粘贴），False 表示剪贴板
        无图片，调用方继续走文本粘贴路径。

        优先级（Typora 式）：
        1. 本地图片文件（资源管理器 Ctrl+C 文件）→ get_files() 拿路径，复制到 assets
        2. 位图（截图工具 / 浏览器右键复制图片）→ get_image() 拿 bytes，PIL 标准化为 PNG

        平台约束（Flet 0.86.2 Clipboard 源码验证）：
        - set_image 仅 Android/iOS/Web，桌面端不支持（不影响本粘贴功能）
        - get_image 桌面端支持（无平台限制）
        - get_files / set_files 桌面端支持
        """
        clipboard = ctx.clipboard_ref.current if ctx.clipboard_ref is not None else None
        if clipboard is None:
            return False

        # 文档未保存或 assets 目录创建失败：SnackBar 提示（Typora 行为）
        resolved = _resolve_assets_dir(ctx.file_path)
        if resolved is None:
            # ft.context.page 在组件卸载/测试场景可能抛 RuntimeError（无 Flet 上下文），
            # 防御性捕获：无法弹提示时静默返回 True（仍阻止回退到文本粘贴，
            # 避免剪贴板位图二进制数据被当文本插入文档）
            try:
                page = ft.context.page
            except RuntimeError:
                page = None
            if page is not None:
                msg = "请先保存文档后再粘贴图片" if not ctx.file_path else "无法创建 assets 目录"
                _show_snack(page, msg)
            return True  # 已处理（避免回退到文本粘贴导致二进制数据被当文本插入）

        assets_abs, assets_rel = resolved
        inserted_any = False

        # 1. 本地图片文件（资源管理器复制）
        try:
            files = await clipboard.get_files()
        except Exception:
            files = None
        image_files = [f for f in (files or []) if _is_image_path(f) and os.path.isfile(f)]
        for src_path in image_files:
            target = _unique_copy_path(assets_abs, src_path)
            try:
                shutil.copy2(src_path, target)
            except OSError:
                continue
            _insert_image_md(_rel_url(assets_rel, target), "")
            inserted_any = True
        if inserted_any:
            return True

        # 2. 位图（截图工具 / 浏览器右键复制图片）
        try:
            img_data = await clipboard.get_image()
        except Exception:
            img_data = None
        if img_data:
            # PIL 加载并标准化为 PNG（保证格式一致，避免 BMP/原始字节流兼容问题）
            try:
                img = _PILImage.open(io.BytesIO(img_data))
                img.load()  # 触发解码，确保数据完整
            except Exception:
                # 数据无效：已尝试，不回退到文本（避免二进制当文本粘贴）
                return True
            target = _unique_image_path(assets_abs, ".png")
            try:
                img.save(target, "PNG")
            except (OSError, ValueError):
                return True
            _insert_image_md(_rel_url(assets_rel, target), "")
            return True

        return False

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

    return {
        "on_image_action": on_image_action,
        "paste_image_from_clipboard": paste_image_from_clipboard,
    }
