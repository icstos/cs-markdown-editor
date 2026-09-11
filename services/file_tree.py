"""文件树领域服务：扫描 / 签名 / 过滤 / 扁平化 / 路径收集（纯逻辑，无 UI）。

从 views/sidebar.py 迁出：侧边栏文件面板原先同时承载 UI 与文件系统逻辑，
使搜索浮层与测试需要反向依赖视图模块的私有函数。图标映射与文件类型判断
属于「文件树如何被描述」，一并置于此。

对外接口：scan_files / tree_signature / poll_fs_changes / filter_tree /
flatten_tree / collect_md_paths / file_icon / file_row_icon_data / FILE_ICON_MAP。
"""

import asyncio
import os
from collections.abc import Callable

import flet as ft

from services import shortcut

MD_EXTS = (".md", ".markdown")
MAX_DEPTH = 8  # 扫描最大深度（VSCode 风格全类型扫描，8 层覆盖典型项目结构）
MAX_FILES = 5000  # 单次扫描文件数上限保护（防止 node_modules 拖慢 UI）
WATCH_INTERVAL = 2.0  # 外部文件系统变化轮询间隔（秒）


FILE_ICON_MAP: dict[str, str] = {
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
    # 指向 .md 的快捷方式在行渲染处改用 link 主题色（见 file_row_icon_data）
    ".lnk": ft.Icons.SHORTCUT,
}


def file_icon(name: str, c) -> tuple[str, str]:
    """按文件扩展名返回 (图标名, 颜色)。

    .md/.markdown 用 c.link 主题色突出可编辑文件；其余统一 c.muted 避免色彩过载。
    未知扩展名兜底 INSERT_DRIVE_FILE_OUTLINED。扩展名匹配大小写不敏感。
    .lnk 快捷方式用 SHORTCUT 图标，与 .md 的 DESCRIPTION 文档图标在形状上区分。
    """
    lower = name.lower()
    _, ext = os.path.splitext(lower)
    icon = FILE_ICON_MAP.get(ext, ft.Icons.INSERT_DRIVE_FILE_OUTLINED)
    color = c.link if ext in (".md", ".markdown") else c.muted
    return icon, color


def file_row_icon_data(
    name: str, abspath: str | None, c
) -> tuple[str, str, str | None]:
    """按文件扩展名返回 (图标名, 颜色, tooltip)。

    与 file_icon 的区别：.lnk 一律用 SHORTCUT 主图标（不随目标类型变化），
    确保链接与 .md 文档在图标形状上一眼可分；指向 .md 的快捷方式用 c.link
    主题色提示「可在编辑器中打开」，tooltip 显示目标路径（资源管理器直觉）。
    其余文件直接委托 file_icon，tooltip 为 None。
    """
    if abspath and shortcut.is_shortcut(name):
        lnk_target = shortcut.resolve_md_target(abspath)
        icon = ft.Icons.SHORTCUT
        color = c.link if lnk_target is not None else c.muted
        tooltip = f"→ {lnk_target}" if lnk_target is not None else None
        return icon, color, tooltip
    icon, color = file_icon(name, c)
    return icon, color, None


# ---- 数据派生 ----


def scan_files(
    root: str,
    max_depth: int = MAX_DEPTH,
    max_files: int = MAX_FILES,
) -> list:
    """递归扫描 root 下的全类型文件，返回嵌套结构（VSCode 风格资源管理器）。

    元素格式：
      ("dir", name, children_list)
      ("file", name, abs_path)
    目录在前、字母序排序；跳过隐藏目录与常见忽略目录。失败时返回 []。

    与旧 _scan_markdown_files 的差异：
    - 收录所有类型文件（不再按 MD_EXTS 过滤），让用户看到图片/代码等资源
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


def tree_signature(tree: list) -> str:
    """文件树内容签名：递归拼接「目录相对路径 + 文件绝对路径」。

    目录节点仅存 name（scan_files 格式），walk 时按「父/子」累积相对路径；
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
    每轮在后台线程重扫（与 scan_files 同源同限：深度/数量/忽略目录一致），
    签名 != base_holder.current 时更新基准并上报（天然按 interval 节流）。

    base_holder.current 为 None 时先做一次基准扫描；调用方在应用内文件操作
    触发重扫后把最新签名写入 base_holder，避免轮询对同一变更重复上报。
    should_stop() 返回 True 时退出（任务 cancel 的兜底，防目录切换后旧轮询残留）。
    """
    if base_holder.current is None:
        base_holder.current = tree_signature(
            await asyncio.to_thread(scan_files, root_dir)
        )
    while True:
        await asyncio.sleep(interval)
        if should_stop is not None and should_stop():
            return
        sig = tree_signature(await asyncio.to_thread(scan_files, root_dir))
        if sig != base_holder.current:
            base_holder.current = sig
            on_change()


def filter_tree(tree: list, query: str) -> list:
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


def flatten_tree(
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
                out.extend(flatten_tree(
                    node[2], depth + 1, dir_path, expanded, force_expand,
                ))
    return out


def collect_md_paths(tree: list) -> list[str]:
    """从嵌套文件树扁平化提取所有 .md/.markdown 文件绝对路径（深度优先，字母序）。

    复用 scan_files 产出的全类型树结构，仅供跨文件搜索使用。
    扫描改为全类型后此处必须按 MD_EXTS 过滤，否则跨文件搜索会尝试读取
    图片/二进制等非文本文件（search_in_file 的 UnicodeDecodeError 兜底
    会静默跳过，但浪费 IO 且语义不符）。
    快捷方式：指向 .md 的 .lnk 以目标路径参与搜索（搜索目标内容，点击
    打开目标文档）；目标与树内 .md 重复时去重保序。
    """
    paths: list[str] = []

    def _walk(node):
        if node[0] == "file":
            name_l = node[1].lower()
            if name_l.endswith(MD_EXTS):
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
