"""Git 仓库门面：把命令行细节收敛成一组语义化方法。

上层（``app/_git_controller``）只调用本模块的方法，不接触任何 git 参数。
每个方法都返回数据模型或抛 ``services.git.errors`` 的具体异常，
因此调用方可以按异常类型给出差异化提示。

命令选型说明（保证跨平台 / 跨 git 版本稳定）：
- 所有写操作显式指定 pathspec（``--`` 分隔），避免「文件名与分支名同名」歧义；
- 差异一律带 ``--no-color --no-ext-diff``（不依赖用户配置，输出可解析）；
- ``--find-renames`` 打开重命名检测（VS Code 默认展示 renamed 而非 D+A）；
- 网络命令使用更长的超时（见 ``NETWORK_TIMEOUT``）。
"""

from __future__ import annotations

import logging
import os

from services.git.difftext import (
    detect_binary,
    parse_single_file_diff,
    parse_unified_diff,
    scan_merge_markers,
    synthetic_added_diff,
)
from services.git.errors import (
    GitCommandError,
    GitConflictError,
    GitError,
    NotARepositoryError,
    classify_error,
)
from services.git.models import (
    BranchInfo,
    ChangeKind,
    CommitDetail,
    CommitInfo,
    FileChange,
    FileDiff,
    GitStatus,
    MergeMarkers,
)
from services.git.porcelain import (
    BRANCH_FORMAT,
    LOG_FORMAT,
    parse_branches,
    parse_log,
    parse_name_status,
    parse_numstat,
    parse_status,
)
from services.git.runner import NETWORK_TIMEOUT, GitRunner

log = logging.getLogger(__name__)

__all__ = [
    "GitRepository",
    "GitService",
    "discover_root",
    "git_version",
    "is_git_available",
    "is_repository",
]

#: 历史列表默认页大小（「历史记录分页加载」）
DEFAULT_PAGE_SIZE = 50
#: 单次 log 请求上限（防御性上限，避免误传超大 limit 拖慢 UI）
MAX_PAGE_SIZE = 500

#: 正在进行的操作对应的 git 状态文件
_OP_FILES = (
    ("MERGE_HEAD", "merge"),
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
)

_OP_LABELS = {
    "merge": "合并中",
    "rebase": "变基中",
    "cherry-pick": "拣选中",
    "revert": "回滚中",
}


# ---------------------------------------------------------------------------
# 模块级探测
# ---------------------------------------------------------------------------


def is_git_available() -> bool:
    """系统是否安装了可用的 git。"""
    return GitRunner().available()


def git_version() -> str:
    return GitRunner().version()


def discover_root(start: str | None) -> str | None:
    """从 ``start``（文件或目录）向上查找仓库根；不是仓库返回 None。

    对应 VS Code 的 repository discovery：打开子目录文件时仍能定位到仓库根。
    """
    if not start:
        return None
    path = start
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not path or not os.path.isdir(path):
        return None
    runner = GitRunner(cwd=path)
    if not runner.available():
        return None
    res = runner.run(
        ["rev-parse", "--show-toplevel"], check=False, timeout=8.0
    )
    if not res.ok:
        return None
    root = res.stdout.strip()
    if not root:
        return None
    return os.path.normpath(root)


def is_repository(path: str | None) -> bool:
    """目录是否位于某个 Git 工作区内。"""
    return discover_root(path) is not None


def _is_internal_path(path: str) -> bool:
    """是否为 git 内部路径（``.git`` 目录内），删除操作必须跳过。"""
    parts = path.replace("\\", "/").split("/")
    return ".git" in parts


# ---------------------------------------------------------------------------
# 仓库对象
# ---------------------------------------------------------------------------


class GitRepository:
    """单个仓库的操作门面（线程安全：无共享可变状态，命令串行由调用方保证）。"""

    def __init__(self, root: str, *, runner: GitRunner | None = None) -> None:
        self.root = os.path.normpath(root)
        self.runner = runner or GitRunner(cwd=self.root)
        self.runner.cwd = self.root
        self._git_dir: str | None = None
        self._unborn_cache: bool | None = None

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<GitRepository {self.root}>"

    # ---------------------------------------------------------------- 基础
    @property
    def name(self) -> str:
        return os.path.basename(self.root) or self.root

    def abspath(self, rel_path: str) -> str:
        """仓库相对路径 → 绝对路径（POSIX 分隔符在 Windows 下会被规范化）。"""
        return os.path.normpath(os.path.join(self.root, rel_path))

    def relpath(self, abs_path: str) -> str:
        """绝对路径 → 仓库相对路径（POSIX 风格，与 git 输出一致）。"""
        try:
            rel = os.path.relpath(abs_path, self.root)
        except ValueError:
            return abs_path.replace("\\", "/")
        return rel.replace("\\", "/")

    def contains(self, abs_path: str) -> bool:
        """路径是否位于本仓库工作区内。"""
        try:
            rel = os.path.relpath(os.path.abspath(abs_path), self.root)
        except ValueError:
            return False
        return not rel.startswith("..")

    @property
    def git_dir(self) -> str:
        """``.git`` 目录绝对路径（缓存；worktree 场景也正确）。"""
        if self._git_dir is None:
            res = self.runner.run(
                ["rev-parse", "--absolute-git-dir"], check=False, timeout=8.0
            )
            self._git_dir = res.stdout.strip() if res.ok and res.stdout.strip() else os.path.join(
                self.root, ".git"
            )
        return self._git_dir

    def current_operation(self) -> str | None:
        """正在进行的 git 操作（merge / rebase / cherry-pick / revert），无则 None。"""
        git_dir = self.git_dir
        for fname, op in _OP_FILES:
            if os.path.exists(os.path.join(git_dir, fname)):
                return op
        return None

    @staticmethod
    def operation_label(op: str | None) -> str:
        return _OP_LABELS.get(op or "", "")

    # ---------------------------------------------------------------- 查询
    def status(self) -> GitStatus:
        """读取完整仓库状态（含分支 / 领先落后 / 分区）。"""
        res = self.runner.run(
            ["status", "--porcelain=v2", "--branch", "--untracked-files=all", "-z"],
            timeout=20.0,
        )
        st = parse_status(res.stdout)
        st.op = self.current_operation()
        self._unborn_cache = st.unborn
        return st

    def has_commits(self) -> bool:
        """仓库是否已有提交（空仓库返回 False）。"""
        return self._head_exists()

    def _head_exists(self) -> bool:
        res = self.runner.run(["rev-parse", "--verify", "-q", "HEAD"], check=False, timeout=8.0)
        return res.ok and bool(res.stdout.strip())

    def _is_unborn(self) -> bool:
        if self._unborn_cache is None:
            self._unborn_cache = not self._head_exists()
        return self._unborn_cache

    def branches(self, *, include_remote: bool = True) -> list[BranchInfo]:
        """分支列表（当前分支置顶由视图层负责排序）。"""
        args = ["branch", f"--format={BRANCH_FORMAT}", "--sort=-committerdate"]
        if include_remote:
            args.append("-a")
        res = self.runner.run(args, timeout=15.0)
        return parse_branches(res.stdout)

    def local_branches(self) -> list[BranchInfo]:
        return self.branches(include_remote=False)

    def remotes(self) -> list[str]:
        """远端名列表。"""
        res = self.runner.run(["remote"], check=False, timeout=10.0)
        if not res.ok:
            return []
        return [r.strip() for r in res.stdout.splitlines() if r.strip()]

    def default_branch_name(self) -> str:
        """新建仓库时的默认分支名：优先用户配置，其次 main。"""
        res = self.runner.run(
            ["config", "--get", "init.defaultBranch"], check=False, timeout=8.0
        )
        name = res.stdout.strip() if res.ok else ""
        return name or "main"

    def log(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        author: str | None = None,
        keyword: str | None = None,
        path: str | None = None,
        all_branches: bool = True,
    ) -> list[CommitInfo]:
        """读取提交历史（分页 + 按作者 / 关键词 / 文件路径过滤）。

        Args:
            limit: 本页条数（会被裁剪到 ``[1, MAX_PAGE_SIZE]``）。
            offset: 跳过条数（分页游标）。
            author: 作者名或邮箱（git ``--author`` 正则子串匹配）。
            keyword: 提交信息关键词（git ``--grep``）。
            path: 仅该文件的提交（仓库相对路径）。
            all_branches: 是否包含所有分支（VSCode 历史默认看当前分支，这里
                默认看全部以便「文件历史」不遗漏其他分支的改动）。
        """
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        offset = max(0, int(offset))
        if not self._head_exists():
            return []
        args = ["log", f"--pretty=format:{LOG_FORMAT}", "--date-order", f"-n{limit}"]
        if offset:
            args.append(f"--skip={offset}")
        if author:
            args.append(f"--author={author}")
        if keyword:
            args.append(f"--grep={keyword}")
        if all_branches:
            args.append("--all")
        if path:
            args.extend(["--", path])
        res = self.runner.run(args, check=False, timeout=30.0)
        if not res.ok:
            # 空仓库 / 无匹配时 git 返回非零且无输出
            if not res.stdout.strip():
                return []
            raise classify_error(res.stderr or res.stdout, res.returncode, command=args)
        return parse_log(res.stdout)

    def commit_info(self, sha: str) -> CommitInfo | None:
        """读取单个提交的元信息。"""
        res = self.runner.run(
            ["log", "-1", f"--pretty=format:{LOG_FORMAT}", sha], check=False, timeout=15.0
        )
        if not res.ok:
            return None
        commits = parse_log(res.stdout)
        return commits[0] if commits else None

    def commit_detail(self, sha: str) -> CommitDetail:
        """提交详情：元信息 + 变更文件列表（含增删行数）。"""
        info = self.commit_info(sha)
        if info is None:
            raise GitCommandError(
                "无法读取该提交",
                detail=f"提交 {sha} 不存在或对象已损坏",
                command=f"git log -1 {sha}",
            )
        return CommitDetail(info=info, files=self.commit_files(sha))

    def commit_files(self, sha: str) -> list[FileChange]:
        """提交涉及的文件列表（含重命名与增删行数）。"""
        ns = self.runner.run(
            [
                "diff-tree",
                "--no-commit-id",
                "--name-status",
                "-r",
                "-M",
                "--root",
                "-z",
                sha,
            ],
            check=False,
            timeout=25.0,
        )
        if not ns.ok:
            return []
        files = parse_name_status(ns.stdout)
        num = self.runner.run(
            [
                "diff-tree",
                "--no-commit-id",
                "--numstat",
                "-r",
                "-M",
                "--root",
                "-z",
                sha,
            ],
            check=False,
            timeout=25.0,
        )
        if num.ok and num.stdout:
            counts = parse_numstat(num.stdout)
            for f in files:
                got = counts.get(f.path)
                if got:
                    f.additions, f.deletions = got
        return files

    def working_diff(self, path: str, *, staged: bool = False) -> FileDiff:
        """工作区（或暂存区）中单个文件的差异。

        未跟踪文件 git 不产生 diff，这里读取文件内容合成「全新增」差异，
        与 VS Code 未跟踪文件的 diff 视图一致。
        """
        entry = None
        try:
            st = parse_status(
                self.runner.run(
                    ["status", "--porcelain=v2", "-z", "--", path], check=False, timeout=15.0
                ).stdout
            )
            entry = st.entry_for(path)
        except GitError:
            entry = None

        if entry is not None and entry.worktree_kind == ChangeKind.UNTRACKED and not staged:
            return self._untracked_diff(path)

        args = ["diff", "--no-color", "--no-ext-diff", "--find-renames", "-U3"]
        if staged:
            args.append("--cached")
        args.extend(["--", path])
        res = self.runner.run(args, check=False, timeout=30.0)
        if not res.ok:
            fd = FileDiff(path=path)
            fd.error = (res.stderr or res.stdout).strip()[:400]
            return fd
        fd = parse_single_file_diff(res.stdout, path)
        fd.raw = res.stdout
        return fd

    def commit_file_diff(self, sha: str, path: str) -> FileDiff:
        """某个提交中单个文件的历史差异。"""
        res = self.runner.run(
            [
                "show",
                "--no-color",
                "--no-ext-diff",
                "--find-renames",
                "-U3",
                "--format=",
                sha,
                "--",
                path,
            ],
            check=False,
            timeout=30.0,
        )
        if not res.ok:
            fd = FileDiff(path=path)
            fd.error = (res.stderr or res.stdout).strip()[:400]
            return fd
        if "Binary files" in res.stdout:
            fd = FileDiff(path=path, is_binary=True)
            fd.raw = res.stdout
            return fd
        fd = parse_single_file_diff(res.stdout, path)
        fd.raw = res.stdout
        return fd

    def commit_diff(self, sha: str) -> list[FileDiff]:
        """整个提交的差异（多文件）。"""
        res = self.runner.run(
            [
                "show",
                "--no-color",
                "--no-ext-diff",
                "--find-renames",
                "-U3",
                "--format=",
                sha,
            ],
            check=False,
            timeout=40.0,
        )
        if not res.ok:
            return []
        return parse_unified_diff(res.stdout)

    def _untracked_diff(self, path: str) -> FileDiff:
        """为未跟踪文件合成全新增差异。"""
        full = self.abspath(path)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError as exc:
            fd = FileDiff(path=path)
            fd.error = f"无法读取文件：{exc}"
            return fd
        if detect_binary(data):
            return FileDiff(path=path, is_binary=True, is_new=True)
        text = data.decode("utf-8", errors="replace")
        fd = synthetic_added_diff(path, text)
        fd.raw = text
        return fd

    def read_text(self, path: str) -> str:
        """读取仓库内文件文本（失败返回空串）。"""
        try:
            with open(self.abspath(path), "rb") as f:
                data = f.read()
        except OSError:
            return ""
        if detect_binary(data):
            return ""
        return data.decode("utf-8", errors="replace")

    def conflict_markers(self, path: str) -> MergeMarkers:
        """扫描文件中的冲突标记行号。"""
        return scan_merge_markers(self.read_text(path), path)

    # ---------------------------------------------------------------- 暂存区
    def stage(self, paths: list[str]) -> None:
        """暂存指定路径（``git add``）。"""
        self._require_paths(paths)
        self.runner.run(["add", "-A", "--", *paths], timeout=30.0)

    def stage_all(self) -> None:
        """暂存全部变更（含未跟踪与删除）。"""
        self.runner.run(["add", "-A", "--", "."], timeout=60.0)

    def unstage(self, paths: list[str]) -> None:
        """取消暂存指定路径（保留工作区内容）。"""
        self._require_paths(paths)
        if self._is_unborn():
            # 尚无 HEAD：restore --staged 无处可取，改用 rm --cached
            res = self.runner.run(
                ["rm", "--cached", "-r", "-q", "--", *paths], check=False, timeout=30.0
            )
            if not res.ok and "did not match any file" not in (res.stderr or ""):
                raise classify_error(res.stderr, res.returncode, command=("git", "rm", *paths))
            return
        self.runner.run(["restore", "--staged", "--", *paths], timeout=30.0)

    def unstage_all(self) -> None:
        """取消暂存全部（保留工作区内容）。"""
        if self._is_unborn():
            res = self.runner.run(
                ["rm", "--cached", "-r", "-q", "--", "."], check=False, timeout=60.0
            )
            if not res.ok and "did not match any file" not in (res.stderr or ""):
                raise classify_error(res.stderr, res.returncode, command=("git", "rm", "--cached"))
            return
        self.runner.run(["restore", "--staged", "--", "."], timeout=60.0)

    def discard_working(self, paths: list[str]) -> None:
        """丢弃指定路径的工作区改动（``restore --worktree``，不触碰暂存区）。"""
        self._require_paths(paths)
        self.runner.run(["restore", "--worktree", "--", *paths], timeout=30.0)

    def discard_all(self) -> None:
        """丢弃全部工作区改动（仅已跟踪文件，未跟踪文件由 ``delete_untracked`` 处理）。"""
        if self._is_unborn():
            return
        self.runner.run(["restore", "--worktree", "--", "."], timeout=60.0)

    def delete_untracked(self, paths: list[str]) -> list[str]:
        """删除未跟踪文件（「丢弃」未跟踪变更的语义），返回实际删除的路径。

        安全约束：只删文件、只删位于仓库工作区内的路径；不做递归目录删除。
        """
        removed: list[str] = []
        for rel in paths:
            if not rel or _is_internal_path(rel):
                continue
            full = self.abspath(rel)
            if not self.contains(full):
                continue
            if not os.path.isfile(full):
                continue
            try:
                os.remove(full)
                removed.append(rel)
            except OSError as exc:
                log.warning("删除未跟踪文件失败 path=%s err=%s", rel, exc)
        return removed

    def unstage_and_delete(self, paths: list[str]) -> None:
        """丢弃「已暂存的新增文件」：先从索引移除，再删磁盘文件。"""
        self._require_paths(paths)
        res = self.runner.run(
            ["rm", "--force", "-q", "--cached", "--", *paths], check=False, timeout=30.0
        )
        if not res.ok and "did not match" not in (res.stderr or ""):
            raise classify_error(res.stderr, res.returncode, command=("git", "rm", "--cached"))
        for rel in paths:
            full = self.abspath(rel)
            if self.contains(full) and os.path.isfile(full):
                try:
                    os.remove(full)
                except OSError as exc:
                    log.warning("删除文件失败 path=%s err=%s", rel, exc)

    # ---------------------------------------------------------------- 提交
    def commit(self, message: str, *, all_changes: bool = False) -> str:
        """提交并返回新提交的短哈希。

        Args:
            message: 提交信息（多行原样传入，通过 stdin 传递避免命令行长度/转义问题）。
            all_changes: True 时等价 ``git commit -a``（提交所有已跟踪改动）。

        Raises:
            GitCommandError: 没有可提交内容。
            GitConflictError: 存在未解决的冲突。
        """
        if not message.strip():
            raise GitCommandError("提交信息不能为空", command="git commit")
        args = ["commit", "-F", "-"]
        if all_changes:
            args.append("-a")
        res = self.runner.run(args, check=False, input_text=message, timeout=120.0)
        if not res.ok:
            combined = f"{res.stdout}\n{res.stderr}".lower()
            if "nothing to commit" in combined or "no changes added" in combined:
                raise GitCommandError(
                    "没有可提交的更改",
                    detail="请先暂存要提交的文件，或选择「提交所有更改」。",
                    command="git commit",
                )
            if "unmerged" in combined or "conflict" in combined:
                raise GitConflictError(
                    "存在未解决的冲突",
                    detail="解决冲突并暂存相关文件后再提交。",
                    command="git commit",
                    returncode=res.returncode,
                )
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", "commit"))
        head = self.runner.run(
            ["rev-parse", "--short", "HEAD"], check=False, timeout=8.0
        )
        return head.stdout.strip() if head.ok else ""

    def amend(self, message: str | None = None, *, all_changes: bool = False) -> str:
        """修补上一次提交（未推送时修正信息或补充文件）。"""
        args = ["commit", "--amend"]
        if all_changes:
            args.append("-a")
        if message and message.strip():
            args.extend(["-F", "-"])
            res = self.runner.run(args, check=False, input_text=message, timeout=120.0)
        else:
            args.append("--no-edit")
            res = self.runner.run(args, check=False, timeout=120.0)
        if not res.ok:
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", "commit"))
        head = self.runner.run(["rev-parse", "--short", "HEAD"], check=False, timeout=8.0)
        return head.stdout.strip() if head.ok else ""

    def undo_last_commit(self, *, keep_staged: bool = False) -> None:
        """撤销上一次提交。

        Args:
            keep_staged: True → ``--soft``（改动留在暂存区，VS Code 行为）；
                False → ``--mixed``（改动回到工作区，未暂存）。

        Raises:
            GitCommandError: 没有可撤销的提交（仓库只有一个提交 / 无提交）。
        """
        if not self._head_exists():
            raise GitCommandError("没有可撤销的提交", detail="仓库尚无任何提交。")
        check = self.runner.run(["rev-parse", "--verify", "-q", "HEAD~1"], check=False, timeout=8.0)
        if not check.ok:
            raise GitCommandError(
                "没有可撤销的提交",
                detail="当前分支只有这一个提交，撤销后将不剩任何提交。",
            )
        mode = "--soft" if keep_staged else "--mixed"
        res = self.runner.run(["reset", mode, "HEAD~1"], check=False, timeout=30.0)
        if not res.ok:
            raise classify_error(res.stderr, res.returncode, command=("git", "reset", mode))
        self._unborn_cache = None

    # ---------------------------------------------------------------- 远端同步
    def fetch(self, *, remote: str | None = None, prune: bool = True) -> str:
        """获取远端更新（不改动本地分支）。"""
        args = ["fetch"]
        if remote:
            args.append(remote)
        else:
            if not self.remotes():
                raise GitCommandError("未配置远端仓库", detail="请先添加远端（git remote add）。")
            args.append("--all")
        if prune:
            args.append("--prune")
        res = self.runner.run(args, check=False, timeout=NETWORK_TIMEOUT)
        if not res.ok:
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", *args))
        return res.stderr.strip()

    def pull(self, *, remote: str | None = None, rebase: bool = False) -> str:
        """拉取并合并远端更新（返回 git 的输出摘要）。"""
        args = ["pull", "--no-edit"]
        if rebase:
            args.append("--rebase")
        if remote:
            args.append(remote)
        res = self.runner.run(args, check=False, timeout=NETWORK_TIMEOUT)
        if not res.ok:
            combined = f"{res.stdout}\n{res.stderr}".lower()
            if "conflict" in combined:
                raise GitConflictError(
                    "拉取产生合并冲突",
                    detail="请解决冲突文件后提交，或执行「中止合并」。",
                    command="git pull",
                    returncode=res.returncode,
                )
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", *args))
        return (res.stdout + "\n" + res.stderr).strip()

    def push(
        self,
        *,
        remote: str | None = None,
        branch: str | None = None,
        set_upstream: bool = False,
        force_with_lease: bool = False,
    ) -> str:
        """推送本地提交到远端。"""
        remotes = self.remotes()
        if not remotes:
            raise GitCommandError("未配置远端仓库", detail="请先添加远端（git remote add）。")
        target_remote = remote or ("origin" if "origin" in remotes else remotes[0])
        args = ["push"]
        if set_upstream:
            args.extend(["--set-upstream", target_remote])
            if branch:
                args.append(branch)
        elif branch:
            args.extend([target_remote, branch])
        if force_with_lease:
            args.append("--force-with-lease")
        res = self.runner.run(args, check=False, timeout=NETWORK_TIMEOUT)
        if not res.ok:
            combined = f"{res.stdout}\n{res.stderr}".lower()
            if "set-upstream" in combined or "no upstream branch" in combined:
                raise GitCommandError(
                    "当前分支未关联远端分支",
                    detail="请使用「发布分支」（push -u）后再推送。",
                    command="git push",
                    returncode=res.returncode,
                )
            if "non-fast-forward" in combined or "rejected" in combined:
                raise GitConflictError(
                    "推送被拒绝：远端有新提交",
                    detail="请先执行「拉取」合并远端更新后再推送。",
                    command="git push",
                    returncode=res.returncode,
                )
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", *args))
        return (res.stdout + "\n" + res.stderr).strip()

    def publish_branch(self, branch: str | None = None) -> str:
        """首次推送并把本地分支关联到远端（``push -u``）。"""
        return self.push(branch=branch, set_upstream=True)

    # ---------------------------------------------------------------- 分支
    def switch_branch(self, name: str) -> None:
        """切换到已有分支（``git switch``）。"""
        if not name:
            raise GitCommandError("分支名不能为空")
        res = self.runner.run(["switch", name], check=False, timeout=60.0)
        if not res.ok:
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", "switch", name))
        self._unborn_cache = None

    def create_branch(self, name: str, *, checkout: bool = True) -> None:
        """创建分支（可选立即切换）。"""
        name = (name or "").strip()
        if not name:
            raise GitCommandError("分支名不能为空")
        if not checkout:
            res = self.runner.run(["branch", name], check=False, timeout=30.0)
            if not res.ok:
                raise classify_error(res.stderr, res.returncode, command=("git", "branch", name))
            return
        args = ["switch", "-c", name]
        if not self._head_exists():
            args = ["switch", "--orphan", name]
        res = self.runner.run(args, check=False, timeout=60.0)
        if not res.ok:
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", *args))
        self._unborn_cache = None

    def delete_branch(self, name: str, *, force: bool = False) -> None:
        """删除本地分支。"""
        args = ["branch", "-D" if force else "-d", name]
        res = self.runner.run(args, check=False, timeout=30.0)
        if not res.ok:
            raise classify_error(res.stderr, res.returncode, command=("git", *args))

    def rename_branch(self, new_name: str) -> None:
        """重命名当前分支。"""
        res = self.runner.run(["branch", "-m", new_name], check=False, timeout=30.0)
        if not res.ok:
            raise classify_error(res.stderr, res.returncode, command=("git", "branch", "-m"))

    # ---------------------------------------------------------------- 合并 / 中止
    def merge_branch(self, name: str) -> str:
        """把指定分支合并进当前分支。

        Raises:
            GitConflictError: 产生冲突（工作区留下冲突标记，等待人工解决）。
        """
        if not name:
            raise GitCommandError("分支名不能为空")
        res = self.runner.run(["merge", "--no-edit", name], check=False, timeout=120.0)
        if not res.ok:
            combined = f"{res.stdout}\n{res.stderr}".lower()
            if "conflict" in combined or "automatic merge failed" in combined:
                raise GitConflictError(
                    "合并产生冲突",
                    detail="请解决冲突文件后暂存并提交，或执行「中止合并」。",
                    command=f"git merge {name}",
                    returncode=res.returncode,
                )
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", "merge", name))
        self._unborn_cache = None
        return (res.stdout + "\n" + res.stderr).strip()

    def abort_merge(self) -> None:
        """中止进行中的合并，恢复到合并前状态。"""
        res = self.runner.run(["merge", "--abort"], check=False, timeout=60.0)
        if not res.ok:
            raise classify_error(res.stderr, res.returncode, command=("git", "merge", "--abort"))
        self._unborn_cache = None

    def abort_rebase(self) -> None:
        """中止进行中的变基。"""
        res = self.runner.run(["rebase", "--abort"], check=False, timeout=60.0)
        if not res.ok:
            raise classify_error(res.stderr, res.returncode, command=("git", "rebase", "--abort"))
        self._unborn_cache = None

    # ---------------------------------------------------------------- 初始化
    @staticmethod
    def init(path: str, *, branch: str | None = None) -> GitRepository:
        """在目录下初始化仓库并返回仓库对象。"""
        if not os.path.isdir(path):
            raise NotARepositoryError("目标目录不存在", detail=path)
        runner = GitRunner(cwd=path)
        args = ["init"]
        if branch:
            args.extend(["-b", branch])
        res = runner.run(args, check=False, timeout=60.0)
        if not res.ok:
            raise classify_error(res.stderr or res.stdout, res.returncode, command=("git", "init"))
        return GitRepository(path, runner=GitRunner(cwd=path))

    def add_remote(self, name: str, url: str) -> None:
        """添加远端。"""
        res = self.runner.run(["remote", "add", name, url], check=False, timeout=20.0)
        if not res.ok:
            raise classify_error(res.stderr, res.returncode, command=("git", "remote", "add"))

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _require_paths(paths: list[str]) -> None:
        if not paths:
            raise GitCommandError("未指定文件")


class GitService:
    """按仓库根缓存 ``GitRepository``，避免重复创建 runner。

    生命周期与 App 一致；工作区切换 / 仓库初始化后调用 :meth:`invalidate`。
    """

    def __init__(self) -> None:
        self._repos: dict[str, GitRepository] = {}

    def repository(self, root: str) -> GitRepository:
        key = os.path.normpath(root)
        repo = self._repos.get(key)
        if repo is None:
            repo = GitRepository(key)
            self._repos[key] = repo
        return repo

    def invalidate(self, root: str | None = None) -> None:
        if root is None:
            self._repos.clear()
        else:
            self._repos.pop(os.path.normpath(root), None)
