"""YAML 前置元数据（frontmatter）的渲染与解析。

从 views/line_view.py 迁出：这是行渲染分支里最大的一块（原 612 行），
与其它行类型无关，且 `tests/test_frontmatter*.py` 已直接测试其中的纯函数。

对外接口：
- `render_frontmatter(...)`：Obsidian 风格可编辑属性卡片
- `parse_yaml_pairs` / `pairs_to_yaml`：纯函数（扁平 key: value 解析与回写）
- `drag_src_idx` / `reorder_pairs`：拖拽重排的属性行工具
"""

import re
from collections.abc import Callable

import flet as ft

from models.document import Line
from services.clipboard import (
    copy_code_to_clipboard,
    copy_text_to_clipboard,
    paste_row_from_clipboard,
)
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, _current_colors, only_border
from views import _block_frame

# 前置元数据属性行拖拽分组（避免与其他 Draggable 冲突）
_FM_DRAG_GROUP = "frontmatter-rows"



def render_frontmatter(
    line: Line,
    line_idx: int,
    base: int,
    content_width: float | None,
    clipboard_ref: ft.Ref | None,
    on_change_code: Callable[[int, str], None] | None,
    on_code_focus: Callable[[int], None] | None,
    on_code_blur: Callable[[int], None] | None,
    code_field_ref: ft.Ref | None,
    is_current_line: bool,
    is_flash: bool = False,
    on_line_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
) -> ft.Control:
    """YAML 前置元数据渲染（Obsidian 风格可编辑属性表格）。

    渲染态：键值对表格化展示，键/值均为 TextField 可直接编辑。
    整体卡片式带浅色背景和左侧彩色边框，可折叠/展开。
    增删改：底部"+"新增行，每行右侧"×"删除行，键/值 TextField 实时编辑。

    交互：
    - 键/值 TextField 直接编辑 → 实时序列化为 YAML 写回文档
    - 底部"添加属性"按钮 → 新增空属性行
    - 每行"×"按钮 → 删除该行
    - 折叠按钮 → 切换折叠/展开（折叠时仅显示首行摘要）
    - 复制按钮 → 复制原始 YAML 文本
    """
    c = _current_colors()
    content = line.segments[0].text if line.segments else ""
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK

    # 语义化数据类型颜色（科学区分 YAML 值类型，亮/暗主题各自适配）
    # 取色原则：每种类型一个固定色相，亮暗模式仅调整明度/饱和度
    if is_dark:
        _type_colors = {
            "bool":   "#6BA0F5",  # 柔蓝（真/假：逻辑值）
            "number": "#65C292",  # 柔薄荷绿（数值）
            "date":   "#DD9658",  # 柔琥珀橙（日期时间）
            "array":  "#B08FD8",  # 柔丁香紫（列表/字典）
            "null":   "#8B939E",  # 中性灰（空值）
            "string": "#E6EDF3",  # 主文本色（字符串）
        }
        _key_color = "#75A4F0"   # 柔雾蓝（键名 + 标题，突出属性标识）
    else:
        _type_colors = {
            "bool":   "#1677FF",  # Ant Design 蓝（逻辑值）
            "number": "#0E7C66",  # 深青绿（数值）
            "date":   "#B54708",  # 焦糖橙（日期时间）
            "array":  "#6B5B95",  # 雅致紫（列表/字典）
            "null":   "#8A919E",  # 中性灰（空值）
            "string": "#1F2329",  # 主文本色（字符串）
        }
        _key_color = "#1A4480"   # 深海军蓝（键名 + 标题，权威标识）

    # ---- 状态 ----
    copied, set_copied = ft.use_state(False)
    is_collapsed, set_collapsed = ft.use_state(False)
    # 复制/剪切的行缓冲（内部粘贴源，跨渲染保留）
    copied_row, set_copied_row = ft.use_state(None)
    # 拖拽悬停目标行索引（-1=无），用于合法目标高亮
    drop_hover, set_drop_hover = ft.use_state(-1)

    # ---- 解析键值对 ----
    pairs = parse_yaml_pairs(content) if content else []

    # ---- 复制按钮 ----
    copy_btn = ft.IconButton(
        icon=ft.Icons.CHECK if copied else ft.Icons.CONTENT_COPY,
        icon_size=13,
        tooltip="已复制" if copied else "复制 YAML",
        padding=ft.Padding.all(Spacing.SM),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            color=ft.Colors.GREEN if copied else c.muted,
        ),
        on_click=lambda e, txt=content: (
            page.run_task(copy_code_to_clipboard, clipboard_ref, txt, set_copied)
            if page is not None and not copied else None
        ),
    )

    # ---- 折叠按钮 ----
    collapse_btn = ft.IconButton(
        icon=ft.Icons.EXPAND_MORE if is_collapsed else ft.Icons.EXPAND_LESS,
        icon_size=13,
        tooltip="展开" if is_collapsed else "折叠",
        padding=ft.Padding.all(Spacing.SM),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            color=c.muted,
        ),
        on_click=lambda e: set_collapsed(not is_collapsed),
    )

    # ---- 头部工具栏 ----
    header = ft.Row(
        controls=[
            ft.Icon(ft.Icons.DATA_OBJECT, size=14, color=_key_color),
            ft.Text(
                value="YAML 前置元数据",
                size=11,
                color=_key_color,
                font_family=FONT_MAIN,
                weight=ft.FontWeight.W_600,
            ),
            ft.Container(expand=True),
            ft.Container(
                content=ft.Text(
                    value=f"{len(pairs)} 项" if pairs else "空",
                    size=10,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
                bgcolor=ft.Colors.with_opacity(0.06, c.text),
                padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                border_radius=Radius.SM,
            ),
            copy_btn,
            collapse_btn,
        ],
        spacing=Spacing.SM,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ---- 可编辑属性表格 ----
    _HANDLE_WIDTH = 22    # 拖拽把手列宽度
    _KEY_COL_WIDTH = 140  # 键列固定宽度
    _MORE_BTN_WIDTH = 30  # 行操作菜单（⋮）列宽度
    _DEL_BTN_WIDTH = 32   # 删除按钮列宽度

    # 编辑态键值对列表：本地 state 管理实时编辑，变化时序列化写回文档
    editing_pairs, set_editing_pairs = ft.use_state(
        [list(p) for p in pairs] if pairs else []
    )

    # 文档内容外部变更（撤销/重做/拆分视口对侧编辑/外部重载）时同步本地编辑态：
    # 否则撤销后表格仍显示修改前的旧内容，Ctrl+Z 看似失效。
    # 仅当文档内容与当前编辑态序列化不一致时才重置，避免打断正在输入的内容
    # （含键为空尚未写入文档的待定行），也不会与自身 _commit_pairs 写回产生回路。
    def _sync_editing_pairs() -> None:
        if content == pairs_to_yaml(editing_pairs):
            return
        set_editing_pairs([list(p) for p in pairs] if pairs else [])

    ft.use_effect(_sync_editing_pairs, [content])

    def _value_style(val: str) -> tuple[str, str]:
        """根据值内容推断 (color, font_family)。

        语义化数据类型着色：
        布尔值 → 蓝；数字 → 绿；日期 → 橙；列表/字典 → 紫；空 → 灰；字符串 → 主文本色。
        等宽字体用于布尔/数字/列表（结构化数据），正常字体用于日期/字符串（自然语言）。
        """
        if not val:
            return _type_colors["null"], FONT_MAIN
        vl = val.lower()
        if vl in ("true", "false", "yes", "no", "null", "~", "none"):
            return _type_colors["bool"], FONT_MONO
        stripped = val.replace(".", "").replace("-", "")
        if stripped.isdigit():
            return _type_colors["number"], FONT_MONO
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", val):
            return _type_colors["date"], FONT_MAIN
        if val.startswith("[") or val.startswith("{"):
            return _type_colors["array"], FONT_MONO
        return _type_colors["string"], FONT_MAIN

    def _commit_pairs(new_pairs: list[list[str]]) -> None:
        """把编辑后的键值对序列化为 YAML 写回文档。

        跳过键为空的行（避免 YAML 语法错误）；过滤后全空则写空串。
        """
        yaml_text = pairs_to_yaml(new_pairs)
        if on_change_code is not None:
            on_change_code(line_idx, yaml_text)

    def _on_key_change(idx: int, new_key: str) -> None:
        """键 TextField 输入变化：更新本地 state 并写回文档。"""
        new_pairs = [list(p) for p in editing_pairs]
        if idx < len(new_pairs):
            new_pairs[idx][0] = new_key
        else:
            new_pairs.append([new_key, ""])
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    def _on_value_change(idx: int, new_val: str) -> None:
        """值 TextField 输入变化：更新本地 state 并写回文档。"""
        new_pairs = [list(p) for p in editing_pairs]
        if idx < len(new_pairs):
            new_pairs[idx][1] = new_val
        else:
            new_pairs.append(["", new_val])
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    def _add_row() -> None:
        """新增空属性行。"""
        new_pairs = [list(p) for p in editing_pairs]
        new_pairs.append(["", ""])
        set_editing_pairs(new_pairs)
        # 不立即 commit：键为空的行不写入 YAML，等用户输入键后再写

    def _delete_row(idx: int) -> None:
        """删除指定属性行。"""
        new_pairs = [list(p) for p in editing_pairs]
        if 0 <= idx < len(new_pairs):
            new_pairs.pop(idx)
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    # ---- 行操作：复制 / 剪切 / 粘贴 / 删除（右键菜单 + ⋮ 按钮共用）----

    def _row_text(idx: int) -> str:
        """行序列化文本（"key: value"，用于系统剪贴板）。"""
        if not (0 <= idx < len(editing_pairs)):
            return ""
        p = editing_pairs[idx]
        return f"{p[0]}: {p[1]}" if p[0] else ""

    def _copy_row(idx: int) -> None:
        """复制行：写入内部缓冲区 + 系统剪贴板。"""
        if not (0 <= idx < len(editing_pairs)):
            return
        pair = editing_pairs[idx]
        set_copied_row((pair[0], pair[1]))
        if page is not None:
            page.run_task(copy_text_to_clipboard, clipboard_ref, _row_text(idx))

    def _cut_row(idx: int) -> None:
        """剪切行：复制 + 删除。"""
        _copy_row(idx)
        _delete_row(idx)

    def _paste_row(idx: int) -> None:
        """在 idx 行之后插入一行：优先内部缓冲区（本会话复制），否则读系统剪贴板。"""
        def _insert(k: str, v: str) -> None:
            new_pairs = [list(p) for p in editing_pairs]
            insert_at = min(idx + 1, len(new_pairs))
            new_pairs.insert(insert_at, [k, v])
            set_editing_pairs(new_pairs)
            _commit_pairs(new_pairs)
        if copied_row is not None:
            _insert(copied_row[0], copied_row[1])
            return
        if page is not None:
            page.run_task(paste_row_from_clipboard, clipboard_ref, _insert)

    def _row_menu_items(idx: int) -> list[ft.PopupMenuItem]:
        """行操作菜单项：剪切 / 复制 / 粘贴 / 删除（桌面端交互直觉）。"""
        return [
            ft.PopupMenuItem(
                content="剪切", icon=ft.Icons.CONTENT_CUT,
                on_click=lambda e, i=idx: _cut_row(i),
            ),
            ft.PopupMenuItem(
                content="复制", icon=ft.Icons.CONTENT_COPY,
                on_click=lambda e, i=idx: _copy_row(i),
            ),
            ft.PopupMenuItem(
                content="粘贴", icon=ft.Icons.CONTENT_PASTE,
                on_click=lambda e, i=idx: _paste_row(i),
            ),
            ft.PopupMenuItem(),  # 分隔
            ft.PopupMenuItem(
                content="删除", icon=ft.Icons.DELETE_OUTLINE,
                on_click=lambda e, i=idx: _delete_row(i),
            ),
        ]

    # ---- 拖拽更换顺序 ----
    # 行索引挂在 Draggable.data 上（见模块级 drag_src_idx），事件时从 e.src 取回；
    # 不依赖 id() 注册表——重渲染后 e.src 可能指向旧对象，id 查找会落空。
    def _src_idx_of(e) -> int | None:
        return drag_src_idx(e, len(editing_pairs))

    def _on_will_accept(e, dst_idx: int) -> bool:
        """拖拽进入目标行：非自身时高亮（合法目标桌面直觉）。"""
        src_idx = _src_idx_of(e)
        ok = src_idx is not None and src_idx != dst_idx
        set_drop_hover(dst_idx if ok else -1)
        return True

    def _on_drag_leave(e, _dst_idx: int) -> None:
        set_drop_hover(-1)

    def _on_row_drop(e, dst_idx: int) -> None:
        """拖放：把源行移动到目标行位置，其余顺移。"""
        set_drop_hover(-1)
        src_idx = _src_idx_of(e)
        if src_idx is None or src_idx == dst_idx:
            return
        new_pairs = reorder_pairs(editing_pairs, src_idx, dst_idx)
        if new_pairs == editing_pairs:
            return
        set_editing_pairs(new_pairs)
        _commit_pairs(new_pairs)

    def _row_preview(idx: int) -> ft.Control:
        """拖拽时跟随指针的紧凑预览（key: value pill）。"""
        p = editing_pairs[idx] if 0 <= idx < len(editing_pairs) else ["", ""]
        return ft.Container(
            bgcolor=ft.Colors.with_opacity(0.96, c.surface),
            border=ft.Border.all(1, c.border),
            border_radius=Radius.SM,
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DRAG_INDICATOR, size=12, color=c.muted),
                    ft.Text(
                        p[0] or "新属性",
                        size=base - 6,
                        color=_key_color,
                        font_family=FONT_MONO,
                        weight=ft.FontWeight.W_500,
                    ),
                    ft.Text(": ", size=base - 6, color=c.muted),
                    ft.Text(
                        p[1],
                        size=base - 6,
                        color=c.text,
                        font_family=FONT_MAIN,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=Spacing.XS,
                tight=True,
            ),
        )

    def _build_property_table() -> ft.Control:
        """构造 Obsidian 风格的可编辑属性表格。

        表头行（属性 | 值 | 操作）+ 数据行（TextField 键/值 + 删除按钮），
        底部新增行按钮。键列固定宽度，值列自适应。整体圆角裁剪。
        """
        # ---- 表头行：略深背景，与数据行明显分层（紧凑高度）----
        header_bg = ft.Colors.with_opacity(0.09 if is_dark else 0.06, c.text)
        header_row = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(width=_HANDLE_WIDTH),  # 拖拽把手列占位
                    ft.Container(
                        content=ft.Text(
                            value="属性",
                            size=base - 7,
                            color=c.muted,
                            font_family=FONT_MAIN,
                            weight=ft.FontWeight.W_600,
                        ),
                        width=_KEY_COL_WIDTH,
                        padding=ft.Padding.only(left=Spacing.SM, right=Spacing.SM),
                    ),
                    ft.Text(
                        value="值",
                        size=base - 7,
                        color=c.muted,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_600,
                        expand=True,
                    ),
                    ft.Container(width=_MORE_BTN_WIDTH + _DEL_BTN_WIDTH),  # 操作列占位
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=header_bg,
            padding=ft.Padding.symmetric(vertical=3),
        )

        # ---- 数据行：斑马纹增强可读性，拖拽悬停高亮合法目标 ----
        zebra_bg = ft.Colors.with_opacity(0.045 if is_dark else 0.03, c.text)
        hover_bg = ft.Colors.with_opacity(0.10 if is_dark else 0.08, c.link)
        data_rows: list[ft.Control] = [header_row]

        rows_data = editing_pairs if editing_pairs else []
        for idx, pair in enumerate(rows_data):
            key_val = pair[0] if len(pair) > 0 else ""
            val_val = pair[1] if len(pair) > 1 else ""
            val_color, val_font = _value_style(val_val)
            row_bg = zebra_bg if idx % 2 == 1 else None

            # 键 TextField：品牌蓝色突出属性标识，等宽字体（紧凑高度）
            key_field = ft.TextField(
                value=key_val,
                text_size=base - 6,
                color=_key_color,
                text_style=ft.TextStyle(font_family=FONT_MONO, weight=ft.FontWeight.W_500),
                border=ft.InputBorder.NONE,
                fill_color=ft.Colors.TRANSPARENT,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=1),
                hint_text="键名",
                hint_style=ft.TextStyle(
                    size=base - 6,
                    color=ft.Colors.with_opacity(0.35, c.muted),
                    font_family=FONT_MONO,
                ),
                on_change=lambda e, i=idx: _on_key_change(i, e.control.value or ""),
                on_focus=lambda e: on_code_focus(line_idx) if on_code_focus is not None else None,
                on_blur=lambda e: on_code_blur(line_idx) if on_code_blur is not None else None,
            )
            # 值 TextField：按数据类型着色，无边框透明底（紧凑高度）
            val_field = ft.TextField(
                value=val_val,
                text_size=base - 5,
                color=val_color,
                text_style=ft.TextStyle(font_family=val_font),
                border=ft.InputBorder.NONE,
                fill_color=ft.Colors.TRANSPARENT,
                dense=True,
                content_padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=1),
                hint_text="值",
                hint_style=ft.TextStyle(
                    size=base - 5,
                    color=ft.Colors.with_opacity(0.35, c.muted),
                ),
                on_change=lambda e, i=idx: _on_value_change(i, e.control.value or ""),
                on_focus=lambda e: on_code_focus(line_idx) if on_code_focus is not None else None,
                on_blur=lambda e: on_code_blur(line_idx) if on_code_blur is not None else None,
            )
            # 删除按钮：悬停时显红色警示
            del_btn = ft.IconButton(
                icon=ft.Icons.CLOSE,
                icon_size=13,
                tooltip="删除此行",
                padding=ft.Padding.all(2),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=Radius.SM),
                    color={
                        ft.ControlState.HOVERED: "#E5484D",
                        ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.4, c.muted),
                    },
                    bgcolor={
                        ft.ControlState.HOVERED: ft.Colors.with_opacity(0.08, "#E5484D"),
                        ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                    },
                ),
                on_click=lambda e, i=idx: _delete_row(i),
            )
            # 行操作菜单按钮（⋮）：剪切/复制/粘贴/删除（始终可用，不受字段原生菜单影响）
            more_btn = ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                icon_size=13,
                icon_color=ft.Colors.with_opacity(0.4, c.muted),
                items=_row_menu_items(idx),
                padding=ft.Padding.all(2),
            )
            # 拖拽把手：仅把手可拖起（避免与键/值字段内的文本选择拖拽冲突）
            handle = ft.Draggable(
                group=_FM_DRAG_GROUP,
                content=ft.Icon(
                    ft.Icons.DRAG_INDICATOR,
                    size=14,
                    color=ft.Colors.with_opacity(0.35, c.muted),
                ),
                content_when_dragging=ft.Icon(
                    ft.Icons.DRAG_INDICATOR,
                    size=14,
                    color=ft.Colors.with_opacity(0.15, c.muted),
                ),
                content_feedback=_row_preview(idx),
                data=idx,
            )

            # 行内容：把手 | 键 | 值 | ⋮ | ×（紧凑高度）
            row_inner = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Container(
                            content=handle,
                            width=_HANDLE_WIDTH,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Container(
                            content=key_field,
                            width=_KEY_COL_WIDTH,
                        ),
                        val_field,
                        ft.Container(
                            content=more_btn,
                            width=_MORE_BTN_WIDTH,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Container(
                            content=del_btn,
                            width=_DEL_BTN_WIDTH,
                            alignment=ft.Alignment.CENTER,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=hover_bg if drop_hover == idx else row_bg,
                padding=ft.Padding.symmetric(vertical=0),
            )
            # 放置目标：整行可接收拖放（拖起只能从把手开始）
            row = ft.DragTarget(
                group=_FM_DRAG_GROUP,
                content=row_inner,
                on_will_accept=lambda e, i=idx: _on_will_accept(e, i),
                on_accept=lambda e, i=idx: _on_row_drop(e, i),
                on_leave=lambda e, i=idx: _on_drag_leave(e, i),
            )
            # 右键菜单：剪切 / 复制 / 粘贴 / 删除
            row = ft.ContextMenu(content=row, secondary_items=_row_menu_items(idx))
            data_rows.append(row)

        # ---- 新增行按钮：悬停高亮 ----
        add_row_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ADD, size=14, color=_key_color),
                    ft.Text(
                        value="添加属性",
                        size=base - 6,
                        color=_key_color,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_500,
                    ),
                ],
                spacing=Spacing.XS,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
            ink=True,
            border_radius=Radius.SM,
            on_click=lambda e: _add_row(),
        )
        data_rows.append(add_row_btn)

        # ---- 表格容器：圆角裁剪 + 清晰边框 ----
        table_border = ft.Colors.with_opacity(0.14 if is_dark else 0.10, c.text)
        return ft.Container(
            content=ft.Column(
                controls=data_rows,
                spacing=0,
            ),
            border_radius=Radius.SM,
            border=only_border(
                top=ft.BorderSide(1, table_border),
                bottom=ft.BorderSide(1, table_border),
                left=ft.BorderSide(1, table_border),
                right=ft.BorderSide(1, table_border),
            ),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    # ---- 折叠态摘要 ----
    collapsed_preview = ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(
                    value=(pairs[0][0] + ": " + pairs[0][1]) if pairs else "(空)",
                    size=base - 6,
                    color=c.muted,
                    font_family=FONT_MONO,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.Text(
                    value=f"{len(pairs)} 项",
                    size=10,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
            ],
            spacing=Spacing.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.XS),
    )

    # ---- 主内容：折叠时显示摘要，否则显示可编辑表格 ----
    body = collapsed_preview if is_collapsed else _build_property_table()

    main_content = ft.Column(
        controls=[header, body],
        spacing=Spacing.XS,
    )

    # ---- 卡片容器（Obsidian 风格：左侧彩色边框 + 浅色背景）----
    border_color = ft.Colors.with_opacity(0.12 if is_dark else 0.08, c.text)
    # 左侧强调色：与键名/标题色一致（_key_color），整体配色统一
    accent = _key_color
    content_ctrl = ft.Container(
        content=main_content,
        bgcolor=ft.Colors.with_opacity(0.5, c.code_block_bg),
        border_radius=Radius.MD,
        padding=ft.Padding.only(
            left=Spacing.MD, right=Spacing.MD,
            top=Spacing.XS, bottom=Spacing.SM
        ),
        border=only_border(
            top=ft.BorderSide(1, border_color),
            bottom=ft.BorderSide(1, border_color),
            left=ft.BorderSide(3, accent),
            right=ft.BorderSide(1, border_color),
        ),
    )

    return _block_frame.wrap_block(
        content_ctrl, line, base, line_idx,
        is_current_line=is_current_line,
        is_flash=is_flash,
        on_size_change=on_line_size_change,
        diff_mark=diff_mark,
    )


def parse_yaml_pairs(content: str) -> list[tuple[str, str]]:
    """简易 YAML 键值对解析（仅支持扁平 key: value 格式）。

    不引入 PyYAML 依赖，仅做行级拆分：每行以 "key: value" 形式存在。
    复杂结构（嵌套/列表/多行字符串）的行原样保留为键值对（key=原行，value=""）。
    """
    pairs: list[tuple[str, str]] = []
    for raw_line in content.split("\n"):
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        idx = raw_line.find(":")
        if idx <= 0:
            continue
        key = raw_line[:idx].strip()
        val = raw_line[idx + 1:].strip()
        pairs.append((key, val))
    return pairs


def pairs_to_yaml(pairs: list) -> str:
    """把键值对列表序列化为 YAML 文本（跳过键为空的行，两侧空白剥离）。

    与 _commit_pairs 写回文档的口径一致；供写回与“外部内容同步判定”共用，
    保证对比口径相同（编辑态含待定空键行时不会误判为外部变更）。
    """
    filtered = [(k.strip(), v.strip()) for k, v in pairs if k.strip()]
    return "\n".join(f"{k}: {v}" for k, v in filtered) if filtered else ""


def drag_src_idx(e, n_pairs: int) -> int | None:
    """从拖拽事件解析源行索引。

    行索引直接挂在 Draggable.data 上（替代 id 注册表）：组件重渲染后 e.src
    可能指向旧对象，id() 查注册表会落空；data 随控件迁移/重建保留，无论
    e.src 解析到新旧对象都能取回正确行索引。
    """
    src = getattr(e, "src", None)
    if src is None:
        return None
    idx = getattr(src, "data", None)
    if not isinstance(idx, int) or not (0 <= idx < n_pairs):
        return None
    return idx


def reorder_pairs(pairs: list, src_idx: int, dst_idx: int) -> list:
    """把 src_idx 行移动到 dst_idx 位置，其余顺移（返回新列表）。"""
    new_pairs = [list(p) for p in pairs]
    if not (0 <= src_idx < len(new_pairs) and 0 <= dst_idx < len(new_pairs)):
        return new_pairs
    item = new_pairs.pop(src_idx)
    new_pairs.insert(dst_idx, item)
    return new_pairs
