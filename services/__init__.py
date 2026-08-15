"""业务逻辑层：快捷键管理、文件 IO。

子模块：
- shortcuts：ShortcutManager，快捷键读取/更新/冲突检测
- file_io：read_text / write_text，文件读写工具
- shortcut：Windows 快捷方式（.lnk）目标解析（纯 Python + PowerShell 回退 + 缓存）

历史记录（EditHistory）已迁移至 core/history.py，与编辑器核心状态同层。
"""

from services import file_io, shortcut, shortcuts  # noqa: F401
