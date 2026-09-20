"""``services.git.difftext`` 测试：统一 diff 解析、分栏对齐、冲突扫描、大文件保护。"""

from __future__ import annotations

from services.git.difftext import (
    build_split_rows,
    parse_single_file_diff,
    parse_unified_diff,
    scan_merge_markers,
    synthetic_added_diff,
)
from services.git.models import DiffLineKind


SINGLE_FILE_DIFF = """\
diff --git a/notes.md b/notes.md
index 1111111..2222222 100644
--- a/notes.md
+++ b/notes.md
@@ -1,4 +1,4 @@
 # 标题
-旧的一行
+新的一行
 第三行
 第四行
"""

MULTI_FILE_DIFF = """\
diff --git a/a.md b/a.md
index 1111111..2222222 100644
--- a/a.md
+++ b/a.md
@@ -1,3 +1,4 @@
 line1
+inserted
 line2
 line3
diff --git a/img.png b/img.png
index 3333333..4444444 100644
Binary files a/img.png and b/img.png differ
diff --git a/old.md b/new.md
similarity index 90%
rename from old.md
rename to new.md
index 5555555..6666666 100644
--- a/old.md
+++ b/new.md
@@ -1 +1 @@
-x
+y
"""

NEW_FILE_DIFF = """\
diff --git a/fresh.md b/fresh.md
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/fresh.md
@@ -0,0 +1,3 @@
+alpha
+beta
+gamma
"""

DELETED_FILE_DIFF = """\
diff --git a/dead.md b/dead.md
deleted file mode 100644
index abc1234..0000000
--- a/dead.md
+++ /dev/null
@@ -1,2 +0,0 @@
-gone1
-gone2
"""


def test_parse_single_file_diff_line_numbers():
    fd = parse_single_file_diff(SINGLE_FILE_DIFF, "notes.md")
    assert fd.path == "notes.md"
    assert (fd.additions, fd.deletions) == (1, 1)
    assert len(fd.hunks) == 1
    hunk = fd.hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (1, 4, 1, 4)
    kinds = [ln.kind for ln in hunk.lines]
    assert kinds == [
        DiffLineKind.CONTEXT,
        DiffLineKind.REMOVED,
        DiffLineKind.ADDED,
        DiffLineKind.CONTEXT,
        DiffLineKind.CONTEXT,
    ]
    # 行号：上下文双编号，删除只占旧号，新增只占新号
    assert hunk.lines[0].old_no == 1 and hunk.lines[0].new_no == 1
    assert hunk.lines[1].old_no == 2 and hunk.lines[1].new_no is None
    assert hunk.lines[2].old_no is None and hunk.lines[2].new_no == 2
    assert hunk.lines[3].old_no == 3 and hunk.lines[3].new_no == 3


def test_parse_multi_file_diff_including_binary_and_rename():
    files = parse_unified_diff(MULTI_FILE_DIFF)
    assert [f.path for f in files] == ["a.md", "img.png", "new.md"]
    assert files[1].is_binary and files[1].hunks == []
    assert files[2].is_renamed and files[2].old_path == "old.md"
    assert files[2].display_path == "old.md → new.md"
    assert files[0].additions == 1 and files[0].deletions == 0


def test_parse_new_and_deleted_file_flags():
    new = parse_unified_diff(NEW_FILE_DIFF)[0]
    assert new.is_new and not new.is_deleted
    assert new.additions == 3
    dead = parse_unified_diff(DELETED_FILE_DIFF)[0]
    assert dead.is_deleted and dead.deletions == 2


def test_parse_ignores_no_newline_marker():
    text = (
        "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n"
    )
    fd = parse_unified_diff(text)[0]
    assert len(fd.hunks[0].lines) == 2


def test_parse_truncates_oversized_diff():
    body = "\n".join(f"+line{i}" for i in range(50))
    text = f"diff --git a/big.md b/big.md\n--- a/big.md\n+++ b/big.md\n@@ -0,0 +1,50 @@\n{body}\n"
    fd = parse_unified_diff(text, max_lines=10)[0]
    assert fd.truncated
    assert fd.line_count <= 11  # 块头 + 被保留的行


def test_build_split_rows_pairs_removed_with_added():
    fd = parse_single_file_diff(SINGLE_FILE_DIFF, "notes.md")
    rows = build_split_rows(fd)
    assert rows[0].is_hunk_header and rows[0].header_text.startswith("@@")
    # 第 2 行（索引 1）是被改动的行：左侧删除、右侧新增，行号各自独立
    changed = rows[2]
    assert changed.left_kind == DiffLineKind.REMOVED
    assert changed.left_no == 2 and changed.left_text == "旧的一行"
    assert changed.right_kind == DiffLineKind.ADDED
    assert changed.right_no == 2 and changed.right_text == "新的一行"


def test_build_split_rows_pads_uneven_change_block():
    text = (
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1,3 +1,1 @@\n"
        "-one\n-two\n-three\n+replacement\n"
    )
    fd = parse_unified_diff(text)[0]
    rows = build_split_rows(fd)
    body = [r for r in rows if not r.is_hunk_header]
    assert len(body) == 3  # 3 删 1 增 → 3 行，多出的左侧配空行
    assert body[0].left_no == 1 and body[0].right_no == 1
    assert body[1].left_no == 2 and body[1].right_no is None
    assert body[1].right_kind == DiffLineKind.EMPTY
    assert body[2].left_no == 3 and body[2].right_text == ""


def test_build_split_rows_respects_max_rows():
    body = "\n".join(f"+line{i}" for i in range(100))
    text = f"diff --git a/big b/big\n--- a/big\n+++ b/big\n@@ -0,0 +1,100 @@\n{body}\n"
    fd = parse_unified_diff(text)[0]
    rows = build_split_rows(fd, max_rows=20)
    assert len(rows) == 20


def test_synthetic_added_diff_marks_all_lines_added():
    fd = synthetic_added_diff("new.md", "一\n二\n三")
    assert fd.is_new
    assert fd.additions == 3 and fd.deletions == 0
    hunk = fd.hunks[0]
    assert hunk.old_start == 0 and hunk.new_count == 3
    assert all(ln.kind == DiffLineKind.ADDED for ln in hunk.lines)
    assert [ln.new_no for ln in hunk.lines] == [1, 2, 3]
    # 同样可参与分栏对齐
    assert len(build_split_rows(fd)) == 4


def test_synthetic_added_diff_truncates():
    fd = synthetic_added_diff("big.md", "\n".join(str(i) for i in range(100)), max_lines=10)
    assert fd.truncated and fd.additions == 10


def test_scan_merge_markers_locates_all_markers():
    text = "\n".join(
        [
            "line1",
            "<<<<<<< HEAD",
            "ours",
            "=======",
            "theirs",
            ">>>>>>> feature/x",
            "line7",
            "<<<<<<< HEAD",
            "a",
            "||||||| base",
            "b",
            "=======",
            "c",
            ">>>>>>> other",
        ]
    )
    m = scan_merge_markers(text, "conflict.md")
    assert m.has_conflict and m.count == 2
    assert m.start_lines == [2, 8]
    assert m.first_line == 2
    # 分隔线含 diff3 风格的 ||||||| 基线与两处 =======
    assert 4 in m.separator_lines and 12 in m.separator_lines
    assert m.end_lines == [6, 14]


def test_scan_merge_markers_ignores_lookalikes():
    m = scan_merge_markers("<<<<<<<< not a marker\n>>>>>>>> nope\n")
    assert not m.has_conflict


def test_parse_empty_text_returns_placeholder_for_single_file():
    fd = parse_single_file_diff("", "x.md")
    assert fd.path == "x.md"
    assert fd.hunks == []
    assert fd.is_empty
