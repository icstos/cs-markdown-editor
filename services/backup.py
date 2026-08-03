"""备份模块：自动备份、崩溃恢复、覆盖前历史副本。

独立于自动保存机制（保存是把脏文档写回原路径；备份是把任意状态写入
专用备份目录，用于意外发生后找回）。所有函数纯 / 无 UI 依赖，可单测。

平台化存储路径：
- Windows：``%APPDATA%/{应用名称}/backup/``
- Linux：``~/.config/{应用名称}/backup/``
- macOS：``~/Library/Application Support/{应用名称}/backup/``

目录结构：按 ``YYYY-MM-DD`` 格式按天创建子文件夹，当日备份归入对应日期目录。

文件命名：
- 已命名文档：``{原文件名}-{时分秒}.md.autosave``
- 未命名草稿：``untitled-{首行标题前10字}-{时分秒}.md.autosave``

文件内容：头部注入 HTML 注释元数据块（备份时间戳、原文件绝对路径、文档唯一 ID、
光标位置、滚动偏移），正文为完整 Markdown 源文本。

依赖项：
- 标准库（os / sys / time / datetime / hashlib / re / shutil / tempfile / platform）
- services.file_io.write_text_atomic（备份本身也走原子写入，防止备份过程崩溃导致半成品）

对外接口（模块级函数，无状态）：
- get_backup_root(settings) -> str：解析备份根目录（自定义 > 平台默认）
- get_today_backup_dir(settings, now=None) -> str：当日备份目录（不存在则创建）
- make_backup_filename(tab_data, now=None) -> str：根据 tab 字段生成备份文件名
- format_backup_header(metadata) -> str：构造 HTML 注释元数据块
- parse_backup_header(content) -> dict | None：从备份文件解析元数据
- write_backup(settings, tab_data, content, cursor_pos, scroll_offset, doc_id, now=None) -> str | None
- scan_backups(settings, max_age_days=None) -> list[BackupInfo]：扫描备份目录
- parse_backup_file(path) -> tuple[dict | None, str]：解析备份文件 -> (元数据, 正文)
- cleanup_old_backups(settings) -> int：清理过期备份，返回删除数
- BackupInfo：备份元信息 dataclass（用于 UI 列表）
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from typing import Any

from services.file_io import write_text_atomic

# 应用名称（用于构造备份根目录，与 pyproject.toml name 保持一致）
APP_NAME = "cs-markdown-editor"

# 备份文件后缀：用 .autosave 后缀避免被侧边栏文件树识别为普通 Markdown 文件
BACKUP_SUFFIX = ".md.autosave"

# 元数据块标记（HTML 注释，渲染时不可见，备份文件被误打开也不会污染视图）
_META_BEGIN = "<!-- cs-md-backup-meta"
_META_END = "-->"
# 单行元数据键值对格式：key=value（支持 = 转义）
_META_KV_PATTERN = re.compile(r"^([a-z_]+)=(.*)$")

# 大文件优化阈值（字节）：超过此值的文档降低备份频率
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10 MB


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """单条备份信息（用于恢复面板列表展示）。

    - backup_path：备份文件绝对路径
    - backup_time：备份创建时间（datetime，从文件名解析，回退 mtime）
    - original_path：原文件绝对路径（未命名草稿为 None）
    - doc_id：文档唯一 ID（用于去重 / 关联同一文档的多次备份）
    - cursor_pos：备份时光标位置 (row, col)，无则为 (1, 1)
    - scroll_offset：备份时滚动偏移（像素），无则为 0
    - preview：正文首行预览（截断 80 字符）
    - size_bytes：备份文件大小（字节）
    - is_untitled：是否为未命名草稿备份
    """

    backup_path: str
    backup_time: datetime.datetime
    original_path: str | None
    doc_id: str | None
    cursor_pos: tuple[int, int]
    scroll_offset: float
    preview: str
    size_bytes: int
    is_untitled: bool = False


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------

def get_backup_root(settings: dict[str, Any]) -> str:
    """解析备份根目录。

    优先级：settings.backup_dir（自定义路径） > 平台默认路径。
    自定义路径不存在时尝试创建；平台默认路径同样确保存在。
    返回绝对路径，以 os.sep 分隔。
    """
    custom = settings.get("backup_dir")
    if custom:
        root = os.path.abspath(custom)
    else:
        root = _platform_default_root()
    # 确保根目录存在（自定义路径可能不存在）
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        # 创建失败时回退到用户家目录下的 .cs-md-backup（保证备份总能写入）
        fallback = os.path.join(os.path.expanduser("~"), ".cs-md-backup")
        try:
            os.makedirs(fallback, exist_ok=True)
        except OSError:
            pass
        root = fallback
    return root


def _platform_default_root() -> str:
    """平台默认备份根目录。

    - Windows：%APPDATA%/{APP_NAME}/backup/
    - Linux：~/.config/{APP_NAME}/backup/
    - macOS：~/Library/Application Support/{APP_NAME}/backup/
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME, "backup")
    if sys.platform == "darwin":
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", APP_NAME, "backup"
        )
    # Linux / 其他 Unix
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME, "backup")


def get_today_backup_dir(
    settings: dict[str, Any], now: datetime.datetime | None = None
) -> str:
    """当日备份子目录（YYYY-MM-DD），不存在则创建。"""
    root = get_backup_root(settings)
    now = now or datetime.datetime.now()
    day_dir = os.path.join(root, now.strftime("%Y-%m-%d"))
    try:
        os.makedirs(day_dir, exist_ok=True)
    except OSError:
        pass
    return day_dir


# ---------------------------------------------------------------------------
# 文件命名
# ---------------------------------------------------------------------------

def make_backup_filename(
    tab_data: dict[str, Any], now: datetime.datetime | None = None
) -> str:
    """根据 tab 字段生成备份文件名。

    - 已命名文档：{原文件名去扩展}-{HHMMSS}.md.autosave
    - 未命名草稿：untitled-{首行标题前10字}-{HHMMSS}.md.autosave

    文件名中的非法字符已 sanitize（仅保留字母数字、中文、- _ . 空格）。
    """
    now = now or datetime.datetime.now()
    time_str = now.strftime("%H%M%S")

    path = _extract_tab_path(tab_data)
    if path:
        # 已命名文档：取主文件名（去扩展名）
        base = os.path.splitext(os.path.basename(path))[0]
        base = _sanitize_filename_component(base) or "doc"
        return f"{base}-{time_str}{BACKUP_SUFFIX}"

    # 未命名草稿：取首行文本前 10 字符作为标识
    first_line = _extract_first_line_preview(tab_data, max_chars=10)
    if first_line:
        slug = _sanitize_filename_component(first_line) or "draft"
        return f"untitled-{slug}-{time_str}{BACKUP_SUFFIX}"
    return f"untitled-{time_str}{BACKUP_SUFFIX}"


def _extract_tab_path(tab_data: dict[str, Any]) -> str | None:
    """从 tab 字段提取主路径（diff 标签取左路径，普通标签取 file_path）。"""
    if tab_data.get("type") == "diff":
        return tab_data.get("left_path") or tab_data.get("right_path")
    return tab_data.get("file_path")


def _extract_first_line_preview(tab_data: dict[str, Any], max_chars: int = 80) -> str:
    """从 tab 文档提取首行非空文本预览（供备份文件名 / 恢复列表展示）。"""
    doc = tab_data.get("document")
    if doc is None and tab_data.get("type") == "diff":
        doc = tab_data.get("left_doc") or tab_data.get("right_doc")
    if doc is None:
        return ""
    for line in getattr(doc, "lines", []):
        raw = getattr(line, "raw", "") or ""
        stripped = raw.strip()
        if stripped:
            # 去掉 Markdown 标题前缀和列表前缀，取纯文本
            cleaned = re.sub(r"^\s{0,3}#{1,6}\s+", "", stripped)
            cleaned = re.sub(r"^\s{0,3}[-*+]\s+", "", cleaned)
            cleaned = re.sub(r"^\s{0,3}\d+\.\s+", "", cleaned)
            cleaned = re.sub(r"^\s{0,3}>\s+", "", cleaned)
            cleaned = cleaned.strip()
            if cleaned:
                return cleaned[:max_chars]
    return ""


_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename_component(name: str) -> str:
    """清理文件名组件：去除非法字符、首尾空格和点。"""
    if not name:
        return ""
    # 替换非法字符为下划线（保留中文 / 字母 / 数字 / - _ . 空格）
    cleaned = _INVALID_FS_CHARS.sub("_", name)
    cleaned = cleaned.strip().rstrip(". ")
    # 控制长度避免文件名过长
    if len(cleaned) > 40:
        cleaned = cleaned[:40]
    return cleaned


# ---------------------------------------------------------------------------
# 元数据块
# ---------------------------------------------------------------------------

def format_backup_header(metadata: dict[str, Any]) -> str:
    """构造 HTML 注释元数据块。

    格式：
        <!-- cs-md-backup-meta
        timestamp=1753977600
        iso_time=2025-07-31T16:00:00
        original_path=/path/to/file.md
        doc_id=abc123
        cursor_row=12
        cursor_col=5
        scroll_offset=320.5
        -->
    """
    lines = [_META_BEGIN]
    # 写入键值对（按固定顺序，便于人工排查）
    ordered_keys = (
        "timestamp", "iso_time", "original_path", "doc_id",
        "cursor_row", "cursor_col", "scroll_offset",
    )
    for key in ordered_keys:
        if key in metadata:
            value = metadata[key]
            if value is None:
                continue
            # 转义换行（路径中不会出现，但防御性处理）
            value_str = str(value).replace("\n", "\\n")
            lines.append(f"{key}={value_str}")
    # 额外键（向后兼容扩展）
    for key, value in metadata.items():
        if key in ordered_keys or value is None:
            continue
        value_str = str(value).replace("\n", "\\n")
        lines.append(f"{key}={value_str}")
    lines.append(_META_END)
    return "\n".join(lines)


def parse_backup_header(content: str) -> dict[str, Any] | None:
    """从备份文件内容解析元数据块。

    返回 dict 或 None（无元数据块时）。元数据类型自动转换：
    - timestamp / cursor_row / cursor_col -> int
    - scroll_offset -> float
    - 其余保留为字符串
    """
    begin_idx = content.find(_META_BEGIN)
    if begin_idx == -1:
        return None
    end_idx = content.find(_META_END, begin_idx)
    if end_idx == -1:
        return None
    block = content[begin_idx + len(_META_BEGIN):end_idx]
    metadata: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _META_KV_PATTERN.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        # 反转义换行
        value = value.replace("\\n", "\n")
        # 类型转换
        if key in ("timestamp", "cursor_row", "cursor_col"):
            try:
                metadata[key] = int(value)
            except ValueError:
                pass
        elif key == "scroll_offset":
            try:
                metadata[key] = float(value)
            except ValueError:
                pass
        else:
            metadata[key] = value
    return metadata


# ---------------------------------------------------------------------------
# 写入备份
# ---------------------------------------------------------------------------

def write_backup(
    settings: dict[str, Any],
    tab_data: dict[str, Any],
    content: str,
    cursor_pos: tuple[int, int] = (1, 1),
    scroll_offset: float = 0.0,
    doc_id: str | None = None,
    now: datetime.datetime | None = None,
) -> str | None:
    """写入一份完整备份。

    - 仅在 settings.backup_enabled=True 时执行；关闭时返回 None。
    - 文件名按 make_backup_filename 规则生成；目录为当日子目录。
    - 文件内容 = 元数据块 + 空行 + 完整 Markdown 正文。
    - 写入采用 write_text_atomic 保证原子性（防止备份过程崩溃导致半成品）。
    - doc_id 缺省时基于 original_path + content 哈希生成（同一文档多次备份 ID 一致，
      便于在恢复列表中按文档分组）。

    返回备份文件绝对路径；失败时返回 None（不抛异常，避免打断编辑流程）。
    """
    if not settings.get("backup_enabled", True):
        return None
    # 空内容不备份（避免无意义空文件堆积）
    if not content or not content.strip():
        return None

    now = now or datetime.datetime.now()
    try:
        day_dir = get_today_backup_dir(settings, now)
        filename = make_backup_filename(tab_data, now)
        backup_path = os.path.join(day_dir, filename)

        original_path = _extract_tab_path(tab_data)
        if doc_id is None:
            doc_id = _compute_doc_id(original_path, content)

        metadata = {
            "timestamp": int(now.timestamp()),
            "iso_time": now.isoformat(timespec="seconds"),
            "original_path": original_path or "",
            "doc_id": doc_id,
            "cursor_row": int(cursor_pos[0]) if cursor_pos else 1,
            "cursor_col": int(cursor_pos[1]) if cursor_pos else 1,
            "scroll_offset": float(scroll_offset or 0.0),
        }
        header = format_backup_header(metadata)
        # 元数据块与正文之间空一行，便于 parse_backup_file 切分
        full_content = f"{header}\n\n{content}"
        write_text_atomic(backup_path, full_content)
        return backup_path
    except Exception:
        # 备份失败不抛异常，避免打断编辑流程；上层可记录日志或提示
        return None


def _compute_doc_id(original_path: str | None, content: str) -> str:
    """生成文档唯一 ID：基于路径 + 内容首 1KB 的 SHA256 前 12 位。

    同一文档（路径相同 + 内容相似）多次备份 ID 接近，便于在恢复列表中按文档分组。
    路径为空（未命名草稿）时仅基于内容哈希。
    """
    h = hashlib.sha256()
    h.update((original_path or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(content[:1024].encode("utf-8"))
    return h.hexdigest()[:12]


# ---------------------------------------------------------------------------
# 扫描与解析
# ---------------------------------------------------------------------------

def scan_backups(
    settings: dict[str, Any], max_age_days: int | None = None
) -> list[BackupInfo]:
    """扫描备份目录，返回所有有效备份的 BackupInfo 列表。

    - max_age_days=None 时返回全部；指定时仅返回最近 N 天的备份。
    - 列表按 backup_time 降序排列（最新在前）。
    - 无效 / 损坏的备份文件被跳过（不抛异常）。
    """
    root = get_backup_root(settings)
    if not os.path.isdir(root):
        return []

    cutoff_ts: float | None = None
    if max_age_days is not None:
        cutoff_ts = time.time() - max_age_days * 86400

    infos: list[BackupInfo] = []
    for day_entry in os.listdir(root):
        day_dir = os.path.join(root, day_entry)
        if not os.path.isdir(day_dir):
            continue
        # 按 YYYY-MM-DD 子目录过滤（避免扫描无关文件）
        day_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", day_entry)
        if not day_match:
            continue
        # 解析日期目录 -> (year, month, day)，传给时间解析器构造完整 datetime
        try:
            day_date = datetime.date(
                int(day_match.group(1)),
                int(day_match.group(2)),
                int(day_match.group(3)),
            )
        except ValueError:
            continue
        for fname in os.listdir(day_dir):
            if not fname.endswith(BACKUP_SUFFIX):
                continue
            fpath = os.path.join(day_dir, fname)
            info = _build_backup_info(fpath, fname, day_date)
            if info is None:
                continue
            if cutoff_ts is not None and info.backup_time.timestamp() < cutoff_ts:
                continue
            infos.append(info)

    infos.sort(key=lambda x: x.backup_time, reverse=True)
    return infos


def _build_backup_info(
    path: str, filename: str, day_date: datetime.date | None = None
) -> BackupInfo | None:
    """从备份文件构造 BackupInfo（解析元数据 + 取正文预览）。"""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None
    metadata = parse_backup_header(content) or {}
    body = _strip_header(content, metadata is not None)

    # 解析备份时间：优先元数据 timestamp（最权威），回退文件名 + day_date，
    # 最后用文件 mtime
    backup_time: datetime.datetime | None = None
    ts = metadata.get("timestamp")
    if ts:
        try:
            backup_time = datetime.datetime.fromtimestamp(int(ts))
        except (ValueError, OSError):
            backup_time = None
    if backup_time is None:
        backup_time = _parse_backup_time_from_filename(filename, day_date)
    if backup_time is None:
        try:
            backup_time = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        except OSError:
            return None

    original_path = metadata.get("original_path") or None
    if original_path == "":
        original_path = None

    cursor_pos = (
        int(metadata.get("cursor_row", 1) or 1),
        int(metadata.get("cursor_col", 1) or 1),
    )
    try:
        scroll_offset = float(metadata.get("scroll_offset", 0.0) or 0.0)
    except (TypeError, ValueError):
        scroll_offset = 0.0

    preview = _first_body_line(body, max_chars=80)
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        size_bytes = 0

    return BackupInfo(
        backup_path=path,
        backup_time=backup_time,
        original_path=original_path,
        doc_id=metadata.get("doc_id"),
        cursor_pos=cursor_pos,
        scroll_offset=scroll_offset,
        preview=preview,
        size_bytes=size_bytes,
        is_untitled=original_path is None,
    )


def _strip_header(content: str, has_header: bool) -> str:
    """去除元数据块，返回正文部分。"""
    if not has_header:
        return content
    end_idx = content.find(_META_END)
    if end_idx == -1:
        return content
    body_start = end_idx + len(_META_END)
    # 跳过元数据块后的空行
    body = content[body_start:]
    return body.lstrip("\n")


def _parse_backup_time_from_filename(
    filename: str, day_date: datetime.date | None = None
) -> datetime.datetime | None:
    """从备份文件名解析 HHMMSS 时间部分（结合当天日期目录）。

    day_date 为 None 时退化为 today()，可能跨日丢失日期精度（仅作为 fallback）。
    """
    # 文件名格式：{name}-{HHMMSS}.md.autosave
    base = filename[: -len(BACKUP_SUFFIX)] if filename.endswith(BACKUP_SUFFIX) else filename
    m = re.search(r"-(\d{6})$", base)
    if not m:
        return None
    time_str = m.group(1)
    try:
        hh, mm, ss = int(time_str[:2]), int(time_str[2:4]), int(time_str[4:6])
        base_date = day_date or datetime.date.today()
        return datetime.datetime.combine(
            base_date, datetime.time(hh, mm, ss)
        )
    except (ValueError, OSError):
        return None


def _first_body_line(body: str, max_chars: int = 80) -> str:
    """提取正文第一行非空文本作为预览。"""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            # 去除 Markdown 标题 / 列表前缀，得到纯文本预览
            cleaned = re.sub(r"^\s{0,3}#{1,6}\s+", "", stripped)
            cleaned = re.sub(r"^\s{0,3}[-*+]\s+", "", cleaned)
            cleaned = re.sub(r"^\s{0,3}\d+\.\s+", "", cleaned)
            cleaned = cleaned.strip()
            if cleaned:
                return cleaned[:max_chars]
    return "(空文档)"


def parse_backup_file(path: str) -> tuple[dict[str, Any] | None, str]:
    """解析备份文件 -> (元数据 dict | None, 正文 str)。

    用于恢复时读取备份内容。失败时返回 (None, "")。
    """
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None, ""
    metadata = parse_backup_header(content)
    body = _strip_header(content, metadata is not None)
    return metadata, body


# ---------------------------------------------------------------------------
# 过期清理
# ---------------------------------------------------------------------------

def cleanup_old_backups(settings: dict[str, Any]) -> int:
    """清理过期备份，返回删除的文件数。

    - 已命名文档备份：保留最近 backup_retention_days 天（默认 30）
    - 未命名草稿备份：保留最近 recover_untitled_days 天（默认 7，短于已命名）
    - 整天子目录过期后整体删除（含其中所有备份）

    清理失败时静默跳过，不影响其他备份。
    """
    root = get_backup_root(settings)
    if not os.path.isdir(root):
        return 0

    named_days = int(settings.get("backup_retention_days", 30))
    untitled_days = int(settings.get("recover_untitled_days", 7))
    now = time.time()
    deleted_count = 0

    for day_entry in list(os.listdir(root)):
        day_dir = os.path.join(root, day_entry)
        if not os.path.isdir(day_dir):
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day_entry):
            continue
        # 解析日期目录的时间戳
        try:
            day_date = datetime.datetime.strptime(day_entry, "%Y-%m-%d")
            day_ts = day_date.timestamp()
        except ValueError:
            continue
        age_days = (now - day_ts) / 86400

        # 若目录已老于「最长保留期」（已命名 30 天 / 未命名 7 天，取较长者），
        # 整目录删除（两种备份混合时按已命名保留期处理，避免误删）
        if age_days > max(named_days, untitled_days):
            try:
                shutil.rmtree(day_dir)
                # 估算删除数（无法精确计数，按目录非空估算）
                deleted_count += 1
            except OSError:
                pass
            continue

        # 介于两者之间：删除未命名草稿（已命名文档仍保留）
        if age_days > untitled_days:
            for fname in list(os.listdir(day_dir)):
                if not fname.endswith(BACKUP_SUFFIX):
                    continue
                fpath = os.path.join(day_dir, fname)
                # 通过元数据判断是否为未命名草稿
                metadata, _ = parse_backup_file(fpath)
                if metadata is None:
                    continue
                original = metadata.get("original_path") or ""
                if not original:
                    try:
                        os.remove(fpath)
                        deleted_count += 1
                    except OSError:
                        pass

    return deleted_count


def delete_backup(path: str) -> bool:
    """删除单个备份文件。返回是否删除成功。"""
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def is_large_content(content: str) -> bool:
    """判断内容是否达到大文件优化阈值（>10MB）。"""
    return len(content.encode("utf-8")) > LARGE_FILE_THRESHOLD
