"""业务逻辑层：快捷键管理、文件 IO。

子模块：
- shortcuts：ShortcutManager，快捷键读取/更新/冲突检测
- file_io：read_text / write_text，文件读写工具

历史记录（EditHistory）已迁移至 core/history.py，与编辑器核心状态同层。
"""

from services import file_io, shortcuts  # noqa: F401
