"""Windows 快捷方式（.lnk）支持测试。

覆盖：
- services.shortcut：纯字节解析（手工构造 MS-SHLLINK 结构，不依赖 Windows）/
  is_shortcut 扩展名判断 / resolve_md_target 类型分流（.md ✓、.txt ✗、
  失效目标 ✗）/ 真实 .lnk 解析（PowerShell WScript.Shell 创建，仅 Windows）
- services.file_ops.rename_path：.lnk 重命名保留扩展（防快捷方式失效）
- views.sidebar._collect_md_paths：指向 .md 的 .lnk 以目标路径参与跨文件搜索

纯字节解析用例手工构造最小合法 .lnk（Header + LinkInfo），不经过
PowerShell，保证全平台可跑（mbcs 编码仅 Windows 存在，构造用 ASCII 路径）。
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from services import shortcut
from services.file_ops import rename_path
from views.sidebar import _collect_md_paths

# ---- 最小 .lnk 构造（MS-SHLLINK） ----

_LNK_CLSID = bytes.fromhex("0114020000000000C000000000000046")


def _header(flags: int) -> bytes:
    h = struct.pack("<I", 0x4C) + _LNK_CLSID + struct.pack("<I", flags)
    h += struct.pack("<I", 0x80)  # FileAttributes: NORMAL
    h += struct.pack("<Q", 0) * 3  # 三个时间戳
    h += struct.pack("<I", 0)  # FileSize
    h += struct.pack("<i", 0)  # IconIndex
    h += struct.pack("<I", 1)  # ShowCommand: SW_SHOWNORMAL
    h += struct.pack("<H", 0)  # HotKey
    h += struct.pack("<H", 0) + struct.pack("<I", 0) * 2  # Reserved
    assert len(h) == 0x4C
    return h


def _link_info_ansi(target: str) -> bytes:
    """LinkInfo：HeaderSize=0x1C（无 Unicode 字段），仅 ANSI LocalBasePath。

    布局：Header(0x1C) + ANSI 路径（\0 结尾）。LocalBasePathOffset=0x1C。
    VolumeIDOffset=0（解析器不读取 VolumeID，结构上仅 LocalBasePath 有效）。
    """
    path = target.encode("ascii") + b"\x00"
    header = struct.pack("<I", 0x1C)  # LinkInfoHeaderSize
    header += struct.pack("<I", shortcut._LI_FLAG_LOCAL_BASE)  # LinkInfoFlags
    header += struct.pack("<I", 0)  # VolumeIDOffset
    header += struct.pack("<I", 0x1C)  # LocalBasePathOffset
    header += struct.pack("<I", 0)  # CommonNetworkRelativeLinkOffset
    header += struct.pack("<I", 0)  # CommonPathSuffixOffset
    # HeaderSize=0x1C 含 LinkInfoSize 字段自身（完整 LinkInfo 头 28 字节）
    assert len(header) == 0x18
    body = header + path
    # LinkInfoSize = 完整结构长度（含 size 字段自身 4 字节）
    return struct.pack("<I", len(body) + 4) + body


def _link_info_unicode(target: str) -> bytes:
    """LinkInfo：HeaderSize=0x24，ANSI 与 Unicode 路径并存（Unicode 应优先）。"""
    ansi = b"C:\\fake_old_ansi.md\x00"
    uni = target.encode("utf-16-le") + b"\x00\x00"
    ansi_off, uni_off = 0x24, 0x24 + len(ansi)
    header = struct.pack("<I", 0x24)
    header += struct.pack("<I", shortcut._LI_FLAG_LOCAL_BASE)
    header += struct.pack("<I", 0)  # VolumeIDOffset
    header += struct.pack("<I", ansi_off)  # LocalBasePathOffset
    header += struct.pack("<I", 0) * 2  # CNRL / CommonPathSuffix
    header += struct.pack("<I", uni_off)  # LocalBasePathUnicodeOffset
    header += struct.pack("<I", 0)  # CommonPathSuffixUnicodeOffset
    # HeaderSize=0x24 含 LinkInfoSize 字段自身（完整 LinkInfo 头 36 字节）
    assert len(header) == 0x20
    body = header + ansi + uni
    # LinkInfoSize = 完整结构长度（含 size 字段自身 4 字节）
    return struct.pack("<I", len(body) + 4) + body


def _write_lnk(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)


# ---- is_shortcut ----


@pytest.mark.parametrize(
    "path,expected",
    [
        ("C:/a/b.md.LNK", True),
        ("note.lnk", True),
        ("note.md", False),
        ("", False),
        (None, False),
    ],
)
def test_is_shortcut(path, expected):
    assert shortcut.is_shortcut(path) is expected


# ---- 纯字节解析（全平台） ----


def test_parse_ansi_local_base_path(tmp_path):
    """LinkInfo ANSI LocalBasePath 解析（HeaderSize=0x1C）。"""
    target = str(tmp_path / "note.md")
    lnk = _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(target)
    p = str(tmp_path / "a.lnk")
    _write_lnk(p, lnk)
    assert shortcut.resolve_shortcut_target(p) == target


def test_parse_unicode_preferred_over_ansi(tmp_path):
    """Unicode LocalBasePath 存在时优先于 ANSI（HeaderSize>=0x24）。"""
    target = str(tmp_path / "笔记.md")
    lnk = _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_unicode(target)
    p = str(tmp_path / "u.lnk")
    _write_lnk(p, lnk)
    assert shortcut.resolve_shortcut_target(p) == target


def test_parse_rejects_non_lnk(tmp_path):
    """非 .lnk 内容（签名不符）返回 None，不走 PowerShell 回退产生误报。"""
    p = str(tmp_path / "fake.md")
    _write_lnk(p, b"\x00" * 128)
    assert shortcut.resolve_shortcut_target(p) is None


def test_parse_relative_path_fallback(tmp_path):
    """无 LinkInfo 时 HasRelativePath 回退：基于 .lnk 所在目录解析。"""
    rel = struct.pack("<H", 7) + "note.md".encode("utf-16-le")
    data = _header(shortcut._FLAG_HAS_RELATIVE) + rel
    p = str(tmp_path / "r.lnk")
    _write_lnk(p, data)
    assert shortcut.resolve_shortcut_target(p) == str(tmp_path / "note.md")


def test_cache_invalidates_on_content_change(tmp_path):
    """缓存按 (mtime, size) 失效：.lnk 内容变化后重新解析。"""
    t1 = str(tmp_path / "one.md")
    t2 = str(tmp_path / "two.md")
    p = str(tmp_path / "c.lnk")
    _write_lnk(p, _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(t1))
    assert shortcut.resolve_shortcut_target(p) == t1
    # 确保写入时间戳差异（Windows mtime 精度）后替换目标
    os.utime(p, (0, 0))
    _write_lnk(p, _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(t2))
    assert shortcut.resolve_shortcut_target(p) == t2


# ---- resolve_md_target 分流 ----


def test_resolve_md_target_md_file(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("# hi", encoding="utf-8")
    p = str(tmp_path / "md.lnk")
    _write_lnk(p, _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(str(target)))
    assert shortcut.resolve_md_target(p) == str(target)


def test_resolve_md_target_rejects_txt(tmp_path):
    """目标非 .md/.markdown → None（即使目标存在）。"""
    target = tmp_path / "readme.txt"
    target.write_text("t", encoding="utf-8")
    p = str(tmp_path / "txt.lnk")
    _write_lnk(p, _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(str(target)))
    assert shortcut.resolve_md_target(p) is None


def test_resolve_md_target_rejects_dead_link(tmp_path):
    """目标 .md 已不存在（快捷方式失效）→ None。"""
    p = str(tmp_path / "dead.lnk")
    _write_lnk(
        p,
        _header(shortcut._FLAG_HAS_LINK_INFO)
        + _link_info_ansi(str(tmp_path / "gone.md")),
    )
    assert shortcut.resolve_md_target(p) is None


def test_resolve_md_target_non_lnk_returns_none(tmp_path):
    """非 .lnk 输入直接返回 None，无需调用方预判扩展名。"""
    p = tmp_path / "plain.md"
    p.write_text("# x", encoding="utf-8")
    assert shortcut.resolve_md_target(str(p)) is None


def test_resolve_md_target_chains_lnk_to_lnk(tmp_path):
    """lnk → lnk → md 链式展开（最多 5 层）。"""
    md = tmp_path / "deep.md"
    md.write_text("# d", encoding="utf-8")
    inner = str(tmp_path / "inner.lnk")
    _write_lnk(
        inner, _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(str(md))
    )
    outer = str(tmp_path / "outer.lnk")
    _write_lnk(outer, _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(inner))
    assert shortcut.resolve_md_target(outer) == str(md)


# ---- 真实 .lnk（仅 Windows，PowerShell 创建） ----


@pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows 有 WScript.Shell")
def test_resolve_real_lnk_created_by_powershell(tmp_path):
    """PowerShell WScript.Shell 创建真实快捷方式（含中文路径）→ 纯解析器应命中。"""
    md = tmp_path / "笔记.md"
    md.write_text("# 真实", encoding="utf-8")
    lnk = tmp_path / "真实快捷.lnk"
    escaped = str(lnk).replace("'", "''")
    import subprocess

    r = subprocess.run(  # noqa: S603
        [
            "powershell", "-NoProfile", "-Command",
            f"$s=(New-Object -ComObject WScript.Shell)"
            f".CreateShortcut('{escaped}'); $s.TargetPath='{md}'; $s.Save()",
        ],
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert r.returncode == 0, r.stderr
    assert shortcut.resolve_md_target(str(lnk)) == str(md)


# ---- rename_path：保留扩展 ----


def test_rename_lnk_keeps_extension(tmp_path):
    """重命名 .lnk 未写扩展时自动保留 .lnk（防快捷方式失效）。"""
    p = tmp_path / "旧名.lnk"
    p.write_bytes(b"x")
    new = rename_path(str(p), "新名字")
    assert new.endswith(".lnk")
    assert os.path.basename(new) == "新名字.lnk"


def test_rename_lnk_explicit_extension_untouched(tmp_path):
    """用户显式写了扩展时不再追加。"""
    p = tmp_path / "a.lnk"
    p.write_bytes(b"x")
    new = rename_path(str(p), "b.lnk")
    assert os.path.basename(new) == "b.lnk"


def test_rename_md_still_forces_md(tmp_path):
    """.md 重命名逻辑不受影响：无扩展时仍强制 .md。"""
    p = tmp_path / "old.md"
    p.write_text("x", encoding="utf-8")
    new = rename_path(str(p), "newname")
    assert os.path.basename(new) == "newname.md"


# ---- 侧边栏跨文件搜索：.lnk 目标参与 ----


def test_collect_md_paths_includes_lnk_targets(tmp_path):
    """指向 .md 的 .lnk 以目标路径参与；与树内同名 .md 去重；非 md 目标忽略。"""
    md = tmp_path / "note.md"
    md.write_text("# n", encoding="utf-8")
    (tmp_path / "ref.txt").write_text("t", encoding="utf-8")
    lnk_md = tmp_path / "alias.md.lnk"
    _write_lnk(
        str(lnk_md), _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(str(md))
    )
    lnk_txt = tmp_path / "ref.lnk"
    _write_lnk(
        str(lnk_txt),
        _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(str(tmp_path / "ref.txt")),
    )
    tree = [
        ("file", "note.md", str(md)),
        ("file", "alias.md.lnk", str(lnk_md)),
        ("file", "ref.lnk", str(lnk_txt)),
    ]
    # 去重后仅一个目标（note.md 与 alias.md.lnk 指向同一路径）
    assert _collect_md_paths(tree) == [str(md)]


def test_collect_md_paths_lnk_target_outside_tree(tmp_path):
    """目标在工作区外时也参与搜索（返回目标绝对路径）。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    md = outside / "external.md"
    md.write_text("# e", encoding="utf-8")
    lnk = tmp_path / "ext.lnk"
    _write_lnk(str(lnk), _header(shortcut._FLAG_HAS_LINK_INFO) + _link_info_ansi(str(md)))
    tree = [("file", "ext.lnk", str(lnk))]
    assert _collect_md_paths(tree) == [str(md)]
