"""flet 内部 API 的**单一隔离层**（仅测试夹具使用）。

产品代码只依赖 ``ui``，但进程内渲染夹具必须复现 flet 声明式组件的调度语义：
``Renderer`` / ``Session`` / ``_context_page`` / ``EffectHook`` 这些名字不在 flet
的公开导出里，路径随版本重组会静默断裂。把它们收敛到这一个模块，好处有三：

1. 升级 flet 时只需要改这里一处，其余测试文件不动；
2. ``grep "from flet" tests/`` 只会命中本文件，审查时能一眼看出耦合边界；
3. 与 ``tests/harness.py`` 的 ``_HarnessSession.schedule_effect`` 覆写配套 ——
   那里接管队列格式，这里集中导入内部类型，两者一起把「版本漂移」挡在夹具内。

这不是给应用用的公共 API：产品代码请一律 ``import ui``。
"""

from __future__ import annotations

from flet.components.component import Component, Renderer
from flet.components.hooks.use_effect import EffectHook
from flet.controls.context import _context_page
from flet.messaging.session import Session
from flet.utils.object_model import get_param_count, patch_dataclass

__all__ = [
    "Component",
    "EffectHook",
    "Renderer",
    "Session",
    "_context_page",
    "get_param_count",
    "patch_dataclass",
]
