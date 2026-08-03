"""自动保存：间隔触发（Typora 风格）。

设计变更：原为 2s debounce 保存（编辑即触发）；现改为间隔触发——
由 _backup_controller 启动定时器，每 auto_save_interval 分钟扫描全部
脏标签并写入原文件。窗口失焦/最小化时由控制器即时触发一次。

保留 schedule_autosave 入口（_focus_router.on_dirty_change 调用），
但实现为空操作：间隔触发模型下「变脏」无需立即调度，定时器会在下个
tick 自动覆盖。保留入口避免大规模改动调用方。

依赖项：
- 标准库 asyncio
- app._tab_helpers（tab_is_dirty / tab_paths）
- flet（ft.Ref 类型注解）

对外接口：
- autosave_enabled_for(settings, tab)：自动保存是否对该标签生效
- autosave_all_dirty(ctx)：扫描 tabs_ref，对所有脏且可自动保存的标签
  异步触发 save_doc。返回本次触发的标签数量。
- AutosaveContext：依赖注入容器（保留供未来扩展与单测复用）
- schedule_autosave(ctx)：兼容入口，间隔触发模型下为空操作
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from app._tab_helpers import Tab, tab_is_dirty, tab_paths


def autosave_enabled_for(settings: dict[str, Any], tab: Tab | None) -> bool:
    """自动保存是否对该标签生效：需开启 auto_save 且标签有可写路径。

    对比标签任一侧有路径即生效；普通标签需有 file_path。
    """
    if not settings.get("auto_save", False) or not tab:
        return False
    return bool(tab_paths(tab))


@dataclass(slots=True)
class AutosaveContext:
    """自动保存上下文：注入闭包依赖，避免调度协程捕获渲染期快照。

    - settings：当前设置（含 auto_save 开关与 auto_save_interval 间隔）
    - page_ref：ft.Ref，指向 page 对象（用于 run_task 调度协程）
    - tabs_ref：ft.Ref，指向最新 tabs 列表
    - save_doc_fn：异步保存回调，签名为 async (tab_index: int) -> bool
    - set_status_fn：状态栏消息推送 (msg, kind) -> None
    """

    settings: dict[str, Any]
    page_ref: ft.Ref
    tabs_ref: ft.Ref
    save_doc_fn: Callable[[int], Any]
    set_status_fn: Callable[[str, str], None] | None = None


def autosave_all_dirty(ctx: AutosaveContext) -> int:
    """扫描 tabs_ref，对所有脏且可自动保存的标签异步触发 save_doc。

    返回本次触发的标签数量（仅调度，不等待完成）。调度通过 page.run_task
    异步执行，避免阻塞调用方。save_doc 内部已实现原子写入 + 覆盖前备份 +
    写入失败兜底，此处仅负责筛选与调度。
    """
    page = ctx.page_ref.current
    if page is None:
        return 0
    if not ctx.settings.get("auto_save", False):
        return 0
    ts = ctx.tabs_ref.current or []
    triggered = 0
    for i, tab in enumerate(ts):
        if tab_is_dirty(tab) and autosave_enabled_for(ctx.settings, tab):
            page.run_task(ctx.save_doc_fn, i)
            triggered += 1
    if triggered and ctx.set_status_fn is not None:
        ctx.set_status_fn("已自动保存", "success")
    return triggered


def schedule_autosave(ctx: AutosaveContext) -> None:
    """兼容入口：间隔触发模型下为空操作。

    历史上 on_dirty_change 触发 2s debounce 保存；现改为定时器扫描，
    变脏事件无需立即调度。保留入口避免改动 _focus_router 调用链。
    """
    return None
