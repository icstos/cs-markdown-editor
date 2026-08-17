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
from services import shortcut
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, _current_colors, only_border

_MD_EXTS = (".md", ".markdown")
_MAX_DEPTH = 8  # 文件树扫描最大深度（VSCode 风格全类型扫描，8 层覆盖典型项目结构）
_MAX_FILES = 5000  # 单次扫描文件数上限保护（防止 node_modules 等巨型目录拖慢 UI）
_WATCH_INTERVAL = 2.0  # 外部文件系统变化轮询间隔（秒）：VSCode watcher 的零依赖替代
_MAX_RESULTS = 200  # 当前文档搜索结果上限，防止超长文档卡顿
_PREVIEW_RADIUS = 30  # 搜索预览匹配位前后字符数
# 跨文件搜索性能保护
_MAX_CROSS_FILES = 500      # 最多扫描文件数
_MAX_PER_FILE = 50          # 每文件结果上限
_MAX_CROSS_TOTAL = 1000     # 跨文件总结果上限
_MAX_FILE_SIZE = 1_000_000  # 跳过 >1MB 的文件（getsize 先判断，不读盘）
_MAX_LINE_LEN = 2000        # 超长行只取首匹配（防 minified 文件卡 finditer）

# 文件类型 → 图标映射（VSCode 风格 Seti/Material 混合：按扩展名给语义化图标，
# 一眼区分 Markdown / 图片 / 代码 / 文档 / 压缩包 / 音视频）。颜色统一 c.muted，
# 仅 .md 用 c.link 突出可编辑文件，避免色彩过载。
_FILE_ICON_MAP: dict[str, str] = {
    # Markdown 文档
    ".md": ft.Icons.DESCRIPTION,
    ".markdown": ft.Icons.DESCRIPTION,
    # 图片
    ".png": ft.Icons.IMAGE,
    ".jpg": ft.Icons.IMAGE,
    ".jpeg": ft.Icons.IMAGE,
    ".gif": ft.Icons.IMAGE,
    ".svg": ft.Icons.IMAGE,
    ".webp": ft.Icons.IMAGE,
    ".bmp": ft.Icons.IMAGE,
    ".ico": ft.Icons.IMAGE,
    # 代码 / 配置
    ".py": ft.Icons.CODE,
    ".json": ft.Icons.CODE,
    ".yaml": ft.Icons.CODE,
    ".yml": ft.Icons.CODE,
    ".toml": ft.Icons.CODE,
    ".ini": ft.Icons.CODE,
    ".cfg": ft.Icons.CODE,
    ".js": ft.Icons.JAVASCRIPT,
    ".ts": ft.Icons.JAVASCRIPT,
    ".html": ft.Icons.HTML,
    ".css": ft.Icons.CSS,
    ".txt": ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
    # 文档
    ".pdf": ft.Icons.PICTURE_AS_PDF,
    ".doc": ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
    ".docx": ft.Icons.INSERT_DRIVE_FILE_OUTLINED,
    # 压缩包
    ".zip": ft.Icons.FOLDER_ZIP,
    ".tar": ft.Icons.FOLDER_ZIP,
    ".gz": ft.Icons.FOLDER_ZIP,
    ".7z": ft.Icons.FOLDER_ZIP,
    ".rar": ft.Icons.FOLDER_ZIP,
    # 音视频
    ".mp3": ft.Icons.MUSIC_NOTE,
    ".wav": ft.Icons.MUSIC_NOTE,
    ".flac": ft.Icons.MUSIC_NOTE,
    ".mp4": ft.Icons.MOVIE,
    ".mov": ft.Icons.MOVIE,
    ".avi": ft.Icons.MOVIE,
    # Windows 快捷方式：一律 SHORTCUT 主图标（与 .md 的 DESCRIPTION 在形状上区分），
    # 指向 .md 的快捷方式在行渲染处改用 link 主题色（见 _file_row_icon_data）
    ".lnk": ft.Icons.SHORTCUT,
}


def _file_icon(name: str, c) -> tuple[str, str]:
    """按文件扩展名返回 (图标名, 颜色)。

    .md/.markdown 用 c.link 主题色突出可编辑文件；其余统一 c.muted 避免色彩过载。
    未知扩展名兜底 INSERT_DRIVE_FILE_OUTLINED。扩展名匹配大小写不敏感。
    .lnk 快捷方式用 SHORTCUT 图标，与 .md 的 DESCRIPTION 文档图标在形状上区分。
    """
    lower = name.lower()
    _, ext = os.path.splitext(lower)
    icon = _FILE_ICON_MAP.get(ext, ft.Icons.INSERT_DRIVE_FILE_OUTLINED)
    color = c.link if ext in (".md", ".markdown") else c.muted
    return icon, color


def _file_row_icon_data(
    name: str, abspath: str | None, c
) -> tuple[str, str, str | None]:
    """按文件扩展名返回 (图标名, 颜色, tooltip)。

    与 _file_icon 的区别：.lnk 一律用 SHORTCUT 主图标（不随目标类型变化），
    确保链接与 .md 文档在图标形状上一眼可分；指向 .md 的快捷方式用 c.link
    主题色提示「可在编辑器中打开」，tooltip 显示目标路径（资源管理器直觉）。
    其余文件直接委托 _file_icon，tooltip 为 None。
    """
    if abspath and shortcut.is_shortcut(name):
        lnk_target = shortcut.resolve_md_target(abspath)
        icon = ft.Icons.SHORTCUT
        color = c.link if lnk_target is not None else c.muted
        tooltip = f"→ {lnk_target}" if lnk_target is not None else None
        return icon, color, tooltip
    icon, color = _file_icon(name, c)
    return icon, color, None


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


def _scan_files(
    root: str,
    max_depth: int = _MAX_DEPTH,
    max_files: int = _MAX_FILES,
) -> list:
    """递归扫描 root 下的全类型文件，返回嵌套结构（VSCode 风格资源管理器）。

    元素格式：
      ("dir", name, children_list)
      ("file", name, abs_path)
    目录在前、字母序排序；跳过隐藏目录与常见忽略目录。失败时返回 []。

    与旧 _scan_markdown_files 的差异：
    - 收录所有类型文件（不再按 _MD_EXTS 过滤），让用户看到图片/代码等资源
    - 保留空目录（VSCode 显示空目录，移除旧 `if children:` 过滤）
    - max_files 上限保护：闭包计数器超限即停止追加，防止 node_modules 等巨型目录拖慢 UI
    """
    if not root or not os.path.isdir(root):
        return []
    counter = [0]  # 闭包计数器，跨递归层累计

    def _walk(dir_path: str, depth: int) -> list:
        if depth > max_depth or counter[0] >= max_files:
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
            if counter[0] >= max_files:
                break
            if entry.name.startswith(".") or entry.name in (
                "__pycache__",
                "node_modules",
                ".git",
            ):
                continue
            if entry.is_dir():
                children = _walk(entry.path, depth + 1)
                # 空目录也保留（VSCode 显示空目录），不再过滤 children 为空的目录
                result.append(("dir", entry.name, children))
            elif entry.is_file():
                result.append(("file", entry.name, entry.path))
                counter[0] += 1
        return result

    return _walk(root, 0)


def _tree_signature(tree: list) -> str:
    """文件树内容签名：递归拼接「目录相对路径 + 文件绝对路径」。

    目录节点仅存 name（_scan_files 格式），walk 时按「父/子」累积相对路径；
    文件节点用绝对路径。签名与树内容一一对应，供轮询 watcher 做变更比较。
    返回 str（而非 list/tuple）：ft.Ref 内部用 weakref 存值，内置容器不可弱引用。
    """
    parts: list[str] = []

    def _walk(node, dir_path: str):
        if node[0] == "file":
            parts.append("f:" + node[2])
        else:
            d = dir_path + "/" + node[1]
            parts.append("d:" + d)
            for child in node[2]:
                _walk(child, d)

    for n in tree:
        _walk(n, "")
    return "\n".join(parts)


async def poll_fs_changes(
    root_dir: str,
    interval: float,
    base_holder,
    on_change: Callable[[], None],
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """轮询监测 root_dir 文件树变化，变化时调用 on_change()（模块级便于单测）。

    覆盖外部程序对文件夹的创建/删除/重命名（文件内容修改不影响树结构，不触发）。
    每轮在后台线程重扫（与 _scan_files 同源同限：深度/数量/忽略目录一致），
    签名 != base_holder.current 时更新基准并上报（天然按 interval 节流）。

    base_holder.current 为 None 时先做一次基准扫描；调用方在应用内文件操作
    触发重扫后把最新签名写入 base_holder，避免轮询对同一变更重复上报。
    should_stop() 返回 True 时退出（任务 cancel 的兜底，防目录切换后旧轮询残留）。
    """
    if base_holder.current is None:
        base_holder.current = _tree_signature(
            await asyncio.to_thread(_scan_files, root_dir)
        )
    while True:
        await asyncio.sleep(interval)
        if should_stop is not None and should_stop():
            return
        sig = _tree_signature(await asyncio.to_thread(_scan_files, root_dir))
        if sig != base_holder.current:
            base_holder.current = sig
            on_change()


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


def _flatten_tree(
    tree: list,
    depth: int = 0,
    root_dir: str = "",
    expanded: frozenset[str] | None = None,
    force_expand: bool = False,
) -> list[tuple[str, str, str | None, int]]:
    """扁平化为 [(type, name, abspath_or_None, depth), ...]，便于一次性渲染。

    目录的 abspath 由 root_dir + 目录名拼接（供右键菜单与展开/折叠状态匹配使用）。

    展开/折叠控制（VSCode 风格动态扁平化）：
    - expanded=None：全展开（向后兼容旧语义，供测试/无状态场景使用）
    - force_expand=True：强制全展开（过滤模式下显示所有匹配项，忽略折叠状态）
    - 否则：仅当 dir_path ∈ expanded 时递归展开子层，实现点击 toggle 展开/折叠
    目录节点本身始终输出（让用户能看到折叠的目录并点击展开）。
    """
    out: list[tuple[str, str, str | None, int]] = []
    for node in tree:
        if node[0] == "file":
            out.append(("file", node[1], node[2], depth))
        else:
            dir_path = os.path.join(root_dir, node[1]) if root_dir else node[1]
            out.append(("dir", node[1], dir_path, depth))
            should_recurse = force_expand or expanded is None or dir_path in expanded
            if should_recurse:
                out.extend(_flatten_tree(
                    node[2], depth + 1, dir_path, expanded, force_expand,
                ))
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


def _flatten_matches(
    search_results: list[tuple[int, list[tuple[int, int]]]],
) -> list[tuple[int, int, int]]:
    """将当前文档搜索结果扁平化为 [(li, s, e), ...]。

    供"替换当前"按索引取匹配、导航上下翻、计数显示用。
    """
    flat: list[tuple[int, int, int]] = []
    for li, matches in search_results:
        for s, e in matches:
            flat.append((li, s, e))
    return flat


def _flatten_cross_matches(
    cross_results: list[tuple[str, str, list[tuple[int, list[tuple[int, int]]]]]],
) -> list[tuple[str, int, int, int]]:
    """将跨文件搜索结果扁平化为 [(path, li, s, e), ...]。"""
    flat: list[tuple[str, int, int, int]] = []
    for path, _name, hits in cross_results:
        for li, matches in hits:
            for s, e in matches:
                flat.append((path, li, s, e))
    return flat


def _convert_vscode_backrefs(replace_text: str) -> str:
    r"""将 VSCode 风格 $N 反向引用转换为 Python \g<N> 语法。

    match.expand() 仅识别 \\1 / \\g<1>（Python re 语法），VSCode/Typora 用户
    习惯 $1 / $2 写法。此处做一次性转换，两种语法并存：
    - $$ → 字面量 $
    - $N（N 为 1 位或多位数字）→ \\g<N>
    - $ 后非数字 → 字面量 $（如 $abc 保持不变）
    - \\1 / \\g<1> 等 Python 语法原样保留，match.expand() 原生处理
    """
    # $$ → 临时占位符（避免被 $N 规则误匹配）
    result = replace_text.replace("$$", "\x00")
    # $N → \g<N>
    result = re.sub(r"\$(\d+)", r"\\g<\1>", result)
    # 恢复字面量 $
    return result.replace("\x00", "$")


def _expand_replacement(
    match: re.Match,
    replace_text: str,
    regex_mode: bool,
) -> str:
    r"""展开替换文本中的反向引用。

    regex 模式：同时支持 VSCode 风格 $1/$2 与 Python 风格 \1/\g<1>。
    先把 $N 转为 \g<N>，再交 match.expand() 统一展开。
    非 regex 模式：$ 和 \ 为字面量，直接返回 replace_text 不做展开。
    """
    if regex_mode:
        try:
            return match.expand(_convert_vscode_backrefs(replace_text))
        except (re.error, ValueError):
            return replace_text
    # 非 regex：$ 和 \ 无特殊含义，直接返回字面量
    return replace_text


def _find_match_at(
    pattern: re.Pattern,
    raw: str,
    start: int,
    end: int,
) -> re.Match | None:
    """在 raw 中查找起始/结束位置与 (start, end) 匹配的 re.Match 对象。

    供 _expand_replacement 需要完整 Match（含捕获组）时使用。
    """
    for m in pattern.finditer(raw):
        if m.start() == start and m.end() == end:
            return m
        if m.start() > start:
            break
    return None


def _replace_in_string(
    raw: str,
    pattern: re.Pattern,
    spans: list[tuple[int, int]],
    replace_text: str,
    regex_mode: bool,
) -> tuple[str, int]:
    """行内替换所有匹配区间，右→左处理保偏移。返回 (new_raw, count)。

    regex 模式用 pattern.finditer 重建 Match 做反向引用展开；
    非 regex 模式直接用 replace_text 字面量替换。
    """
    if not spans:
        return raw, 0
    # regex 模式：预建 (start,end)→Match 映射，供 expand 使用
    match_map: dict[tuple[int, int], re.Match] = {}
    if regex_mode:
        for m in pattern.finditer(raw):
            match_map[(m.start(), m.end())] = m

    new_raw = raw
    count = 0
    # 右→左：左侧替换不破坏右侧偏移
    for s, e in sorted(spans, key=lambda t: t[0], reverse=True):
        if s < 0 or e > len(new_raw) or s > e:
            continue
        if regex_mode and (s, e) in match_map:
            replacement = _expand_replacement(match_map[(s, e)], replace_text, True)
        else:
            replacement = replace_text
        new_raw = new_raw[:s] + replacement + new_raw[e:]
        count += 1
    return new_raw, count


def _replace_in_file_text(
    text: str,
    pattern: re.Pattern,
    replace_text: str,
    regex_mode: bool,
) -> tuple[str, int]:
    """跨文件单文件文本替换：按 \\n 切行逐行替换。返回 (new_text, count)。

    与 _search_in_file 的行切分方式一致（text.split("\\n")），保证 line_idx 对齐。
    """
    lines = text.split("\n")
    total = 0
    for i, raw in enumerate(lines):
        spans = [(m.start(), m.end()) for m in pattern.finditer(raw)]
        if spans:
            new_raw, count = _replace_in_string(raw, pattern, spans, replace_text, regex_mode)
            lines[i] = new_raw
            total += count
    return "\n".join(lines), total


def _collect_md_paths(tree: list) -> list[str]:
    """从嵌套文件树扁平化提取所有 .md/.markdown 文件绝对路径（深度优先，字母序）。

    复用 _scan_files 产出的全类型树结构，仅供跨文件搜索使用。
    扫描改为全类型后此处必须按 _MD_EXTS 过滤，否则跨文件搜索会尝试读取
    图片/二进制等非文本文件（_search_in_file 的 UnicodeDecodeError 兜底
    会静默跳过，但浪费 IO 且语义不符）。
    快捷方式：指向 .md 的 .lnk 以目标路径参与搜索（搜索目标内容，点击
    打开目标文档）；目标与树内 .md 重复时去重保序。
    """
    paths: list[str] = []

    def _walk(node):
        if node[0] == "file":
            name_l = node[1].lower()
            if name_l.endswith(_MD_EXTS):
                paths.append(node[2])
            elif name_l.endswith(".lnk"):
                target = shortcut.resolve_md_target(node[2])
                if target:
                    paths.append(target)
        else:
            for child in node[2]:
                _walk(child)

    for node in tree:
        _walk(node)
    return list(dict.fromkeys(paths))  # 去重保序


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
    menu_state: dict | None = None,
) -> ft.ContextMenu:
    """将列表项包裹在右键菜单中。

    文件菜单：打开 / 选择以进行比较 / 与已选项目进行比较 /
            新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 创建副本 / 删除
    文件夹菜单：打开 / 新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 删除
    （文件夹无"比较"和"创建副本"）
    「打开」语义：文件 → 编辑器（.md）/ 系统默认程序（非 .md）；文件夹 → 资源管理器打开。
    compare_source 非空时，文件项显示「与已选项目进行比较」。
    key 透传至 ft.ContextMenu，供 ListView 按路径复用项实例（虚拟化 reconciliation）。

    menu_state 非空时（工作区模式，外层有空白菜单 GestureDetector），用
    GestureDetector 监听 on_secondary_tap_down（按下阶段）置 inner_active 标志位，
    供外层在 on_secondary_tap_up（抬起阶段）判断右键是否命中文件项——
    避免右键文件项时内外两层菜单同时弹出。
    """
    items: list[ft.PopupMenuItem] = []

    # 打开：文件用 OPEN_IN_NEW，文件夹用 FOLDER_OPEN（区分语义）
    items.append(
        ft.PopupMenuItem(
            content="打开",
            icon=ft.Icons.FOLDER_OPEN if is_dir else ft.Icons.OPEN_IN_NEW,
            on_click=lambda e, p=path: on_action("open", p),
        )
    )

    if not is_dir:
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

    if menu_state is None:
        # 最近文件列表模式：无外层空白菜单，保持默认自动触发即可
        return ft.ContextMenu(
            content=content,
            secondary_items=items,
            key=key,
        )

    # 工作区模式：行菜单由 ContextMenu 原生 down 触发（Listener 实现）。
    # 此标志 GD 仅在非拖拽模式生效（拖拽模式下被行 DragTarget 吞掉，天然
    # 失效但无害——行/空白分流由手势竞技场与行占位 GD 完成，见 _wrap_row_gesture）。
    # 回调仅置标志位，不消费事件，不影响左键点击与列表滚动。
    def _on_inner_secondary(_e):
        menu_state["inner_active"] = True

    def _on_inner_close(_e):
        menu_state["inner_active"] = False

    detector = ft.GestureDetector(
        content=content,
        on_secondary_tap_down=_on_inner_secondary,
    )
    return ft.ContextMenu(
        content=detector,
        secondary_items=items,
        key=key,
        on_dismiss=_on_inner_close,
        on_select=_on_inner_close,
    )


def _wrap_blank_context_menu(
    content: ft.Control,
    root_dir: str,
    on_action: Callable[[str, str], None],
    c,
    menu_state: dict,
) -> tuple[ft.GestureDetector, ft.ContextMenu]:
    """文件面板空白区域右键菜单（VSCode 资源管理器风格）。

    仅包含不依赖具体文件项的操作：新建文件 / 新建文件夹 / 复制路径 / 打开文件位置。
    path=root_dir（is_dir=True），复用 on_sidebar_context_action 的新建逻辑
    （dir_path = path if is_dir else dirname(path) → 在根目录下创建）。

    返回 (blank_body, holder)：
    - blank_body：GestureDetector 包裹列表主体（含其外层根 DragTarget），
      on_secondary_tap_down 手动 open 菜单，必须在所有 DragTarget 外层
      （实测 DragTarget 会吞掉其子孙 GD 的右键 tap 识别器）；
    - holder：零尺寸 ContextMenu 载体，不参与命中测试（自动触发物理上不可能），
      仅在 open() 时显示 items——不依赖客户端对 secondary_trigger=None 的支持。

    行/空白分流机制（拖拽模式）：行级菜单由行 ContextMenu 原生 down 触发
    （Listener 实现，不受 DragTarget 影响）；行的最外层「竞技场占位 GD」
    （见 _wrap_row_gesture）以更深的识别器在右键行时赢得手势竞技场，本 GD
    被竞技场拒绝——只有右键空白区域时本 GD 才是唯一识别器而获胜并弹菜单。

    inner_active 标志为非拖拽模式的兜底（无 DragTarget 时行内标志 GD 存活，
    置位后此处跳过；行菜单 on_dismiss/on_select 复位，无残留）。
    """
    items: list[ft.PopupMenuItem] = [
        ft.PopupMenuItem(
            content="新建文件", icon=ft.Icons.NOTE_ADD,
            on_click=lambda e, p=root_dir: on_action("new_file", p),
        ),
        ft.PopupMenuItem(
            content="新建文件夹", icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=lambda e, p=root_dir: on_action("new_folder", p),
        ),
        ft.PopupMenuItem(),  # 分隔
        ft.PopupMenuItem(
            content="复制路径", icon=ft.Icons.CONTENT_COPY,
            on_click=lambda e, p=root_dir: on_action("copy_path", p),
        ),
        ft.PopupMenuItem(
            content="打开文件位置", icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e, p=root_dir: on_action("reveal", p),
        ),
    ]

    async def _on_blank_secondary_down(e):
        # child-first：右键文件项时内层已置位标志（同 down 相位、必先执行），
        # 此处跳过并复位；右键空白区域时标志为 False → 立即弹空白菜单
        if menu_state.get("inner_active"):
            menu_state["inner_active"] = False
            return
        await holder.open(global_position=e.global_position)

    blank_body = ft.GestureDetector(
        content=content,
        on_secondary_tap_down=_on_blank_secondary_down,
        expand=True,
    )
    holder = ft.ContextMenu(
        content=ft.Container(width=0, height=0),
        items=items,
        key="blank-area",
    )
    return blank_body, holder


# ---- 文件树拖拽（VSCode 资源管理器风格） ----

_DRAG_GROUP = "filetree"  # 拖拽组：仅文件树内部可互相拖放


def _drop_allowed(src: str, dst_dir: str) -> bool:
    """拖放合法性（与 file_ops.move_path 校验一致，UI 层提前判断控制高亮）。"""
    if not src or not dst_dir:
        return False
    try:
        src_abs = os.path.abspath(src)
        dst_abs = os.path.abspath(dst_dir)
    except OSError:
        return False
    if not os.path.isdir(dst_abs):
        return False
    if os.path.dirname(src_abs) == dst_abs:
        return False  # 已在目标文件夹中
    if os.path.isdir(src_abs):
        s, d = os.path.normcase(src_abs), os.path.normcase(dst_abs)
        if d == s or d.startswith(s + os.sep):
            return False  # 不能移到自身或其子文件夹
    return True


def _drag_feedback(name: str, icon_name: str, icon_color, c) -> ft.Control:
    """拖拽时跟随指针的紧凑预览标签（图标 + 名称）。"""
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.95, c.surface),
        border=ft.Border.all(1, c.border),
        border_radius=Radius.MD,
        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
        content=ft.Row(
            controls=[
                ft.Icon(icon_name, size=13, color=icon_color),
                ft.Text(
                    name,
                    size=12,
                    color=c.text,
                    font_family=FONT_MAIN,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            spacing=Spacing.SM,
            tight=True,
        ),
    )


def _wrap_draggable(
    row: ft.Control,
    ghost: ft.Control,
    feedback: ft.Control,
    drag_registry: dict,
    path: str,
    key: str | None = None,
) -> ft.Draggable:
    """将文件树行包装为可拖拽项。

    - content_when_dragging：拖起后原位置显示半透明占位（VSCode 直觉）；
    - drag_registry：id(Draggable) → 路径，DragTarget 事件经 e.src 反查源路径
      （拖拽会话期间行不重建，id 映射稳定）；
    - key 供 ListView 按路径复用行实例（虚拟化 reconciliation）。
    """
    d = ft.Draggable(
        group=_DRAG_GROUP,
        content=row,
        content_when_dragging=ghost,
        content_feedback=feedback,
        key=key,
    )
    drag_registry[id(d)] = path
    return d


def _wrap_drop_target(
    content: ft.Control,
    dst_dir: str,
    drag_registry: dict,
    on_drop: Callable[[str, str], None],
    set_highlight: Callable[[str | None], None],
    key: str | None = None,
) -> ft.DragTarget:
    """将文件夹行（或根区域）包装为放置目标。

    高亮采用状态驱动：Flet 0.86 组件 render 中创建的控件构建后被冻结，
    禁止命令式 row.update()（抛 RuntimeError），故 on_will_accept/on_leave
    仅调用 set_highlight 更新 Sidebar 的 drop_hover_dir state，由渲染层
    根据该 state 计算行背景（VSCode 资源管理器直觉的合法目标高亮）。
    非法目标（自身/子孙/原地）不高亮，松手静默忽略。
    """
    def _src_of(e) -> str | None:
        src = getattr(e, "src", None)
        return drag_registry.get(id(src)) if src is not None else None

    def on_will_accept(e):
        src = _src_of(e)
        if src is not None and _drop_allowed(src, dst_dir):
            set_highlight(dst_dir)
        else:
            set_highlight(None)

    def on_leave(e):
        set_highlight(None)

    def on_accept(e):
        set_highlight(None)
        src = _src_of(e)
        if src is not None and _drop_allowed(src, dst_dir):
            on_drop(src, dst_dir)

    return ft.DragTarget(
        group=_DRAG_GROUP,
        content=content,
        on_will_accept=on_will_accept,
        on_leave=on_leave,
        on_accept=on_accept,
        key=key,
    )


def _wrap_row_gesture(row: ft.Control, key: str | None = None) -> ft.GestureDetector:
    """行级「竞技场占位」GestureDetector（拖拽模式下行的最外层包装）。

    背景（Flet 0.86 / Flutter 实测行为）：DragTarget 会吞掉其子孙
    GestureDetector 的右键 tap 识别器——行内各 GD 的 secondary_tap_down
    均无法送达 Python；行级菜单靠 ContextMenu 原生 down 触发（Listener
    实现，不受影响）。但若行上无任何存活的 secondary tap 识别器，右键行
    时外层空白菜单 GD 将成为手势竞技场中唯一识别器而获胜，误弹空白菜单。

    此占位 GD 位于行 DragTarget 外层（识别器存活）、比外层空白菜单 GD
    更深（先加入竞技场，右键行时获胜），回调为空操作——实际菜单由行级
    ContextMenu 原生弹出，外层空白 GD 被竞技场拒绝，空白菜单只在真正
    的空白区域触发。key 供 ListView 按路径复用行实例（虚拟化 reconciliation）。
    """
    return ft.GestureDetector(
        content=row,
        on_secondary_tap_down=lambda _e: None,
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
    expanded_dirs: frozenset[str] = frozenset(),
    on_toggle_dir: Callable[[str], None] | None = None,
    on_open_external: Callable[[str], None] | None = None,
    on_file_drop: Callable[[str, str], None] | None = None,
    # 拖拽悬停高亮（状态驱动）：当前悬停目标文件夹路径 + setter
    highlight_dir: str | None = None,
    set_highlight_dir: Callable[[str | None], None] | None = None,
) -> ft.Control:
    """文件面板：有根目录显示文件树+过滤；否则显示最近文件列表。

    文件树扫描与扁平化由 Sidebar 异步预计算后传入（flat），本函数仅负责
    渲染：根目录模式渲染搜索框 + ListView（虚拟化，仅构建可见行）；
    无根目录模式渲染最近文件 ListView。每个文件/文件夹项包裹
    ft.ContextMenu 提供右键菜单；compare_source 非空时文件项显示「比较」。
    flat 为空且 root_dir 存在时显示「无匹配 / 无文件」或异步加载提示。

    VSCode 风格文件树：
    - 文件夹行：chevron（▸/▾）+ 动态 folder icon（折叠/展开）+ 整行点击 toggle
    - 文件行：_file_row_icon_data 按扩展名映射图标（.md=DESCRIPTION 文档图标；
      .lnk=SHORTCUT 链接图标，与 .md 在形状上一眼可分）+ chevron 占位
    - 最近文件列表同样按扩展名区分图标（不再统一一个图标）
    - 点击分流：.md → 编辑器打开；非 .md → 系统默认程序打开（on_open_external）
    - active 高亮仅对 .md 文件生效（非 md 不在编辑器打开，无需高亮）
    - 拖拽移动（on_file_drop 非空时启用）：文件/文件夹可拖到文件夹行或根空白区，
      合法目标行高亮，松手移动；拖起后原位半透明 + 指针跟随预览标签
    """
    # 无根目录：最近文件列表
    if not root_dir:
        existing = [p for p in recent_files if os.path.exists(p)]
        if not existing:
            return _empty_hint("暂无最近文件\n打开或保存一个文件后此处会显示", c)

        items = []
        for p in existing:
            # 按扩展名区分图标；.lnk 用 SHORTCUT，指向 .md 时 tooltip 显示目标路径
            icon_name, icon_color, tooltip = _file_row_icon_data(
                os.path.basename(p), p, c
            )
            items.append(
                _wrap_context_menu(
                    _list_item(
                        ft.Row(
                            controls=[
                                ft.Icon(icon_name, size=14, color=icon_color),
                                ft.Text(
                                    os.path.basename(p),
                                    size=12,
                                    color=c.text,
                                    font_family=FONT_MAIN,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True,
                                    tooltip=tooltip,
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
            )
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

    # 内外层右键菜单协调状态：右键文件项时 inner_active=True，外层空白菜单据此跳过
    menu_state: dict = {"inner_active": False}
    # 拖拽注册表：id(Draggable) → 路径（本渲染周期有效，DragTarget 事件反查源路径）
    drag_registry: dict = {}
    # 高亮 setter 兜底 no-op（独立调用本函数时无需真实 state）
    _set_hl: Callable[[str | None], None] = (
        set_highlight_dir if set_highlight_dir is not None else (lambda v: None)
    )
    can_drag = on_file_drop is not None

    if not flat:
        # flat 为空可能是真无文件，也可能是异步扫描进行中（首帧）。
        # 有过滤词时提示无匹配，否则提示无文件（异步加载完成后 fs_version 变化会刷新）。
        body: ft.Control = _empty_hint(
            "无匹配文件" if file_filter.strip() else "该目录下无文件",
            c,
        )
    else:
        rows = []
        for kind, name, abspath, depth in flat:
            indent = depth * 14 + Spacing.XL
            if kind == "file":
                # 快捷方式：解析指向的 .md 目标（有 mtime/size 缓存，仅 .lnk 读盘）。
                # 目标有效时按 .md 对待：文档图标+角标、点击进编辑器、
                # active 高亮匹配目标路径（打开目标后快捷方式行亮起）
                lnk_target = (
                    shortcut.resolve_md_target(abspath)
                    if abspath and shortcut.is_shortcut(name)
                    else None
                )
                is_md = name.lower().endswith(_MD_EXTS) or lnk_target is not None
                is_active = (
                    is_md
                    and active_abs is not None
                    and abspath is not None
                    and (
                        os.path.abspath(abspath) == active_abs
                        # normcase：目标路径大小写可能与实际文件不一致（解析来源）
                        or (
                            lnk_target is not None
                            and os.path.normcase(lnk_target)
                            == os.path.normcase(active_abs)
                        )
                    )
                )
                # .lnk 一律 SHORTCUT 主图标（与 .md 的 DESCRIPTION 在形状上区分）；
                # 指向 .md 的快捷方式用 link 主题色提示「可在编辑器中打开」
                if shortcut.is_shortcut(name):
                    icon_name, icon_color = ft.Icons.SHORTCUT, (
                        c.link if lnk_target is not None else c.muted
                    )
                else:
                    icon_name, icon_color = _file_icon(name, c)
                # 快捷方式行 tooltip 显示目标路径（资源管理器「指向 …」直觉）
                name_tooltip = (
                    f"→ {lnk_target}" if lnk_target is not None else name
                )
                # 点击分流：.md / 指向 .md 的快捷方式 → 编辑器打开（open_file_by_path
                # 内部解析 .lnk）；其余快捷方式与非 md → 系统默认程序
                if is_md:
                    _file_click: Callable | None = lambda e, p=abspath: on_open_file(p)
                elif on_open_external is not None:
                    _file_click = lambda e, p=abspath: on_open_external(p)
                else:
                    _file_click = None

                def _file_row_icon() -> ft.Control:
                    """文件图标：.lnk 用 SHORTCUT，与 .md 文档图标一眼可分。"""
                    return ft.Icon(
                        icon_name,
                        size=13,
                        color=c.link if is_active else icon_color,
                    )

                def _build_file_row() -> ft.Container:
                    return _list_item(
                        ft.Row(
                            controls=[
                                ft.Container(width=14),  # chevron 占位，与文件夹行对齐
                                _file_row_icon(),
                                ft.Text(
                                    name,
                                    size=12,
                                    color=c.text,
                                    font_family=FONT_MAIN,
                                    weight=ft.FontWeight.W_600 if is_active else ft.FontWeight.NORMAL,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True,
                                    tooltip=name_tooltip,
                                ),
                            ],
                            spacing=Spacing.MD,
                        ),
                        c,
                        on_click=_file_click,
                        indent=indent,
                        active=is_active,
                    )

                inner = _wrap_context_menu(
                    _build_file_row(),
                    abspath or "",
                    is_dir=False,
                    on_action=on_file_context_action,
                    compare_source=compare_source,
                    key=f"tree-{abspath}" if not can_drag else None,
                    menu_state=menu_state,
                )
                if can_drag and abspath:
                    # 拖拽模式：行包 Draggable（原位半透明占位 + 指针跟随预览），
                    # 最外层再包「竞技场占位 GD」——右键行时以更深的识别器赢得
                    # 手势竞技场，阻止外层空白菜单 GD 误触发（见 _wrap_row_gesture）
                    rows.append(
                        _wrap_row_gesture(
                            _wrap_draggable(
                                inner,
                                ft.Container(opacity=0.35, content=_build_file_row()),
                                _drag_feedback(name, icon_name, icon_color, c),
                                drag_registry,
                                abspath,
                            ),
                            key=f"tree-{abspath}",
                        )
                    )
                else:
                    rows.append(inner)
            else:
                # 文件夹行：chevron（▸折叠/▾展开）+ 动态 folder icon + 整行点击 toggle
                is_expanded = bool(abspath and abspath in expanded_dirs)
                chevron_icon = ft.Icons.EXPAND_MORE if is_expanded else ft.Icons.CHEVRON_RIGHT
                folder_icon = ft.Icons.FOLDER_OPEN_OUTLINED if is_expanded else ft.Icons.FOLDER_OUTLINED
                if on_toggle_dir is not None:
                    _dir_click: Callable | None = lambda e, p=abspath: on_toggle_dir(p)
                else:
                    _dir_click = None

                def _build_dir_row() -> ft.Container:
                    return _list_item(
                        ft.Row(
                            controls=[
                                ft.Icon(chevron_icon, size=14, color=c.muted),
                                ft.Icon(folder_icon, size=13, color=c.muted),
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
                        on_click=_dir_click,
                        indent=indent,
                    )

                dir_row = _build_dir_row()
                # 拖拽悬停高亮（状态驱动）：构建期按 highlight_dir 计算背景，
                # 事件回调经 set_highlight_dir 触发重渲染刷新（控件冻结禁 update）
                if can_drag and abspath == highlight_dir:
                    dir_row.bgcolor = ft.Colors.with_opacity(0.18, c.link)
                inner = _wrap_context_menu(
                    dir_row,
                    abspath or "",
                    is_dir=True,
                    on_action=on_file_context_action,
                    key=f"tree-{abspath}" if not can_drag else None,
                    menu_state=menu_state,
                )
                if can_drag and abspath:
                    # 拖拽模式：行包 Draggable，再包 DragTarget（文件夹 = 放置目标），
                    # 最外层「竞技场占位 GD」同文件行（见 _wrap_row_gesture）
                    draggable = _wrap_draggable(
                        inner,
                        ft.Container(opacity=0.35, content=_build_dir_row()),
                        _drag_feedback(name, folder_icon, c.muted, c),
                        drag_registry,
                        abspath,
                    )
                    rows.append(
                        _wrap_row_gesture(
                            _wrap_drop_target(
                                draggable,
                                abspath,
                                drag_registry,
                                on_file_drop,
                                _set_hl,
                            ),
                            key=f"tree-{abspath}",
                        )
                    )
                else:
                    rows.append(inner)
        body = ft.ListView(
            controls=rows,
            spacing=0,
            expand=True,
            first_item_prototype=True,
            padding=ft.Padding.symmetric(vertical=Spacing.XS),
        )

    # 工作区模式：空白区域右键菜单（新建文件/文件夹、复制路径、打开文件位置）
    # 包裹顺序关键（实测 Flet/Flutter 行为）：DragTarget 会吞掉其子孙
    # GestureDetector 的右键 tap 识别器，故空白菜单 GD 必须在根 DragTarget
    # 外层（GD > DT > ListView）；行/空白分流靠手势竞技场（行占位 GD 更深
    # 先赢，见 _wrap_row_gesture），右键行弹行级菜单、右键空白弹空白菜单
    blank_holder: ft.Control | None = None
    if root_dir:
        # 根区域拖放目标在内层：拖到列表空白区（行间/底部）= 移动到工作区根目录
        if can_drag:
            body = _wrap_drop_target(
                body, root_dir, drag_registry, on_file_drop,
                _set_hl, key="root-drop",
            )
        # 空白菜单 GD 最外层（自带 expand=True 撑满 Column）
        body, blank_holder = _wrap_blank_context_menu(
            body, root_dir, on_file_context_action, c, menu_state
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
            # 空白菜单零尺寸载体（不占布局、不可命中），供手动 open() 显示
            *( [blank_holder] if blank_holder is not None else [] ),
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
    on_prev: Callable[[], None] | None = None,
    on_next: Callable[[], None] | None = None,
) -> ft.Control:
    """搜索选项工具栏：4 个切换按钮 + 右侧结果计数 + 上下翻导航。

    布局：[📁文件夹] [Aa大小写] [ab整词] [.*正则]  ----  [N 个结果] [↑] [↓]
    on_prev/on_next 非 None 时显示导航按钮（当前文档模式有匹配时）。
    """
    right_controls: list = [
        ft.Text(count_text, size=11, color=c.muted, font_family=FONT_MAIN),
    ]
    if on_prev is not None:
        right_controls.append(
            ft.IconButton(
                icon=ft.Icons.KEYBOARD_ARROW_UP,
                tooltip="上一个匹配 (Shift+Enter)",
                icon_size=14,
                on_click=lambda e: on_prev(),
                style=ft.ButtonStyle(color=c.muted, padding=Spacing.XS),
            )
        )
    if on_next is not None:
        right_controls.append(
            ft.IconButton(
                icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                tooltip="下一个匹配 (Enter)",
                icon_size=14,
                on_click=lambda e: on_next(),
                style=ft.ButtonStyle(color=c.muted, padding=Spacing.XS),
            )
        )
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
                *right_controls,
            ],
            spacing=Spacing.XS,
        ),
    )


def _render_replace_bar(
    replace_text: str,
    set_replace_text: Callable[[str], None],
    replace_expanded: bool,
    on_replace_current: Callable[[], None],
    on_replace_all: Callable[[], None],
    has_query: bool,
    c,
) -> ft.Control:
    """替换栏：折叠时 height=0；展开时替换输入 + 替换/全部替换按钮。

    VSCode 风格：替换栏在搜索框下方、选项工具栏上方，折叠时完全隐藏。
    has_query=False 时按钮禁用（无搜索词时无法替换）。
    """
    if not replace_expanded:
        return ft.Container(height=0)
    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.XL + 28,  # 对齐搜索框（减去 chevron 宽度）
            right=Spacing.LG,
            top=Spacing.XS,
            bottom=Spacing.XS,
        ),
        content=ft.Row(
            controls=[
                ft.TextField(
                    value=replace_text,
                    hint_text="替换为…",
                    dense=True,
                    border=ft.InputBorder.UNDERLINE,
                    text_size=12,
                    content_padding=ft.Padding.symmetric(
                        horizontal=Spacing.SM, vertical=Spacing.LG
                    ),
                    on_change=lambda e: set_replace_text(e.control.value or ""),
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.FIND_REPLACE,
                    tooltip="替换当前 (Alt+Enter)",
                    icon_size=14,
                    on_click=lambda e: on_replace_current(),
                    style=ft.ButtonStyle(
                        color=c.link if has_query else c.muted,
                        padding=Spacing.XS,
                    ),
                ),
                ft.IconButton(
                    icon=ft.Icons.AUTORENEW_OUTLINED,
                    tooltip="全部替换 (Ctrl+Alt+Enter)",
                    icon_size=14,
                    on_click=lambda e: on_replace_all(),
                    style=ft.ButtonStyle(
                        color=c.link if has_query else c.muted,
                        padding=Spacing.XS,
                    ),
                ),
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
    # 替换相关参数
    replace_text: str = "",
    set_replace_text: Callable[[str], None] | None = None,
    replace_expanded: bool = False,
    on_set_replace_expanded: Callable[[bool], None] | None = None,
    on_replace_current: Callable[[], None] | None = None,
    on_replace_all: Callable[[], None] | None = None,
    current_match_idx: int = 0,
    total_matches: int = 0,
    on_prev_match: Callable[[], None] | None = None,
    on_next_match: Callable[[], None] | None = None,
) -> ft.Control:
    """搜索面板：搜索框（+折叠按钮）+ 替换栏 + 选项工具栏 + 结果列表。

    - search_opts：4 选项当前值（folder/case/word/regex）
    - search_results：当前文档结果 [(li, [(s,e),...]), ...]
    - cross_results：跨文件分组 [(path, name, [(li, [(s,e),...]), ...]), ...]
    - regex_invalid：正则编译失败时显示错误提示
    - replace_expanded：替换栏展开状态（VSCode 风格 Ctrl+H 切换）
    - current_match_idx/total_matches：当前匹配索引/总数（"X / Y" 显示）
    """
    placeholder = "在文件夹中查找…" if search_opts["folder"] else "在当前文档中查找…"
    has_query = bool(search_query.strip())

    # 搜索框 + 左侧折叠 chevron（VSCode 风格）
    _chevron = ft.Icons.ARROW_DROP_DOWN if replace_expanded else ft.Icons.ARROW_RIGHT
    _on_toggle_replace = on_set_replace_expanded or (lambda v: None)
    search_row = ft.Row(
        controls=[
            ft.IconButton(
                icon=_chevron,
                tooltip="展开/收起替换栏 (Ctrl+H)",
                icon_size=16,
                on_click=lambda e: _on_toggle_replace(not replace_expanded),
                style=ft.ButtonStyle(color=c.muted, padding=Spacing.XS),
            ),
            _search_box(search_query, set_search_query, placeholder, c),
        ],
        spacing=Spacing.XS,
    )

    # 头部：搜索框 + 替换栏 + 选项工具栏
    header = [
        ft.Container(
            padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
            content=search_row,
        ),
        _render_replace_bar(
            replace_text,
            set_replace_text or (lambda v: None),
            replace_expanded,
            on_replace_current or (lambda: None),
            on_replace_all or (lambda: None),
            has_query,
            c,
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
            header[-1] = _render_search_toolbar(
                search_opts, on_toggle_opt,
                f"{total} 个结果 / {len(cross_results)} 文件", c,
            )
        return ft.Column(controls=[*header, body], spacing=0, expand=True)

    # 当前文档模式
    if not search_results:
        header[-1] = _render_search_toolbar(search_opts, on_toggle_opt, "无匹配结果", c)
        body = _empty_hint("无匹配结果", c)
        return ft.Column(controls=[*header, body], spacing=0, expand=True)

    # 计数 + 导航：有匹配时显示 "X / Y" 和上下翻按钮
    _count_text = f"{current_match_idx + 1} / {total_matches}" if total_matches > 0 else ""
    header[-1] = _render_search_toolbar(
        search_opts, on_toggle_opt, _count_text, c,
        on_prev=on_prev_match, on_next=on_next_match,
    )

    items = [
        _render_search_result_item(
            li, None, matches, document, None,
            on_jump_to_line, on_open_file_and_jump, c,
        )
        for li, matches in search_results
    ]
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
    # 替换功能：当前文档内存替换 + 跨文件写盘 + 快捷键桥接
    on_replace_match_in_doc: Callable[[int, int, int, str], None] | None = None,
    on_replace_all_in_doc: Callable[[list], int] | None = None,
    on_bump_fs_version: Callable[[], None] | None = None,
    replace_actions_ref: ft.Ref | None = None,
    # VSCode 风格文件树：非 md 文件用系统默认程序打开（资源管理器双击直觉）
    on_open_external: Callable[[str], None] | None = None,
    # VSCode 风格文件树拖拽：文件/文件夹移动到目标文件夹 (src, dst_dir)
    on_file_drop: Callable[[str, str], None] | None = None,
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
    # 拖拽悬停高亮：当前悬停的放置目标文件夹路径（None = 无高亮）。
    # Flet 0.86 冻结控件禁止命令式 update，高亮必须走 set_state 重渲染
    drop_hover_dir, set_drop_hover_dir = ft.use_state(None)
    # 替换状态：替换文本、当前匹配索引、替换后跳转索引
    replace_text, set_replace_text = ft.use_state("")
    current_match_idx, set_current_match_idx = ft.use_state(0)
    pending_jump_idx, set_pending_jump_idx = ft.use_state(-1)  # -1 = 无待跳转

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
            tree = await asyncio.to_thread(_scan_files, root_dir)
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
            # session 销毁防御：异步搜索完成时 session 可能已销毁（标签关闭/退出）
            try:
                set_cross_results(tuple(groups))
                set_cross_loading(False)
            except RuntimeError:
                pass

        page.run_task(_do)

    ft.use_effect(_search_cross_files, [pattern, _search_folder, root_dir, fs_version])

    # ---- 替换栏展开状态（从 settings 读取，持久化）----
    _replace_expanded = settings.get("search_replace_expanded", False)
    # 跨文件替换防竞态 token
    replace_token_ref = ft.use_ref(0)

    def _on_set_replace_expanded(v: bool):
        if on_update_setting is not None:
            on_update_setting("search_replace_expanded", v)

    # ---- 当前匹配扁平化 + 索引修正 ----
    flat_matches = _flatten_matches(search_results)
    total_matches = len(flat_matches)

    # search_results 变化时修正 current_match_idx（越界则回退）
    def _clamp_match_idx():
        if total_matches == 0:
            if current_match_idx != 0:
                set_current_match_idx(0)
        elif current_match_idx >= total_matches:
            set_current_match_idx(total_matches - 1)

    ft.use_effect(_clamp_match_idx, [total_matches])

    # ---- 替换后跳转：search_results 变化 + pending_jump_idx >= 0 时跳到下一个匹配 ----
    # 替换使 line.raw 变 → _lines_sig 变 → search_results use_memo 重算（少一条匹配）
    # → effect 取 flat_matches[idx]（自然成为下一个匹配）→ on_jump_to_line。
    def _do_jump_after_replace():
        if pending_jump_idx < 0:
            return
        # 清除 pending（无论是否找到匹配，避免重复跳转）
        set_pending_jump_idx(-1)
        if not flat_matches:
            return
        idx = min(pending_jump_idx, len(flat_matches) - 1)
        if idx < 0:
            idx = 0
        set_current_match_idx(idx)
        li, s, _e = flat_matches[idx]
        on_jump_to_line(li, s)

    ft.use_effect(_do_jump_after_replace, [search_results, pending_jump_idx])

    # ---- 替换回调 ----
    def _expand_for_match(li: int, s: int, e: int) -> str:
        """对当前文档的单个匹配展开反向引用。"""
        if document is None or not (0 <= li < len(document.lines)):
            return replace_text
        raw = document.lines[li].raw or ""
        if _regex and pattern is not None:
            m = _find_match_at(pattern, raw, s, e)
            if m is not None:
                return _expand_replacement(m, replace_text, True)
        return replace_text

    def _on_replace_current():
        if not search_query.strip() or pattern is None:
            return
        if _search_folder:
            _do_replace_current_cross()
            return
        # 当前文档模式
        if not flat_matches:
            return
        idx = min(current_match_idx, len(flat_matches) - 1)
        if idx < 0:
            idx = 0
        li, s, e = flat_matches[idx]
        new_text = _expand_for_match(li, s, e)
        if on_replace_match_in_doc is not None:
            on_replace_match_in_doc(li, s, e, new_text)
        # 替换后跳到下一个匹配：document 变 → search_results 重算 → effect 跳转
        set_pending_jump_idx(idx)

    def _on_replace_all():
        if not search_query.strip() or pattern is None:
            return
        if _search_folder:
            _do_replace_all_cross()
            return
        # 当前文档模式：构建 replacements 列表（含已展开 new_text）
        if not search_results:
            return
        replacements: list[tuple[int, list[tuple[int, int, str]]]] = []
        for li, matches in search_results:
            if document is None or not (0 <= li < len(document.lines)):
                continue
            raw = document.lines[li].raw or ""
            spans_with_text: list[tuple[int, int, str]] = []
            for s, e in matches:
                if _regex and pattern is not None:
                    m = _find_match_at(pattern, raw, s, e)
                    nt = _expand_replacement(m, replace_text, True) if m else replace_text
                else:
                    nt = replace_text
                spans_with_text.append((s, e, nt))
            if spans_with_text:
                replacements.append((li, spans_with_text))
        if replacements and on_replace_all_in_doc is not None:
            on_replace_all_in_doc(replacements)
            set_current_match_idx(0)

    def _on_prev_match():
        if not flat_matches:
            return
        idx = (current_match_idx - 1) % len(flat_matches)
        set_current_match_idx(idx)
        li, s, _e = flat_matches[idx]
        on_jump_to_line(li, s)

    def _on_next_match():
        if not flat_matches:
            return
        idx = (current_match_idx + 1) % len(flat_matches)
        set_current_match_idx(idx)
        li, s, _e = flat_matches[idx]
        on_jump_to_line(li, s)

    # ---- 跨文件替换 ----
    def _do_replace_current_cross():
        """跨文件替换当前匹配：当前文件走内存，其他文件读→改→写→打开跳转。"""
        flat_cross = _flatten_cross_matches(cross_results)
        if not flat_cross:
            return
        idx = min(current_match_idx, len(flat_cross) - 1)
        if idx < 0:
            idx = 0
        path, li, s, e = flat_cross[idx]
        if path == file_path:
            # 当前文档走内存替换
            new_text = _expand_for_match(li, s, e)
            if on_replace_match_in_doc is not None:
                on_replace_match_in_doc(li, s, e, new_text)
            set_pending_jump_idx(idx)
        else:
            # 其他文件：读→改→写→打开跳转
            page = page_ref.current
            if page is None:
                return
            replace_token_ref.current += 1
            my_token = replace_token_ref.current

            async def _do():
                try:
                    if os.path.getsize(path) > _MAX_FILE_SIZE:
                        return
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (OSError, UnicodeDecodeError):
                    return
                if replace_token_ref.current != my_token:
                    return
                lines = text.split("\n")
                if li >= len(lines):
                    return
                raw = lines[li]
                m = None
                if pattern is not None:
                    m = _find_match_at(pattern, raw, s, e)
                if _regex and m is not None:
                    new_text = _expand_replacement(m, replace_text, True)
                else:
                    new_text = replace_text
                new_raw = raw[:s] + new_text + raw[e:]
                lines[li] = new_raw
                if replace_token_ref.current != my_token:
                    return
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                except OSError:
                    return
                # 写盘后打开文件并跳转到替换位置之后
                new_off = s + len(new_text)
                if on_open_file_and_jump is not None:
                    on_open_file_and_jump(path, li, new_off)
                if on_bump_fs_version is not None:
                    on_bump_fs_version()

            page.run_task(_do)

    def _do_replace_all_cross():
        """跨文件全部替换：逐文件读→改→写，当前文件走内存。"""
        if not cross_results:
            return
        page = page_ref.current
        if page is None:
            return
        replace_token_ref.current += 1
        my_token = replace_token_ref.current

        # 当前文档的 replacements（走内存）
        current_replacements: list[tuple[int, list[tuple[int, int, str]]]] = []
        other_files: list[tuple[str, list[tuple[int, list[tuple[int, int]]]]]] = []

        for path, _name, hits in cross_results:
            if path == file_path:
                for li, matches in hits:
                    if document is None or not (0 <= li < len(document.lines)):
                        continue
                    raw = document.lines[li].raw or ""
                    spans_with_text: list[tuple[int, int, str]] = []
                    for s, e in matches:
                        if _regex and pattern is not None:
                            m = _find_match_at(pattern, raw, s, e)
                            nt = _expand_replacement(m, replace_text, True) if m else replace_text
                        else:
                            nt = replace_text
                        spans_with_text.append((s, e, nt))
                    if spans_with_text:
                        current_replacements.append((li, spans_with_text))
            else:
                other_files.append((path, hits))

        async def _do():
            # 当前文档走内存替换
            if current_replacements and on_replace_all_in_doc is not None:
                on_replace_all_in_doc(current_replacements)
            # 其他文件逐个读→改→写
            for fpath, hits in other_files:
                if replace_token_ref.current != my_token:
                    return
                try:
                    if os.path.getsize(fpath) > _MAX_FILE_SIZE:
                        continue
                    with open(fpath, encoding="utf-8") as f:
                        text = f.read()
                except (OSError, UnicodeDecodeError):
                    continue
                if replace_token_ref.current != my_token:
                    return
                new_text, count = _replace_in_file_text(text, pattern, replace_text, _regex)
                if count == 0:
                    continue
                try:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(new_text)
                except OSError:
                    continue
            # 刷新文件系统版本（触发跨文件搜索重扫）
            if on_bump_fs_version is not None:
                try:
                    on_bump_fs_version()
                except RuntimeError:
                    pass
            # session 销毁防御：异步替换完成时 session 可能已销毁
            try:
                set_current_match_idx(0)
            except RuntimeError:
                pass

        page.run_task(_do)

    # ---- 注册替换回调到 replace_actions_ref（供 KeyDispatcher 桥接）----
    if replace_actions_ref is not None:
        replace_actions_ref.current = {
            "replace_current": _on_replace_current,
            "replace_all": _on_replace_all,
        }

    # ---- 文件树：异步扫描 + scan_token 防竞态 ----
    # use_effect 依赖 [root_dir, fs_version]：根目录切换 / 文件增删改后重扫 /
    # 外部变化轮询上报后重扫。asyncio.to_thread 把同步磁盘扫描移出 UI 线程；
    # scan_token 丢弃过期结果。
    file_tree, set_file_tree = ft.use_state(())
    scan_token_ref = ft.use_ref(0)
    # 外部变化轮询 watcher 状态：基准签名（str 可弱引用，list/tuple 不行）+ 任务句柄。
    # 声明在 _scan_fs 前：重扫成功后同步基准，应用内变更不会被轮询重复上报。
    watch_base = ft.use_ref(None)
    watch_task_ref = ft.use_ref(None)

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
            tree = await asyncio.to_thread(_scan_files, root_dir)
            # 过期任务（已切目录 / 又有新变更），丢弃避免覆盖最新树
            if scan_token_ref.current != my_token:
                return
            # session 销毁防御：标签关闭 / 应用退出后，异步扫描完成时 session 可能
            # 已销毁，set_file_tree → schedule_update 抛 RuntimeError。静默丢弃
            # 卸载后的状态更新（与 status_bar._update_counts 同模式）。
            try:
                set_file_tree(tuple(tree))
                # watcher 基准同步：应用内操作引发的这次重扫即最新事实，
                # 轮询以此为基准，不再对同一变更重复 bump。
                watch_base.current = _tree_signature(tree)
            except RuntimeError:
                pass

        page.run_task(_do_scan)

    ft.use_effect(_scan_fs, [root_dir, fs_version])

    # ---- 外部文件系统变化监测（轮询 watcher，零依赖）----
    # 后台每 _WATCH_INTERVAL 秒重扫 root_dir，外部程序创建/删除/重命名文件 →
    # 树签名变化 → on_bump_fs_version 走既有 fs_version 重扫链路（文件树/
    # 跨文件搜索同步刷新）。root_dir 变化时 cleanup 取消旧任务并重置基准，
    # effect 重启监测新目录；组件卸载时同样取消，无任务残留。
    def _watch_fs():
        if on_bump_fs_version is None:
            return  # 无变更上报通道（如独立预览场景），不启动监测
        if not root_dir or not os.path.isdir(root_dir):
            return
        page = page_ref.current
        if page is None:
            return
        my_root = root_dir  # 锚定本次监测目录（root 切换后旧任务由 cleanup 取消）
        bump = on_bump_fs_version

        async def _run():
            await poll_fs_changes(my_root, _WATCH_INTERVAL, watch_base, bump)

        watch_task_ref.current = page.run_task(_run)

    def _stop_watch_fs():
        t = watch_task_ref.current
        if t is not None:
            t.cancel()
            watch_task_ref.current = None
        watch_base.current = None  # 目录切换/卸载：基准失效，重启时重新初始化

    ft.use_effect(_watch_fs, [root_dir], cleanup=_stop_watch_fs)

    # ---- 文件夹展开/折叠状态（VSCode 风格动态扁平化）----
    # frozenset 不可变，== 比较内容触发更新；存目录绝对路径（与 _flatten_tree 的
    # dir_path 拼接方式一致）。默认空集合 = 根级直接子项可见、子目录折叠。
    expanded_dirs, set_expanded_dirs = ft.use_state(frozenset())
    # ref 持最新值供 toggle / reveal 闭包读取（避免 stale 闭包丢失用户已展开的目录）
    expanded_dirs_ref = ft.use_ref(frozenset())
    expanded_dirs_ref.current = expanded_dirs

    def _toggle_dir(dir_path: str):
        """切换目录展开/折叠（整行点击 chevron 或文件夹区域均触发）。"""
        current = set(expanded_dirs_ref.current)
        if dir_path in current:
            current.discard(dir_path)
        else:
            current.add(dir_path)
        set_expanded_dirs(frozenset(current))

    # root_dir 切换时重置展开状态（切换工作区/文件夹后旧的展开路径无意义）
    ft.use_effect(lambda: set_expanded_dirs(frozenset()), [root_dir])

    # reveal：当前文件变化时自动展开其祖先目录链（VSCode "Reveal in Explorer" 行为）
    # 让用户切标签打开深层文件时文件树自动定位到该文件所在目录。
    def _reveal_current_file():
        if not file_path or not root_dir:
            return
        try:
            rel = os.path.relpath(file_path, root_dir)
        except ValueError:
            return  # Windows 跨盘符 relpath 抛 ValueError
        if rel.startswith("..") or os.path.isabs(rel):
            return  # 文件不在 root_dir 下（跨目录打开）
        parts = rel.split(os.sep)
        if len(parts) <= 1:
            return  # 文件在根目录直接子项，无需展开祖先
        # 构造祖先目录路径，与 _flatten_tree 的 dir_path = os.path.join(root, name) 对齐
        ancestors: set[str] = set()
        cur = root_dir
        for part in parts[:-1]:  # 不含文件名本身
            cur = os.path.join(cur, part)
            ancestors.add(cur)
        if not ancestors:
            return
        current = set(expanded_dirs_ref.current)
        if ancestors <= current:
            return  # 祖先已全部展开，无需更新
        set_expanded_dirs(frozenset(current | ancestors))

    ft.use_effect(_reveal_current_file, [file_path, root_dir])

    # ---- 文件树过滤 + 扁平化：use_memo 缓存（仅文件树 / 过滤词 / 展开状态变化才重算）----
    filtered = ft.use_memo(
        lambda: _filter_tree(list(file_tree), file_filter),
        [file_tree, file_filter],
    )
    _has_filter = bool(file_filter.strip())
    flat = ft.use_memo(
        lambda: (
            _flatten_tree(
                filtered, root_dir=root_dir,
                expanded=expanded_dirs, force_expand=_has_filter,
            ) if root_dir else []
        ),
        [filtered, root_dir, expanded_dirs, _has_filter],
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
            expanded_dirs=expanded_dirs,
            on_toggle_dir=_toggle_dir,
            on_open_external=on_open_external,
            on_file_drop=on_file_drop,
            highlight_dir=drop_hover_dir,
            set_highlight_dir=set_drop_hover_dir,
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
            # 替换相关参数
            replace_text=replace_text,
            set_replace_text=set_replace_text,
            replace_expanded=_replace_expanded,
            on_set_replace_expanded=_on_set_replace_expanded,
            on_replace_current=_on_replace_current,
            on_replace_all=_on_replace_all,
            current_match_idx=current_match_idx,
            total_matches=total_matches if not _search_folder else 0,
            on_prev_match=_on_prev_match if not _search_folder else None,
            on_next_match=_on_next_match if not _search_folder else None,
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
