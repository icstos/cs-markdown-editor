"""系统剪贴板辅助（无 UI 依赖，供代码块/表格/前置元数据行复用）。

从 views/line_view.py 迁出：这些函数原先定义在行视图模块内，导致
views/table_view.py 需要反向导入另一个视图模块的私有函数。剪贴板读写
属于服务层能力，与渲染无关。
"""

import asyncio
import contextlib
from collections.abc import Callable

import flet as ft


def _clipboard(clipboard_ref: ft.Ref | None):
    return clipboard_ref.current if clipboard_ref is not None else None


async def copy_code_to_clipboard(
    clipboard_ref: ft.Ref | None,
    text: str,
    set_copied: Callable[[bool], None],
) -> None:
    """复制文本并在 1.2 秒内把 set_copied 置 True（按钮"已复制"反馈）。"""
    clipboard = _clipboard(clipboard_ref)
    if clipboard is None:
        return
    try:
        await clipboard.set(text)
    except Exception:
        return
    set_copied(True)
    await asyncio.sleep(1.2)
    set_copied(False)


async def copy_text_to_clipboard(clipboard_ref: ft.Ref | None, text: str) -> None:
    """把文本写入系统剪贴板（无反馈闪烁版本，供前置元数据行复制/剪切）。"""
    clipboard = _clipboard(clipboard_ref)
    if clipboard is None:
        return
    with contextlib.suppress(Exception):
        await clipboard.set(text)


async def paste_row_from_clipboard(
    clipboard_ref: ft.Ref | None,
    on_insert: Callable[[str, str], None],
) -> None:
    """读取剪贴板文本，按 "key: value" 解析后插入新属性行。

    无冒号则整段作为值、键为空（用户可补键名）。
    """
    clipboard = _clipboard(clipboard_ref)
    if clipboard is None:
        return
    try:
        text = await clipboard.get()
    except Exception:
        return
    if not text:
        return
    if ":" in text:
        key, _, val = text.partition(":")
        on_insert(key.strip(), val.strip())
    else:
        on_insert("", text.strip())
