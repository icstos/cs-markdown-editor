"""Git 数据模型：状态 / 提交 / 差异 / 分支的纯数据结构（不依赖 flet）。

所有模型都是不可变友好的 dataclass，字段命名与 git porcelain 输出一一对应，
解析层（porcelain / difftext）负责填充，视图层只读。

约定：
- 文件路径一律使用**相对仓库根**的 POSIX 风格路径（git 原样输出）；
  需要绝对路径时由调用方与 ``repo_root`` 拼接（见 ``abspath``）。
- 行号从 1 开始；缺失侧为 ``None``（供分栏视图留空）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

__all__ = [
    "BranchInfo",
    "ChangeKind",
    "CommitDetail",
    "CommitInfo",
    "DiffHunk",
    "DiffLine",
    "FileChange",
    "FileDiff",
    "GitStatus",
    "MergeMarkers",
    "SplitRow",
    "StatusEntry",
    "kind_from_xy",
    "status_letter",
]


class ChangeKind(StrEnum):
    """文件变更类型（对齐 ``git status`` 的 XY 语义）。"""

    UNMODIFIED = "unmodified"
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
    UNTRACKED = "untracked"
    IGNORED = "ignored"
    CONFLICTED = "conflicted"


#: git XY 单字符 → ChangeKind
_XY_TO_KIND: dict[str, ChangeKind] = {
    "M": ChangeKind.MODIFIED,
    "T": ChangeKind.TYPE_CHANGED,
    "A": ChangeKind.ADDED,
    "D": ChangeKind.DELETED,
    "R": ChangeKind.RENAMED,
    "C": ChangeKind.COPIED,
    "U": ChangeKind.CONFLICTED,
    "?": ChangeKind.UNTRACKED,
    "!": ChangeKind.IGNORED,
    " ": ChangeKind.UNMODIFIED,
    ".": ChangeKind.UNMODIFIED,
}


def kind_from_xy(ch: str) -> ChangeKind:
    """单个 XY 字符 → ChangeKind（未知字符按「已修改」处理）。"""
    return _XY_TO_KIND.get((ch or " ")[:1].upper(), ChangeKind.MODIFIED)


#: 变更类型 → 单字母标记（对标 VS Code 源代码管理视图）
#: - 未跟踪 ``U``、已修改 ``M``、新增 ``A``、删除 ``D``、重命名 ``R``、复制 ``C``
#: - 冲突 ``!``（VS Code 冲突用独立的感叹号装饰，与未跟踪的 U 区分）
#: - 忽略 ``I``（SCM 视图默认不展示，仅在被显式列出时使用）
_LETTERS: dict[ChangeKind, str] = {
    ChangeKind.UNMODIFIED: "",
    ChangeKind.ADDED: "A",
    ChangeKind.MODIFIED: "M",
    ChangeKind.DELETED: "D",
    ChangeKind.RENAMED: "R",
    ChangeKind.COPIED: "C",
    ChangeKind.TYPE_CHANGED: "T",
    ChangeKind.UNTRACKED: "U",
    ChangeKind.IGNORED: "I",
    ChangeKind.CONFLICTED: "!",
}

#: 变更类型 → 中文标签（tooltip / 无障碍名称）
_LABELS: dict[ChangeKind, str] = {
    ChangeKind.UNMODIFIED: "未变更",
    ChangeKind.ADDED: "新增",
    ChangeKind.MODIFIED: "已修改",
    ChangeKind.DELETED: "已删除",
    ChangeKind.RENAMED: "已重命名",
    ChangeKind.COPIED: "已复制",
    ChangeKind.TYPE_CHANGED: "类型变更",
    ChangeKind.UNTRACKED: "未跟踪",
    ChangeKind.IGNORED: "已忽略",
    ChangeKind.CONFLICTED: "冲突",
}


def status_letter(kind: ChangeKind) -> str:
    """变更类型 → 单字母标记（VS Code 风格）。"""
    return _LETTERS.get(kind, "M")


def kind_label(kind: ChangeKind) -> str:
    """变更类型 → 中文标签。"""
    return _LABELS.get(kind, str(kind))


@dataclass(slots=True)
class StatusEntry:
    """单条工作区状态记录（一次「git status」中的一个路径）。

    Attributes:
        path: 仓库相对路径（renamed 时为**新**路径）。
        orig_path: renamed/copied 时的原路径，其余为 None。
        index_kind: 索引（暂存区）中的变更类型。
        worktree_kind: 工作区中的变更类型。
        is_conflicted: 处于未合并（冲突）状态。
    """

    path: str
    orig_path: str | None = None
    index_kind: ChangeKind = ChangeKind.UNMODIFIED
    worktree_kind: ChangeKind = ChangeKind.UNMODIFIED
    is_conflicted: bool = False

    # ---- 派生 ----
    @property
    def is_staged(self) -> bool:
        """该路径在暂存区中有变更（可在「暂存的更改」分区出现）。"""
        return self.is_conflicted or self.index_kind != ChangeKind.UNMODIFIED

    @property
    def is_unstaged(self) -> bool:
        """该路径在工作区中有未暂存变更（可在「更改」分区出现）。

        判据是「工作区相对索引有差异」——即 XY 的 Y 位非空。
        被忽略（``!``）的路径不属于待处理变更，两个分区都不出现。
        """
        if self.is_conflicted:
            return True
        if self.worktree_kind == ChangeKind.IGNORED:
            return False
        return self.worktree_kind != ChangeKind.UNMODIFIED

    @property
    def kind(self) -> ChangeKind:
        """展示用主类型：优先工作区，其次索引。"""
        if self.is_conflicted:
            return ChangeKind.CONFLICTED
        if self.worktree_kind not in (ChangeKind.UNMODIFIED,):
            return self.worktree_kind
        return self.index_kind

    @property
    def letter(self) -> str:
        return status_letter(self.kind)

    @property
    def label(self) -> str:
        return kind_label(self.kind)

    @property
    def display_name(self) -> str:
        """展示名：重命名显示「旧 → 新」的双名（给 tooltip 用）。"""
        if self.orig_path and self.orig_path != self.path:
            return f"{self.orig_path} → {self.path}"
        return self.path

    @property
    def basename(self) -> str:
        return os.path.basename(self.path) or self.path

    @property
    def dirname(self) -> str:
        """仓库相对目录（用于分组显示 / tooltip），根目录返回 ""。"""
        d = os.path.dirname(self.path)
        return d.replace("\\", "/")


@dataclass(slots=True)
class GitStatus:
    """仓库整体状态快照。

    Attributes:
        branch: 当前分支名；分离 HEAD 时为 None。
        upstream: 上游分支全名（如 ``origin/main``），未设置时为 None。
        ahead: 领先上游的提交数。
        behind: 落后上游的提交数。
        entries: 全部状态记录。
        detached: 是否处于 detached HEAD。
        unborn: 是否尚无任何提交（刚 init 的空仓库）。
        op: 正在进行的操作（merge / rebase / cherry-pick），用于提示。
        head_sha: 当前提交短哈希（unborn 时为 None）。
    """

    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    entries: list[StatusEntry] = field(default_factory=list)
    detached: bool = False
    unborn: bool = False
    op: str | None = None
    head_sha: str | None = None

    # ---- 分区 ----
    @property
    def staged(self) -> list[StatusEntry]:
        """「暂存的更改」分区（冲突文件同时出现在两个分区，VS Code 行为）。"""
        return [e for e in self.entries if e.is_staged]

    @property
    def unstaged(self) -> list[StatusEntry]:
        """「更改」分区（含未跟踪文件）。"""
        return [e for e in self.entries if e.is_unstaged]

    @property
    def untracked(self) -> list[StatusEntry]:
        return [e for e in self.entries if e.worktree_kind == ChangeKind.UNTRACKED]

    @property
    def conflicted(self) -> list[StatusEntry]:
        return [e for e in self.entries if e.is_conflicted]

    # ---- 计数（状态栏角标 / 面板标题）----
    @property
    def change_count(self) -> int:
        """待提交变更数（被忽略的路径不计入）。"""
        return len({e.path for e in self.entries if e.kind != ChangeKind.IGNORED})

    @property
    def staged_count(self) -> int:
        return len(self.staged)

    @property
    def unstaged_count(self) -> int:
        return len(self.unstaged)

    @property
    def is_clean(self) -> bool:
        return self.change_count == 0

    @property
    def in_operation(self) -> bool:
        """是否处于 merge/rebase/cherry-pick/revert 进行中。"""
        return bool(self.op)

    @property
    def op_label(self) -> str:
        """进行中操作的中文名（merge→合并中 / rebase→变基中 …）。"""
        return {
            "merge": "合并",
            "rebase": "变基",
            "cherry-pick": "拣选",
            "revert": "回滚",
        }.get(self.op or "", "")

    def entry_for(self, path: str) -> StatusEntry | None:
        """按路径查找状态记录（用于打开 diff 时判断暂存侧）。"""
        for e in self.entries:
            if e.path == path:
                return e
        return None


@dataclass(slots=True)
class CommitInfo:
    """一条提交记录。"""

    sha: str
    short_sha: str
    author_name: str
    author_email: str
    timestamp: int  # Unix 秒
    summary: str
    body: str = ""
    parents: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    @property
    def time_text(self) -> str:
        """相对时间文案（VSCode 历史列表风格：刚刚 / 3 分钟前 / 2 天前 / 日期）。"""
        delta = datetime.now().timestamp() - self.timestamp
        if delta < 0:
            delta = 0
        if delta < 60:
            return "刚刚"
        if delta < 3600:
            return f"{int(delta // 60)} 分钟前"
        if delta < 86400:
            return f"{int(delta // 3600)} 小时前"
        if delta < 86400 * 30:
            return f"{int(delta // 86400)} 天前"
        if delta < 86400 * 365:
            return f"{int(delta // (86400 * 30))} 个月前"
        return f"{int(delta // (86400 * 365))} 年前"

    @property
    def absolute_time_text(self) -> str:
        return self.datetime.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


@dataclass(slots=True)
class FileChange:
    """提交中的单个文件变更（历史详情里的文件列表项）。"""

    path: str
    kind: ChangeKind
    orig_path: str | None = None
    additions: int | None = None
    deletions: int | None = None

    @property
    def letter(self) -> str:
        return status_letter(self.kind)

    @property
    def label(self) -> str:
        return kind_label(self.kind)

    @property
    def basename(self) -> str:
        return os.path.basename(self.path) or self.path

    @property
    def dirname(self) -> str:
        return os.path.dirname(self.path).replace("\\", "/")

    @property
    def display_name(self) -> str:
        if self.orig_path and self.orig_path != self.path:
            return f"{self.orig_path} → {self.path}"
        return self.path


@dataclass(slots=True)
class CommitDetail:
    """提交详情：提交信息 + 变更文件列表。"""

    info: CommitInfo
    files: list[FileChange] = field(default_factory=list)

    @property
    def total_additions(self) -> int:
        return sum(f.additions or 0 for f in self.files)

    @property
    def total_deletions(self) -> int:
        return sum(f.deletions or 0 for f in self.files)


@dataclass(slots=True)
class BranchInfo:
    """本地（或远端）分支条目。"""

    name: str
    current: bool = False
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    is_remote: bool = False
    sha: str = ""
    subject: str = ""
    last_commit_at: int = 0

    @property
    def time_text(self) -> str:
        if not self.last_commit_at:
            return ""
        return CommitInfo(
            sha=self.sha,
            short_sha=self.sha[:7],
            author_name="",
            author_email="",
            timestamp=self.last_commit_at,
            summary=self.subject,
        ).time_text


# ---------------------------------------------------------------------------
# 差异模型
# ---------------------------------------------------------------------------


class DiffLineKind(StrEnum):
    CONTEXT = "context"
    ADDED = "added"
    REMOVED = "removed"
    EMPTY = "empty"  # 分栏视图中对侧为空行的占位


@dataclass(slots=True)
class DiffLine:
    """差异中的一行。"""

    kind: DiffLineKind
    text: str
    old_no: int | None = None
    new_no: int | None = None


@dataclass(slots=True)
class DiffHunk:
    """一个差异块（``@@ -a,b +c,d @@``）。"""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def section(self) -> str:
        """``@@ ... @@`` 之后的函数上下文（可能为空）。"""
        parts = self.header.split("@@")
        return parts[2].strip() if len(parts) > 2 else ""


@dataclass(slots=True)
class FileDiff:
    """单个文件的差异（工作区 / 暂存区 / 历史提交均复用）。"""

    path: str
    old_path: str | None = None
    hunks: list[DiffHunk] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False
    is_new: bool = False
    is_deleted: bool = False
    is_renamed: bool = False
    old_mode: str | None = None
    new_mode: str | None = None
    truncated: bool = False  # 命中大文件保护，仅保留了部分块
    raw: str = ""  # 原始 diff 文本（供「复制补丁」/ 详情）
    error: str = ""  # 解析或读取失败时的说明

    @property
    def has_changes(self) -> bool:
        return bool(self.hunks) or self.is_binary or bool(self.error)

    @property
    def is_empty(self) -> bool:
        """完全无差异（例如文件已还原 / 内容一致）。"""
        return not self.hunks and not self.is_binary and not self.error

    @property
    def basename(self) -> str:
        return os.path.basename(self.path) or self.path

    @property
    def display_path(self) -> str:
        if self.old_path and self.old_path != self.path:
            return f"{self.old_path} → {self.path}"
        return self.path

    @property
    def line_count(self) -> int:
        """差异总行数（含块头），用于渲染前的规模估算。"""
        return sum(len(h.lines) + 1 for h in self.hunks)


@dataclass(slots=True)
class SplitRow:
    """分栏（并排）视图中的一行：左右两侧对齐后的内容。"""

    left_no: int | None
    left_text: str
    left_kind: DiffLineKind
    right_no: int | None
    right_text: str
    right_kind: DiffLineKind
    is_hunk_header: bool = False
    header_text: str = ""

    @property
    def row_kind(self) -> DiffLineKind:
        """整行着色类型：任一侧为增/删即取该侧（供行背景色）。"""
        if self.left_kind != DiffLineKind.CONTEXT and self.left_kind != DiffLineKind.EMPTY:
            return self.left_kind
        return self.right_kind


@dataclass(slots=True)
class MergeMarkers:
    """冲突标记定位结果（供「跳转到冲突位置」）。"""

    path: str
    start_lines: list[int] = field(default_factory=list)  # ``<<<<<<<`` 所在行（1 起）
    separator_lines: list[int] = field(default_factory=list)  # ``=======``
    end_lines: list[int] = field(default_factory=list)  # ``>>>>>>>``

    @property
    def has_conflict(self) -> bool:
        return bool(self.start_lines)

    @property
    def count(self) -> int:
        return len(self.start_lines)

    @property
    def first_line(self) -> int | None:
        return self.start_lines[0] if self.start_lines else None
