"""恢复模块：启动扫描、草稿去重、恢复会话状态。

与 services/backup.py 配合：backup 负责写入 / 扫描单条备份；recovery 负责会话
级别的恢复逻辑——「上次退出后是否有未保存文档」「按文档去重展示」「启动时
非阻塞提示」等。

恢复会话机制：
- 应用退出时（on_disconnect / atexit）将「当前会话备份路径列表」写入 sentinel 文件
  ``last_session.json``（位于备份根目录）。
- 启动时若发现 sentinel 存在，列出其中记录的备份路径，过滤掉已被用户主动删除的，
  作为「可恢复草稿」展示。
- 用户点击「打开」后在新标签页加载备份内容；用户点击「关闭」或全部恢复后，
  sentinel 文件被删除，下次启动不再提示。
- 手动恢复入口（设置面板按钮）则跳过 sentinel，直接展示最近 N 天的全量备份。

依赖项：
- 标准库（os / json / time / datetime）
- services.backup（BackupInfo / scan_backups / parse_backup_file / get_backup_root）

对外接口：
- LAST_SESSION_SENTINEL：sentinel 文件名常量
- write_last_session_sentinel(settings, backup_paths) -> bool：退出时写入会话备份清单
- read_last_session_sentinel(settings) -> list[str] | None：启动时读取会话备份清单
- clear_last_session_sentinel(settings) -> None：删除 sentinel（恢复完成 / 用户跳过）
- find_recoverable_on_startup(settings) -> list[BackupInfo]：启动时扫描可恢复草稿
- find_recent_backups(settings, days=None) -> list[BackupInfo]：手动恢复入口扫描全量
- dedupe_backups_by_doc(infos) -> list[BackupInfo]：按文档 ID 去重，每组保留最新
- load_backup_content(path) -> tuple[str, tuple[int, int], float] | None：读取备份内容
  返回 (markdown 正文, 光标位置, 滚动偏移)；失败返回 None
"""

from __future__ import annotations

import datetime
import json
import os
import time
from typing import Any

from services.backup import (
    BackupInfo,
    get_backup_root,
    parse_backup_file,
    scan_backups,
)
# _build_backup_info 延迟导入（见 find_recoverable_on_startup），避免循环引用

# sentinel 文件名（位于备份根目录）
LAST_SESSION_SENTINEL = "last_session.json"

# sentinel 最大有效期（秒）：超过 24 小时的 sentinel 视为过期，启动时不再提示
_SENTINEL_MAX_AGE = 24 * 3600


def _sentinel_path(settings: dict[str, Any]) -> str:
    """sentinel 文件绝对路径（位于备份根目录）。"""
    return os.path.join(get_backup_root(settings), LAST_SESSION_SENTINEL)


def write_last_session_sentinel(
    settings: dict[str, Any], backup_paths: list[str]
) -> bool:
    """退出时写入「上次会话备份清单」sentinel。

    - backup_paths：本次会话产生的备份文件绝对路径列表（用于下次启动时定位）。
    - sentinel 同时记录写入时间戳，超过 24 小时被视为过期（避免长期未启动后
      误提示历史会话）。
    - 失败时返回 False（不影响退出流程）。
    """
    if not backup_paths:
        # 无备份时清掉旧 sentinel，避免下次启动误提示
        clear_last_session_sentinel(settings)
        return True
    payload = {
        "written_at": time.time(),
        "iso_time": datetime.datetime.now().isoformat(timespec="seconds"),
        "backup_paths": list(backup_paths),
    }
    try:
        path = _sentinel_path(settings)
        # 原子写入：临时文件 → 替换
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def read_last_session_sentinel(
    settings: dict[str, Any],
) -> list[str] | None:
    """启动时读取 sentinel，返回其中记录的备份路径列表。

    - sentinel 不存在 / 已过期 / 损坏 → 返回 None（视为无可恢复草稿）。
    - sentinel 存在但其中记录的备份文件已被删除 → 过滤掉这些路径，返回剩余。
    - 返回空列表表示 sentinel 有效但所有备份已删除（调用方可清掉 sentinel）。
    """
    path = _sentinel_path(settings)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    # 过期检查
    written_at = payload.get("written_at", 0)
    try:
        written_at = float(written_at)
    except (TypeError, ValueError):
        return None
    if time.time() - written_at > _SENTINEL_MAX_AGE:
        return None
    paths = payload.get("backup_paths") or []
    if not isinstance(paths, list):
        return None
    # 过滤掉已被删除的备份文件
    return [p for p in paths if isinstance(p, str) and os.path.isfile(p)]


def clear_last_session_sentinel(settings: dict[str, Any]) -> None:
    """删除 sentinel（恢复完成 / 用户跳过 / 写入空备份时调用）。"""
    try:
        path = _sentinel_path(settings)
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def find_recoverable_on_startup(
    settings: dict[str, Any],
) -> list[BackupInfo]:
    """启动时扫描可恢复草稿：基于 sentinel 找出上次会话的未保存文档。

    返回按时间降序排列的 BackupInfo 列表（同文档多次备份只保留最新）。
    返回空列表表示无可恢复草稿（此时 sentinel 也应被清除）。
    """
    paths = read_last_session_sentinel(settings)
    if not paths:
        # sentinel 不存在或已过期 / 已清空 → 顺带清理
        if paths is None:
            clear_last_session_sentinel(settings)
        return []
    infos: list[BackupInfo] = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        # 复用 _build_backup_info（延迟导入避免循环）
        from services.backup import _build_backup_info  # noqa: PLC0415
        # 从路径反推 day_date
        parent = os.path.basename(os.path.dirname(p))
        day_date: datetime.date | None = None
        try:
            day_date = datetime.date.fromisoformat(parent)
        except ValueError:
            pass
        info = _build_backup_info(p, os.path.basename(p), day_date)
        if info is not None:
            infos.append(info)
    # 清理 sentinel（无论用户是否恢复，启动提示只触发一次）
    clear_last_session_sentinel(settings)
    # 按时间降序排序后再去重：dedupe_backups_by_doc 保留每组首个，
    # 需先排序确保最新备份排在前面（sentinel 中路径顺序不保证按时间）
    infos.sort(key=lambda x: x.backup_time, reverse=True)
    return dedupe_backups_by_doc(infos)


def find_recent_backups(
    settings: dict[str, Any], days: int | None = None
) -> list[BackupInfo]:
    """手动恢复入口：扫描最近 N 天的全量备份（不限于上次会话）。

    - days=None 时使用 settings.backup_retention_days 作为扫描范围。
    - 按文档 ID 去重，每个文档保留最新备份，便于用户快速定位。
    """
    if days is None:
        days = int(settings.get("backup_retention_days", 30))
    infos = scan_backups(settings, max_age_days=days)
    return dedupe_backups_by_doc(infos)


def dedupe_backups_by_doc(infos: list[BackupInfo]) -> list[BackupInfo]:
    """按文档 ID 去重，每个 doc_id 保留最新备份（已按时间降序排好）。

    无 doc_id 的备份视为独立条目，不去重（避免误删不同未命名草稿）。
    """
    seen: set[str] = set()
    result: list[BackupInfo] = []
    for info in infos:
        if not info.doc_id:
            result.append(info)
            continue
        if info.doc_id in seen:
            continue
        seen.add(info.doc_id)
        result.append(info)
    return result


def load_backup_content(
    path: str,
) -> tuple[str, tuple[int, int], float] | None:
    """读取备份文件内容供恢复使用。

    返回 (markdown 正文, (cursor_row, cursor_col), scroll_offset)。
    失败（文件不存在 / 解析失败）返回 None。
    """
    metadata, body = parse_backup_file(path)
    if not body:
        return None
    cursor = (
        int((metadata or {}).get("cursor_row", 1) or 1),
        int((metadata or {}).get("cursor_col", 1) or 1),
    )
    try:
        scroll = float((metadata or {}).get("scroll_offset", 0.0) or 0.0)
    except (TypeError, ValueError):
        scroll = 0.0
    return body, cursor, scroll
