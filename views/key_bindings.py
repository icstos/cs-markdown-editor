"""键盘事件分发器：替代 main.py 的 on_key 闭包。

把 main.py 中 ~240 行的 _do_copy / _do_cut / _do_paste_check / _combo / _matches
/ on_key 整块抽成 KeyDispatcher 类。main.py 持有实例并绑定到 page.on_keyboard_event。

设计要点：
- 持有 actions_ref（原 nav_ref）引用。editor.py 每次渲染写入最新 EditorActions，
  dispatcher 读 actions_ref.current 即最新值，无需 on_key_ref 中转层。
- actions.cursor_ref.current.base / .extent / .draft_len 实时读取光标位置
  （这些值在 on_selection_change 中直接修改，非 set_state 触发，不能用渲染期快照）。
- _combo 为模块级函数；matches 复用 services.shortcuts.matches。
"""

import asyncio
from collections.abc import Awaitable, Callable

import flet as ft

from models import BlockType
from services.shortcuts import ShortcutManager, matches
from state.actions import EditorActions


def _combo(e) -> str:
    """把 KeyboardEvent 规范化为 "ctrl+shift+key" 形式的小写字符串。

    与 services.shortcuts.normalize 配套：ctrl+comma 在 normalize 中转为 ctrl+,，
    此处也把 "comma" 映射为 ","，保证 matches() 比对一致。
    """
    parts: list[str] = []
    if getattr(e, "ctrl", False) or getattr(e, "meta", False):
        parts.append("ctrl")
    if getattr(e, "shift", False):
        parts.append("shift")
    if getattr(e, "alt", False):
        parts.append("alt")
    key = (e.key or "").replace(" ", "").lower()
    if key in ("control", "meta", "shift", "alt"):
        return ""
    mapping = {
        "arrowleft": "left",
        "arrowright": "right",
        "arrowup": "up",
        "arrowdown": "down",
        " ": "space",
        "comma": ",",
        "escape": "esc",
        "enter": "enter",
    }
    key = mapping.get(key, key)
    return "+".join(parts + [key])


class KeyDispatcher:
    """键盘事件分发器：浏览态 / 编辑态两层快捷键 + 编辑态光标导航。

    main.py 每次渲染重建实例（ShortcutManager 是无状态读取器，重建无副作用）。
    page.on_keyboard_event = dispatcher.handle 直接绑定，无需 on_key_ref 中转。
    """

    def __init__(
        self,
        shortcut_mgr: ShortcutManager,
        actions_ref: ft.Ref,  # ft.Ref[EditorActions | None]
        clipboard_ref: ft.Ref,
        page_ref: ft.Ref,
        paste_old_draft: ft.Ref,
        app_callbacks: dict[str, Callable[[], None]],
        # 期望键：save / new / open / toggle_sidebar / toggle_theme / open_settings
    ):
        self._shortcut_mgr = shortcut_mgr
        self._actions_ref = actions_ref
        self._clipboard_ref = clipboard_ref
        self._page_ref = page_ref
        self._paste_old_draft = paste_old_draft
        self._app_callbacks = app_callbacks

    # ---- 主入口 ----
    def handle(self, e) -> None:
        combo = _combo(e)
        key = e.key or ""
        norm = key.replace(" ", "").lower()
        actions: EditorActions | None = self._actions_ref.current
        layer = "edit" if actions is not None and actions.active is not None else "browse"
        shortcuts = self._shortcut_mgr.get(layer)

        if layer == "edit" and actions is not None:
            if self._handle_edit_nav(actions, e, norm):
                return

        # 浏览态 Backspace：删除 SelectionArea 选区文本
        if (
            norm == "backspace"
            and actions is not None
            and actions.active is None
            and not actions.raw_mode
        ):
            plain = actions.selection_text_ref.current or ""
            if plain:
                actions.handle_delete_selection(plain)
                return

        page = self._page_ref.current
        if page is None:
            return
        self._handle_shortcuts(page, actions, combo, shortcuts, layer)

    # ---- 编辑态光标导航（home/end/up/down/backspace/delete/tab/越界 arrow）----
    def _handle_edit_nav(self, actions: EditorActions, e, norm: str) -> bool:
        """处理编辑态纯导航键。返回 True 表示已消费，False 继续走快捷键分支。"""
        if norm == "home":
            actions.move_line_start() if e.ctrl else actions.move_home()
            return True
        if norm == "end":
            actions.move_line_end() if e.ctrl else actions.move_end()
            return True
        if norm == "arrowup":
            actions.move_up()
            return True
        if norm == "arrowdown":
            actions.move_down()
            return True
        if norm == "backspace":
            actions.backspace_core()
            return True
        if norm == "delete":
            actions.delete_core()
            return True
        if norm == "tab":
            # 代码块 Tab 由 editor.py 的 _on_key_down 处理（缩进），此处跳过不拦截
            if (
                actions.active_line is not None
                and getattr(actions.active_line, "block_type", None) == BlockType.CODE
            ):
                return True
            if e.shift:
                if actions.indent_or_outdent:
                    actions.indent_or_outdent(-1)
                else:
                    actions.move_left()
            else:
                if actions.indent_or_outdent:
                    actions.indent_or_outdent(1)
                else:
                    actions.move_right()
            return True
        cur = actions.cursor_ref.current
        if norm == "arrowleft" and cur.extent == 0 and cur.base == 0:
            actions.move_left()
            return True
        if (
            norm == "arrowright"
            and cur.extent == cur.draft_len
            and cur.base == cur.draft_len
        ):
            actions.move_right()
            return True
        return False

    # ---- 快捷键分发（浏览态 / 编辑态各自匹配）----
    def _handle_shortcuts(
        self,
        page: ft.Page,
        actions: EditorActions | None,
        combo: str,
        shortcuts: dict[str, str],
        layer: str,
    ) -> None:
        cb = self._app_callbacks
        if layer == "browse":
            if matches(combo, shortcuts.get("save", "ctrl+s")):
                page.run_task(cb["save"])
            elif matches(combo, shortcuts.get("new", "ctrl+n")):
                cb["new"]()
            elif matches(combo, shortcuts.get("open", "ctrl+o")):
                page.run_task(cb["open"])
            elif matches(combo, shortcuts.get("toggle_sidebar", "ctrl+b")):
                cb["toggle_sidebar"]()
            elif matches(combo, shortcuts.get("toggle_theme", "ctrl+shift+l")):
                cb["toggle_theme"]()
            elif matches(combo, shortcuts.get("toggle_raw", "ctrl+/")):
                if actions is not None:
                    actions.toggle_raw()
            elif matches(combo, shortcuts.get("open_settings", "ctrl+comma")):
                cb["open_settings"]()
            elif matches(combo, shortcuts.get("focus_mode", "ctrl+k")):
                if actions is not None:
                    actions.toggle_focus_mode()
            elif matches(
                combo, shortcuts.get("redo", "ctrl+y")
            ) or matches(combo, shortcuts.get("redo_alt", "ctrl+shift+z")):
                if actions is not None:
                    actions.redo()
            elif matches(combo, shortcuts.get("undo", "ctrl+z")):
                if actions is not None:
                    actions.undo()
            elif combo == "ctrl+c":
                if actions is None or actions.active is None:
                    page.run_task(self._do_copy)
            elif combo == "ctrl+x":
                if actions is None or actions.active is None:
                    page.run_task(self._do_cut)
            elif combo == "ctrl+v":
                if actions is not None and actions.active is not None:
                    self._paste_old_draft.current = actions.draft
                    page.run_task(self._do_paste_check)
            return
        # edit 层
        if matches(combo, shortcuts.get("save", "ctrl+s")):
            page.run_task(cb["save"])
        elif matches(combo, shortcuts.get("undo", "ctrl+z")):
            if actions is not None:
                actions.undo()
        elif matches(
            combo, shortcuts.get("redo", "ctrl+y")
        ) or matches(combo, shortcuts.get("redo_alt", "ctrl+shift+z")):
            if actions is not None:
                actions.redo()
        elif matches(combo, shortcuts.get("toggle_raw", "ctrl+enter")):
            if actions is not None:
                actions.toggle_raw()
        elif matches(combo, shortcuts.get("toggle_sidebar", "escape")):
            cb["toggle_sidebar"]()
        elif combo == "ctrl+c":
            if actions is None or actions.active is None:
                page.run_task(self._do_copy)
        elif combo == "ctrl+x":
            if actions is None or actions.active is None:
                page.run_task(self._do_cut)
        elif combo == "ctrl+v":
            if actions is not None and actions.active is not None:
                self._paste_old_draft.current = actions.draft
                page.run_task(self._do_paste_check)

    # ---- 剪贴板异步操作 ----
    async def _do_copy(self) -> None:
        """Ctrl+C：用 SelectionArea 选区文本计算 Markdown 覆盖剪贴板。

        用 selection_text_ref（on_change 上报的选区纯文本）而非 clipboard.get()
        读取：原生 SelectionArea 复制到剪贴板的时序不可靠（sleep 0.2s 仍可能读到
        空或旧值），且 selection_text_ref 与 BackSpace 删除选区共用同一数据源，
        行为一致更可靠。
        """
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        plain = actions.selection_text_ref.current or ""
        if not plain:
            return
        try:
            md = actions.compute_markdown_from_text(plain)
            if md and md != plain:
                clipboard = self._clipboard_ref.current
                if clipboard is not None:
                    await clipboard.set(md)
        except Exception:
            return

    async def _do_cut(self) -> None:
        """Ctrl+X：用 SelectionArea 选区文本计算 Markdown 覆盖剪贴板，并删除选中内容。"""
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        plain = actions.selection_text_ref.current or ""
        if not plain:
            return
        try:
            await actions.handle_cut(plain)
        except Exception:
            return

    async def _do_paste_check(self) -> None:
        """Ctrl+V 后异步检查剪贴板是否含多行内容，若是则拆分为多行插入。"""
        await asyncio.sleep(0.05)
        clipboard = self._clipboard_ref.current
        if clipboard is None:
            return
        try:
            text = await clipboard.get()
        except Exception:
            return
        if not text or "\n" not in text:
            return
        actions = self._actions_ref.current
        if actions is None:
            return
        try:
            actions.handle_paste(text, self._paste_old_draft.current)
        except Exception:
            return
