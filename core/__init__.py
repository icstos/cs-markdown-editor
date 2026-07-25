"""核心流程层：编辑器状态容器与对外动作集合。

替代原 state/ 目录，整合 state/actions.py、state/cursor.py、services/history.py
三个紧密耦合的编辑器核心状态模块到同一目录。

子模块：
- cursor：CursorState，TextField 光标位置镜像（ref 而非 state）
- actions：EditorActions，编辑器对外动作集合（main.py on_key 据此分发）
- history：EditHistory，撤销/重做栈

对外接口：core.CursorState / core.EditorActions / core.EditHistory / core.EditorSnapshot
"""

from core.actions import EditorActions
from core.cursor import CursorState
from core.history import EditHistory, EditorSnapshot

__all__ = ["CursorState", "EditorActions", "EditHistory", "EditorSnapshot"]
