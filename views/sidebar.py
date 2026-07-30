"""左侧侧边栏：文件 / 大纲 / 搜索三面板。

- 文件面板：.md/.markdown 文件树 + 搜索过滤。根目录优先级：
  settings.workspace_folder（显式「打开文件夹」锚定的工作区）>
  当前打开文件所在目录 > 最近文件列表（settings.recent_files）。
  工作区模式下打开子目录文件时文件树仍以工作区根排布，不随当前文件目录漂移；
  顶部显示文件夹名头与关闭按钮，并在树中高亮当前打开的文件。
- 大纲面板：从 document.lines 派生标题树，点击跳转到对应行。
- 搜索面板：当前文档内行级子串匹配，点击跳转。

跳转通过 on_jump_to_line(li) 回调上抛，由 App 转发到 editor.nav_ref.jump_to_line。
大纲/搜索由 Sidebar 从 document.lines 自行派生（document 是 @ft.observable，实时刷新）。
"""

import os
from collections.abc import Callable

import flet as ft

from models import BlockType, Document, SegType
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, _current_colors, only_border

_MD_EXTS = (".md", ".markdown")
_MAX_DEPTH = 3  # 文件树扫描最大深度
_MAX_RESULTS = 200  # 搜索结果上限，防止超长文档卡顿
_PREVIEW_RADIUS = 30  # 搜索预览匹配位前后字符数


# ---- 数据派生 ----


def _compute_toc(document: Document) -> list[tuple[int, int, str]]:
    """复用 editor.toc_entries 的派生逻辑：返回 [(line_idx, level, text), ...]。"""
    if document is None:
        return []
    result: list[tuple[int, int, str]] = []
    for i, line in enumerate(document.lines):
        if line.block_type != BlockType.HEADING:
            continue
        text = "".join(
            s.text for s in line.segments if s.seg_type != SegType.HEADING_PREFIX
        ).strip()
        if text:
            result.append((i, line.level, text))
    return result


def _scan_markdown_files(root: str, max_depth: int = _MAX_DEPTH) -> list:
    """递归扫描 root 下的 .md/.markdown 文件，返回嵌套结构。

    元素格式：
      ("dir", name, children_list)
      ("file", name, abs_path)
    目录在前、字母序排序；跳过隐藏目录与常见忽略目录。失败时返回 []。
    """
    if not root or not os.path.isdir(root):
        return []

    def _walk(dir_path: str, depth: int) -> list:
        if depth > max_depth:
            return []
        try:
            entries = sorted(
                os.scandir(dir_path),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except OSError:
            return []
        result: list = []
        for entry in entries:
            if entry.name.startswith(".") or entry.name in (
                "__pycache__",
                "node_modules",
                ".git",
            ):
                continue
            if entry.is_dir():
                children = _walk(entry.path, depth + 1)
                if children:
                    result.append(("dir", entry.name, children))
            elif entry.is_file() and entry.name.lower().endswith(_MD_EXTS):
                result.append(("file", entry.name, entry.path))
        return result

    return _walk(root, 0)


def _filter_tree(tree: list, query: str) -> list:
    """子串过滤文件树（大小写不敏感），保留含匹配项的父目录。"""
    if not query.strip():
        return tree
    q = query.strip().lower()

    def _filter(node):
        if node[0] == "file":
            return node if q in node[1].lower() else None
        children = [c for c in (_filter(x) for x in node[2]) if c]
        if children or q in node[1].lower():
            return ("dir", node[1], children)
        return None

    return [c for c in (_filter(x) for x in tree) if c]


def _flatten_tree(tree: list, depth: int = 0, root_dir: str = "") -> list[tuple[str, str, str | None, int]]:
    """扁平化为 [(type, name, abspath_or_None, depth), ...]，便于一次性渲染。

    目录的 abspath 由 root_dir + 目录名拼接（供右键菜单使用）。
    """
    out: list[tuple[str, str, str | None, int]] = []
    for node in tree:
        if node[0] == "file":
            out.append(("file", node[1], node[2], depth))
        else:
            dir_path = os.path.join(root_dir, node[1]) if root_dir else node[1]
            out.append(("dir", node[1], dir_path, depth))
            out.extend(_flatten_tree(node[2], depth + 1, dir_path))
    return out


def _match_lines(
    document: Document, query: str, limit: int = _MAX_RESULTS
) -> list[tuple[int, str]]:
    """行级子串匹配（大小写不敏感），返回 [(line_idx, preview), ...]。

    preview 取匹配位前后 _PREVIEW_RADIUS 字符，超界加 …。
    """
    if document is None or not query.strip():
        return []
    q = query.strip().lower()
    results: list[tuple[int, str]] = []
    for i, line in enumerate(document.lines):
        raw = line.raw or ""
        if q in raw.lower():
            idx = raw.lower().find(q)
            start = max(0, idx - _PREVIEW_RADIUS)
            end = min(len(raw), idx + len(q) + _PREVIEW_RADIUS)
            preview = raw[start:end]
            if start > 0:
                preview = "…" + preview
            if end < len(raw):
                preview = preview + "…"
            results.append((i, preview))
            if len(results) >= limit:
                break
    return results


# ---- 通用控件工厂 ----


def _wrap_context_menu(
    content: ft.Control,
    path: str,
    is_dir: bool,
    on_action: Callable[[str, str], None],
    compare_source: str | None = None,
) -> ft.ContextMenu:
    """将列表项包裹在右键菜单中。

    文件菜单：打开 / 选择以进行比较 / 与已选项目进行比较 /
            新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 创建副本 / 删除
    文件夹菜单：新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 删除
    （文件夹无"打开"、"比较"和"创建副本"）
    compare_source 非空时，文件项显示「与已选项目进行比较」。
    """
    items: list[ft.PopupMenuItem] = []

    if not is_dir:
        items.append(
            ft.PopupMenuItem(
                content="打开", icon=ft.Icons.OPEN_IN_NEW,
                on_click=lambda e, p=path: on_action("open", p),
            )
        )
        # 文件比较：选择以进行比较 / 与已选项目进行比较（VSCode 风格）
        items.append(
            ft.PopupMenuItem(
                content="选择以进行比较", icon=ft.Icons.DIFFERENCE,
                on_click=lambda e, p=path: on_action("select_for_compare", p),
            )
        )
        if compare_source and os.path.abspath(compare_source) != os.path.abspath(path):
            items.append(
                ft.PopupMenuItem(
                    content="与已选项目进行比较", icon=ft.Icons.COMPARE_ARROWS,
                    on_click=lambda e, p=path: on_action("compare_with_selected", p),
                )
            )
        items.append(ft.PopupMenuItem())  # 分隔

    # 新建文件/文件夹
    items.append(
        ft.PopupMenuItem(
            content="新建文件", icon=ft.Icons.NOTE_ADD,
            on_click=lambda e, p=path: on_action("new_file", p),
        )
    )
    items.append(
        ft.PopupMenuItem(
            content="新建文件夹", icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=lambda e, p=path: on_action("new_folder", p),
        )
    )
    items.append(ft.PopupMenuItem())  # 分隔

    # 路径操作
    items.append(
        ft.PopupMenuItem(
            content="复制路径", icon=ft.Icons.CONTENT_COPY,
            on_click=lambda e, p=path: on_action("copy_path", p),
        )
    )
    items.append(
        ft.PopupMenuItem(
            content="打开文件位置", icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e, p=path: on_action("reveal", p),
        )
    )
    items.append(ft.PopupMenuItem())  # 分隔

    # 文件操作
    items.append(
        ft.PopupMenuItem(
            content="重命名", icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
            on_click=lambda e, p=path: on_action("rename", p),
        )
    )
    if not is_dir:
        items.append(
            ft.PopupMenuItem(
                content="创建副本", icon=ft.Icons.FILE_COPY_OUTLINED,
                on_click=lambda e, p=path: on_action("duplicate", p),
            )
        )
    items.append(
        ft.PopupMenuItem(
            content="删除", icon=ft.Icons.DELETE_OUTLINE,
            on_click=lambda e, p=path: on_action("delete", p),
        )
    )

    return ft.ContextMenu(
        content=content,
        secondary_items=items,
    )


def _search_box(
    value: str,
    on_change: Callable[[str], None],
    placeholder: str,
    c,
) -> ft.Control:
    """侧边栏搜索/过滤输入框（下划线边框，紧凑）。"""
    return ft.TextField(
        value=value,
        hint_text=placeholder,
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        border=ft.InputBorder.UNDERLINE,
        text_size=12,
        content_padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        on_change=lambda e: on_change(e.control.value or ""),
    )


def _empty_hint(text: str, c) -> ft.Control:
    """居中浅色提示。"""
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            value=text,
            size=12,
            color=c.muted,
            font_family=FONT_MAIN,
            text_align=ft.TextAlign.CENTER,
        ),
    )


def _list_item(
    content: ft.Control,
    c,
    on_click: Callable | None = None,
    indent: int = Spacing.XL,
    active: bool = False,
) -> ft.Control:
    """通用列表项：左侧缩进、hover ink 反馈。

    active=True 时以主题色半透明背景高亮（用于标记当前打开的文件）。
    """
    return ft.Container(
        content=content,
        padding=ft.Padding.only(left=indent, top=Spacing.SM, bottom=Spacing.SM, right=Spacing.LG),
        on_click=on_click,
        ink=True,
        bgcolor=ft.Colors.with_opacity(0.12, c.link) if active else None,
        border_radius=Radius.LG,
    )


def _outline_color_bar(level: int, c) -> ft.Control:
    """大纲级别色条：3px 宽细竖线，颜色复用 styles.heading_colors（红橙绿青蓝紫）。

    替代 H1~H6 文字徽章，视觉更清爽；同级别条目左对齐到同一缩进位置，
    色条颜色一眼区分标题级别。
    """
    color = c.heading_colors.get(level, c.muted)
    return ft.Container(
        width=1,
        height=14,
        bgcolor=color,
        border_radius=2,
    )


# ---- 面板渲染 ----


def _resolve_files_root(
    workspace_folder: str | None, file_path: str | None
) -> tuple[str | None, str | None, bool]:
    """计算文件面板根目录与展示标签。

    优先级：workspace_folder（显式「打开文件夹」锚定的工作区，须为现存目录）>
    当前文件所在目录 > None（最近文件列表）。

    返回 (root_dir, root_label, is_workspace)：
    - 工作区模式：root_label 为文件夹名，is_workspace=True
    - 文件目录模式：root_label=None，is_workspace=False
    - 无根：root_dir=None，is_workspace=False
    """
    if workspace_folder and os.path.isdir(workspace_folder):
        return workspace_folder, os.path.basename(workspace_folder) or workspace_folder, True
    if file_path:
        return os.path.dirname(file_path), None, False
    return None, None, False


def _render_files_panel(
    file_path: str | None,
    recent_files: list[str],
    workspace_folder: str | None,
    file_filter: str,
    set_file_filter: Callable[[str], None],
    on_open_file: Callable[[str], None],
    on_file_context_action: Callable[[str, str], None],
    on_close_folder: Callable[[], None] | None,
    c,
    compare_source: str | None = None,
) -> ft.Control:
    """文件面板：有根目录显示文件树+过滤；否则显示最近文件列表。

    根目录优先级：workspace_folder（显式「打开文件夹」锚定的工作区）>
    当前文件所在目录。工作区模式下打开子目录文件时，文件树仍以工作区
    根排布，不随当前文件目录漂移。工作区模式下顶部显示文件夹名头与
    关闭按钮，并在树中高亮当前打开的文件。

    每个文件/文件夹项包裹 ft.ContextMenu，右键提供完整文件操作菜单。
    compare_source 非空时，文件项的右键菜单显示「与已选项目进行比较」。
    """
    # 根目录优先级：工作区文件夹 > 当前文件所在目录 > None
    root_dir, root_label, is_workspace = _resolve_files_root(workspace_folder, file_path)

    # 无根目录：最近文件列表
    if not root_dir:
        existing = [p for p in recent_files if os.path.exists(p)]
        if not existing:
            return _empty_hint("暂无最近文件\n打开或保存一个文件后此处会显示", c)
        items = [
            _wrap_context_menu(
                _list_item(
                    ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=14, color=c.muted
                            ),
                            ft.Text(
                                os.path.basename(p),
                                size=12,
                                color=c.text,
                                font_family=FONT_MAIN,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=Spacing.MD,
                    ),
                    c,
                    on_click=lambda e, p=p: on_open_file(p),
                ),
                p,
                is_dir=False,
                on_action=on_file_context_action,
                compare_source=compare_source,
            )
            for p in existing
        ]
        return ft.Column(
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
                    content=ft.Text(
                        "最近文件",
                        size=11,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                ),
                ft.Container(
                    expand=True,
                    content=ft.Column(
                        controls=items,
                        spacing=0,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )

    # 有根目录：搜索框 + 文件树
    full_tree = _scan_markdown_files(root_dir)
    filtered = _filter_tree(full_tree, file_filter)
    flat = _flatten_tree(filtered, root_dir=root_dir)

    # 当前打开文件绝对路径（用于高亮活动文件行）
    active_abs = os.path.abspath(file_path) if file_path else None

    if not flat:
        body: ft.Control = _empty_hint(
            "无匹配文件" if file_filter.strip() else "该目录下无 Markdown 文件",
            c,
        )
    else:
        rows = []
        for kind, name, abspath, depth in flat:
            indent = depth * 14 + Spacing.XL
            is_active = (
                kind == "file"
                and active_abs is not None
                and abspath is not None
                and os.path.abspath(abspath) == active_abs
            )
            if kind == "file":
                rows.append(
                    _wrap_context_menu(
                        _list_item(
                            ft.Row(
                                controls=[
                                    ft.Icon(
                                        ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
                                        size=13,
                                        color=c.link if is_active else c.muted,
                                    ),
                                    ft.Text(
                                        name,
                                        size=12,
                                        color=c.text,
                                        font_family=FONT_MAIN,
                                        weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                        expand=True,
                                    ),
                                ],
                                spacing=Spacing.MD,
                            ),
                            c,
                            on_click=lambda e, p=abspath: on_open_file(p),
                            indent=indent,
                            active=is_active,
                        ),
                        abspath or "",
                        is_dir=False,
                        on_action=on_file_context_action,
                        compare_source=compare_source,
                    )
                )
            else:
                rows.append(
                    _wrap_context_menu(
                        _list_item(
                            ft.Row(
                                controls=[
                                    ft.Icon(
                                        ft.Icons.FOLDER_OUTLINED, size=13, color=c.muted
                                    ),
                                    ft.Text(
                                        name,
                                        size=12,
                                        color=c.text,
                                        font_family=FONT_MAIN,
                                        weight=ft.FontWeight.W_600,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                        expand=True,
                                    ),
                                ],
                                spacing=Spacing.MD,
                            ),
                            c,
                            indent=indent,
                        ),
                        abspath or "",
                        is_dir=True,
                        on_action=on_file_context_action,
                    )
                )
        body = ft.Container(
            expand=True,
            content=ft.Column(controls=rows, spacing=0, scroll=ft.ScrollMode.AUTO),
        )

    # 工作区模式：顶部文件夹名头 + 关闭按钮（VSCode 风格资源管理器标题栏）
    header_controls: list = []
    if is_workspace:
        header_controls.append(
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.FOLDER_OPEN, size=14, color=c.muted),
                        ft.Text(
                            root_label,
                            size=11,
                            color=c.muted,
                            font_family=FONT_MAIN,
                            weight=ft.FontWeight.W_600,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            tooltip="关闭文件夹",
                            on_click=lambda e: on_close_folder() if on_close_folder else None,
                            icon_size=14,
                            style=ft.ButtonStyle(
                                color=c.muted,
                                padding=Spacing.XS,
                            ),
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
        )

    return ft.Column(
        controls=[
            *header_controls,
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
                content=_search_box(file_filter, set_file_filter, "过滤文件…", c),
            ),
            body,
        ],
        spacing=0,
        expand=True,
    )


def _render_outline_panel(
    toc_entries: list[tuple[int, int, str]],
    on_jump_to_line: Callable[[int], None],
    c,
) -> ft.Control:
    """大纲面板：标题按级别缩进，左侧细竖线色条着色，点击跳转。

    同级别条目左对齐到同一缩进位置（(lvl-1)*14 + Spacing.XL）；
    色条颜色对应 heading_colors（红橙绿青蓝紫），一眼区分标题级别。
    H1/H2 文本加粗以突出主要章节。
    """
    if not toc_entries:
        return _empty_hint("文档无标题", c)
    items = [
        _list_item(
            ft.Row(
                controls=[
                    _outline_color_bar(lvl, c),
                    ft.Text(
                        value=text,
                        size=12,
                        color=c.text,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_600 if lvl <= 2 else ft.FontWeight.NORMAL,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                ],
                spacing=Spacing.MD,
            ),
            c,
            on_click=lambda e, li=li: on_jump_to_line(li),
            indent=(lvl - 1) * 14 + Spacing.XL,
        )
        for li, lvl, text in toc_entries
    ]
    return ft.Container(
        expand=True,
        content=ft.Column(controls=items, spacing=0, scroll=ft.ScrollMode.AUTO),
    )


def _render_search_panel(
    search_query: str,
    set_search_query: Callable[[str], None],
    search_results: list[tuple[int, str]],
    on_jump_to_line: Callable[[int], None],
    c,
) -> ft.Control:
    """搜索面板：搜索框 + 结果列表。"""
    if not search_query.strip():
        return ft.Column(
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
                    content=_search_box(
                        search_query, set_search_query, "在当前文档中查找…", c
                    ),
                ),
                _empty_hint("输入关键词以搜索文档", c),
            ],
            spacing=0,
            expand=True,
        )

    if not search_results:
        return ft.Column(
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
                    content=_search_box(
                        search_query, set_search_query, "在当前文档中查找…", c
                    ),
                ),
                _empty_hint("无匹配结果", c),
            ],
            spacing=0,
            expand=True,
        )

    items = [
        _list_item(
            ft.Column(
                controls=[
                    ft.Text(
                        value=f"行 {li + 1}",
                        size=10,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Text(
                        value=preview,
                        size=11,
                        color=c.text,
                        font_family=FONT_MONO,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=0,
            ),
            c,
            on_click=lambda e, li=li: on_jump_to_line(li),
        )
        for li, preview in search_results
    ]
    return ft.Column(
        controls=[
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
                content=_search_box(
                    search_query, set_search_query, "在当前文档中查找…", c
                ),
            ),
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.SM),
                content=ft.Text(
                    value=f"{len(search_results)} 个结果",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
            ),
            ft.Container(
                expand=True,
                content=ft.Column(controls=items, spacing=0, scroll=ft.ScrollMode.AUTO),
            ),
        ],
        spacing=0,
        expand=True,
    )


@ft.component
def Sidebar(
    document: Document,
    file_path: str | None,
    theme_mode: ft.ThemeMode,
    settings: dict,
    active_panel: str,
    on_change_panel: Callable[[str], None],
    on_open_file: Callable[[str], None],
    on_jump_to_line: Callable[[int], None],
    on_width_change: Callable[[int], None] | None = None,
    on_file_context_action: Callable[[str, str], None] | None = None,
    on_close_folder: Callable[[], None] | None = None,
    compare_source: str | None = None,
):
    """左侧侧边栏：文件 / 大纲 / 搜索三面板，顶部图标切换，右侧可拖拽调宽。

    文件面板根目录优先级：settings.workspace_folder（显式「打开文件夹」锚定的
    工作区）> 当前文件所在目录 > None（最近文件列表）。工作区模式下打开子目录
    文件时文件树仍以工作区根排布。

    on_file_context_action(action, path)：文件/文件夹右键菜单回调。
    action ∈ {"open","select_for_compare","compare_with_selected","new_file","new_folder",
    "copy_path","reveal","rename","duplicate","delete"}。
    on_close_folder()：关闭工作区文件夹（清空 workspace_folder，回退到当前文件目录）。
    compare_source 非空时，文件项右键菜单显示「与已选项目进行比较」。
    """
    c = _current_colors()

    # 宽度：内部 state（拖拽时实时更新），ref 同步避免 stale 闭包
    _INIT_W = settings.get("sidebar_width", 256)
    width, set_width = ft.use_state(_INIT_W)
    width_ref = ft.use_ref(_INIT_W)
    width_ref.current = width

    _MIN_W, _MAX_W = 180, 600

    # 内部状态：文件过滤与文档搜索词
    file_filter, set_file_filter = ft.use_state("")
    search_query, set_search_query = ft.use_state("")

    # 派生数据
    recent_files = settings.get("recent_files", [])
    workspace_folder = settings.get("workspace_folder")
    toc_entries = _compute_toc(document)
    search_results = _match_lines(document, search_query)

    # ---- 拖拽调宽 ----
    def _on_pan_update(e: ft.DragUpdateEvent):
        new_w = int(max(_MIN_W, min(_MAX_W, width_ref.current + e.local_delta.x)))
        if new_w != width_ref.current:
            width_ref.current = new_w
            set_width(new_w)

    def _on_pan_end(e):
        if on_width_change is not None:
            on_width_change(width_ref.current)

    drag_handle = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.RESIZE_COLUMN,
        on_pan_update=_on_pan_update,
        on_pan_end=_on_pan_end,
        content=ft.Container(
            width=4,
            bgcolor=ft.Colors.with_opacity(0.0, c.link),
            expand=True,
        ),
    )

    # ---- 顶部 Tab 切换 ----
    def _panel_tab(key: str, icon: str, label: str) -> ft.Control:
        active = active_panel == key
        return ft.Container(
            expand=True,
            border_radius=Radius.MD,
            bgcolor=ft.Colors.with_opacity(0.10, c.link) if active else None,
            content=ft.IconButton(
                icon=icon,
                tooltip=label,
                icon_size=18,
                on_click=lambda e: on_change_panel(key),
                style=ft.ButtonStyle(
                    color=c.link if active else c.muted,
                    padding=Spacing.MD,
                ),
            ),
        )

    tabs = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.LG),
        content=ft.Row(
            controls=[
                _panel_tab("files", ft.Icons.FOLDER_OUTLINED, "文件"),
                _panel_tab("outline", ft.Icons.FORMAT_LIST_BULLETED, "大纲"),
                _panel_tab("search", ft.Icons.SEARCH, "搜索"),
            ],
            spacing=Spacing.XS,
        ),
    )

    # ---- 面板选择 ----
    # 文件面板右键回调：未提供时用 no-op 避免崩溃
    _file_ctx = on_file_context_action if on_file_context_action is not None else (lambda action, path: None)
    if active_panel == "files":
        panel: ft.Control = _render_files_panel(
            file_path,
            recent_files,
            workspace_folder,
            file_filter,
            set_file_filter,
            on_open_file,
            _file_ctx,
            on_close_folder,
            c,
            compare_source=compare_source,
        )
    elif active_panel == "outline":
        panel = _render_outline_panel(toc_entries, on_jump_to_line, c)
    else:  # search
        panel = _render_search_panel(
            search_query,
            set_search_query,
            search_results,
            on_jump_to_line,
            c,
        )

    return ft.Row(
        controls=[
            ft.Container(
                width=width,
                bgcolor=c.surface,
                content=ft.Column(
                    controls=[tabs, panel],
                    spacing=0,
                    expand=True,
                ),
            ),
            drag_handle,
        ],
        spacing=0,
    )
