"""标签页纯计算助手：从 App 闭包剥离的无状态函数。

依赖项：
- 标准库 os
- utils.file_helpers（file_name：路径 → 文件名）

对外接口（均为内部助手，下划线前缀）：
- tab_is_dirty(tab)：统一脏状态判断（diff 标签任一侧脏即为脏）
- tab_paths(tab)：统一路径列表（diff 标签返回两侧路径）
- doc_has_text(doc)：文档是否有可见文本
- is_blank_untitled(tab)：是否为空白未命名标签（可复用为新建/打开载体）
- tab_display_name(tab)：统一标签显示名（diff 标签显示「left ⟷ right」）

设计要点：
- 这些函数原本定义在 App 组件闭包内（每次重渲染重新创建）或 main.py 模块级，
  统一迁出到本模块后：①不再随渲染重建；②可独立单元测试；
  ③为阶段 4 控制器封装提供无状态复用基础。
- 纯度判定：函数体内不读 tabs / active_index / settings / *_ref.current / set_*，
  只通过参数进出。读取闭包状态的函数（如 _autosave_enabled_for 依赖 settings）
  不在此处，仍留在闭包内（阶段 2 迁入 app/autosave.py）。
- Tab 类型别名（PEP 695）统一定义在此处，main.py 反向导入。
"""

import os
from typing import Any

from utils.file_helpers import file_name

# PEP 695 类型别名：标签页字典（统一 tab 字段注解，替代裸 dict）
type Tab = dict[str, Any]


def tab_is_dirty(tab: Tab) -> bool:
    """统一脏状态判断：diff 标签任一侧脏即为脏，否则取 dirty 字段。"""
    if tab.get("type") == "diff":
        return bool(tab.get("left_dirty")) or bool(tab.get("right_dirty"))
    return bool(tab.get("dirty", False))


def tab_paths(tab: Tab) -> list[str]:
    """统一路径列表：diff 标签返回 [left_path, right_path]，否则 [file_path]。

    用于文件重命名同步、比较文本获取等需要按路径匹配标签的场景。
    """
    if tab.get("type") == "diff":
        return [p for p in (tab.get("left_path"), tab.get("right_path")) if p]
    p = tab.get("file_path")
    return [p] if p else []


def doc_has_text(doc) -> bool:
    """文档是否有可见文本（任一行 raw 非空白）。"""
    return any(line.raw.strip() for line in doc.lines)


def is_blank_untitled(tab: Tab) -> bool:
    """是否为空白未命名标签（可复用为新建/打开的载体）。

    diff 标签始终非空白（不复用为新建/打开的载体）。
    """
    if tab.get("type") == "diff":
        return False
    return (
        tab.get("file_path") is None
        and not tab.get("dirty")
        and not doc_has_text(tab["document"])
    )


def tab_display_name(tab: Tab) -> str:
    """统一标签显示名：diff 标签显示「left ⟷ right」，否则取文件名。"""
    if tab.get("type") == "diff":
        left = os.path.basename(tab.get("left_path")) if tab.get("left_path") else "未命名"
        right = os.path.basename(tab.get("right_path")) if tab.get("right_path") else "未命名"
        return f"{left} ⟷ {right}"
    return file_name(tab.get("file_path"))
