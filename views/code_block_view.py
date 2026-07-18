"""代码块编辑逻辑：Tab 缩进、Backspace 反缩进、Delete、Enter 自动缩进、退出。

从 views/editor.py 抽出，封装为 CodeBlockEditor 类。editor.py 实例化时注入
状态访问器与回调，避免直接依赖 editor.py 闭包。所有方法假设当前激活行是
CODE 块（由调用方 _on_key_down 判断 block_type 后再调用）。
"""

from collections.abc import Awaitable, Callable

import flet as ft

from models import BlockType, Line
from state.cursor import CursorState

# 代码块缩进宽度（4 空格，与原 _code_indent_width 一致）
_INDENT = "    "


class CodeBlockEditor:
    """代码块内部编辑操作集合。

    通过注入的回调操作外部状态（draft / cursor / active / dirty），
    不直接持有 editor.py 闭包，便于测试与复用。

    cursor_ref 期望是 ft.Ref[CursorState]（Step 7.1 接入后）。
    """

    def __init__(
        self,
        get_active: Callable[[], tuple[int, int] | None],
        get_line: Callable[[int], Line | None],
        draft_ref: ft.Ref,
        cursor_ref: ft.Ref,  # ft.Ref[CursorState]
        active_field_ref: ft.Ref,
        selection_text_ref: ft.Ref,
        clipboard_ref: ft.Ref | None,
        set_draft: Callable[[str], None],
        mark_dirty: Callable[[], None],
        commit_active: Callable[[], None],
        suppress_blur: Callable[[], None],
        deactivate: Callable[[], None],
    ):
        self._get_active = get_active
        self._get_line = get_line
        self._draft_ref = draft_ref
        self._cursor_ref = cursor_ref
        self._active_field_ref = active_field_ref
        self._selection_text_ref = selection_text_ref
        self._clipboard_ref = clipboard_ref
        self._set_draft = set_draft
        self._mark_dirty = mark_dirty
        self._commit_active = commit_active
        self._suppress_blur = suppress_blur
        self._deactivate = deactivate

    # ---- 内部状态访问 ----
    def _text(self) -> str:
        return self._draft_ref.current

    def _selection(self) -> tuple[int, int]:
        cur = self._cursor_ref.current
        return cur.base, cur.extent

    def _set_selection(self, pos: int) -> None:
        cur = self._cursor_ref.current
        cur.base = pos
        cur.extent = pos

    def _selection_text(self) -> str:
        return self._selection_text_ref.current or ""

    def _sync_editor_selection(self, pos: int) -> None:
        """显式同步 TextField.selection（绕过 frozen 状态）。

        整段复制自原 editor.py _sync_code_editor_selection，保留 _frozen hack
        以避免触发不必要的 on_selection_change。
        """
        if self._active_field_ref is None:
            return
        ctrl = self._active_field_ref.current
        if ctrl is None:
            return
        frozen = getattr(ctrl, "_frozen", None)
        if frozen is not None:
            del ctrl._frozen
        try:
            ctrl.selection = ft.TextSelection(base_offset=pos, extent_offset=pos)
            ctrl.update()
        except Exception:
            pass
        finally:
            if frozen is not None:
                ctrl._frozen = frozen

    def _is_code_active(self) -> bool:
        active = self._get_active()
        if active is None:
            return False
        line = self._get_line(active[0])
        return line is not None and line.block_type == BlockType.CODE

    # ---- 公开操作 ----
    def tab(self, delta: int) -> None:
        """Tab/Shift+Tab：多行缩进 / 反缩进，或单行插入 / 删除缩进。"""
        if not self._is_code_active():
            return
        text = self._text()
        base, extent = self._selection()
        if base != extent:
            start, end = sorted((base, extent))
            lines = text.split("\n")
            offsets = []
            pos = 0
            for i, line in enumerate(lines):
                offsets.append((pos, pos + len(line), i))
                pos += len(line) + 1
            affected = [i for s, e, i in offsets if not (e < start or s > end)]
            if not affected:
                return
            if delta > 0:
                for i in affected:
                    lines[i] = _INDENT + lines[i]
                self._set_draft("\n".join(lines))
                pos = end + len(_INDENT) * len(affected)
                self._set_selection(pos)
                self._sync_editor_selection(pos)
            else:
                for i in affected:
                    if lines[i].startswith(_INDENT):
                        lines[i] = lines[i][len(_INDENT) :]
                self._set_draft("\n".join(lines))
                pos = max(0, start - len(_INDENT) * len(affected))
                self._set_selection(pos)
                self._sync_editor_selection(pos)
            return
        if delta > 0:
            self._set_draft(text + _INDENT)
            pos = len(text) + len(_INDENT)
            self._set_selection(pos)
            self._sync_editor_selection(pos)
        else:
            if text.endswith(_INDENT):
                self._set_draft(text[: -len(_INDENT)])
                pos = max(0, len(text) - len(_INDENT))
                self._set_selection(pos)
                self._sync_editor_selection(pos)

    def backspace(self) -> None:
        """Backspace：选区删除 / 行首反缩进 / 软换行前导缩进删除 / 选区文本删除。"""
        if not self._is_code_active():
            return
        text = self._text()
        base, extent = self._selection()
        sel = self._selection_text()
        if base != extent:
            start, end = sorted((base, extent))
            self._set_draft(text[:start] + text[end:])
            self._set_selection(start)
            self._sync_editor_selection(start)
            return
        left = text[:base]
        if left.endswith(_INDENT):
            self._set_draft(left[: -len(_INDENT)] + text[extent:])
            pos = base - len(_INDENT)
            self._set_selection(pos)
            self._sync_editor_selection(pos)
        elif left.endswith("\n"):
            prev_nl = left[:-1].rfind("\n")
            line_start = prev_nl + 1
            prefix = left[line_start:base]
            if prefix == _INDENT:
                self._set_draft(text[:line_start] + text[base:])
                self._set_selection(line_start)
                self._sync_editor_selection(line_start)
        elif sel:
            self._set_draft(text[:base] + text[extent:])
            self._set_selection(base)

    def delete(self) -> None:
        """Delete：仅处理行尾换行符合并（选区已由 TextField 处理）。"""
        if not self._is_code_active():
            return
        text = self._text()
        base, extent = self._selection()
        if base != extent:
            return
        if base < len(text) and text[base] == "\n":
            self._set_draft(text[:base] + text[base + 1 :])
            self._sync_editor_selection(base)

    def enter(self) -> None:
        """Enter：自动继承当前行缩进；行尾 : { [ ( 时额外 +1 级缩进。"""
        if not self._is_code_active():
            return
        text = self._text()
        base, extent = self._selection()
        if base != extent:
            start, end = sorted((base, extent))
            self._set_draft(text[:start] + text[end:])
            self._set_selection(start)
            return
        left = text[:base]
        line_start = left.rfind("\n") + 1
        current = left[line_start:]
        indent = len(current) - len(current.lstrip(" \t"))
        prefix = current[:indent]
        extra = prefix
        trimmed = current.rstrip()
        if trimmed.endswith((":", "{", "[", "(")):
            extra = prefix + _INDENT
        insert = "\n" + extra
        self._set_draft(text[:base] + insert + text[extent:])
        pos = base + len(insert)
        self._set_selection(pos)
        self._sync_editor_selection(pos)

    def exit(self) -> None:
        """退出代码块编辑：提交草稿 + 抑制 blur + 取消激活。"""
        if not self._is_code_active():
            return
        self._commit_active()
        self._suppress_blur()
        self._deactivate()

    async def copy_code(self, text: str) -> None:
        """复制代码到剪贴板（供 line_view.py 代码块复制按钮调用）。"""
        clipboard = self._clipboard_ref.current if self._clipboard_ref is not None else None
        if clipboard is None:
            return
        try:
            await clipboard.set(text)
        except Exception:
            return
