"""编辑历史：撤销 / 重做栈。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorSnapshot:
    """编辑器可恢复状态（Stack 双层光标级架构）。"""

    markdown: str
    cursor_li: int | None  # 激活行号 | None（浏览态）
    cursor_off: int  # 行级 raw 偏移 0..len(line.raw)
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
