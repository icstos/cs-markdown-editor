"""services/file_io + services/file_ops 单元测试。

覆盖 read_text / write_text UTF-8 往返，以及 file_ops 的名称校验、
新建/重命名/副本/删除（用 tmp_path 隔离）。
不依赖 UI 层。reveal_in_explorer 因依赖系统进程，仅测错误路径。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from services import file_ops  # noqa: E402
from services.file_io import read_text, write_text  # noqa: E402


# ---------------- file_io ----------------
def test_read_write_utf8_roundtrip(tmp_path):
    p = tmp_path / "note.md"
    text = "# 标题\n\n中文内容 emoji 🎉\n"
    write_text(str(p), text)
    assert read_text(str(p)) == text


def test_write_text_overwrites(tmp_path):
    p = tmp_path / "a.md"
    write_text(str(p), "old")
    write_text(str(p), "new")
    assert read_text(str(p)) == "new"


def test_read_text_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_text(str(tmp_path / "nope.md"))


def test_read_text_utf8_bomless(tmp_path):
    """写入不应带 BOM。"""
    p = tmp_path / "b.md"
    write_text(str(p), "abc")
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


# ---------------- sanitize_name ----------------
def test_sanitize_name_strips():
    assert file_ops.sanitize_name("  name  ") == "name"


def test_sanitize_name_removes_invalid_chars():
    # 输入 a<b>c:"d/e\e|f?g*h：去除非法字符后保留 a b c d e e f g h（e\e 中两个 e）
    assert file_ops.sanitize_name('a<b>c:"d/e\\e|f?g*h') == "abcdeefgh"


def test_sanitize_name_strips_trailing_dot_space():
    assert file_ops.sanitize_name("name.  ") == "name"


def test_sanitize_name_empty_after_strip():
    assert file_ops.sanitize_name('<>') == ""


# ---------------- ensure_md_extension ----------------
def test_ensure_md_no_extension():
    assert file_ops.ensure_md_extension("note") == "note.md"


def test_ensure_md_already_md():
    assert file_ops.ensure_md_extension("note.md") == "note.md"


def test_ensure_md_already_markdown():
    assert file_ops.ensure_md_extension("note.markdown") == "note.markdown"


def test_ensure_md_replaces_other_extension():
    assert file_ops.ensure_md_extension("note.txt") == "note.md"


# ---------------- name_exists ----------------
def test_name_exists_true(tmp_path):
    (tmp_path / "exists.md").write_text("", encoding="utf-8")
    assert file_ops.name_exists(str(tmp_path), "exists.md")


def test_name_exists_false(tmp_path):
    assert not file_ops.name_exists(str(tmp_path), "nope.md")


# ---------------- create_file ----------------
def test_create_file_creates_empty_md(tmp_path):
    p = file_ops.create_file(str(tmp_path), "note")
    assert p.endswith("note.md")
    assert os.path.exists(p)
    assert read_text(p) == ""


def test_create_file_empty_name_raises(tmp_path):
    with pytest.raises(ValueError):
        file_ops.create_file(str(tmp_path), "   ")


def test_create_file_conflict_raises(tmp_path):
    file_ops.create_file(str(tmp_path), "note")
    with pytest.raises(FileExistsError):
        file_ops.create_file(str(tmp_path), "note")


def test_create_file_sanitizes(tmp_path):
    p = file_ops.create_file(str(tmp_path), "a<b")
    assert p.endswith("ab.md")


# ---------------- create_folder ----------------
def test_create_folder_creates_dir(tmp_path):
    p = file_ops.create_folder(str(tmp_path), "sub")
    assert os.path.isdir(p)


def test_create_folder_conflict_raises(tmp_path):
    file_ops.create_folder(str(tmp_path), "sub")
    with pytest.raises(FileExistsError):
        file_ops.create_folder(str(tmp_path), "sub")


# ---------------- rename_path ----------------
def test_rename_file(tmp_path):
    src = file_ops.create_file(str(tmp_path), "old")
    new = file_ops.rename_path(src, "new")
    assert not os.path.exists(src)
    assert os.path.exists(new)
    assert new.endswith("new.md")


def test_rename_to_conflict_raises(tmp_path):
    a = file_ops.create_file(str(tmp_path), "a")
    file_ops.create_file(str(tmp_path), "b")
    with pytest.raises(FileExistsError):
        file_ops.rename_path(a, "b")


def test_rename_empty_name_raises(tmp_path):
    src = file_ops.create_file(str(tmp_path), "old")
    with pytest.raises(ValueError):
        file_ops.rename_path(src, "  ")


def test_rename_same_path_no_conflict(tmp_path):
    """重命名为自身名称应允许（不报冲突）。"""
    src = file_ops.create_file(str(tmp_path), "old")
    new = file_ops.rename_path(src, "old")
    assert os.path.exists(new)


# ---------------- duplicate_file ----------------
def test_duplicate_file_creates_copy(tmp_path):
    src = file_ops.create_file(str(tmp_path), "note")
    write_text(src, "content")
    dup = file_ops.duplicate_file(src)
    assert dup != src
    assert os.path.exists(dup)
    assert read_text(dup) == "content"


def test_duplicate_file_auto_increment(tmp_path):
    src = file_ops.create_file(str(tmp_path), "note")
    dup1 = file_ops.duplicate_file(src)
    dup2 = file_ops.duplicate_file(src)
    assert dup1 != dup2 != src
    assert os.path.exists(dup1)
    assert os.path.exists(dup2)


def test_duplicate_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        file_ops.duplicate_file(str(tmp_path / "nope.md"))


# ---------------- delete_path ----------------
def test_delete_file(tmp_path):
    p = file_ops.create_file(str(tmp_path), "note")
    file_ops.delete_path(p)
    assert not os.path.exists(p)


def test_delete_folder(tmp_path):
    p = file_ops.create_folder(str(tmp_path), "sub")
    file_ops.delete_path(p)
    assert not os.path.exists(p)


def test_delete_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        file_ops.delete_path(str(tmp_path / "nope"))


# ---------------- reveal_in_explorer（仅错误路径）----------------
def test_reveal_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        file_ops.reveal_in_explorer(str(tmp_path / "nope"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
