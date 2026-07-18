"""左侧侧边栏：文件 / 大纲 / 搜索三面板。

- 文件面板：当前打开文件所在目录的 .md/.markdown 文件树 + 搜索过滤；
  无 file_path 时显示最近文件列表（来自 settings.recent_files）。
- 大纲面板：从 document.lines 派生标题树，点击跳转到对应行。
- 搜索面板：当前文档内行级子串匹配，点击跳转。

跳转通过 on_jump_to_line(li) 回调上抛，由 App 转发到 editor.nav_ref.jump_to_line。
大纲/搜索由 Sidebar 从 document.lines 自行派生（document 是 @ft.observable，实时刷新）。
"""

import os
from collections.abc import Callable

import flet as ft

from models import BlockType, Document, SegType
from styles import FONT_MAIN, FONT_MONO, _current_colors, only_border

_MD_EXTS = (".md", ".markdown")
_MAX_DEPTH = 3  # 文件树扫描最大深度
_MAX_RESULTS = 200  # 搜索结果上限，防止超长文档卡顿
_PREVIEW_RADIUS = 30  # 搜索预览匹配位前后字符数


# ---- 数据派生 ----


def _compute_toc(document: Document) -> list[tuple[int, int, str]]:
    """复用 editor.toc_entries 的派生逻辑：返回 [(line_idx, level, text), ...]。"""
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


def _flatten_tree(tree: list, depth: int = 0) -> list[tuple[str, str, str | None, int]]:
    """扁平化为 [(type, name, abspath_or_None, depth), ...]，便于一次性渲染。"""
    out: list[tuple[str, str, str | None, int]] = []
    for node in tree:
        if node[0] == "file":
            out.append(("file", node[1], node[2], depth))
        else:
            out.append(("dir", node[1], None, depth))
            out.extend(_flatten_tree(node[2], depth + 1))
    return out


def _match_lines(
    document: Document, query: str, limit: int = _MAX_RESULTS
) -> list[tuple[int, str]]:
    """行级子串匹配（大小写不敏感），返回 [(line_idx, preview), ...]。

    preview 取匹配位前后 _PREVIEW_RADIUS 字符，超界加 …。
    """
    if not query.strip():
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
        content_padding=ft.Padding.symmetric(horizontal=10, vertical=6),
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
    indent: int = 12,
) -> ft.Control:
    """通用列表项：左侧缩进、hover ink 反馈。"""
    return ft.Container(
        content=content,
        padding=ft.Padding.only(left=indent, top=4, bottom=4, right=8),
        on_click=on_click,
        ink=True,
        border_radius=8,
    )


# ---- 面板渲染 ----


def _render_files_panel(
    file_path: str | None,
    recent_files: list[str],
    file_filter: str,
    set_file_filter: Callable[[str], None],
    on_open_file: Callable[[str], None],
    c,
) -> ft.Control:
    """文件面板：有 file_path 显示目录树+过滤；否则显示最近文件列表。"""
    root_dir = os.path.dirname(file_path) if file_path else None

    # 无根目录：最近文件列表
    if not root_dir:
        existing = [p for p in recent_files if os.path.exists(p)]
        if not existing:
            return _empty_hint("暂无最近文件\n打开或保存一个文件后此处会显示", c)
        items = [
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
                    spacing=6,
                ),
                c,
                on_click=lambda e, p=p: on_open_file(p),
            )
            for p in existing
        ]
        return ft.Column(
            controls=[
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
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
    flat = _flatten_tree(filtered)

    if not flat:
        body: ft.Control = _empty_hint(
            "无匹配文件" if file_filter.strip() else "该目录下无 Markdown 文件",
            c,
        )
    else:
        rows = []
        for kind, name, abspath, depth in flat:
            indent = depth * 14 + 12
            if kind == "file":
                rows.append(
                    _list_item(
                        ft.Row(
                            controls=[
                                ft.Icon(
                                    ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
                                    size=13,
                                    color=c.muted,
                                ),
                                ft.Text(
                                    name,
                                    size=12,
                                    color=c.text,
                                    font_family=FONT_MAIN,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True,
                                ),
                            ],
                            spacing=6,
                        ),
                        c,
                        on_click=lambda e, p=abspath: on_open_file(p),
                        indent=indent,
                    )
                )
            else:
                rows.append(
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
                            spacing=6,
                        ),
                        c,
                        indent=indent,
                    )
                )
        body = ft.Container(
            expand=True,
            content=ft.Column(controls=rows, spacing=0, scroll=ft.ScrollMode.AUTO),
        )

    return ft.Column(
        controls=[
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
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
    """大纲面板：标题按级别缩进，点击跳转。"""
    if not toc_entries:
        return _empty_hint("文档无标题", c)
    items = [
        _list_item(
            ft.Text(
                value=text,
                size=12,
                color=c.text,
                font_family=FONT_MAIN,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            c,
            on_click=lambda e, li=li: on_jump_to_line(li),
            indent=(lvl - 1) * 14 + 12,
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
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
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
                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
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
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                content=_search_box(
                    search_query, set_search_query, "在当前文档中查找…", c
                ),
            ),
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=4),
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
):
    """左侧侧边栏：文件 / 大纲 / 搜索三面板，顶部图标切换，右侧可拖拽调宽。"""
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
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.10, c.link) if active else None,
            content=ft.IconButton(
                icon=icon,
                tooltip=label,
                icon_size=18,
                on_click=lambda e: on_change_panel(key),
                style=ft.ButtonStyle(
                    color=c.link if active else c.muted,
                    padding=6,
                ),
            ),
        )

    tabs = ft.Container(
        bgcolor=c.toolbar_bg,
        border=only_border(bottom=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        content=ft.Row(
            controls=[
                _panel_tab("files", ft.Icons.FOLDER_OUTLINED, "文件"),
                _panel_tab("outline", ft.Icons.FORMAT_LIST_BULLETED, "大纲"),
                _panel_tab("search", ft.Icons.SEARCH, "搜索"),
            ],
            spacing=2,
        ),
    )

    # ---- 面板选择 ----
    if active_panel == "files":
        panel: ft.Control = _render_files_panel(
            file_path,
            recent_files,
            file_filter,
            set_file_filter,
            on_open_file,
            c,
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
