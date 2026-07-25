"""光标状态：TextField 光标位置镜像（ref 而非 state）。

依赖项：标准库 dataclasses。
对外接口：CursorState。

行为约束（来自项目 memory Hard Constraints）：
- cursor_base / cursor_extent 必须用 ft.use_ref(CursorState()) 而非 state，
  避免 on_selection_change 触发重渲染导致光标跳动
- delete_core 用 len(draft_ref.current) 判段尾而非 CursorState.draft_len
  （on_selection_change 不可靠，draft_len 仅作安全网）
- on_change_draft 同步更新 CursorState.draft_len 作为安全网
"""

from dataclasses import dataclass


@dataclass
class CursorState:
    """TextField 光标位置镜像（ref 而非 state）。

    base/extent：当前选区起止偏移（无选区时 base == extent）
    draft_len：当前编辑段文本长度（安全网，避免 on_selection_change 不可靠）
    """

    base: int = 0
    extent: int = 0
    draft_len: int = 0

    def reset(self, pos: int, draft_len: int) -> None:
        """同时重置 base/extent/draft_len（跨段导航 / 块切换时）。"""
        self.base = pos
        self.extent = pos
        self.draft_len = draft_len
