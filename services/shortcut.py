"""Windows 快捷方式（.lnk）解析。

支持场景：快捷方式指向 .md/.markdown 文件时，编辑器打开/编辑目标文档；
复制/移动/重命名/删除始终只操作 .lnk 文件本身（资源管理器直觉）。

解析实现（无 pywin32 依赖）：
- 主路径：纯 Python 解析 Shell Link Binary File Format（MS-SHLLINK）：
  Header（76B，LinkFlags）→ LinkTargetIDList（跳过）→ LinkInfo
  （LocalBasePath ANSI / LocalBasePathUnicode 优先）→ StringData
  （HasRelativePath 回退：目标不存在时结合 .lnk 所在目录解析相对路径）
- 回退：PowerShell WScript.Shell COM（覆盖纯解析不支持的变体，
  如网络路径 CommonNetworkRelativeLink），仅 Windows 且主路径失败时触发
- 缓存：normcase(abspath) → (mtime, size) → target，文件树每次渲染都会
  对每个 .lnk 查询目标，缓存避免重复读盘与 subprocess（GIL 下 dict 原子）

对外接口：
- resolve_shortcut_target(lnk_path) -> str | None：目标绝对路径（解析失败/文件不存在返回 None）
- resolve_md_target(lnk_path) -> str | None：目标存在且为 .md/.markdown 时返回绝对路径
- is_shortcut(path) -> bool：是否 .lnk 文件（扩展名判断）

非 .lnk 输入：resolve_* 一律返回 None（调用方无需预判扩展名）。
"""

import os
import platform
import struct
import subprocess
import sys

_MD_EXTS = (".md", ".markdown")
_MAX_CHAIN = 5  # .lnk → .lnk → … → .md 链式展开上限（防循环/恶意深链）

# MS-SHLLINK Header：SizeOfLinkHeader(4) + LinkCLSID(16)
_LNK_CLSID = bytes.fromhex("0114020000000000C000000000000046")
_HEADER_SIZE = 0x4C  # 76

# LinkFlags 位
_FLAG_HAS_IDLIST = 0x1
_FLAG_HAS_LINK_INFO = 0x2
_FLAG_HAS_RELATIVE = 0x8

# LinkInfo 字段偏移（相对 LinkInfo 起始）
_LI_HEADER_SIZE = 0x04
_LI_FLAGS = 0x08
_LI_LOCAL_BASE_PATH = 0x10
_LI_LOCAL_BASE_PATH_UNI = 0x1C

_LI_FLAG_LOCAL_BASE = 0x1  # VolumeIDAndLocalBasePath 有效

# 目标缓存：normcase(abspath) -> ((mtime, size), target | None)
_cache: dict[str, tuple[tuple[int, int], str | None]] = {}


def is_shortcut(path: str | None) -> bool:
    """是否 Windows 快捷方式（.lnk）文件（仅扩展名判断）。"""
    return bool(path) and path.lower().endswith(".lnk")


def _read_c_utf16(buf: bytes, off: int) -> str:
    """从 off 读取 UTF-16LE 以 \\0\\0 结尾的字符串。"""
    chars: list[bytes] = []
    i = off
    while i + 1 < len(buf):
        if buf[i] == 0 and buf[i + 1] == 0:
            break
        chars.append(buf[i : i + 2])
        i += 2
    return b"".join(chars).decode("utf-16-le", errors="replace")


def _read_c_ansi(buf: bytes, off: int) -> str:
    """从 off 读取 ANSI（Windows 代码页）以 \\0 结尾的字符串。"""
    end = buf.find(b"\x00", off)
    if end < 0:
        end = len(buf)
    raw = buf[off:end]
    try:
        return raw.decode("mbcs")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _parse_lnk_bytes(data: bytes, lnk_dir: str) -> str | None:
    """解析 .lnk 二进制内容，返回目标路径（相对路径基于 lnk_dir 解析）。

    不符合 MS-SHLLINK 头部签名时返回 None。
    """
    if len(data) < _HEADER_SIZE:
        return None
    if struct.unpack_from("<I", data, 0)[0] != _HEADER_SIZE or data[4:20] != _LNK_CLSID:
        return None
    flags = struct.unpack_from("<I", data, 20)[0]
    pos = _HEADER_SIZE

    # 跳过 LinkTargetIDList
    if flags & _FLAG_HAS_IDLIST:
        if pos + 2 > len(data):
            return None
        idlist_size = struct.unpack_from("<H", data, pos)[0]
        pos += 2 + idlist_size
        if pos > len(data):
            return None

    # LinkInfo：LocalBasePath（Unicode 优先于 ANSI）
    if flags & _FLAG_HAS_LINK_INFO and pos + 0x1C <= len(data):
        li_size = struct.unpack_from("<I", data, pos)[0]
        header_size = struct.unpack_from("<I", data, pos + _LI_HEADER_SIZE)[0]
        if li_size >= 0x1C and pos + li_size <= len(data):
            li = data[pos : pos + li_size]
            li_flags = struct.unpack_from("<I", li, _LI_FLAGS)[0]
            target: str | None = None
            if li_flags & _LI_FLAG_LOCAL_BASE:
                # Unicode 版本（HeaderSize >= 0x24 时存在）
                if header_size >= 0x24:
                    uni_off = struct.unpack_from("<I", li, _LI_LOCAL_BASE_PATH_UNI)[0]
                    if 0 < uni_off < len(li):
                        target = _read_c_utf16(li, uni_off) or None
                # ANSI 版本回退
                if not target:
                    ansi_off = struct.unpack_from("<I", li, _LI_LOCAL_BASE_PATH)[0]
                    if 0 < ansi_off < len(li):
                        target = _read_c_ansi(li, ansi_off) or None
            if target:
                return _clean_path(target)

    # StringData 回退：HasRelativePath（LinkInfo 缺失/无 LocalBasePath 时）
    if flags & _FLAG_HAS_RELATIVE:
        rel = _read_string_data(data, pos, flags)
        if rel:
            return _clean_path(os.path.normpath(os.path.join(lnk_dir, rel)))
    return None


def _read_string_data(data: bytes, pos: int, flags: int) -> str | None:
    """按 flags 顺序读取 StringData，返回 RelativePath 字符串。

    顺序（存在与否由 flags 决定）：Name / RelativePath / WorkingDir /
    Arguments / IconLocation，各为 2 字节字符数 + UTF-16 内容。
    """
    # 各 string 对应的 flag 位（Name=0x4, RelativePath=0x8, WorkingDir=0x10）；
    # 仅 flag 启用的 string 才存在于数据流中，按顺序消费字节
    order = [(0x4, False), (0x8, True), (0x10, False)]
    for flag_bit, is_rel in order:
        if not (flags & flag_bit):
            continue
        if pos + 2 > len(data):
            return None
        cc = struct.unpack_from("<H", data, pos)[0]
        s = data[pos + 2 : pos + 2 + cc * 2]
        pos += 2 + cc * 2
        if is_rel:
            return s.decode("utf-16-le", errors="replace")
    return None


def _clean_path(p: str) -> str:
    """规整目标路径：剥离 \\??\\ 前缀、展开环境变量（%USERPROFILE% 等）。"""
    if p.startswith("\\??\\"):
        p = p[4:]
    if "%" in p:
        p = os.path.expandvars(p)
    return p


def _ps_resolve(path: str) -> str | None:
    """PowerShell（WScript.Shell COM）解析目标——纯解析失败时的回退。"""
    if platform.system() != "Windows":
        return None
    escaped = path.replace("'", "''")
    try:
        out = subprocess.run(  # noqa: S603
            [
                "powershell", "-NoProfile", "-STA", "-Command",
                f"(New-Object -ComObject WScript.Shell)"
                f".CreateShortcut('{escaped}').TargetPath",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def resolve_shortcut_target(lnk_path: str | None) -> str | None:
    """解析 .lnk 目标绝对路径。

    返回 None：非 .lnk / 文件不存在 / 解析失败 / 目标为空字符串。
    带 (mtime, size) 缓存；.lnk 内容变化后自动重解析。
    注意：返回的目标路径可能已不存在（快捷方式失效），调用方按需校验。
    """
    if not is_shortcut(lnk_path):
        return None
    try:
        st = os.stat(lnk_path)
    except OSError:
        return None
    key = os.path.normcase(os.path.abspath(lnk_path))
    sig = (st.st_mtime_ns, st.st_size)
    cached = _cache.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1]

    target: str | None = None
    try:
        with open(lnk_path, "rb") as f:
            data = f.read(256 * 1024)
        # lnk_dir 保留原始大小写（normcase 仅用于缓存键），相对路径回退不受影响
        target = _parse_lnk_bytes(data, os.path.dirname(os.path.abspath(lnk_path)))
    except OSError:
        target = None
    if not target:
        target = _ps_resolve(key)
    if target:
        target = os.path.abspath(target)
    _cache[key] = (sig, target)
    return target


def resolve_md_target(lnk_path: str | None, _depth: int = 0) -> str | None:
    """解析 .lnk 指向的 Markdown 文件绝对路径。

    目标存在且扩展名为 .md/.markdown 时返回；目标本身是 .lnk 时链式展开
    （上限 _MAX_CHAIN 层，防循环引用）；其余情况返回 None。
    """
    target = resolve_shortcut_target(lnk_path)
    if not target:
        return None
    if _depth >= _MAX_CHAIN:
        return None
    if os.path.normcase(target).endswith(_MD_EXTS):
        return target if os.path.isfile(target) else None
    if is_shortcut(target):
        return resolve_md_target(target, _depth + 1)
    return None
