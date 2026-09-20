"""``services.git.porcelain`` 解析器测试：固定输入 → 确定模型。

这些解析函数的输入来自 git 的机器可读输出，格式细节（分隔符、路径中的
空格与中文、重命名的双路径）一旦解析错就会让整个 Git 面板显示错误的文件，
因此用固定样本把每条规则钉死。
"""

from __future__ import annotations

import pytest

from services.git.models import ChangeKind
from services.git.porcelain import (
    LOG_FORMAT,
    parse_ahead_behind,
    parse_branches,
    parse_log,
    parse_name_status,
    parse_numstat,
    parse_status,
)

# ---------------------------------------------------------------------------
# status --porcelain=v2 -z
# ---------------------------------------------------------------------------

_STATUS_V2 = "\0".join(
    [
        "# branch.oid e3f0b0b8a2b1c4d5e6f708192a3b4c5d6e7f8091",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +2 -1",
        "1 M. N... 100644 100644 100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb staged_only.md",
        "1 .M N... 100644 100644 100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb worktree_only.md",
        "1 MM N... 100644 100644 100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb both.md",
        "2 R. N... 100644 100644 100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb R100 renamed_new.md",
        "renamed_old.md",
        "u UU N... 100644 100644 100644 100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb cccccccccccccccccccccccccccc"
        "cccccccccccc conflicted.md",
        "? 未跟踪 文件.md",
        "! build/out.log",
        "",
    ]
)


def test_parse_status_headers_and_counts():
    st = parse_status(_STATUS_V2)
    assert st.branch == "main"
    assert st.upstream == "origin/main"
    assert st.ahead == 2
    assert st.behind == 1
    assert st.head_sha == "e3f0b0b"
    assert not st.detached
    assert not st.unborn


def test_parse_status_splits_staged_and_unstaged():
    st = parse_status(_STATUS_V2)
    staged_paths = [e.path for e in st.staged]
    unstaged_paths = [e.path for e in st.unstaged]
    assert "staged_only.md" in staged_paths
    assert "staged_only.md" not in unstaged_paths
    assert "worktree_only.md" in unstaged_paths
    assert "worktree_only.md" not in staged_paths
    # 同时有暂存与未暂存改动的文件出现在两个分区（VS Code 行为）
    assert "both.md" in staged_paths
    assert "both.md" in unstaged_paths


def test_parse_status_rename_uses_following_token_as_orig_path():
    st = parse_status(_STATUS_V2)
    entry = st.entry_for("renamed_new.md")
    assert entry is not None
    assert entry.orig_path == "renamed_old.md"
    assert entry.index_kind == ChangeKind.RENAMED
    assert entry.display_name == "renamed_old.md → renamed_new.md"
    assert entry.letter == "R"


def test_parse_status_conflict_and_untracked_and_ignored():
    st = parse_status(_STATUS_V2)
    assert [e.path for e in st.conflicted] == ["conflicted.md"]
    conf = st.conflicted[0]
    assert conf.is_staged and conf.is_unstaged
    assert conf.letter == "!"
    assert [e.path for e in st.untracked] == ["未跟踪 文件.md"]
    assert st.untracked[0].letter == "U"
    # 忽略项不计入待提交变更数，也不出现在任何分区
    assert st.change_count == 6
    assert "build/out.log" not in [e.path for e in st.unstaged]
    assert "build/out.log" not in [e.path for e in st.staged]
    assert not st.is_clean


def test_parse_status_detached_and_unborn():
    detached = parse_status(
        "\0".join(
            [
                "# branch.oid 1234567890abcdef",
                "# branch.head (detached)",
                "",
            ]
        )
    )
    assert detached.detached and detached.branch is None

    unborn = parse_status("# branch.oid (initial)\0# branch.head main\0")
    assert unborn.unborn and unborn.head_sha is None and unborn.branch == "main"


def test_parse_status_handles_path_with_spaces_and_unicode():
    payload = (
        "1 M. N... 100644 100644 100644 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 目录/带 空格 的 文件.md\0"
    )
    st = parse_status(payload)
    assert st.entries[0].path == "目录/带 空格 的 文件.md"
    assert st.entries[0].basename == "带 空格 的 文件.md"
    assert st.entries[0].dirname == "目录"


def test_parse_ahead_behind_variants():
    assert parse_ahead_behind("+3 -5") == (3, 5)
    assert parse_ahead_behind("ahead 2, behind 4") == (2, 4)
    assert parse_ahead_behind("[gone]") == (0, 0)
    assert parse_ahead_behind("") == (0, 0)


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def _log_record(**kw) -> str:
    fields = {
        "sha": "a" * 40,
        "short": "aaaaaaa",
        "an": "张三",
        "ae": "z@example.com",
        "at": "1700000000",
        "parents": "b" * 40,
        "refs": "HEAD -> main, origin/main",
        "subject": "修复：解析器崩溃",
        "body": "详细说明\n第二行",
    }
    fields.update(kw)
    return "\x1f".join(
        [
            fields["sha"],
            fields["short"],
            fields["an"],
            fields["ae"],
            fields["at"],
            fields["parents"],
            fields["refs"],
            fields["subject"],
            fields["body"],
        ]
    ) + "\x1e"


def test_parse_log_fields_and_body_with_newlines():
    commits = parse_log(_log_record())
    assert len(commits) == 1
    c = commits[0]
    assert c.short_sha == "aaaaaaa"
    assert c.author_name == "张三"
    assert c.timestamp == 1700000000
    assert c.summary == "修复：解析器崩溃"
    assert c.body == "详细说明\n第二行"
    assert c.refs == ("HEAD -> main", "origin/main")
    assert not c.is_merge


def test_parse_log_multiple_records_and_merge_commit():
    text = _log_record(subject="one") + _log_record(subject="two", parents="p1 p2")
    commits = parse_log(text)
    assert [c.summary for c in commits] == ["one", "two"]
    assert commits[1].is_merge


def test_parse_log_tolerates_body_containing_separator():
    # 正文字段用 maxsplit 保护：多余的 \x1f 留在正文里而不破坏前 8 列
    record = "\x1f".join(
        ["f" * 40, "fff", "n", "e@x", "1", "", "", "s", "body\x1fwith\x1fsep"]
    ) + "\x1e"
    commits = parse_log(record)
    assert commits[0].summary == "s"
    assert "body" in commits[0].body


def test_log_format_roundtrip_shape():
    # LOG_FORMAT 必须与解析器的分隔符约定一致（防止两边各改一处）
    assert "%x1f" in LOG_FORMAT and "%x1e" in LOG_FORMAT
    assert parse_log("") == []


# ---------------------------------------------------------------------------
# name-status / numstat
# ---------------------------------------------------------------------------


def test_parse_name_status_with_rename_and_delete():
    payload = "\0".join(
        [
            "M", "a.md",
            "A", "new.md",
            "D", "gone.md",
            "R100", "old/x.md", "new/x.md",
            "",
        ]
    )
    files = parse_name_status(payload)
    kinds = [(f.letter, f.path) for f in files]
    assert kinds == [
        ("M", "a.md"),
        ("A", "new.md"),
        ("D", "gone.md"),
        ("R", "new/x.md"),
    ]
    renamed = files[-1]
    assert renamed.orig_path == "old/x.md"
    assert renamed.display_name == "old/x.md → new/x.md"


def test_parse_numstat_handles_binary_and_rename():
    payload = "\0".join(
        [
            "10\t2\ta.md",
            "-\t-\timg.png",
            "3\t0\t", "old/b.md", "new/b.md",
            "",
        ]
    )
    counts = parse_numstat(payload)
    assert counts["a.md"] == (10, 2)
    assert counts["img.png"] == (None, None)
    assert counts["new/b.md"] == (3, 0)


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------


def test_parse_branches_local_and_remote():
    sep = "\x1f"
    rec = "\x1e"
    payload = (
        f"main{sep}*{sep}abc1234{sep}origin/main{sep}ahead 1, behind 2{sep}1700000000"
        f"{sep}最新提交{sep}refs/heads/main{rec}"
        f"feature/x{sep}{sep}def5678{sep}{sep}{sep}1699999999{sep}特性分支{sep}refs/heads/feature/x{rec}"
        f"origin/main{sep}{sep}abc1234{sep}{sep}{sep}1700000000{sep}最新提交{sep}refs/remotes/origin/main{rec}"
        f"origin/HEAD{sep}{sep}abc1234{sep}{sep}{sep}1700000000{sep}x{sep}refs/remotes/origin/HEAD{rec}"
        f"{sep}"
    )
    branches = parse_branches(payload)
    names = [b.name for b in branches]
    assert "origin/HEAD" not in names
    assert names == ["main", "feature/x", "origin/main"]
    cur = branches[0]
    assert cur.current and not cur.is_remote
    assert cur.upstream == "origin/main"
    assert (cur.ahead, cur.behind) == (1, 2)
    # 含 "/" 的**本地**分支不能被误判为远端
    assert not branches[1].is_remote
    assert branches[2].is_remote


def test_branch_time_text_is_relative():
    sep = "\x1f"
    rec = "\x1e"
    payload = f"main{sep}*{sep}abc{sep}{sep}{sep}1700000000{sep}s{sep}refs/heads/main{rec}"
    b = parse_branches(payload)[0]
    assert b.time_text  # 非空即可（具体文案随时间推移变化）
    assert b.sha == "abc"


@pytest.mark.parametrize("payload", ["", "\0", "\0\0\0"])
def test_parse_status_empty_payloads(payload):
    st = parse_status(payload)
    assert st.entries == [] or all(e.path for e in st.entries)
