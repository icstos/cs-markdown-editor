"""光标状态：替代 cursor_ref 的 dict["base"/"extent"/"draft_len"]。

行为约束（来自 memory Hard Constraints #3/#34/#35）：
- cursor_base / cursor_extent 必须用 ft.use_ref(CursorState()) 而非 state，
  避免 on_selection_change 触发重渲染导致光标跳动
- delete_core 用 len(draft_ref.current) 判段尾而非 CursorState.draft_len
  （on_selection_change 不可靠，draft_len 仅作安全网）
- on_change_draft 同步更新 CursorState.draft_len 作为安全网
"""

from dataclasses import dataclass


@dataclass
class CursorState:
    """TextField 光标位置镜像（ref 而非 state）。"""

    base: int = 0
    extent: int = 0
    draft_len: int = 0

    def reset(self, pos: int, draft_len: int) -> None:
        """同时重置 base/extent/draft_len（跨段导航 / 块切换时）。"""
        self.base = pos
        self.extent = pos
        self.draft_len = draft_len
