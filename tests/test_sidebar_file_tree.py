"""VSCode 风格资源管理器文件树测试。

覆盖：
- views.sidebar._scan_files：全类型收录 / 跳过隐藏与忽略目录 / 保留空目录 /
  深度上限 / 文件数上限 / 目录在前字母序 / 无效根返回空 / OSError 静默
- views.sidebar._flatten_tree：expanded 集合控制递归 / force_expand 全展开 /
  expanded=None 兼容旧语义 / depth 正确 / dir_path 拼接
- views.sidebar._collect_md_paths：混入非 md 节点只返回 md / 深度优先字母序 / 空树
- views.sidebar._file_icon：.md 返回 DESCRIPTION+link 色 / 已知扩展名映射 /
  未知兜底 / 大小写不敏感 / 非 md 用 muted 色
- views.sidebar._file_row_icon_data：.md 委托 _file_icon / .lnk 一律 SHORTCUT /
  指向 .md 的快捷方式用 link 色 + 目标 tooltip / 其余 .lnk 用 muted 色
- services.file_ops.open_external：mock platform.system 验证 Windows/macOS/Linux
  分流 / 文件不存在抛错

不依赖 UI 渲染，纯函数 + tmp_path 文件系统直接调用验证。
"""

import os
import struct
import sys
import types
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft

from services import shortcut
from services.file_ops import open_external
from views.sidebar import (
    _collect_md_paths,
    _drop_allowed,
    _file_icon,
    _file_row_icon_data,
    _flatten_tree,
    _scan_files,
    _wrap_drop_target,
)

# ---- 辅助 ----


def _make_colors():
    """简易 Colors mock（覆盖 _file_icon 用到的 link / muted 字段）。"""
    return types.SimpleNamespace(
        text="#1F2329",
        muted="#8A919E",
        link="#3370FF",
    )


def _tree_names(tree):
    """提取树中所有顶层节点名（用于断言收录情况）。"""
    return [n for _, n, _ in tree]


def _flat_names(flat):
    """提取扁平化列表中所有节点名。"""
    return [n for _, n, _, _ in flat]


# ---- _scan_files：全类型收录 ----


def test_scan_files_collects_all_types(tmp_path):
    """全类型收录：.md / .png / .py / .json 等均出现在树中。"""
    (tmp_path / "note.md").write_text("")
    (tmp_path / "pic.png").write_bytes(b"")
    (tmp_path / "script.py").write_text("")
    (tmp_path / "config.json").write_text("")
    tree = _scan_files(str(tmp_path))
    names = _tree_names(tree)
    assert "note.md" in names
    assert "pic.png" in names
    assert "script.py" in names
    assert "config.json" in names


# ---- _scan_files：跳过隐藏与忽略目录 ----


def test_scan_files_skips_hidden_and_ignored(tmp_path):
    """跳过 . 开头目录与 .git / node_modules / __pycache__。"""
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.md").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("")
    (tmp_path / "visible.md").write_text("")
    tree = _scan_files(str(tmp_path))
    names = _tree_names(tree)
    assert "visible.md" in names
    assert ".hidden" not in names
    assert "node_modules" not in names
    assert ".git" not in names


# ---- _scan_files：保留空目录 ----


def test_scan_files_keeps_empty_dirs(tmp_path):
    """保留空目录（VSCode 显示空目录，移除旧 if children 过滤）。"""
    (tmp_path / "empty").mkdir()
    tree = _scan_files(str(tmp_path))
    dirs = [n for t, n, _ in tree if t == "dir"]
    assert "empty" in dirs


# ---- _scan_files：目录在前、字母序 ----


def test_scan_files_dirs_first_alpha_order(tmp_path):
    """目录在前，同类型字母序排序。"""
    (tmp_path / "zfile.md").write_text("")
    (tmp_path / "adir").mkdir()
    (tmp_path / "bdir").mkdir()
    (tmp_path / "afile.md").write_text("")
    tree = _scan_files(str(tmp_path))
    types_names = [(t, n) for t, n, _ in tree]
    # 目录在前
    assert types_names[0] == ("dir", "adir")
    assert types_names[1] == ("dir", "bdir")
    # 文件在后，字母序
    assert types_names[2] == ("file", "afile.md")
    assert types_names[3] == ("file", "zfile.md")


# ---- _scan_files：深度上限 ----


def test_scan_files_depth_limit(tmp_path):
    """超过 max_depth 的层级不扫描（depth > max_depth 时返回空）。"""
    deep = tmp_path
    for i in range(5):
        deep = deep / f"d{i}"
        deep.mkdir()
    (deep / "deep.md").write_text("")
    tree = _scan_files(str(tmp_path), max_depth=2)
    # depth 0: root → d0, depth 1: d0 → d1, depth 2: d1 → d2, depth 3: d2 截断
    assert tree[0][0] == "dir"
    assert tree[0][1] == "d0"
    assert tree[0][2][0][1] == "d1"
    # d2 在 depth 2 仍被扫描（depth ≤ max_depth），但其子项在 depth 3 被截断
    d1_children = tree[0][2][0][2]
    assert len(d1_children) == 1
    assert d1_children[0][1] == "d2"
    assert d1_children[0][2] == []  # d2 子项被深度限制截断


# ---- _scan_files：文件数上限 ----


def test_scan_files_max_files_limit(tmp_path):
    """文件数上限：超限停止追加。"""
    for i in range(10):
        (tmp_path / f"f{i}.md").write_text("")
    tree = _scan_files(str(tmp_path), max_files=3)
    files = [n for t, n, _ in tree if t == "file"]
    assert len(files) == 3


# ---- _scan_files：无效根 ----


def test_scan_files_invalid_root_returns_empty():
    """无效根目录（空串 / 不存在）返回 []。"""
    assert _scan_files("") == []
    assert _scan_files("/nonexistent/path/xyz/abc") == []


# ---- _scan_files：OSError 静默 ----


def test_scan_files_oserror_silent(tmp_path):
    """os.scandir 抛 OSError 时静默返回空（无读权限目录不崩溃）。"""
    (tmp_path / "ok.md").write_text("")
    with patch("views.sidebar.os.scandir", side_effect=OSError("denied")):
        tree = _scan_files(str(tmp_path))
    assert tree == []


# ---- _flatten_tree：expanded 控制递归 ----


def test_flatten_tree_collapsed_excludes_children():
    """未展开的目录不递归子层（仅输出目录节点本身）。"""
    tree = [
        ("dir", "sub", [
            ("file", "a.md", "/root/sub/a.md"),
        ]),
        ("file", "b.md", "/root/b.md"),
    ]
    flat = _flatten_tree(tree, root_dir="/root", expanded=frozenset())
    assert ("dir", "sub", os.path.join("/root", "sub"), 0) in flat
    assert ("file", "a.md", "/root/sub/a.md", 1) not in flat
    assert ("file", "b.md", "/root/b.md", 0) in flat


def test_flatten_tree_expanded_includes_children():
    """expanded 集合包含目录路径时递归子层。"""
    tree = [
        ("dir", "sub", [
            ("file", "a.md", "/root/sub/a.md"),
        ]),
    ]
    flat = _flatten_tree(
        tree, root_dir="/root", expanded=frozenset({os.path.join("/root", "sub")}),
    )
    assert ("dir", "sub", os.path.join("/root", "sub"), 0) in flat
    assert ("file", "a.md", "/root/sub/a.md", 1) in flat


# ---- _flatten_tree：force_expand ----


def test_flatten_tree_force_expand():
    """force_expand=True 全展开（过滤模式忽略折叠状态）。"""
    tree = [
        ("dir", "sub", [
            ("file", "a.md", "/root/sub/a.md"),
        ]),
    ]
    flat = _flatten_tree(
        tree, root_dir="/root", expanded=frozenset(), force_expand=True,
    )
    assert ("file", "a.md", "/root/sub/a.md", 1) in flat


# ---- _flatten_tree：expanded=None 兼容 ----


def test_flatten_tree_expanded_none_compat():
    """expanded=None 全展开（向后兼容旧语义）。"""
    tree = [
        ("dir", "sub", [
            ("file", "a.md", "/root/sub/a.md"),
        ]),
    ]
    flat = _flatten_tree(tree, root_dir="/root", expanded=None)
    assert ("file", "a.md", "/root/sub/a.md", 1) in flat


# ---- _flatten_tree：depth 正确 ----


def test_flatten_tree_depth_correct():
    """嵌套层级 depth 递增。"""
    tree = [
        ("dir", "d1", [
            ("dir", "d2", [
                ("file", "f.md", "/root/d1/d2/f.md"),
            ]),
        ]),
    ]
    flat = _flatten_tree(tree, root_dir="/root", expanded=None)
    depths = {name: depth for _, name, _, depth in flat}
    assert depths["d1"] == 0
    assert depths["d2"] == 1
    assert depths["f.md"] == 2


# ---- _flatten_tree：dir_path 拼接 ----


def test_flatten_tree_dir_path_join():
    """dir_path 由 root_dir + 目录名逐层拼接。"""
    tree = [
        ("dir", "sub", [
            ("dir", "deep", [
                ("file", "f.md", "/root/sub/deep/f.md"),
            ]),
        ]),
    ]
    flat = _flatten_tree(tree, root_dir="/root", expanded=None)
    paths = {name: path for kind, name, path, _ in flat if kind == "dir"}
    assert paths["sub"] == os.path.join("/root", "sub")
    assert paths["deep"] == os.path.join("/root", "sub", "deep")


# ---- _collect_md_paths：混入非 md 过滤 ----


def test_collect_md_paths_filters_non_md():
    """混入非 md 节点只返回 md 路径。"""
    tree = [
        ("dir", "docs", [
            ("file", "a.md", "/abs/docs/a.md"),
            ("file", "b.png", "/abs/docs/b.png"),
            ("file", "c.py", "/abs/docs/c.py"),
        ]),
        ("file", "d.md", "/abs/d.md"),
        ("file", "e.json", "/abs/e.json"),
    ]
    paths = _collect_md_paths(tree)
    assert paths == ["/abs/docs/a.md", "/abs/d.md"]


def test_collect_md_paths_markdown_extension():
    """.markdown 扩展名也被收录。"""
    tree = [
        ("file", "a.markdown", "/abs/a.markdown"),
        ("file", "b.txt", "/abs/b.txt"),
    ]
    paths = _collect_md_paths(tree)
    assert paths == ["/abs/a.markdown"]


# ---- _file_icon ----


def test_file_icon_md_returns_description_and_link():
    """.md 返回 DESCRIPTION 图标 + link 主题色。"""
    c = _make_colors()
    icon, color = _file_icon("note.md", c)
    assert icon == ft.Icons.DESCRIPTION
    assert color == c.link


def test_file_icon_markdown_extension():
    """.markdown 同样返回 DESCRIPTION + link 色。"""
    c = _make_colors()
    icon, color = _file_icon("note.markdown", c)
    assert icon == ft.Icons.DESCRIPTION
    assert color == c.link


def test_file_icon_known_extensions():
    """已知扩展名映射到对应图标。"""
    c = _make_colors()
    assert _file_icon("pic.png", c)[0] == ft.Icons.IMAGE
    assert _file_icon("script.py", c)[0] == ft.Icons.CODE
    assert _file_icon("page.html", c)[0] == ft.Icons.HTML
    assert _file_icon("style.css", c)[0] == ft.Icons.CSS
    assert _file_icon("archive.zip", c)[0] == ft.Icons.FOLDER_ZIP
    assert _file_icon("doc.pdf", c)[0] == ft.Icons.PICTURE_AS_PDF
    assert _file_icon("song.mp3", c)[0] == ft.Icons.MUSIC_NOTE
    assert _file_icon("video.mp4", c)[0] == ft.Icons.MOVIE


def test_file_icon_unknown_fallback():
    """未知扩展名兜底 INSERT_DRIVE_FILE_OUTLINED。"""
    c = _make_colors()
    icon, _ = _file_icon("data.xyzunknown", c)
    assert icon == ft.Icons.INSERT_DRIVE_FILE_OUTLINED


def test_file_icon_no_extension_fallback():
    """无扩展名兜底。"""
    c = _make_colors()
    icon, _ = _file_icon("Makefile", c)
    assert icon == ft.Icons.INSERT_DRIVE_FILE_OUTLINED


def test_file_icon_case_insensitive():
    """扩展名大小写不敏感。"""
    c = _make_colors()
    assert _file_icon("PIC.PNG", c)[0] == ft.Icons.IMAGE
    assert _file_icon("Note.MD", c)[0] == ft.Icons.DESCRIPTION
    assert _file_icon("Script.PY", c)[0] == ft.Icons.CODE


def test_file_icon_non_md_uses_muted_color():
    """非 md 文件用 c.muted 色（避免色彩过载）。"""
    c = _make_colors()
    _, color = _file_icon("pic.png", c)
    assert color == c.muted
    _, color = _file_icon("script.py", c)
    assert color == c.muted


# ---- _file_row_icon_data ----

_LNK_CLSID = bytes.fromhex("0114020000000000C000000000000046")


def _lnk_header(flags: int) -> bytes:
    """最小 MS-SHLLINK Header（与 test_shortcut.py 同构）。"""
    h = struct.pack("<I", 0x4C) + _LNK_CLSID + struct.pack("<I", flags)
    h += struct.pack("<I", 0x80)  # FileAttributes: NORMAL
    h += struct.pack("<Q", 0) * 3  # 时间戳
    h += struct.pack("<I", 0)  # FileSize
    h += struct.pack("<i", 0)  # IconIndex
    h += struct.pack("<I", 1)  # ShowCommand
    h += struct.pack("<H", 0) + struct.pack("<H", 0) + struct.pack("<I", 0) * 2
    assert len(h) == 0x4C
    return h


def _lnk_link_info_ansi(target: str) -> bytes:
    """LinkInfo：仅 ANSI LocalBasePath（指向 target）。"""
    path = target.encode("ascii") + b"\x00"
    header = struct.pack("<I", 0x1C) + struct.pack("<I", shortcut._LI_FLAG_LOCAL_BASE)
    header += struct.pack("<I", 0)  # VolumeIDOffset
    header += struct.pack("<I", 0x1C)  # LocalBasePathOffset
    header += struct.pack("<I", 0) + struct.pack("<I", 0)
    body = header + path
    return struct.pack("<I", len(body) + 4) + body


def _write_lnk(path: str, target: str):
    with open(path, "wb") as f:
        f.write(_lnk_header(shortcut._FLAG_HAS_LINK_INFO) + _lnk_link_info_ansi(target))


def test_file_row_icon_data_md():
    """.md 委托 _file_icon：DESCRIPTION + link 色，无 tooltip。"""
    c = _make_colors()
    icon, color, tooltip = _file_row_icon_data("note.md", r"C:\x\note.md", c)
    assert icon == ft.Icons.DESCRIPTION
    assert color == c.link
    assert tooltip is None


def test_file_row_icon_data_lnk_to_md(tmp_path):
    """指向 .md 的快捷方式：SHORTCUT 图标 + link 色 + 目标 tooltip。"""
    c = _make_colors()
    md = tmp_path / "target.md"
    md.write_text("# t", encoding="utf-8")
    lnk = tmp_path / "shortcut.lnk"
    _write_lnk(str(lnk), str(md))
    icon, color, tooltip = _file_row_icon_data("shortcut.lnk", str(lnk), c)
    assert icon == ft.Icons.SHORTCUT
    assert color == c.link
    assert tooltip == f"→ {md}"


def test_file_row_icon_data_lnk_other(tmp_path):
    """不指向 .md 的快捷方式：SHORTCUT 图标 + muted 色，无 tooltip。"""
    c = _make_colors()
    lnk = tmp_path / "app.lnk"
    _write_lnk(str(lnk), str(tmp_path / "app.exe"))
    icon, color, tooltip = _file_row_icon_data("app.lnk", str(lnk), c)
    assert icon == ft.Icons.SHORTCUT
    assert color == c.muted
    assert tooltip is None


def test_file_row_icon_data_other_extension():
    """其他扩展名走 _file_icon 映射。"""
    c = _make_colors()
    icon, color, tooltip = _file_row_icon_data("pic.png", r"C:\x\pic.png", c)
    assert icon == ft.Icons.IMAGE
    assert color == c.muted
    assert tooltip is None


# ---- open_external：平台分流 ----


def test_open_external_windows(tmp_path):
    """Windows: os.startfile 被调用。"""
    f = tmp_path / "test.png"
    f.write_bytes(b"")
    with patch("services.file_ops.platform.system", return_value="Windows"), \
         patch("services.file_ops.os.startfile", create=True) as mock_start:
        open_external(str(f))
        mock_start.assert_called_once_with(str(f))


def test_open_external_macos(tmp_path):
    """macOS: open 命令被调用。"""
    f = tmp_path / "test.png"
    f.write_bytes(b"")
    with patch("services.file_ops.platform.system", return_value="Darwin"), \
         patch("services.file_ops.subprocess.Popen") as mock_popen:
        open_external(str(f))
        mock_popen.assert_called_once_with(["open", str(f)])


def test_open_external_linux(tmp_path):
    """Linux: xdg-open 命令被调用。"""
    f = tmp_path / "test.png"
    f.write_bytes(b"")
    with patch("services.file_ops.platform.system", return_value="Linux"), \
         patch("services.file_ops.subprocess.Popen") as mock_popen:
        open_external(str(f))
        mock_popen.assert_called_once_with(["xdg-open", str(f)])


def test_open_external_file_not_found():
    """文件不存在抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        open_external("/nonexistent/path/file.xyz")


# ---- 拖拽放置目标（文件树拖拽移动）----


def test_drop_allowed_none_dst_rejects():
    """dst_dir=None（文件行拒绝型占位目标）恒拒绝，防命中穿透到根目录。"""
    assert not _drop_allowed("/a/b.md", None)
    assert not _drop_allowed("/a/b.md", "")
    assert not _drop_allowed(None, "/a")


def test_drop_allowed_same_dir_rejects(tmp_path):
    """源已在目标文件夹中：拒绝（防止原地拖拽造成抖动）。"""
    src = tmp_path / "sub" / "b.md"
    src.parent.mkdir()
    src.write_text("x")
    assert not _drop_allowed(str(src), str(src.parent))


def test_drop_allowed_self_and_descendant_reject(tmp_path):
    """文件夹不能移入自身或子孙；反向（独立文件夹入另一文件夹）合法。"""
    a = tmp_path / "a"
    b = a / "b"
    c = tmp_path / "c"
    b.mkdir(parents=True)
    c.mkdir()
    assert not _drop_allowed(str(a), str(a))
    assert not _drop_allowed(str(a), str(b))
    assert _drop_allowed(str(c), str(a))


def test_wrap_drop_target_none_never_invokes_drop(tmp_path):
    """文件行拒绝型目标：悬停清高亮、松手不触发移动（不误移入根目录）。"""
    src_path = tmp_path / "b.md"
    src_path.write_text("x")
    registry: dict = {}
    src = ft.Draggable(group="filetree", content=ft.Container())
    registry[id(src)] = str(src_path)
    drops: list = []
    hl: list = []
    dt = _wrap_drop_target(
        ft.Container(), None, registry,
        lambda s, d: drops.append((s, d)), hl.append,
    )
    e = types.SimpleNamespace(src=src)
    dt.on_will_accept(e)
    assert hl == [None]  # 悬停不高亮
    dt.on_accept(e)
    assert drops == []  # 松手不移动
    assert hl == [None, None]  # accept 先清高亮


def test_wrap_drop_target_folder_accepts(tmp_path):
    """文件夹行目标：合法时悬停高亮目标、松手触发移动。"""
    dst = tmp_path / "dst"
    dst.mkdir()
    src_path = tmp_path / "b.md"
    src_path.write_text("x")
    registry: dict = {}
    src = ft.Draggable(group="filetree", content=ft.Container())
    registry[id(src)] = str(src_path)
    drops: list = []
    hl: list = []
    dt = _wrap_drop_target(
        ft.Container(), str(dst), registry,
        lambda s, d: drops.append((s, d)), hl.append,
    )
    e = types.SimpleNamespace(src=src)
    dt.on_will_accept(e)
    assert hl == [str(dst)]  # 悬停高亮目标文件夹
    dt.on_accept(e)
    assert drops == [(str(src_path), str(dst))]  # 松手移动
    assert hl == [str(dst), None]  # 移动后清高亮

