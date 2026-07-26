"""编辑历史：撤销 / 重做栈。

依赖项：标准库 dataclasses。
对外接口：EditorSnapshot、EditHistory。

从 services/history.py 迁移到 core/，与 cursor.py、actions.py 同层，
因为三者共同构成编辑器核心状态管理。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorSnapshot:
    """编辑器可恢复状态（Stack 双层光标级架构）。

    markdown：序列化后的 Markdown 文本（raw_mode 下为 raw_draft）
    cursor_li：激活行号 | None（浏览态）
    cursor_off：行级 raw 偏移 0..len(line.raw)
    raw_mode：是否原文模式
    raw_draft：原文模式下的草稿
    """

    markdown: str
    cursor_li: int | None
    cursor_off: int
    raw_mode: bool
    raw_draft: str


@dataclass(frozen=True)
class LineEditSnapshot:
    """行级编辑快照：单行 raw 恢复目标 + 光标恢复点。

    适用：字符输入 / backspace / delete / 行内格式包裹等单行编辑。
    不适用：行增删（on_submit / _delete_raw_range / handle_paste 多行），
    这些仍用 EditorSnapshot 全文快照（行结构变化无法用单行表达）。

    raw：恢复时 reparse 到 line_idx 行的 raw 文本。
    - 在 undo 栈上：raw = 编辑前的 raw（撤销恢复到此）
    - 在 redo 栈上：raw = 编辑后的 raw（重做恢复到此，由 undo() 时构造 current 捕获）

    内存 O(1)/操作，大文档撤销栈不再存全文序列化。
    """

    line_idx: int
    raw: str
    cursor_li: int | None
    cursor_off: int
    raw_mode: bool
    raw_draft: str


# 快照联合类型（撤销 / 重做栈元素）
Snapshot = EditorSnapshot | LineEditSnapshot


class EditHistory:
    """撤销 / 重做栈（固定容量，混合行级 + 全文快照）。

    容量超限时丢弃最旧撤销项；新撤销入栈时清空重做栈。
    相邻相同快照去重（push 时比较末项；两类快照字段不同永不相等，安全）。
    """

    def __init__(self, max_size: int = 50):
        self._max = max_size
        self.undo: list[Snapshot] = []
        self.redo: list[Snapshot] = []

    def push(self, snap: Snapshot) -> None:
        """入栈撤销项；与末项相同则去重；超限丢弃最旧；清空重做栈。"""
        if self.undo and self.undo[-1] == snap:
            return
        self.undo.append(snap)
        if len(self.undo) > self._max:
            self.undo.pop(0)
        self.redo.clear()

    def pop_undo(self, current: Snapshot) -> Snapshot | None:
        """撤销：弹出末项，当前状态入重做栈。"""
        if not self.undo:
            return None
        self.redo.append(current)
        return self.undo.pop()

    def pop_redo(self, current: Snapshot) -> Snapshot | None:
        """重做：弹出末项，当前状态入撤销栈。"""
        if not self.redo:
            return None
        self.undo.append(current)
        return self.redo.pop()
