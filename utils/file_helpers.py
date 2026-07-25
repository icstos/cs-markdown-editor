"""文件名/路径派生工具。

依赖项：标准库 os。
对外接口：
- file_name(path: str | None) -> str：从路径派生文件名，无路径回退「未命名.md」

消除重复：原先 main.py / views/editor.py / views/status_bar.py / views/tab_bar.py
各有一份完全相同的 _file_name 实现，此处统一为单一来源。
"""

import os

_UNTITLED = "未命名.md"


def file_name(path: str | None) -> str:
    """从绝对路径派生文件名；path 为 None 或空时回退「未命名.md」。"""
    return os.path.basename(path) if path else _UNTITLED
