"""EditorActions 装配工厂（从 views/editor.py 抽取）。

从 ctx 装配槽读取各工厂产出的闭包，构造 EditorActions 写入 nav_ref.current，
供 main.py 的 KeyDispatcher / 状态栏 / on_key 通过属性访问调用。

行为约束（来自 core/actions.py docstring）：
- 必填字段在 dataclass 构造时即校验（slots=True 缺失立即 TypeError）
- cursor_ref 为 ft.Ref[CursorState]：main.py 实时读取 .current.base/.extent
- nav_seq 仅撤销/重做递增（同行输入不递增以保 IME 组合态）

依赖项：
- core.actions.EditorActions
- models.Line（active_line 类型，无需直接导入——document.lines[i] 即 Line）
"""

from core.actions import EditorActions


def build_actions(ctx) -> EditorActions | None:
    """构造 EditorActions 并写入 nav_ref.current。

    nav_ref 为 None 时（如右侧拆分编辑器无全局快捷键需求）跳过写入，返回 None。
    每次渲染调用：ctx 装配槽已在工厂调用阶段填充完毕，此处仅读取装配。
    """
    if ctx.nav_ref is None:
        return None

    active_line = (
        ctx.document.lines[ctx.cursor_li]
        if ctx.cursor_li is not None and 0 <= ctx.cursor_li < len(ctx.document.lines)
        else None
    )

    actions = EditorActions(
        # ---- 当前状态 ----
        cursor_li=ctx.cursor_li,
        cursor_off=ctx.cursor_off,
        active_line=active_line,
        raw_mode=ctx.raw_mode,
        cursor_ref=ctx.cursor_ref,
        selection_text_ref=ctx.selection_text_ref,
        nav_seq=ctx.nav_seq,
        # ---- 行间光标导航 ----
        move_left=ctx.move_left,
        move_right=ctx.move_right,
        move_home=ctx.move_home,
        move_end=ctx.move_end,
        move_doc_start=ctx.move_doc_start,
        move_doc_end=ctx.move_doc_end,
        move_up=ctx.move_up,
        move_down=ctx.move_down,
        page_up=ctx.page_up,
        page_down=ctx.page_down,
        link_tab_jump=ctx.link_tab_jump,
        # ---- 删除 / 缩进 ----
        backspace_core=ctx.backspace_core,
        delete_core=ctx.delete_core,
        indent_or_outdent=ctx.indent_or_outdent,
        # ---- 剪贴板 / 选区 ----
        handle_paste=ctx.handle_paste,
        handle_paste_plain=ctx.handle_paste_plain,
        handle_cut=ctx.handle_cut,
        handle_delete_selection=ctx.handle_delete_selection,
        apply_inline_format_to_selection=ctx.apply_inline_format_to_selection,
        compute_markdown_from_text=ctx.compute_markdown_from_text,
        paste_image_from_clipboard=ctx.paste_image_from_clipboard,
        # ---- 全局动作 ----
        undo=ctx.undo,
        redo=ctx.redo,
        jump_to_line=ctx.jump_to,
        toggle_raw=ctx.toggle_raw,
        toggle_focus_mode=ctx.toggle_focus_mode,
        set_block=ctx.set_block,
        apply_inline_format=ctx.apply_inline_format,
        toggle_task_at_cursor=ctx.toggle_task_at_cursor,
        format_task=ctx.format_task,
        format_table=ctx.format_table,
        insert_text=ctx.insert_text,
        # ---- 代码块 ----
        code_focus_ref=ctx.code_focus_ref,
        handle_code_backspace=ctx.handle_code_backspace,
        # ---- 表格 ----
        table_focus_ref=ctx.table_focus_ref,
        # ---- 状态栏 ----
        get_cursor_row_col=ctx.get_cursor_row_col,
        # ---- 向外选区 ----
        outward_sel=ctx.outward_sel,
        math_focus_ref=ctx.math_focus_ref,
        shift_pressed_ref=ctx.shift_pressed_ref,
        ctrl_pressed_ref=ctx.ctrl_pressed_ref,
        alt_pressed_ref=ctx.alt_pressed_ref,
        extend_outward_left=lambda: ctx.extend_outward_step(ctx.step_left),
        extend_outward_right=lambda: ctx.extend_outward_step(ctx.step_right),
        extend_outward_up=lambda: ctx.extend_outward_step(ctx.step_up),
        extend_outward_down=lambda: ctx.extend_outward_step(ctx.step_down),
        extend_outward_home=lambda: ctx.extend_outward_step(ctx.step_home),
        extend_outward_end=lambda: ctx.extend_outward_step(ctx.step_end),
        handle_outward_cut=ctx.handle_outward_cut,
        handle_outward_delete=ctx.handle_outward_delete,
        handle_outward_enter=ctx.handle_outward_enter,
        handle_outward_copy=ctx.handle_outward_copy,
        clear_outward_sel=ctx.clear_outward_sel,
        select_all=ctx.select_all,
        cut_current_line=ctx.cut_current_line,
        handle_outward_type_char=ctx.handle_outward_type_char,
        # ---- 多光标 ----
        has_secondary_cursors=ctx.has_secondary_cursors,
        clear_secondary_cursors=ctx.clear_secondary_cursors,
        extend_selection_left=ctx.extend_selection_left,
        extend_selection_right=ctx.extend_selection_right,
        extend_selection_home=ctx.extend_selection_home,
        extend_selection_end=ctx.extend_selection_end,
        has_multi_cursor_selection=ctx.has_multi_cursor_selection,
        copy_multi_cursor_selection=ctx.copy_multi_cursor_selection,
        cut_multi_cursor_selection=ctx.cut_multi_cursor_selection,
        paste_to_multi_cursors=ctx.paste_to_multi_cursors,
        paste_to_multi_cursors_plain=ctx.paste_to_multi_cursors_plain,
        # ---- 粘贴进行中标志（拦截原生 TextField 单行粘贴 on_change 干扰）----
        paste_in_progress_ref=ctx.paste_in_progress_ref,
        # ---- 滚动同步 ----
        get_scroll_state=ctx.get_scroll_state,
        scroll_to_offset=ctx.scroll_to_offset,
        # ---- 替换（搜索面板触发，作用于当前文档）----
        replace_match_in_doc=ctx.replace_match_in_doc,
        replace_all_in_doc=ctx.replace_all_in_doc,
    )
    ctx.nav_ref.current = actions
    return actions
