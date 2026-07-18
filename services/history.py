"""编辑历史：撤销 / 重做栈。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorSnapshot:
    """编辑器可恢复状态。"""

    markdown: str
    active: int | None  # line_idx | None
    active_seg: int | None  # seg_idx | None（段级编辑）
    draft: str
    cursor_base: int
    cursor_extent: int
    raw_mode: bool
    raw_draft: str


class EditHistory:
    """撤销 / 重做栈（固定容量）。"""

    def __init__(self, max_size: int = 50):
        self._max = max_size
        self.undo: list[EditorSnapshot] = []
        self.redo: list[EditorSnapshot] = []

    def push(self, snap: EditorSnapshot) -> None:
        if self.undo and self.undo[-1] == snap:
            return
        self.undo.append(snap)
        if len(self.undo) > self._max:
            self.undo.pop(0)
        self.redo.clear()

    def pop_undo(self, current: EditorSnapshot) -> EditorSnapshot | None:
        if not self.undo:
            return None
        self.redo.append(current)
        return self.undo.pop()

    def pop_redo(self, current: EditorSnapshot) -> EditorSnapshot | None:
        if not self.redo:
            return None
        self.undo.append(current)
        return self.redo.pop()
