"""左侧侧边栏：文件 / 大纲 / 搜索三面板。

- 文件面板：.md/.markdown 文件树 + 搜索过滤。根目录优先级：
  settings.workspace_folder（显式「打开文件夹」锚定的工作区）>
  当前打开文件所在目录 > 最近文件列表（settings.recent_files）。
  工作区模式下打开子目录文件时文件树仍以工作区根排布，不随当前文件目录漂移；
  顶部显示文件夹名头与关闭按钮，并在树中高亮当前打开的文件。
- 大纲面板：从 document.lines 派生标题树，点击跳转到对应行。
- 搜索面板：4 选项（区分大小写/整个单词/正则/搜索文件夹）行级匹配，关键词高亮预览，
  点击跳转到精确 offset；跨文件搜索按文件分组（VSCode 风格）。

跳转通过 on_jump_to_line(li, off) 回调上抛（off=None 退化为行首），由 App 转发到
editor.nav_ref.jump_to_line。跨文件结果点击通过 on_open_file_and_jump(path, li, off)
上抛，App 用 pending_jump 机制处理"打开后跳转"时序。
大纲/搜索由 Sidebar 从 document.lines 自行派生（document 是 @ft.observable，实时刷新）。
"""

import asyncio
import os
import re
from collections.abc import Callable

import flet as ft

from models import BlockType, Document, SegType
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, _current_colors, only_border

_MD_EXTS = (".md", ".markdown")
_MAX_DEPTH = 3  # 文件树扫描最大深度
_MAX_RESULTS = 200  # 当前文档搜索结果上限，防止超长文档卡顿
_PREVIEW_RADIUS = 30  # 搜索预览匹配位前后字符数
# 跨文件搜索性能保护
_MAX_CROSS_FILES = 500      # 最多扫描文件数
_MAX_PER_FILE = 50          # 每文件结果上限
_MAX_CROSS_TOTAL = 1000     # 跨文件总结果上限
_MAX_FILE_SIZE = 1_000_000  # 跳过 >1MB 的文件（getsize 先判断，不读盘）
_MAX_LINE_LEN = 2000        # 超长行只取首匹配（防 minified 文件卡 finditer）


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


def _build_query_regex(
    query: str,
    case_sensitive: bool,
    whole_word: bool,
    regex: bool,
) -> re.Pattern | None:
    """从查询词 + 4 选项构造编译后的正则。

    4 种模式由 re.escape + \\b + re.IGNORECASE 组合表达，单一代码路径避免分支：
    - regex=False：re.escape 转义字面量（"a.b" 不被当模式）
    - regex=True：query 即模式
    - whole_word=True：用 \\b...\\b 包裹
    - case_sensitive=False：加 re.IGNORECASE

    无效正则返回 None（调用方提示"正则表达式无效"）。
    """
    q = query.strip()
    if not q:
        return None
    pattern_str = q if regex else re.escape(q)
    if whole_word:
        # 用 (?:...) 非捕获组包裹，确保 \b 边界作用于整个模式
        # 否则 \bcat|dog\b 会被解析为 (\bcat) | (dog\b)，中间分支无边界保护
        pattern_str = rf"\b(?:{pattern_str})\b"
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern_str, flags)
    except re.error:
        return None


def _match_lines(
    document: Document,
    pattern: re.Pattern | None,
    limit: int = _MAX_RESULTS,
) -> list[tuple[int, list[tuple[int, int]]]]:
    """行级正则匹配，返回 [(line_idx, [(start, end), ...]), ...]。

    每行返回所有匹配区间（VSCode 风格：行内多匹配均高亮）；超长行（> _MAX_LINE_LEN）
    只取首匹配，防 minified 文件卡 finditer。pattern 为 None 时返回 []。
    """
    if document is None or pattern is None:
        return []
    results: list[tuple[int, list[tuple[int, int]]]] = []
    for i, line in enumerate(document.lines):
        raw = line.raw or ""
        if len(raw) > _MAX_LINE_LEN:
            m = pattern.search(raw)
            if m:
                results.append((i, [(m.start(), m.end())]))
        else:
            matches = [(m.start(), m.end()) for m in pattern.finditer(raw)]
            if matches:
                results.append((i, matches))
        if len(results) >= limit:
            break
    return results


def _search_in_file(
    path: str,
    pattern: re.Pattern,
    max_per_file: int = _MAX_PER_FILE,
) -> list[tuple[int, list[tuple[int, int]]]]:
    """读取单文件并按行匹配。读取失败 / 超大文件返回 []。

    不解析成 Document：跨文件只需 line_idx + raw 做匹配，text.split("\\n") 已足够；
    parser.parse_markdown 会构建完整段树，对搜索场景是不必要开销。
    line_idx 按 \\n 切分索引，与编辑器打开后 document.lines[i].raw 一致。
    """
    try:
        if os.path.getsize(path) > _MAX_FILE_SIZE:
            return []
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    results: list[tuple[int, list[tuple[int, int]]]] = []
    for i, raw in enumerate(text.split("\n")):
        if len(raw) > _MAX_LINE_LEN:
            m = pattern.search(raw)
            if m:
                results.append((i, [(m.start(), m.end())]))
        else:
            matches = [(m.start(), m.end()) for m in pattern.finditer(raw)]
            if matches:
                results.append((i, matches))
        if len(results) >= max_per_file:
            break
    return results


def _collect_md_paths(tree: list) -> list[str]:
    """从嵌套文件树扁平化提取所有 .md 文件绝对路径（深度优先，字母序）。

    复用 _scan_markdown_files 产出的树结构，仅供跨文件搜索使用。
    """
    paths: list[str] = []

    def _walk(node):
        if node[0] == "file":
            paths.append(node[2])
        else:
            for child in node[2]:
                _walk(child)

    for node in tree:
        _walk(node)
    return paths


def _build_preview_spans(
    raw: str,
    matches: list[tuple[int, int]],
    c,
    radius: int = _PREVIEW_RADIUS,
) -> ft.Text:
    """构造带高亮的预览文本：匹配段 bgcolor=search_match_bg。

    窗口以首个匹配为中心，前后各 radius 字符；截断处加 …。
    用 ft.Text(spans=...) 单控件支持行内自动换行与 max_lines 截断。
    """
    if not raw:
        return ft.Text("", size=11, font_family=FONT_MONO)
    if not matches:
        return ft.Text(
            raw[:radius * 2], size=11, color=c.text, font_family=FONT_MONO,
            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS,
        )

    first_start = matches[0][0]
    win_start = max(0, first_start - radius)
    # 扩展 win_end 到包含首个匹配完整结尾
    win_end = min(len(raw), max(first_start + radius, matches[0][1]))
    prefix = "…" if win_start > 0 else ""
    suffix = "…" if win_end < len(raw) else ""

    spans: list[ft.TextSpan] = []
    if prefix:
        spans.append(ft.TextSpan(prefix, ft.TextStyle(size=11, color=c.muted, font_family=FONT_MONO)))

    pos = win_start
    base_style = ft.TextStyle(size=11, color=c.text, font_family=FONT_MONO)
    match_style = ft.TextStyle(
        size=11, color=c.search_match_fg, bgcolor=c.search_match_bg, font_family=FONT_MONO,
    )
    for s, e in matches:
        if e <= win_start or s >= win_end:
            continue
        ms, me = max(s, win_start), min(e, win_end)
        if ms > pos:
            spans.append(ft.TextSpan(raw[pos:ms], base_style))
        spans.append(ft.TextSpan(raw[ms:me], match_style))
        pos = me
    if pos < win_end:
        spans.append(ft.TextSpan(raw[pos:win_end], base_style))
    if suffix:
        spans.append(ft.TextSpan(suffix, ft.TextStyle(size=11, color=c.muted, font_family=FONT_MONO)))

    return ft.Text(spans=spans, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)


# ---- 通用控件工厂 ----


def _wrap_context_menu(
    content: ft.Control,
    path: str,
    is_dir: bool,
    on_action: Callable[[str, str], None],
    compare_source: str | None = None,
    key: str | None = None,
) -> ft.ContextMenu:
    """将列表项包裹在右键菜单中。

    文件菜单：打开 / 选择以进行比较 / 与已选项目进行比较 /
            新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 创建副本 / 删除
    文件夹菜单：新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 删除
    （文件夹无"打开"、"比较"和"创建副本"）
    compare_source 非空时，文件项显示「与已选项目进行比较」。
    key 透传至 ft.ContextMenu，供 ListView 按路径复用项实例（虚拟化 reconciliation）。
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
        key=key,
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
    key: str | None = None,
) -> ft.Control:
    """通用列表项：左侧缩进、hover ink 反馈。

    active=True 时以主题色半透明背景高亮（用于标记当前打开的文件）。
    key 透传至 Container，供 ListView 按唯一标识复用项实例。
    """
    return ft.Container(
        content=content,
        padding=ft.Padding.only(left=indent, top=Spacing.SM, bottom=Spacing.SM, right=Spacing.LG),
        on_click=on_click,
        ink=True,
        bgcolor=ft.Colors.with_opacity(0.12, c.link) if active else None,
        border_radius=Radius.LG,
        key=key,
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
    root_dir: str | None,
    root_label: str | None,
    is_workspace: bool,
    flat: list[tuple[str, str, str | None, int]],
    file_filter: str,
    set_file_filter: Callable[[str], None],
    on_open_file: Callable[[str], None],
    on_file_context_action: Callable[[str, str], None],
    on_close_folder: Callable[[], None] | None,
    c,
    compare_source: str | None = None,
) -> ft.Control:
    """文件面板：有根目录显示文件树+过滤；否则显示最近文件列表。

    文件树扫描与扁平化由 Sidebar 异步预计算后传入（flat），本函数仅负责
    渲染：根目录模式渲染搜索框 + ListView（虚拟化，仅构建可见行）；
    无根目录模式渲染最近文件 ListView。每个文件/文件夹项包裹
    ft.ContextMenu 提供右键菜单；compare_source 非空时文件项显示「比较」。
    flat 为空且 root_dir 存在时显示「无匹配 / 无文件」或异步加载提示。
    """
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
                key=f"recent-{p}",
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
                ft.ListView(
                    controls=items,
                    spacing=0,
                    expand=True,
                    first_item_prototype=True,
                    padding=ft.Padding.symmetric(vertical=Spacing.XS),
                ),
            ],
            spacing=0,
            expand=True,
        )

    # 有根目录：搜索框 + 文件树
    # 当前打开文件绝对路径（用于高亮活动文件行）
    active_abs = os.path.abspath(file_path) if file_path else None

    if not flat:
        # flat 为空可能是真无文件，也可能是异步扫描进行中（首帧）。
        # 有过滤词时提示无匹配，否则提示无文件（异步加载完成后 fs_version 变化会刷新）。
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
                        key=f"tree-{abspath}",
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
                        key=f"tree-{abspath}",
                    )
                )
        body = ft.ListView(
            controls=rows,
            spacing=0,
            expand=True,
            first_item_prototype=True,
            padding=ft.Padding.symmetric(vertical=Spacing.XS),
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
            key=f"toc-{li}",
        )
        for li, lvl, text in toc_entries
    ]
    return ft.ListView(
        controls=items,
        spacing=0,
        expand=True,
        first_item_prototype=True,
        padding=ft.Padding.symmetric(vertical=Spacing.XS),
    )


def _option_toggle_text(
    label: str, tooltip: str, active: bool, on_toggle: Callable[[bool], None], c,
) -> ft.Control:
    """文本切换按钮（Aa / ab / .*），active 时主题色半透明背景。

    VSCode 风格：3 个文本选项用 Text-based toggle（避免图标歧义），
    active 时 c.link 半透明背景 + c.link 文字，与 _panel_tab 风格一致。
    """
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.15, c.link) if active else None,
        border_radius=Radius.SM,
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
        content=ft.Text(
            label, size=11, weight=ft.FontWeight.W_600,
            color=c.link if active else c.muted,
            font_family=FONT_MONO,
        ),
        on_click=lambda e: on_toggle(not active),
        ink=True,
        tooltip=tooltip,
    )


def _option_toggle_icon(
    icon: str, tooltip: str, active: bool, on_toggle: Callable[[bool], None], c,
) -> ft.Control:
    """图标切换按钮（文件夹范围），active 时主题色半透明背景。"""
    return ft.IconButton(
        icon=icon,
        tooltip=tooltip,
        icon_size=14,
        on_click=lambda e: on_toggle(not active),
        style=ft.ButtonStyle(
            color=c.link if active else c.muted,
            bgcolor=ft.Colors.with_opacity(0.15, c.link) if active else None,
            padding=Spacing.XS,
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
        ),
    )


def _render_search_toolbar(
    opts: dict[str, bool],
    on_toggle: Callable[[str, bool], None],
    count_text: str,
    c,
) -> ft.Control:
    """搜索选项工具栏：4 个切换按钮 + 右侧结果计数。

    布局：[📁文件夹] [Aa大小写] [ab整词] [.*正则]  ----  [N 个结果]
    """
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
        content=ft.Row(
            controls=[
                _option_toggle_icon(
                    ft.Icons.FOLDER_OPEN, "搜索整个文件夹",
                    opts["folder"], lambda v: on_toggle("folder", v), c,
                ),
                _option_toggle_text(
                    "Aa", "区分大小写",
                    opts["case"], lambda v: on_toggle("case", v), c,
                ),
                _option_toggle_text(
                    "ab", "查找整个单词",
                    opts["word"], lambda v: on_toggle("word", v), c,
                ),
                _option_toggle_text(
                    ".*", "正则表达式",
                    opts["regex"], lambda v: on_toggle("regex", v), c,
                ),
                ft.Container(expand=True),
                ft.Text(count_text, size=11, color=c.muted, font_family=FONT_MAIN),
            ],
            spacing=Spacing.XS,
        ),
    )


def _render_search_panel(
    search_query: str,
    set_search_query: Callable[[str], None],
    search_opts: dict[str, bool],
    on_toggle_opt: Callable[[str, bool], None],
    search_results: list[tuple[int, list[tuple[int, int]]]],
    cross_results: list[tuple[str, str, list[tuple[int, list[tuple[int, int]]]]]],
    cross_loading: bool,
    regex_invalid: bool,
    document: Document,
    on_jump_to_line: Callable[[int, int | None], None],
    on_open_file_and_jump: Callable[[str, int, int | None], None],
    root_dir: str | None,
    c,
) -> ft.Control:
    """搜索面板：搜索框 + 选项工具栏 + 结果列表（当前文档 / 跨文件分组）。

    - search_opts：4 选项当前值（folder/case/word/regex）
    - search_results：当前文档结果 [(li, [(s,e),...]), ...]
    - cross_results：跨文件分组 [(path, name, [(li, [(s,e),...]), ...]), ...]
    - regex_invalid：正则编译失败时显示错误提示
    """
    placeholder = "在文件夹中查找…" if search_opts["folder"] else "在当前文档中查找…"

    # 头部：搜索框 + 选项工具栏（始终展示，便于随时切换）
    header = [
        ft.Container(
            padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
            content=_search_box(search_query, set_search_query, placeholder, c),
        ),
        _render_search_toolbar(search_opts, on_toggle_opt, "", c),
    ]

    # 空查询
    if not search_query.strip():
        body: ft.Control = _empty_hint("输入关键词以搜索文档", c)
        return ft.Column(controls=[*header, body], spacing=0, expand=True)

    # 正则错误
    if regex_invalid:
        body = _empty_hint("正则表达式无效", c)
        return ft.Column(controls=[*header, body], spacing=0, expand=True)

    # 跨文件模式
    if search_opts["folder"]:
        if not root_dir:
            body = _empty_hint("打开文件夹后可跨文件搜索", c)
        elif cross_loading and not cross_results:
            body = _empty_hint("搜索中…", c)
        elif not cross_results:
            body = _empty_hint("无匹配结果", c)
        else:
            total = sum(len(hits) for _, _, hits in cross_results)
            items: list[ft.Control] = []
            for path, name, hits in cross_results:
                # 文件分组标题：文件名 + 匹配数
                items.append(_render_file_group_header(name, len(hits), c))
                for li, matches in hits:
                    raw = _read_file_line_raw(path, li)
                    items.append(_render_search_result_item(
                        li, raw, matches, document, path,
                        on_jump_to_line, on_open_file_and_jump, c,
                    ))
            body = ft.ListView(
                controls=items,
                spacing=0,
                expand=True,
                padding=ft.Padding.symmetric(vertical=Spacing.XS),
            )
            # 计数塞到工具栏右侧（重渲染工具栏）
            header[-1] = _render_search_toolbar(
                search_opts, on_toggle_opt, f"{total} 个结果 / {len(cross_results)} 文件", c,
            )
        return ft.Column(controls=[*header, body], spacing=0, expand=True)

    # 当前文档模式
    if not search_results:
        body = _empty_hint("无匹配结果", c)
        return ft.Column(controls=[*header, body], spacing=0, expand=True)

    items = [
        _render_search_result_item(
            li, None, matches, document, None,
            on_jump_to_line, on_open_file_and_jump, c,
        )
        for li, matches in search_results
    ]
    header[-1] = _render_search_toolbar(
        search_opts, on_toggle_opt, f"{len(search_results)} 个结果", c,
    )
    body = ft.ListView(
        controls=items,
        spacing=0,
        expand=True,
        padding=ft.Padding.symmetric(vertical=Spacing.XS),
    )
    return ft.Column(controls=[*header, body], spacing=0, expand=True)


def _read_file_line_raw(path: str, li: int) -> str:
    """读取跨文件结果行的 raw 文本（供预览高亮）。

    失败时返回空串（预览退化为无高亮）。仅搜索结果点击预览时调用，
    非热路径，简单按行读取即可。
    """
    try:
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == li:
                    return line.rstrip("\n")
        return ""
    except (OSError, UnicodeDecodeError):
        return ""


def _render_file_group_header(name: str, hit_count: int, c) -> ft.Control:
    """跨文件分组标题：文件图标 + 文件名 + 匹配数。"""
    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.LG, top=Spacing.SM, bottom=Spacing.XS, right=Spacing.LG,
        ),
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=12, color=c.muted),
                ft.Text(
                    name, size=11, color=c.text, weight=ft.FontWeight.W_600,
                    font_family=FONT_MAIN, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS, expand=True,
                ),
                ft.Text(f"{hit_count}", size=10, color=c.muted, font_family=FONT_MONO),
            ],
            spacing=Spacing.SM,
        ),
    )


def _render_search_result_item(
    li: int,
    raw: str | None,
    matches: list[tuple[int, int]],
    document: Document,
    path: str | None,
    on_jump_to_line: Callable[[int, int | None], None],
    on_open_file_and_jump: Callable[[str, int, int | None], None],
    c,
) -> ft.Control:
    """单个搜索结果项：行号 + 高亮预览，点击跳转到首匹配 offset。

    raw=None 时从 document.lines[li].raw 读取（当前文档模式）；
    raw=str 时为跨文件模式预读的行文本。
    path=None 时点击调 on_jump_to_line（当前文档）；
    path=str 时调 on_open_file_and_jump（跨文件，App 处理 open+pending jump）。
    """
    if raw is None and document is not None and 0 <= li < len(document.lines):
        raw = document.lines[li].raw or ""
    first_off = matches[0][0] if matches else 0
    if path is None:
        on_click = lambda e, l=li, off=first_off: on_jump_to_line(l, off)
        key = f"search-{li}"
    else:
        on_click = lambda e, p=path, l=li, off=first_off: on_open_file_and_jump(p, l, off)
        key = f"cross-{path}-{li}"
    return _list_item(
        ft.Column(
            controls=[
                ft.Text(
                    value=f"行 {li + 1}",
                    size=10,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                _build_preview_spans(raw or "", matches, c),
            ],
            spacing=0,
        ),
        c,
        on_click=on_click,
        indent=Spacing.XL if path is None else Spacing.XL + 14,
        key=key,
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
    on_jump_to_line: Callable[[int, int | None], None],
    on_width_change: Callable[[int], None] | None = None,
    on_file_context_action: Callable[[str, str], None] | None = None,
    on_close_folder: Callable[[], None] | None = None,
    # 搜索增强：跨文件结果点击（open + pending jump）+ 选项持久化（复用 update_setting）
    on_open_file_and_jump: Callable[[str, int, int | None], None] | None = None,
    on_update_setting: Callable[[str, object], None] | None = None,
    compare_source: str | None = None,
    fs_version: int = 0,
    sidebar_open: bool = True,
):
    """左侧侧边栏：文件 / 大纲 / 搜索三面板，顶部图标切换，右侧可拖拽调宽。

    文件面板根目录优先级：settings.workspace_folder（显式「打开文件夹」锚定的
    工作区）> 当前文件所在目录 > None（最近文件列表）。工作区模式下打开子目录
    文件时文件树仍以工作区根排布。

    性能设计（组件树拆分 + 派生数据 memoize + 长列表虚拟化）：
    - 大纲 / 搜索结果按行内容签名 use_memo 缓存，文档编辑未触及相关行时不重算；
    - 文件树异步扫描（asyncio.to_thread 移出 UI 线程），scan_token 防竞态，
      fs_version 由 App 在文件增删改后递增驱动重扫；
    - 过滤 / 扁平化按 (file_tree, file_filter) / (filtered, root_dir) memoize；
    - 文件树 / 大纲 / 搜索 / 最近文件四处长列表全部用 ListView 虚拟化 + 唯一 key；
    - 拖拽手柄 / 顶部 Tab 用 use_memo 提取为静态控件（仅主题 / 面板变化重建），
      回调经 _cb_ref 读取最新值，避免闭包过期。

    on_file_context_action(action, path)：文件/文件夹右键菜单回调。
    on_close_folder()：关闭工作区文件夹（清空 workspace_folder，回退到当前文件目录）。
    fs_version：文件系统版本号，App 在文件增删改后递增以触发文件树重扫。
    """
    c = _current_colors()

    # 宽度：内部 state（拖拽时实时更新），ref 同步避免 stale 闭包
    _INIT_W = settings.get("sidebar_width", 256)
    width, set_width = ft.use_state(_INIT_W)
    width_ref = ft.use_ref(_INIT_W)
    width_ref.current = width

    # 拖拽中标志：控制外层 Container animate（拖拽时 None 即时跟随，否则 200ms 动画）
    dragging, set_dragging = ft.use_state(False)

    # 外部 settings → 内部 width state 同步：
    # use_state 初始值仅首次挂载生效，reset_settings / 外部改 settings 后需 effect
    # 主动同步。拖拽结束 pan_end→update_setting→_ext_w 变化时 width_ref 已等于 _ext_w，
    # effect 跳过避免冗余更新；reset_settings 时 _ext_w≠width_ref.current 触发同步。
    _ext_w = settings.get("sidebar_width", 256)

    def _sync_from_settings():
        if width_ref.current != _ext_w:
            width_ref.current = _ext_w
            set_width(_ext_w)

    ft.use_effect(_sync_from_settings, [_ext_w])

    _MIN_W, _MAX_W = 180, 600

    # 内部状态：文件过滤与文档搜索词
    file_filter, set_file_filter = ft.use_state("")
    search_query, set_search_query = ft.use_state("")

    # 派生数据
    recent_files = settings.get("recent_files", [])
    workspace_folder = settings.get("workspace_folder")
    root_dir, root_label, is_workspace = _resolve_files_root(workspace_folder, file_path)

    # ---- 回调稳定化：供 use_memo 提取的静态控件读取最新回调（避免闭包过期）----
    _cb_ref = ft.use_ref({})
    _cb_ref.current = {
        "on_change_panel": on_change_panel,
        "on_width_change": on_width_change,
    }

    # ---- 大纲：use_memo 按标题行签名缓存（仅标题增删改才重算）----
    # 签名为 tuple of (i, level, raw)，未编辑行 raw 引用稳定 → 比较近乎 O(1)。
    _toc_sig = tuple(
        (i, ln.level, ln.raw)
        for i, ln in enumerate(document.lines)
        if ln.block_type == BlockType.HEADING
    ) if document is not None else ()
    toc_entries = ft.use_memo(lambda: _compute_toc(document), [_toc_sig])

    # ---- 搜索选项：从 settings 读取（持久化），切换时调 on_update_setting ----
    _search_folder = settings.get("search_folder", False)
    _case_sensitive = settings.get("search_case_sensitive", False)
    _whole_word = settings.get("search_whole_word", False)
    _regex = settings.get("search_regex", False)
    _search_opts = {
        "folder": _search_folder,
        "case": _case_sensitive,
        "word": _whole_word,
        "regex": _regex,
    }
    # 选项 key → settings 字段名映射
    _OPT_KEYS = {
        "folder": "search_folder",
        "case": "search_case_sensitive",
        "word": "search_whole_word",
        "regex": "search_regex",
    }

    def _on_toggle_search_opt(key: str, value: bool):
        if on_update_setting is not None:
            on_update_setting(_OPT_KEYS.get(key, f"search_{key}"), value)

    # ---- 搜索 pattern：use_memo 缓存（4 选项 + 查询词变化才重编译）----
    # pattern 为 None 可能是空查询（正常）或无效正则（regex_invalid 提示）。
    pattern = ft.use_memo(
        lambda: _build_query_regex(search_query, _case_sensitive, _whole_word, _regex),
        [search_query, _case_sensitive, _whole_word, _regex],
    )
    regex_invalid = bool(search_query.strip()) and pattern is None

    # ---- 当前文档搜索：use_memo 按 pattern + 行内容签名缓存 ----
    # 行签名 tuple of raw（指针复制 O(n)，远轻于每次重跑匹配）；pattern 已聚合 4 选项+查询词。
    _lines_sig = tuple(ln.raw for ln in document.lines) if document is not None else ()
    search_results = ft.use_memo(
        lambda: _match_lines(document, pattern),
        [pattern, _lines_sig],
    )

    # ---- 跨文件搜索：异步 + cross_token 防竞态 ----
    # 异步 IO 不能用 use_memo（纯函数约束），用 use_state + use_effect。
    # effect 依赖 [pattern, _search_folder, root_dir, fs_version]：选项/查询词变 → pattern
    # 变 → 重扫；文件夹范围切换 / 文件增删改 → 重扫。
    cross_results, set_cross_results = ft.use_state(())
    cross_loading, set_cross_loading = ft.use_state(False)
    cross_token_ref = ft.use_ref(0)
    # page_ref 与文件树扫描共用（跨文件搜索在前声明，文件树复用）
    page_ref = ft.use_ref(None)
    page_ref.current = ft.context.page

    def _search_cross_files():
        if not _search_folder or pattern is None or not root_dir:
            set_cross_results(())
            set_cross_loading(False)
            return
        page = page_ref.current
        if page is None:
            return
        cross_token_ref.current += 1
        my_token = cross_token_ref.current
        set_cross_loading(True)

        async def _do():
            # 文件树扫描 + 单文件搜索都在线程池，UI 线程零阻塞
            tree = await asyncio.to_thread(_scan_markdown_files, root_dir)
            if cross_token_ref.current != my_token:
                return
            files = _collect_md_paths(tree)[:_MAX_CROSS_FILES]
            groups: list[tuple[str, str, list]] = []
            total = 0
            for fpath in files:
                if total >= _MAX_CROSS_TOTAL:
                    break
                if cross_token_ref.current != my_token:
                    return
                hits = await asyncio.to_thread(_search_in_file, fpath, pattern)
                if hits:
                    groups.append((fpath, os.path.basename(fpath), hits))
                    total += len(hits)
            if cross_token_ref.current != my_token:
                return
            set_cross_results(tuple(groups))
            set_cross_loading(False)

        page.run_task(_do)

    ft.use_effect(_search_cross_files, [pattern, _search_folder, root_dir, fs_version])

    # ---- 文件树：异步扫描 + scan_token 防竞态 ----
    # use_effect 依赖 [root_dir, fs_version]：根目录切换 / 文件增删改后重扫。
    # asyncio.to_thread 把同步磁盘扫描移出 UI 线程；scan_token 丢弃过期结果。
    file_tree, set_file_tree = ft.use_state(())
    scan_token_ref = ft.use_ref(0)

    def _scan_fs():
        if not root_dir or not os.path.isdir(root_dir):
            set_file_tree(())
            return
        page = page_ref.current
        if page is None:
            return
        scan_token_ref.current += 1
        my_token = scan_token_ref.current

        async def _do_scan():
            tree = await asyncio.to_thread(_scan_markdown_files, root_dir)
            # 过期任务（已切目录 / 又有新变更），丢弃避免覆盖最新树
            if scan_token_ref.current != my_token:
                return
            set_file_tree(tuple(tree))

        page.run_task(_do_scan)

    ft.use_effect(_scan_fs, [root_dir, fs_version])

    # ---- 文件树过滤 + 扁平化：use_memo 缓存（仅文件树 / 过滤词变化才重算）----
    filtered = ft.use_memo(
        lambda: _filter_tree(list(file_tree), file_filter),
        [file_tree, file_filter],
    )
    flat = ft.use_memo(
        lambda: (_flatten_tree(filtered, root_dir=root_dir) if root_dir else []),
        [filtered, root_dir],
    )

    # ---- 拖拽调宽手柄：use_memo 提取（仅主题变化重建）----
    def _on_pan_start(e: ft.DragStartEvent):
        set_dragging(True)
        # 标记侧边栏拖拽中：编辑器 _on_content_resize 据此跳过 set_viewport_w，
        # 避免开启换行时拖拽过程每帧触发全量软换行重算（HarfBuzz 测量）导致卡顿。
        # 用 page 属性传递标志，免去跨组件 props 链路改动。
        page = ft.context.page
        if page is not None:
            page.sidebar_dragging = True

    def _on_pan_update(e: ft.DragUpdateEvent):
        new_w = int(max(_MIN_W, min(_MAX_W, width_ref.current + e.local_delta.x)))
        if new_w != width_ref.current:
            width_ref.current = new_w
            set_width(new_w)

    def _on_pan_end(e):
        set_dragging(False)
        page = ft.context.page
        if page is not None:
            page.sidebar_dragging = False
        cb = _cb_ref.current.get("on_width_change")
        if cb is not None:
            cb(width_ref.current)

    drag_handle = ft.use_memo(
        lambda: ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_COLUMN,
            on_pan_start=_on_pan_start,
            on_pan_update=_on_pan_update,
            on_pan_end=_on_pan_end,
            content=ft.Container(
                width=3,
                bgcolor=ft.Colors.with_opacity(0.15, c.text),
                expand=True,
            ),
        ),
        [theme_mode],
    )

    # ---- 顶部 Tab 切换：use_memo 提取（仅面板 / 主题变化重建）----
    def _build_tabs():
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
                    on_click=lambda e, k=key: _cb_ref.current["on_change_panel"](k),
                    style=ft.ButtonStyle(
                        color=c.link if active else c.muted,
                        padding=Spacing.MD,
                    ),
                ),
            )
        return ft.Container(
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

    tabs = ft.use_memo(_build_tabs, [active_panel, theme_mode])

    # ---- 面板选择 ----
    # 文件面板右键回调：未提供时用 no-op 避免崩溃
    _file_ctx = on_file_context_action if on_file_context_action is not None else (lambda action, path: None)
    if active_panel == "files":
        panel: ft.Control = _render_files_panel(
            file_path,
            recent_files,
            root_dir,
            root_label,
            is_workspace,
            flat,
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
        # 跨文件点击回调：未提供时用 no-op 避免崩溃
        _open_and_jump = on_open_file_and_jump if on_open_file_and_jump is not None else (
            lambda path, li, off: on_open_file(path) if on_open_file else None
        )
        panel = _render_search_panel(
            search_query,
            set_search_query,
            _search_opts,
            _on_toggle_search_opt,
            search_results,
            cross_results,
            cross_loading,
            regex_invalid,
            document,
            on_jump_to_line,
            _open_and_jump,
            root_dir,
            c,
        )

    # 外层 Container 统一控制宽度 / 动画 / 裁剪（原 sidebar_container 逻辑内移）：
    # - width 与内部 width state 同源，拖拽时 set_width 即时驱动外层，无 clip 裁剪
    # - dragging 时 animate=None 即时跟随，否则 200ms 动画（展开/折叠平滑过渡）
    # - 内容 Container 用 expand=True 撑满减去 drag_handle(6px) 的空间，零溢出
    # - STRETCH 让 drag_handle 竖向填满，全高可抓
    return ft.Container(
        width=width if sidebar_open else 0,
        animate=None if dragging else ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=ft.Row(
            controls=[
                ft.Container(
                    expand=True,
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
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
