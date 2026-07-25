"""光标状态：TextField 光标位置镜像（ref 而非 state）。

依赖项：标准库 dataclasses。
对外接口：CursorState。

行为约束（来自项目 memory Hard Constraints）：
- base / extent 必须用 ft.use_ref(CursorState()) 而非 state，避免重渲染打断 IME
- base/extent 在 _set_cursor 与 handle_char_input 中通过 reset() 同步更新
- line_view 与 backspace_core/delete_core 通过 cursor_ref.current.base 读取
  IME 期间最新光标位置（cursor_off state 在 IME 期间不更新）
"""

from dataclasses import dataclass


@dataclass
class CursorState:
    """TextField 光标位置镜像（ref 而非 state）。

    base/extent：当前选区起止偏移（无选区时 base == extent）
    draft_len：行 raw 总长度，用于光标越界钳制
    """

    base: int = 0
    extent: int = 0
    draft_len: int = 0

    def reset(self, pos: int, draft_len: int) -> None:
        """同时重置 base/extent/draft_len（跨段导航 / 块切换时）。"""
        self.base = pos
        self.extent = pos
        self.draft_len = draft_len
