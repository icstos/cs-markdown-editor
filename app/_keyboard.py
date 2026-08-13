"""键盘分发控制器（从 main.py 闭包抽取）。

闭包组：build_dispatcher / bind_keyboard

跨组依赖（通过 ctx 装配槽，调用时读取）：
- file_io_ops 组：save_doc / new_doc / open_doc
- settings_controller 组：toggle_sidebar / toggle_theme / toggle_word_wrap /
  toggle_split_editor / open_settings / on_capture / on_cancel_capture
- tab_management 组：close_tab / cycle_tab
- split_editor 组：无（active_nav_ref 选择基于 state 快照，不调用 set_*）

设计要点：
- KeyDispatcher 替代 on_key 闭包。持有 shortcut_mgr + nav_ref 引用，
  editor.py 每次渲染写入最新 EditorActions 后 dispatcher 读到的就是最新值，
  无需 on_key_ref 中转层。
- dispatcher_ref 渲染期同步赋值最新 dispatcher，_handler 通过 ref 读取。
  修复 use_effect(_bind_keyboard, []) 空依赖导致 _handler 闭包捕获首次渲染
  dispatcher 的过期问题——改快捷键后新键位才能立即生效（无需重启）。
- 拆分/对比编辑器：根据当前模式选择对应视口的 nav_ref，键盘事件作用于焦点视口。
- use_ref（paste_old_draft / dispatcher_ref）与 use_effect 调用留在 __init__.py
  （hooks 顺序约束），本控制器仅返回 dispatcher 实例与 bind_keyboard 回调。

依赖项：
- contextlib（_cleanup 静默清理 on_keyboard_event）
- flet
- views.key_bindings.KeyDispatcher
"""

import contextlib

import flet as ft

from views.key_bindings import KeyDispatcher


def build_keyboard(ctx):
    """构造键盘分发控制器。

    返回 dict[str, Any]：
    dispatcher（KeyDispatcher 实例，渲染期由 __init__.py 写入 dispatcher_ref）
    bind_keyboard（use_effect 回调，绑定 page.on_keyboard_event）
    """

    # 拆分/对比编辑器：根据当前模式选择对应视口的 nav_ref
    if ctx.is_diff_tab:
        active_nav_ref = ctx.diff_nav_right if ctx.diff_active_pane == 1 else ctx.diff_nav_left
    elif ctx.split_editor and ctx.active_pane == 1:
        active_nav_ref = ctx.nav_ref_split
    else:
        active_nav_ref = ctx.nav_ref

    dispatcher = KeyDispatcher(
        shortcut_mgr=ctx.shortcut_mgr,
        actions_ref=active_nav_ref,
        clipboard_ref=ctx.clipboard_holder,
        page_ref=ctx.page_ref,
        paste_old_draft=ctx.paste_old_draft,
        app_callbacks={
            "save": ctx.save_doc,
            "save_as": ctx.save_as_doc,
            "new": ctx.new_doc,
            "open": ctx.open_doc,
            "open_folder": ctx.open_folder,
            "toggle_sidebar": ctx.toggle_sidebar,
            "toggle_theme": ctx.toggle_theme,
            "toggle_word_wrap": ctx.toggle_word_wrap,
            "zoom_in": ctx.zoom_in,
            "zoom_out": ctx.zoom_out,
            "zoom_reset": ctx.zoom_reset,
            "toggle_split_editor": ctx.toggle_split_editor,
            "open_settings": ctx.open_settings,
            "close_tab": lambda: ctx.close_tab(ctx.active_index_ref.current),
            "next_tab": lambda: ctx.cycle_tab(1),
            "prev_tab": lambda: ctx.cycle_tab(-1),
            "focus_search": ctx.focus_search,
            "toggle_replace_bar": ctx.toggle_replace_bar,
            "replace_current": ctx.replace_current,
            "replace_all": ctx.replace_all,
        },
        capturing=ctx.capturing,
        on_capture=ctx.on_capture,
        on_cancel_capture=ctx.on_cancel_capture,
    )

    def bind_keyboard():
        """use_effect 回调：绑定 page.on_keyboard_event 到最新 dispatcher。

        通过 dispatcher_ref 读最新 dispatcher，避免闭包捕获首次渲染的过期实例。
        """
        page = ft.context.page
        ctx.page_ref.current = page
        if page is None:
            return lambda: None

        def _handler(e):
            # 通过 ref 读最新 dispatcher，避免闭包捕获首次渲染的过期实例
            d = ctx.dispatcher_ref.current
            if d is None:
                return
            try:
                d.handle(e)
            except Exception:
                return

        page.on_keyboard_event = _handler

        def _cleanup():
            if ctx.page_ref.current is not None:
                with contextlib.suppress(Exception):
                    ctx.page_ref.current.on_keyboard_event = None

        return _cleanup

    return {
        "dispatcher": dispatcher,
        "bind_keyboard": bind_keyboard,
    }
