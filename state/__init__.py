"""状态层：编辑器光标与对外动作的 dataclass 抽象。

把原先散落在 editor.py 的 cursor_ref dict（"base"/"extent"/"draft_len"）与
nav_ref dict（20+ 字符串 key）替换为类型安全的 dataclass，让 main.py 的 on_key
通过属性访问，缺失字段在构造时即报错（替代静默失败）。
"""

from state.actions import EditorActions
from state.cursor import CursorState

__all__ = ["CursorState", "EditorActions"]
