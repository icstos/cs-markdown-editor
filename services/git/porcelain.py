"""porcelain 输出解析：把 git 的机器可读输出转成数据模型。

本模块**只做解析**，不执行任何命令（命令由 ``repository`` 发起）。
所有解析函数都是纯函数，可直接用固定文本做单元测试。

覆盖的命令格式：
- ``git status --porcelain=v2 --branch -z``  → :func:`parse_status`
- ``git log --pretty=<LOG_FORMAT>``         → :func:`parse_log`
- ``git diff-tree --name-status -z``        → :func:`parse_name_status`
- ``git diff-tree --numstat -z``            → :func:`parse_numstat`
- ``git branch --format=<BRANCH_FORMAT>``   → :func:`parse_branches`
"""

from __future__ import annotations

import re

from services.git.models import (
    BranchInfo,
    ChangeKind,
    CommitInfo,
    FileChange,
    GitStatus,
    StatusEntry,
    kind_from_xy,
)

__all__ = [
    "BRANCH_FORMAT",
    "FLAG_FORMAT",
    "LOG_FORMAT",
    "parse_ahead_behind",
    "parse_branches",
    "parse_log",
    "parse_name_status",
    "parse_numstat",
    "parse_status",
]

#: log 记录格式：字段分隔 \x1f，记录分隔 \x1e（避免提交信息里的换行破坏解析）
LOG_FORMAT = "%H%x1f%h%x1f%an%x1f%ae%x1f%at%x1f%P%x1f%D%x1f%s%x1f%b%x1e"

#: branch 记录格式（同上分隔符策略）。
#: 末列 ``%(refname)`` 是完整引用名，用于区分本地（``refs/heads/``）与远端
#: （``refs/remotes/``）——本地分支名本身也可能含 ``/``（如 ``feature/x``），
#: 仅靠名字无法判断。
#:
#: ⚠ ``git branch --format`` / ``git for-each-ref`` 的格式化语言**不支持** ``%xNN``
#: （那是 ``git log --pretty`` 的能力），写 ``%x1f`` 会原样输出字面量。
#: 因此这里直接把真实的 U+001F / U+001E 控制字符嵌进格式串——参数以列表传递，
#: 不经过 shell，控制字符可安全作为字段分隔符。
_SEP = "\x1f"
_REC = "\x1e"
BRANCH_FORMAT = (
    f"%(refname:short){_SEP}%(HEAD){_SEP}%(objectname){_SEP}%(upstream:short)"
    f"{_SEP}%(upstream:track,nobracket){_SEP}%(committerdate:unix)"
    f"{_SEP}%(contents:subject){_SEP}%(refname){_REC}"
)

#: diff-tree 名称状态格式：用 -z 时无需分隔符（每条记录自身以 NUL 结尾）
FLAG_FORMAT = ""

# ``#{ahead 1, behind 2}`` 解析（track 已去括号）
_TRACK_RE = re.compile(r"ahead\s+(\d+)", re.IGNORECASE)
_BEHIND_RE = re.compile(r"behind\s+(\d+)", re.IGNORECASE)

# name-status 的字母 → ChangeKind
_NAME_STATUS_MAP = {
    "A": ChangeKind.ADDED,
    "M": ChangeKind.MODIFIED,
    "D": ChangeKind.DELETED,
    "R": ChangeKind.RENAMED,
    "C": ChangeKind.COPIED,
    "T": ChangeKind.TYPE_CHANGED,
    "U": ChangeKind.CONFLICTED,
    "X": ChangeKind.MODIFIED,
}


def parse_status(text: str) -> GitStatus:
    """解析 ``git status --porcelain=v2 --branch -z`` 输出。

    ``-z`` 模式下记录以 NUL 分隔；重命名记录（``2``）的**原路径**跟在
    当前记录之后，作为独立的一个 NUL 段——这是该格式最容易踩的细节。
    """
    status = GitStatus()
    tokens = (text or "").split("\0")
    i = 0
    total = len(tokens)
    while i < total:
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        head = tok[0]
        if tok.startswith("# "):
            _apply_header(tok, status)
        elif head == "1":
            parts = tok.split(" ", 8)
            if len(parts) < 9:
                continue
            status.entries.append(
                StatusEntry(
                    path=parts[8],
                    index_kind=kind_from_xy(parts[1][:1]),
                    worktree_kind=kind_from_xy(parts[1][1:2]),
                )
            )
        elif head == "2":
            parts = tok.split(" ", 9)
            if len(parts) < 10:
                continue
            path = parts[9]
            orig = tokens[i] if i < total else ""
            i += 1
            status.entries.append(
                StatusEntry(
                    path=path,
                    orig_path=orig or None,
                    index_kind=kind_from_xy(parts[1][:1]),
                    worktree_kind=kind_from_xy(parts[1][1:2]),
                )
            )
        elif head == "u":
            parts = tok.split(" ", 10)
            if len(parts) < 11:
                continue
            status.entries.append(
                StatusEntry(
                    path=parts[10],
                    index_kind=kind_from_xy(parts[1][:1]),
                    worktree_kind=kind_from_xy(parts[1][1:2]),
                    is_conflicted=True,
                )
            )
        elif head == "?":
            status.entries.append(
                StatusEntry(path=tok[2:], worktree_kind=ChangeKind.UNTRACKED)
            )
        elif head == "!":
            status.entries.append(
                StatusEntry(path=tok[2:], worktree_kind=ChangeKind.IGNORED)
            )
    return status


def _apply_header(line: str, status: GitStatus) -> None:
    """解析 ``# branch.*`` 头信息。"""
    body = line[2:]
    if body.startswith("branch.oid "):
        oid = body[len("branch.oid "):].strip()
        if oid and oid != "(initial)":
            status.head_sha = oid[:7]
        else:
            status.unborn = True
    elif body.startswith("branch.head "):
        name = body[len("branch.head "):].strip()
        if name == "(detached)":
            status.detached = True
            status.branch = None
        else:
            status.branch = name or None
    elif body.startswith("branch.upstream "):
        up = body[len("branch.upstream "):].strip()
        status.upstream = up or None
    elif body.startswith("branch.ab "):
        ahead, behind = parse_ahead_behind(body[len("branch.ab "):])
        status.ahead = ahead
        status.behind = behind


def parse_ahead_behind(text: str) -> tuple[int, int]:
    """解析 ``+1 -2`` / ``ahead 1, behind 2`` 形式的领先落后计数。"""
    if not text:
        return 0, 0
    ahead = 0
    behind = 0
    m = re.match(r"\s*\+(\d+)\s*-(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    ma = _TRACK_RE.search(text)
    if ma:
        ahead = int(ma.group(1))
    mb = _BEHIND_RE.search(text)
    if mb:
        behind = int(mb.group(1))
    return ahead, behind


def parse_log(text: str) -> list[CommitInfo]:
    """解析 ``git log --pretty=LOG_FORMAT`` 输出。

    使用 ``split("\\x1f", 8)`` 限制切分次数：提交正文（``%b``）理论上可能
    含分隔符，限制次数后多余内容留在正文字段里，不会破坏前面的列。
    """
    commits: list[CommitInfo] = []
    for record in (text or "").split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f", 8)
        if len(parts) < 8:
            continue
        body = parts[8] if len(parts) > 8 else ""
        parents = tuple(p for p in parts[5].split() if p)
        refs = tuple(r.strip() for r in parts[6].split(",") if r.strip())
        try:
            ts = int(parts[4])
        except ValueError:
            ts = 0
        commits.append(
            CommitInfo(
                sha=parts[0],
                short_sha=parts[1],
                author_name=parts[2],
                author_email=parts[3],
                timestamp=ts,
                summary=parts[7],
                body=body.strip(),
                parents=parents,
                refs=refs,
            )
        )
    return commits


def parse_name_status(text: str) -> list[FileChange]:
    """解析 ``git diff-tree --name-status -z`` 输出。

    ``-z`` 下每条记录是「状态字母，路径」两个 token；重命名/复制是三 token
    （``R100``、原路径、新路径）。
    """
    tokens = [t for t in (text or "").split("\0") if t]
    changes: list[FileChange] = []
    i = 0
    while i < len(tokens):
        code = tokens[i]
        i += 1
        letter = (code or "M")[:1].upper()
        kind = _NAME_STATUS_MAP.get(letter, ChangeKind.MODIFIED)
        if letter in ("R", "C"):
            if i + 1 >= len(tokens):
                break
            orig = tokens[i]
            new = tokens[i + 1]
            i += 2
            changes.append(FileChange(path=new, kind=kind, orig_path=orig))
        else:
            if i >= len(tokens):
                break
            changes.append(FileChange(path=tokens[i], kind=kind))
            i += 1
    return changes


def parse_numstat(text: str) -> dict[str, tuple[int | None, int | None]]:
    """解析 ``git diff-tree --numstat -z`` 输出 → ``{path: (additions, deletions)}``。

    二进制文件的行数是 ``-``（转为 None）；重命名记录会多出两个路径 token。
    """
    tokens = (text or "").split("\0")
    out: dict[str, tuple[int | None, int | None]] = {}
    i = 0
    total = len(tokens)
    while i < total:
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        parts = tok.split("\t")
        if len(parts) < 3:
            continue
        add = _int_or_none(parts[0])
        dele = _int_or_none(parts[1])
        path = parts[2]
        if path == "" and i < total:
            # 重命名：numstat 把两个路径拆成独立 token
            if i < total:
                i += 1  # 原路径
            if i < total:
                path = tokens[i]
                i += 1
        if path:
            out[path] = (add, dele)
    return out


def _int_or_none(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def parse_branches(text: str) -> list[BranchInfo]:
    """解析 ``git branch --format=BRANCH_FORMAT`` 输出。

    远端分支（``refs/remotes``）通过 ``origin/xxx`` 形态识别；
    ``origin/HEAD`` 这类符号引用会被跳过。
    """
    branches: list[BranchInfo] = []
    for record in (text or "").split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f", 7)
        if len(parts) < 7:
            continue
        name = parts[0].strip()
        refname = parts[7].strip() if len(parts) > 7 else ""
        if not name or name.endswith("/HEAD") or name == "HEAD":
            continue
        # refname 缺失时（自定义格式的分支列表）按 ``origin/xxx`` 形态猜测
        is_remote = (
            refname.startswith("refs/remotes/") if refname else "/" in name
        )
        ahead, behind = parse_ahead_behind(parts[4])
        try:
            ts = int(parts[5])
        except ValueError:
            ts = 0
        branches.append(
            BranchInfo(
                name=name,
                current=parts[1].strip() == "*",
                upstream=parts[3].strip() or None,
                ahead=ahead,
                behind=behind,
                is_remote=is_remote,
                sha=parts[2].strip(),
                subject=parts[6].strip(),
                last_commit_at=ts,
            )
        )
    return branches
