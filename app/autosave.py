"""自动保存：延时 2s 保存脏标签（debounce）。

把 main.py 的 _autosave_enabled_for / _schedule_autosave 迁移为独立模块。
原闭包依赖通过 AutosaveContext dataclass 注入，解耦闭包状态。

依赖项：
- 标准库 asyncio
- app._tab_helpers（tab_is_dirty / tab_paths）
- flet（ft.Ref 类型注解）

对外接口：
- autosave_enabled_for(settings, tab)：自动保存是否对该标签生效
- schedule_autosave(ctx)：延时 2s 自动保存当前激活标签

设计要点：
- autosave_enabled_for 为纯函数（仅依赖 settings + tab），可独立单测
- schedule_autosave 通过 AutosaveContext 注入 page_ref / tabs_ref /
  active_index_ref / save_doc 回调，避免闭包捕获渲染期快照
- 捕获调度时的 active_index，即便用户切换到其他标签，仍保存当初变脏的标签
"""

import asyncio
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
    """自动保存上下文：注入闭包依赖，避免 schedule_autosave 捕获渲染期快照。

    - settings：当前设置（含 auto_save 开关）
    - page_ref：ft.Ref，指向 page 对象（用于 run_task 调度协程）
    - tabs_ref：ft.Ref，指向最新 tabs 列表（异步读取避免 stale）
    - active_index_ref：ft.Ref，指向当前激活标签索引
    - cur_tab_fn：返回当前激活标签的回调（从 ref 读取最新值）
    - save_doc_fn：异步保存回调，签名为 async (tab_index: int) -> None
    """

    settings: dict[str, Any]
    page_ref: ft.Ref
    tabs_ref: ft.Ref
    active_index_ref: ft.Ref
    cur_tab_fn: Callable[[], Tab | None]
    save_doc_fn: Callable[[int], Any]


def schedule_autosave(ctx: AutosaveContext) -> None:
    """基于 ctx 读取当前激活标签，延时 2s 自动保存该标签。

    捕获调度时的 active_index，即便用户切换到其他标签，仍保存当初变脏的标签。
    """
    tab = ctx.cur_tab_fn()
    if not tab or not tab_is_dirty(tab) or not autosave_enabled_for(ctx.settings, tab):
        return
    page = ctx.page_ref.current
    if page is None:
        return
    sched_idx = ctx.active_index_ref.current

    async def _debounced_save() -> None:
        await asyncio.sleep(2.0)
        ts = ctx.tabs_ref.current
        if not (0 <= sched_idx < len(ts)):
            return
        t2 = ts[sched_idx]
        if tab_is_dirty(t2) and autosave_enabled_for(ctx.settings, t2):
            await ctx.save_doc_fn(sched_idx)

    page.run_task(_debounced_save)
