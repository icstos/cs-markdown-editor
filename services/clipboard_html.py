"""Windows 剪贴板 HTML Format 读取。

通过 ctypes 调用 user32/kernel32 API 读取剪贴板中的 "HTML Format" 格式，
弥补 Flet Clipboard 仅支持纯文本（CF_UNICODETEXT）的限制。

浏览器（Chrome/Edge/Firefox）复制富文本时会同时写入：
- CF_UNICODETEXT：纯文本（丢失链接 URL、格式信息）
- HTML Format：完整 HTML（含 <a href>、<strong>、<h1> 等标签）

本模块读取 HTML Format 并提取 StartFragment/EndFragment 标记的内容片段，
供 html_to_markdown 转换器转为 Markdown。

依赖项：ctypes（标准库，Windows 内置）。
平台：仅 Windows（win32），其他平台 get_clipboard_html 返回 None。
"""

import asyncio
import ctypes
import re
import sys
from ctypes import wintypes

# 仅 Windows 初始化 API
_is_win = sys.platform == "win32"
if _is_win:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    # 函数原型声明（避免 ctypes 默认 int 截断 64 位指针）
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterClipboardFormatW.restype = wintypes.UINT
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL

    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalSize.restype = ctypes.c_size_t

# CF_HTML 格式 ID（RegisterClipboardFormat 返回，进程内稳定）
_cf_html_cache: int | None = None


def _get_cf_html_id() -> int:
    """获取 "HTML Format" 的剪贴板格式 ID（进程内缓存）。"""
    global _cf_html_cache
    if _cf_html_cache is None and _is_win:
        _cf_html_cache = _user32.RegisterClipboardFormatW("HTML Format")
    return _cf_html_cache or 0


# CF_HTML 头部解析正则：提取 StartFragment / EndFragment 偏移
_RE_FRAGMENT = re.compile(
    r"StartFragment:(\d+).*?EndFragment:(\d+)",
    re.IGNORECASE | re.DOTALL,
)


def get_clipboard_html() -> str | None:
    """从 Windows 剪贴板读取 HTML Format 内容。

    返回 StartFragment/EndFragment 标记之间的 HTML 片段（已解码为 str）。
    无 HTML Format 或非 Windows 平台返回 None。

    CF_HTML 格式说明（MSDN "HTML Clipboard Format"）：
    - 头部含 Version/StartHTML/EndHTML/StartFragment/EndFragment/SourceURL
    - 偏移以字节为单位（UTF-8 编码后）
    - StartFragment/EndFragment 标记实际内容片段（去除 <html><body> 外壳）
    """
    if not _is_win:
        return None

    cf_html = _get_cf_html_id()
    if not cf_html:
        return None

    if not _user32.OpenClipboard(None):
        return None
    try:
        if not _user32.IsClipboardFormatAvailable(cf_html):
            return None
        handle = _user32.GetClipboardData(cf_html)
        if not handle:
            return None
        size = _kernel32.GlobalSize(handle)
        if not size:
            return None
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            raw = ctypes.string_at(ptr, size)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()

    # CF_HTML 头部（Version/StartHTML/...）是 ASCII，安全解码用于解析偏移
    # 但 StartFragment/EndFragment 是字节偏移，含非 ASCII 内容时字节偏移 ≠ 字符偏移
    # 故先在原始字节上切片，再解码为 str
    head = raw[:512].decode("ascii", errors="replace")  # 头部足够覆盖偏移行

    # 提取 Fragment 字节偏移
    m = _RE_FRAGMENT.search(head)
    if m:
        start = int(m.group(1))
        end = int(m.group(2))
        if 0 <= start <= end <= len(raw):
            return raw[start:end].decode("utf-8", errors="replace")

    # 回退：无 Fragment 偏移标记时用注释标记定位（在解码后的 str 上操作）
    full = raw.decode("utf-8", errors="replace")
    sf = full.find("<!--StartFragment-->")
    ef = full.find("<!--EndFragment-->")
    if 0 <= sf < ef:
        return full[sf + len("<!--StartFragment-->"):ef]

    # 最终回退：返回完整内容（含头部，转换器会跳过头部纯文本）
    return full


async def get_clipboard_html_async() -> str | None:
    """异步获取 HTML Format 剪贴板内容（避免阻塞事件循环）。

    Windows API 调用是同步的（OpenClipboard/GetClipboardData），
    用 asyncio.to_thread 包装避免阻塞 Flet 事件循环。
    """
    if not _is_win:
        return None
    return await asyncio.to_thread(get_clipboard_html)
