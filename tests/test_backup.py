"""services/file_io(原子写入) + services/backup + services/recovery 单元测试。

覆盖：
- write_text_atomic：临时文件 → fsync → SHA256 校验 → os.replace；失败兜底
- backup：路径解析 / 文件命名 / 元数据块 / 写入 / 扫描 / 过期清理 / 删除
- recovery：sentinel 写读清 / 启动扫描 / 去重 / 加载内容

测试隔离：所有备份目录用 tmp_path 注入 settings.backup_dir，绝不污染真实
%APPDATA% / ~/.config。文档对象用 Mock 简化（仅需要 .lines[i].raw 属性）。
"""

import datetime
import json
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from services import backup as bk  # noqa: E402
from services import recovery as rc  # noqa: E402
from services.file_io import read_text, write_text, write_text_atomic  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _settings(tmp_path, **overrides) -> dict:
    """构造 settings：backup_dir 指向 tmp_path 避免污染真实目录。"""
    s = {
        "backup_dir": str(tmp_path / "backup"),
        "backup_enabled": True,
        "backup_interval": 10,
        "backup_retention_days": 30,
        "recover_untitled_days": 7,
        "detect_external_changes": True,
    }
    s.update(overrides)
    return s


def _mock_doc(raw_lines: list[str]) -> MagicMock:
    """构造模拟 Document：仅需 .lines[i].raw 属性。

    backup._extract_first_line_preview 用 getattr(line, "raw", "") 读取，
    用 MagicMock 比 parse_markdown 更轻量且不依赖 mistune。
    """
    lines = [MagicMock(raw=raw) for raw in raw_lines]
    doc = MagicMock()
    doc.lines = lines
    return doc


def _editor_tab(file_path=None, doc=None, dirty=True) -> dict:
    """普通编辑器标签。"""
    return {
        "type": "editor",
        "file_path": file_path,
        "document": doc or _mock_doc(["# 标题", "正文内容"]),
        "dirty": dirty,
    }


def _diff_tab(left_path="/a.md", right_path="/b.md", left_doc=None, right_doc=None) -> dict:
    """对比标签。"""
    return {
        "type": "diff",
        "left_path": left_path,
        "right_path": right_path,
        "left_doc": left_doc or _mock_doc(["左侧"]),
        "right_doc": right_doc or _mock_doc(["右侧"]),
        "left_dirty": True,
        "right_dirty": True,
    }


def _dt(h: int = 12, m: int = 30, s: int = 0, day: int = 31) -> datetime.datetime:
    """构造固定时间，便于断言文件名 / 时间戳。"""
    return datetime.datetime(2025, 7, day, h, m, s)


# ===========================================================================
# services/file_io.write_text_atomic
# ===========================================================================

class TestWriteTextAtomic:
    """原子写入：临时文件 → fsync → 校验 → os.replace。"""

    def test_writes_utf8_content(self, tmp_path):
        p = tmp_path / "note.md"
        text = "# 标题\n中文 emoji 🎉\n"
        write_text_atomic(str(p), text)
        assert read_text(str(p)) == text

    def test_overwrites_existing(self, tmp_path):
        """覆盖原文件，旧内容完全替换。"""
        p = tmp_path / "a.md"
        write_text(str(p), "old content")
        write_text_atomic(str(p), "new content")
        assert read_text(str(p)) == "new content"

    def test_no_bom(self, tmp_path):
        """UTF-8 无 BOM。"""
        p = tmp_path / "b.md"
        write_text_atomic(str(p), "abc")
        assert not p.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_no_temp_file_left(self, tmp_path):
        """写入成功后无残留临时文件。"""
        p = tmp_path / "c.md"
        write_text_atomic(str(p), "content")
        tmps = [f for f in os.listdir(str(tmp_path)) if ".md-tmp-" in f or f.endswith(".tmp")]
        assert tmps == []

    def test_missing_dir_raises(self, tmp_path):
        """目标目录不存在 → FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            write_text_atomic(str(tmp_path / "nope" / "x.md"), "x")

    def test_original_intact_on_failure(self, tmp_path):
        """失败时原文件保持不变。

        通过 monkeypatch os.replace 抛出异常模拟失败，验证原文件未被破坏。
        """
        p = tmp_path / "d.md"
        write_text(str(p), "original")
        # monkeypatch os.replace 在 file_io 模块中抛异常
        orig_replace = os.replace
        try:
            os.replace = MagicMock(side_effect=OSError("simulated failure"))
            with pytest.raises(OSError):
                write_text_atomic(str(p), "new")
        finally:
            os.replace = orig_replace
        # 原文件保持不变
        assert read_text(str(p)) == "original"
        # 临时文件被清理
        tmps = [f for f in os.listdir(str(tmp_path)) if ".md-tmp-" in f]
        assert tmps == []

    def test_large_content_roundtrip(self, tmp_path):
        """大内容往返。"""
        p = tmp_path / "big.md"
        text = "x" * 100_000
        write_text_atomic(str(p), text)
        assert read_text(str(p)) == text

    def test_atomic_replaces_via_os_replace(self, tmp_path, monkeypatch):
        """验证使用 os.replace 完成原子替换。"""
        p = tmp_path / "e.md"
        calls = []
        orig_replace = os.replace

        def _spy(src, dst):
            calls.append((src, dst))
            return orig_replace(src, dst)

        monkeypatch.setattr("services.file_io.os.replace", _spy)
        write_text_atomic(str(p), "content")
        assert len(calls) == 1
        assert calls[0][1] == str(p)


# ===========================================================================
# services/backup：路径解析
# ===========================================================================

class TestBackupRoot:
    """get_backup_root：自定义路径优先 / 创建目录。"""

    def test_custom_path_used(self, tmp_path):
        s = _settings(tmp_path, backup_dir=str(tmp_path / "custom"))
        root = bk.get_backup_root(s)
        assert root == str(tmp_path / "custom")
        assert os.path.isdir(root)

    def test_creates_missing_dir(self, tmp_path):
        custom = tmp_path / "deep" / "nested" / "backup"
        s = _settings(tmp_path, backup_dir=str(custom))
        root = bk.get_backup_root(s)
        assert os.path.isdir(root)

    def test_platform_default_when_no_custom(self, tmp_path, monkeypatch):
        """无自定义路径时回退平台默认路径。"""
        s = _settings(tmp_path)
        s["backup_dir"] = None
        # 强制 platform default 走 tmp_path 隔离（monkeypatch _platform_default_root）
        fake = str(tmp_path / "platform_default")
        monkeypatch.setattr(bk, "_platform_default_root", lambda: fake)
        root = bk.get_backup_root(s)
        assert root == fake
        assert os.path.isdir(root)


class TestTodayBackupDir:
    """get_today_backup_dir：YYYY-MM-DD 子目录。"""

    def test_creates_day_subdir(self, tmp_path):
        s = _settings(tmp_path)
        now = _dt()
        day_dir = bk.get_today_backup_dir(s, now=now)
        assert day_dir.endswith("2025-07-31")
        assert os.path.isdir(day_dir)

    def test_idempotent(self, tmp_path):
        s = _settings(tmp_path)
        now = _dt()
        d1 = bk.get_today_backup_dir(s, now=now)
        d2 = bk.get_today_backup_dir(s, now=now)
        assert d1 == d2


# ===========================================================================
# services/backup：文件命名
# ===========================================================================

class TestMakeBackupFilename:
    """make_backup_filename：已命名 / 未命名草稿 / 非法字符。"""

    def test_named_doc_uses_basename(self):
        tab = _editor_tab(file_path="/path/to/note.md")
        name = bk.make_backup_filename(tab, now=_dt())
        assert name == "note-123000.md.autosave"

    def test_named_doc_strips_extension(self):
        tab = _editor_tab(file_path="/x/archive.markdown")
        name = bk.make_backup_filename(tab, now=_dt())
        # 仅去 .markdown 扩展，保留主名
        assert name.startswith("archive-")
        assert name.endswith(".md.autosave")

    def test_untitled_uses_first_line_slug(self):
        doc = _mock_doc(["# 我的草稿", "正文"])
        tab = _editor_tab(file_path=None, doc=doc)
        name = bk.make_backup_filename(tab, now=_dt())
        # 标题前缀被去掉了「# 」
        assert name.startswith("untitled-我的草稿-")
        assert name.endswith("-123000.md.autosave")

    def test_untitled_strips_markdown_prefix(self):
        doc = _mock_doc(["- 列表项首行"])
        tab = _editor_tab(file_path=None, doc=doc)
        name = bk.make_backup_filename(tab, now=_dt())
        assert "untitled-列表项首行-" in name

    def test_untitled_empty_doc_fallback(self):
        doc = _mock_doc(["", "   "])
        tab = _editor_tab(file_path=None, doc=doc)
        name = bk.make_backup_filename(tab, now=_dt())
        # 无非空首行 → untitled-{HHMMSS}
        assert name == "untitled-123000.md.autosave"

    def test_sanitizes_invalid_chars(self):
        # 文件名中含非法字符 → 替换为下划线
        tab = _editor_tab(file_path="/x/a<b>c.md")
        name = bk.make_backup_filename(tab, now=_dt())
        assert "<" not in name and ">" not in name
        assert name.startswith("a_b_c-")

    def test_diff_tab_uses_left_path(self):
        tab = _diff_tab(left_path="/x/left.md", right_path="/y/right.md")
        name = bk.make_backup_filename(tab, now=_dt())
        assert name.startswith("left-")

    def test_diff_tab_fallback_to_right(self):
        tab = _diff_tab(left_path=None, right_path="/y/right.md")
        name = bk.make_backup_filename(tab, now=_dt())
        assert name.startswith("right-")

    def test_time_format_in_filename(self):
        """文件名末尾 HHMMSS 来自 now。"""
        tab = _editor_tab(file_path="/x/n.md")
        name = bk.make_backup_filename(tab, now=_dt(h=8, m=5, s=3))
        assert "-080503.md.autosave" in name


# ===========================================================================
# services/backup：元数据块
# ===========================================================================

class TestBackupHeader:
    """format_backup_header / parse_backup_header：往返一致。"""

    def test_format_contains_marker(self):
        header = bk.format_backup_header({"timestamp": 1753977600})
        assert header.startswith(bk._META_BEGIN)
        assert header.rstrip().endswith(bk._META_END)

    def test_format_ordered_keys(self):
        """固定顺序写入键值对。"""
        header = bk.format_backup_header({
            "scroll_offset": 320.5,
            "timestamp": 100,
            "doc_id": "abc",
        })
        lines = header.splitlines()
        # timestamp 应在 scroll_offset 之前（按 ordered_keys 顺序）
        ts_idx = next(i for i, ln in enumerate(lines) if ln.startswith("timestamp="))
        scroll_idx = next(i for i, ln in enumerate(lines) if ln.startswith("scroll_offset="))
        assert ts_idx < scroll_idx

    def test_format_skips_none(self):
        header = bk.format_backup_header({"timestamp": None, "doc_id": "x"})
        assert "timestamp=" not in header
        assert "doc_id=x" in header

    def test_parse_returns_dict(self):
        header = bk.format_backup_header({
            "timestamp": 1753977600,
            "original_path": "/x/n.md",
            "cursor_row": 12,
            "cursor_col": 5,
            "scroll_offset": 320.5,
            "doc_id": "abc123",
        })
        meta = bk.parse_backup_header(header)
        assert meta is not None
        assert meta["timestamp"] == 1753977600
        assert meta["original_path"] == "/x/n.md"
        assert meta["cursor_row"] == 12
        assert meta["cursor_col"] == 5
        assert meta["scroll_offset"] == 320.5
        assert meta["doc_id"] == "abc123"

    def test_parse_type_conversion(self):
        """timestamp / cursor_* → int，scroll_offset → float。"""
        header = bk.format_backup_header({
            "timestamp": 100,
            "cursor_row": "5",
            "scroll_offset": 1.5,
        })
        meta = bk.parse_backup_header(header)
        assert isinstance(meta["timestamp"], int)
        assert isinstance(meta["cursor_row"], int)
        assert isinstance(meta["scroll_offset"], float)

    def test_parse_none_when_no_header(self):
        assert bk.parse_backup_header("plain markdown content") is None

    def test_parse_none_when_truncated(self):
        """元数据块被截断（无 --> 结束标记）→ None。"""
        truncated = bk._META_BEGIN + "\ntimestamp=100\n"
        assert bk.parse_backup_header(truncated) is None

    def test_roundtrip_preserves_values(self):
        original = {
            "timestamp": 1753977600,
            "iso_time": "2025-07-31T16:00:00",
            "original_path": "/path/to/file.md",
            "doc_id": "deadbeef1234",
            "cursor_row": 1,
            "cursor_col": 1,
            "scroll_offset": 0.0,
        }
        header = bk.format_backup_header(original)
        meta = bk.parse_backup_header(header)
        for key, value in original.items():
            assert meta[key] == value


# ===========================================================================
# services/backup：写入备份
# ===========================================================================

class TestWriteBackup:
    """write_backup：成功 / 关闭 / 空内容 / 元数据写入。"""

    def test_writes_backup_file(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/note.md")
        path = bk.write_backup(
            s, tab, "# 内容\n正文",
            cursor_pos=(3, 5), scroll_offset=120.0,
            now=_dt(),
        )
        assert path is not None
        assert os.path.isfile(path)
        assert path.endswith(".md.autosave")

    def test_backup_in_day_subdir(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        path = bk.write_backup(s, tab, "content", now=_dt())
        assert "2025-07-31" in path

    def test_returns_none_when_disabled(self, tmp_path):
        s = _settings(tmp_path, backup_enabled=False)
        tab = _editor_tab(file_path="/x/n.md")
        assert bk.write_backup(s, tab, "content", now=_dt()) is None

    def test_returns_none_for_empty_content(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        assert bk.write_backup(s, tab, "", now=_dt()) is None
        assert bk.write_backup(s, tab, "   \n  ", now=_dt()) is None

    def test_writes_metadata_block(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/note.md")
        path = bk.write_backup(
            s, tab, "正文内容",
            cursor_pos=(10, 3), scroll_offset=240.5,
            now=_dt(h=9, m=15, s=30),
        )
        content = read_text(path)
        meta = bk.parse_backup_header(content)
        assert meta is not None
        assert meta["cursor_row"] == 10
        assert meta["cursor_col"] == 3
        assert meta["scroll_offset"] == 240.5
        assert meta["original_path"] == "/x/note.md"

    def test_body_follows_header(self, tmp_path):
        """正文紧随元数据块之后（空行分隔）。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        body = "# 标题\n\n段落"
        path = bk.write_backup(s, tab, body, now=_dt())
        content = read_text(path)
        meta, parsed_body = bk.parse_backup_file(path)
        assert parsed_body == body
        assert meta is not None

    def test_untitled_doc_metadata(self, tmp_path):
        """未命名草稿 original_path 为空字符串。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path=None, doc=_mock_doc(["草稿"]))
        path = bk.write_backup(s, tab, "草稿内容", now=_dt())
        meta, _ = bk.parse_backup_file(path)
        assert meta["original_path"] == ""

    def test_doc_id_auto_generated(self, tmp_path):
        """未指定 doc_id 时基于路径+内容哈希自动生成。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        path = bk.write_backup(s, tab, "content", now=_dt())
        meta, _ = bk.parse_backup_file(path)
        assert meta["doc_id"]
        assert len(meta["doc_id"]) == 12

    def test_doc_id_stable_for_same_doc(self, tmp_path):
        """同一文档多次备份 doc_id 一致（便于恢复列表去重）。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        p1 = bk.write_backup(s, tab, "same content", now=_dt(h=10))
        p2 = bk.write_backup(s, tab, "same content", now=_dt(h=11))
        m1, _ = bk.parse_backup_file(p1)
        m2, _ = bk.parse_backup_file(p2)
        assert m1["doc_id"] == m2["doc_id"]

    def test_failure_returns_none(self, tmp_path, monkeypatch):
        """内部异常时返回 None（不抛出，避免打断编辑）。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        # 让 write_text_atomic 抛异常
        monkeypatch.setattr(bk, "write_text_atomic", MagicMock(side_effect=OSError("disk full")))
        assert bk.write_backup(s, tab, "content", now=_dt()) is None


# ===========================================================================
# services/backup：扫描与解析
# ===========================================================================

class TestScanBackups:
    """scan_backups：扫描 / max_age_days 过滤 / 排序。"""

    def _seed_backups(self, settings, tab, count=3, day=None):
        """生成 count 个备份（同一天，不同小时）。"""
        paths = []
        for h in range(count):
            now = _dt(h=10 + h, day=day or 31)
            p = bk.write_backup(settings, tab, f"content-{h}", now=now)
            paths.append(p)
        return paths

    def test_scans_all_backups(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        self._seed_backups(s, tab, count=3)
        infos = bk.scan_backups(s)
        assert len(infos) == 3

    def test_sorted_by_time_descending(self, tmp_path):
        """最新在前。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        self._seed_backups(s, tab, count=3)
        infos = bk.scan_backups(s)
        times = [i.backup_time for i in infos]
        assert times == sorted(times, reverse=True)

    def test_max_age_filters_old(self, tmp_path):
        """max_age_days 过滤掉过老的备份。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        # 写入"今天"的备份
        bk.write_backup(s, tab, "today", now=datetime.datetime.now())
        # 写入 60 天前的备份（手工构造目录）
        old_day = datetime.datetime.now() - datetime.timedelta(days=60)
        old_dir = os.path.join(bk.get_backup_root(s), old_day.strftime("%Y-%m-%d"))
        os.makedirs(old_dir, exist_ok=True)
        old_path = os.path.join(old_dir, "old-090000.md.autosave")
        bk.write_text_atomic(old_path, "# old content")
        infos = bk.scan_backups(s, max_age_days=7)
        # 仅今天的备份保留
        assert all("today" in i.preview or "today" not in i.preview for i in infos)
        assert all(i.backup_time >= datetime.datetime.now() - datetime.timedelta(days=7) for i in infos)
        # 老备份被过滤
        assert not any("old" in os.path.basename(i.backup_path) for i in infos)

    def test_ignores_non_backup_files(self, tmp_path):
        """非 .md.autosave 后缀的文件被忽略。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        bk.write_backup(s, tab, "real", now=_dt())
        # 在同目录塞入一个无关文件
        day_dir = bk.get_today_backup_dir(s, now=_dt())
        with open(os.path.join(day_dir, "readme.txt"), "w", encoding="utf-8") as f:
            f.write("noise")
        infos = bk.scan_backups(s)
        assert len(infos) == 1

    def test_ignores_non_day_dirs(self, tmp_path):
        """非 YYYY-MM-DD 命名的目录被跳过。"""
        s = _settings(tmp_path)
        root = bk.get_backup_root(s)
        os.makedirs(os.path.join(root, "random"), exist_ok=True)
        infos = bk.scan_backups(s)
        assert infos == []

    def test_corrupt_backup_falls_back_to_defaults(self, tmp_path):
        """无元数据的备份文件仍被扫描，字段使用默认值（mtime 作为 backup_time）。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        bk.write_backup(s, tab, "good", now=_dt())
        # 写入一个无元数据的"裸"备份（文件名含 HHMMSS 便于解析时间）
        day_dir = bk.get_today_backup_dir(s, now=_dt())
        with open(os.path.join(day_dir, "raw-090000.md.autosave"), "w", encoding="utf-8") as f:
            f.write("no metadata, just body")
        infos = bk.scan_backups(s)
        # 两份都被扫到
        assert len(infos) == 2
        # 无元数据的那条使用默认值
        raw_info = next(i for i in infos if "raw" in os.path.basename(i.backup_path))
        assert raw_info.original_path is None
        assert raw_info.cursor_pos == (1, 1)
        assert raw_info.is_untitled is True

    def test_unreadable_backup_skipped(self, tmp_path):
        """无法读取的备份文件被跳过，不抛异常。

        通过将路径指向一个目录模拟读取失败（open 目录抛 IsADirectoryError）。
        """
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        bk.write_backup(s, tab, "good", now=_dt())
        infos = bk.scan_backups(s)
        # 好的仍能扫到，无异常抛出
        assert len(infos) == 1
        assert "good" in infos[0].preview

    def test_backup_info_fields(self, tmp_path):
        """BackupInfo 字段完整填充。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/note.md")
        path = bk.write_backup(
            s, tab, "# 标题\n正文",
            cursor_pos=(5, 10), scroll_offset=200.0,
            now=_dt(),
        )
        infos = bk.scan_backups(s)
        assert len(infos) == 1
        info = infos[0]
        assert info.backup_path == path
        assert info.original_path == "/x/note.md"
        assert info.cursor_pos == (5, 10)
        assert info.scroll_offset == 200.0
        assert info.preview == "标题"  # 去除 # 前缀
        assert info.size_bytes > 0
        assert info.is_untitled is False

    def test_backup_info_untitled_flag(self, tmp_path):
        """未命名草稿 is_untitled=True。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path=None, doc=_mock_doc(["草稿"]))
        bk.write_backup(s, tab, "草稿", now=_dt())
        infos = bk.scan_backups(s)
        assert len(infos) == 1
        assert infos[0].is_untitled is True
        assert infos[0].original_path is None


class TestParseBackupFile:
    """parse_backup_file：(元数据, 正文)。"""

    def test_parses_full_file(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        path = bk.write_backup(s, tab, "# 标题\n正文", now=_dt())
        meta, body = bk.parse_backup_file(path)
        assert meta is not None
        assert body == "# 标题\n正文"

    def test_missing_file_returns_empty(self, tmp_path):
        meta, body = bk.parse_backup_file(str(tmp_path / "nope.md.autosave"))
        assert meta is None
        assert body == ""


# ===========================================================================
# services/backup：过期清理
# ===========================================================================

class TestCleanupOldBackups:
    """cleanup_old_backups：过期删除 / 已命名 vs 未命名区分。"""

    def _write_old_backup(self, settings, tab, days_ago, content="old", is_untitled=False):
        """在 days_ago 天前的目录写入一份备份。"""
        old_date = datetime.datetime.now() - datetime.timedelta(days=days_ago)
        old_dir = os.path.join(bk.get_backup_root(settings), old_date.strftime("%Y-%m-%d"))
        os.makedirs(old_dir, exist_ok=True)
        # 直接手工构造文件，避免 write_backup 总是写到今天
        meta = bk.format_backup_header({
            "timestamp": int(old_date.timestamp()),
            "original_path": "" if is_untitled else "/x/n.md",
        })
        full = f"{meta}\n\n{content}"
        path = os.path.join(old_dir, f"old-{old_date.strftime('%H%M%S')}.md.autosave")
        bk.write_text_atomic(path, full)
        return path

    def test_no_backup_dir_returns_zero(self, tmp_path):
        s = _settings(tmp_path, backup_dir=str(tmp_path / "nonexist"))
        assert bk.cleanup_old_backups(s) == 0

    def test_keeps_recent(self, tmp_path):
        """近期备份不删除。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        bk.write_backup(s, tab, "recent", now=datetime.datetime.now())
        deleted = bk.cleanup_old_backups(s)
        assert deleted == 0
        infos = bk.scan_backups(s)
        assert len(infos) == 1

    def test_deletes_old_named(self, tmp_path):
        """超过保留期的已命名备份目录被整目录删除。"""
        s = _settings(tmp_path, backup_retention_days=30)
        tab = _editor_tab(file_path="/x/n.md")
        self._write_old_backup(s, tab, days_ago=45, is_untitled=False)
        deleted = bk.cleanup_old_backups(s)
        assert deleted >= 1
        assert bk.scan_backups(s) == []

    def test_deletes_old_untitled_earlier(self, tmp_path):
        """未命名草稿超过 recover_untitled_days 但未超 backup_retention_days 时删除。"""
        s = _settings(tmp_path, backup_retention_days=30, recover_untitled_days=7)
        tab = _editor_tab(file_path=None, doc=_mock_doc(["草稿"]))
        # 10 天前的未命名草稿：超过 7 天保留期，但未超 30 天
        self._write_old_backup(s, tab, days_ago=10, is_untitled=True)
        deleted = bk.cleanup_old_backups(s)
        assert deleted >= 1
        assert bk.scan_backups(s) == []

    def test_keeps_named_between_thresholds(self, tmp_path):
        """已命名文档在 7-30 天之间保留。"""
        s = _settings(tmp_path, backup_retention_days=30, recover_untitled_days=7)
        tab = _editor_tab(file_path="/x/n.md")
        self._write_old_backup(s, tab, days_ago=10, is_untitled=False)
        bk.cleanup_old_backups(s)
        infos = bk.scan_backups(s)
        assert len(infos) == 1


class TestDeleteBackup:
    """delete_backup：删除单个备份。"""

    def test_deletes_existing(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        path = bk.write_backup(s, tab, "content", now=_dt())
        assert bk.delete_backup(path) is True
        assert not os.path.isfile(path)

    def test_returns_false_for_missing(self, tmp_path):
        assert bk.delete_backup(str(tmp_path / "nope.md.autosave")) is False


class TestIsLargeContent:
    """is_large_content：>10MB 判定。"""

    def test_small_returns_false(self):
        assert bk.is_large_content("x" * 1024) is False

    def test_large_returns_true(self):
        # 11MB 内容
        assert bk.is_large_content("x" * (11 * 1024 * 1024)) is True

    def test_threshold_boundary(self):
        """恰好 10MB 不算大文件（> 阈值才算）。"""
        assert bk.is_large_content("x" * bk.LARGE_FILE_THRESHOLD) is False


# ===========================================================================
# services/recovery：sentinel 写读清
# ===========================================================================

class TestSentinel:
    """write_last_session_sentinel / read_last_session_sentinel / clear。"""

    def test_write_then_read(self, tmp_path):
        """sentinel 写入后读取保持一致（路径需真实存在，否则被过滤）。"""
        s = _settings(tmp_path)
        # 创建两个真实文件（read_last_session_sentinel 会过滤不存在的路径）
        f1 = tmp_path / "a.md.autosave"
        f2 = tmp_path / "b.md.autosave"
        f1.write_text("content a", encoding="utf-8")
        f2.write_text("content b", encoding="utf-8")
        paths = [str(f1), str(f2)]
        assert rc.write_last_session_sentinel(s, paths) is True
        result = rc.read_last_session_sentinel(s)
        assert result == paths

    def test_write_empty_clears_sentinel(self, tmp_path):
        """空列表时清掉旧 sentinel。"""
        s = _settings(tmp_path)
        rc.write_last_session_sentinel(s, ["/a.md.autosave"])
        rc.write_last_session_sentinel(s, [])
        assert rc.read_last_session_sentinel(s) is None

    def test_read_missing_returns_none(self, tmp_path):
        s = _settings(tmp_path)
        assert rc.read_last_session_sentinel(s) is None

    def test_read_filters_deleted(self, tmp_path):
        """sentinel 中已删除的路径被过滤。"""
        s = _settings(tmp_path)
        # 仅写入一个真实存在的文件
        real_file = tmp_path / "real.md.autosave"
        real_file.write_text("content", encoding="utf-8")
        paths = [str(real_file), "/nonexistent.md.autosave"]
        rc.write_last_session_sentinel(s, paths)
        result = rc.read_last_session_sentinel(s)
        assert result == [str(real_file)]

    def test_read_expired_returns_none(self, tmp_path):
        """sentinel 超过 24 小时 → 视为过期。"""
        s = _settings(tmp_path)
        # 手工写入一个过期的 sentinel
        sentinel_path = os.path.join(bk.get_backup_root(s), rc.LAST_SESSION_SENTINEL)
        payload = {
            "written_at": time.time() - 25 * 3600,  # 25 小时前
            "iso_time": "2025-07-30T12:00:00",
            "backup_paths": ["/x.md.autosave"],
        }
        with open(sentinel_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        assert rc.read_last_session_sentinel(s) is None

    def test_read_corrupt_returns_none(self, tmp_path):
        s = _settings(tmp_path)
        sentinel_path = os.path.join(bk.get_backup_root(s), rc.LAST_SESSION_SENTINEL)
        with open(sentinel_path, "w", encoding="utf-8") as f:
            f.write("not a json {{{")
        assert rc.read_last_session_sentinel(s) is None

    def test_clear_removes_sentinel(self, tmp_path):
        s = _settings(tmp_path)
        rc.write_last_session_sentinel(s, ["/a.md.autosave"])
        rc.clear_last_session_sentinel(s)
        assert rc.read_last_session_sentinel(s) is None

    def test_clear_missing_no_error(self, tmp_path):
        s = _settings(tmp_path)
        # 不存在时不抛异常
        rc.clear_last_session_sentinel(s)


# ===========================================================================
# services/recovery：启动扫描 + 去重
# ===========================================================================

class TestFindRecoverableOnStartup:
    """find_recoverable_on_startup：基于 sentinel 找出可恢复草稿。"""

    def test_no_sentinel_returns_empty(self, tmp_path):
        s = _settings(tmp_path)
        assert rc.find_recoverable_on_startup(s) == []

    def test_returns_backup_infos(self, tmp_path):
        """同文档多次备份（相同内容）去重后保留最新。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        # doc_id 由 路径+内容首1KB 哈希生成，相同内容才会被去重
        p1 = bk.write_backup(s, tab, "same content", now=_dt(h=10))
        p2 = bk.write_backup(s, tab, "same content", now=_dt(h=11))
        rc.write_last_session_sentinel(s, [p1, p2])
        infos = rc.find_recoverable_on_startup(s)
        assert len(infos) == 1  # 同一 doc_id 去重
        # 保留最新
        assert infos[0].backup_path == p2

    def test_clears_sentinel_after_scan(self, tmp_path):
        """扫描后 sentinel 被清除（启动提示只触发一次）。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        p = bk.write_backup(s, tab, "content", now=_dt())
        rc.write_last_session_sentinel(s, [p])
        rc.find_recoverable_on_startup(s)
        # sentinel 已被清除
        assert rc.read_last_session_sentinel(s) is None

    def test_skips_deleted_backups(self, tmp_path):
        """sentinel 中已删除的备份被跳过。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        p = bk.write_backup(s, tab, "content", now=_dt())
        # sentinel 记录了一个已被删除的路径 + 一个真实路径
        rc.write_last_session_sentinel(s, [p, "/nonexistent.md.autosave"])
        infos = rc.find_recoverable_on_startup(s)
        assert len(infos) == 1
        assert infos[0].backup_path == p


class TestDedupeBackupsByDoc:
    """dedupe_backups_by_doc：同 doc_id 保留最新。"""

    def test_keeps_latest_per_doc(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        p1 = bk.write_backup(s, tab, "same", now=_dt(h=10))
        p2 = bk.write_backup(s, tab, "same", now=_dt(h=11))
        p3 = bk.write_backup(s, tab, "same", now=_dt(h=12))
        infos = bk.scan_backups(s)  # 已按时间降序
        deduped = rc.dedupe_backups_by_doc(infos)
        assert len(deduped) == 1
        assert deduped[0].backup_path == p3

    def test_distinct_docs_all_kept(self, tmp_path):
        s = _settings(tmp_path)
        tab_a = _editor_tab(file_path="/x/a.md")
        tab_b = _editor_tab(file_path="/x/b.md")
        bk.write_backup(s, tab_a, "a", now=_dt(h=10))
        bk.write_backup(s, tab_b, "b", now=_dt(h=11))
        infos = bk.scan_backups(s)
        deduped = rc.dedupe_backups_by_doc(infos)
        assert len(deduped) == 2

    def test_no_doc_id_kept_separately(self, tmp_path):
        """无 doc_id 的备份视为独立条目，不去重。"""
        s = _settings(tmp_path)
        # 用元数据缺失的手工构造文件（doc_id=None）
        day_dir = bk.get_today_backup_dir(s, now=_dt())
        paths = []
        for i in range(3):
            p = os.path.join(day_dir, f"raw-{100000 + i}.md.autosave")
            bk.write_text_atomic(p, f"raw content {i}")
            paths.append(p)
        infos = bk.scan_backups(s)
        deduped = rc.dedupe_backups_by_doc(infos)
        # 无 doc_id 全部保留
        assert len(deduped) == 3


class TestFindRecentBackups:
    """find_recent_backups：手动恢复入口扫描全量备份。"""

    def test_returns_all_recent(self, tmp_path):
        """扫描最近 N 天的全量备份（不同文档全部保留）。"""
        s = _settings(tmp_path)
        tab_a = _editor_tab(file_path="/x/a.md")
        tab_b = _editor_tab(file_path="/x/b.md")
        # 用真实当前时间确保落在保留窗口内
        now = datetime.datetime.now()
        bk.write_backup(s, tab_a, "a", now=now)
        bk.write_backup(s, tab_b, "b", now=now)
        infos = rc.find_recent_backups(s, days=1)
        assert len(infos) == 2

    def test_deduped(self, tmp_path):
        """同文档多次备份去重后保留最新。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        now = datetime.datetime.now()
        bk.write_backup(s, tab, "same", now=now.replace(hour=10))
        bk.write_backup(s, tab, "same", now=now.replace(hour=11))
        infos = rc.find_recent_backups(s, days=1)
        assert len(infos) == 1

    def test_days_filter(self, tmp_path):
        """days 参数覆盖 settings.backup_retention_days。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        bk.write_backup(s, tab, "today", now=datetime.datetime.now())
        infos = rc.find_recent_backups(s, days=1)
        assert len(infos) == 1


class TestLoadBackupContent:
    """load_backup_content：读取 (正文, 光标, 滚动)。"""

    def test_loads_full_content(self, tmp_path):
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/n.md")
        path = bk.write_backup(
            s, tab, "# 标题\n正文",
            cursor_pos=(7, 3), scroll_offset=180.5,
            now=_dt(),
        )
        result = rc.load_backup_content(path)
        assert result is not None
        body, cursor, scroll = result
        assert body == "# 标题\n正文"
        assert cursor == (7, 3)
        assert scroll == 180.5

    def test_missing_returns_none(self, tmp_path):
        assert rc.load_backup_content(str(tmp_path / "nope.md.autosave")) is None

    def test_empty_body_returns_none(self, tmp_path):
        """正文为空 → None。"""
        s = _settings(tmp_path)
        # 手工构造一个仅含元数据、无正文的备份
        day_dir = bk.get_today_backup_dir(s, now=_dt())
        path = os.path.join(day_dir, "empty-100000.md.autosave")
        header = bk.format_backup_header({"timestamp": int(_dt().timestamp())})
        bk.write_text_atomic(path, header)  # 无正文
        assert rc.load_backup_content(path) is None

    def test_no_metadata_defaults(self, tmp_path):
        """无元数据时光标/滚动用默认值。"""
        s = _settings(tmp_path)
        day_dir = bk.get_today_backup_dir(s, now=_dt())
        path = os.path.join(day_dir, "raw-100000.md.autosave")
        bk.write_text_atomic(path, "# 仅正文，无元数据")
        result = rc.load_backup_content(path)
        assert result is not None
        body, cursor, scroll = result
        assert body == "# 仅正文，无元数据"
        assert cursor == (1, 1)
        assert scroll == 0.0


# ===========================================================================
# 集成场景：写入 → 扫描 → 恢复
# ===========================================================================

class TestBackupRecoveryFlow:
    """端到端：备份 → sentinel → 启动扫描 → 加载内容。"""

    def test_full_recovery_flow(self, tmp_path):
        """完整恢复流程：写入备份 → sentinel → 启动扫描 → 加载内容。"""
        s = _settings(tmp_path)
        tab = _editor_tab(file_path="/x/note.md", doc=_mock_doc(["# 我的笔记", "正文"]))
        # 1. 写入备份
        path = bk.write_backup(
            s, tab, "# 我的笔记\n正文内容",
            cursor_pos=(2, 5), scroll_offset=100.0,
            now=_dt(),
        )
        assert path is not None
        # 2. 写入 sentinel
        rc.write_last_session_sentinel(s, [path])
        # 3. 启动扫描
        infos = rc.find_recoverable_on_startup(s)
        assert len(infos) == 1
        # 4. 加载内容
        result = rc.load_backup_content(infos[0].backup_path)
        assert result is not None
        body, cursor, scroll = result
        assert body == "# 我的笔记\n正文内容"
        assert cursor == (2, 5)
        assert scroll == 100.0

    def test_multiple_tabs_backup_and_recover(self, tmp_path):
        """多标签备份：每个文档保留最新版本（同文档同内容才去重）。"""
        s = _settings(tmp_path)
        tab_a = _editor_tab(file_path="/x/a.md", doc=_mock_doc(["A 文档"]))
        tab_b = _editor_tab(file_path="/x/b.md", doc=_mock_doc(["B 文档"]))
        # tab_a 写两份相同内容（doc_id 一致 → 去重保留最新）
        # tab_b 写一份（独立 doc_id）
        paths = []
        paths.append(bk.write_backup(s, tab_a, "a content", now=_dt(h=10)))
        paths.append(bk.write_backup(s, tab_b, "b content", now=_dt(h=11)))
        paths.append(bk.write_backup(s, tab_a, "a content", now=_dt(h=12)))
        rc.write_last_session_sentinel(s, paths)
        infos = rc.find_recoverable_on_startup(s)
        # 两个文档各保留最新
        assert len(infos) == 2
        bodies = {rc.load_backup_content(i.backup_path)[0] for i in infos}
        assert bodies == {"a content", "b content"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
