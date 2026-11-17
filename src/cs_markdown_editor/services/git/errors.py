"""Git 错误分类：把子进程返回的 stderr / 退出码映射为**可操作**的异常类型。

设计目标（对应「错误场景全覆盖」要求）：网络不可达、认证失败、权限不足、
合并冲突、仓库损坏、命令超时、git 未安装各自独立成类，UI 层据此给出
不同处置建议（重试 / 配置凭据 / 手动解决冲突 / 初始化仓库 / 安装 Git），
而不是笼统的「操作失败」。

分类全部收敛在本模块：``repository`` 只负责调用，``controller`` 只按类型分支。
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "GitAuthError",
    "GitCommandError",
    "GitConflictError",
    "GitError",
    "GitNetworkError",
    "GitNotFoundError",
    "GitPermissionError",
    "GitRepositoryCorruptError",
    "GitTimeoutError",
    "NotARepositoryError",
    "classify_error",
    "friendly_message",
]


class GitError(Exception):
    """Git 相关异常的基类。

    Attributes:
        kind: 机器可读的类别标识（network / auth / conflict / ...）。
        detail: 原始 stderr（截断后），用于日志与「查看详情」。
        command: 触发失败的命令（可读形式），便于排查。
        returncode: 子进程退出码（本地异常为 None）。
    """

    kind: str = "unknown"
    label: str = "Git 操作失败"

    def __init__(
        self,
        message: str,
        *,
        detail: str = "",
        command: str = "",
        returncode: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.command = command
        self.returncode = returncode

    def __str__(self) -> str:  # pragma: no cover - 便于日志阅读
        return self.message


class GitNotFoundError(GitError):
    """找不到 git 可执行文件（未安装 / 不在 PATH）。"""

    kind = "not_found"
    label = "未检测到 Git"


class NotARepositoryError(GitError):
    """目录（及其父目录）不是 Git 仓库。"""

    kind = "not_a_repository"
    label = "不是 Git 仓库"


class GitPermissionError(GitError):
    """文件系统权限不足（工作区只读 / 文件被占用 / 索引不可写）。"""

    kind = "permission"
    label = "权限不足"


class GitNetworkError(GitError):
    """网络不可达 / 连接被拒 / 远端无响应。"""

    kind = "network"
    label = "网络连接失败"


class GitAuthError(GitError):
    """认证失败（凭据缺失、密码错误、SSH 公钥被拒）。"""

    kind = "auth"
    label = "身份认证失败"


class GitConflictError(GitError):
    """合并冲突：命令本身成功但结果需要人工介入。"""

    kind = "conflict"
    label = "存在合并冲突"


class GitRepositoryCorruptError(GitError):
    """仓库对象库 / 索引损坏。"""

    kind = "corrupt"
    label = "仓库数据损坏"


class GitTimeoutError(GitError):
    """命令执行超时（网络挂起、凭据交互被阻塞）。"""

    kind = "timeout"
    label = "操作超时"


class GitCommandError(GitError):
    """其余命令失败（未归类的兜底）。"""

    kind = "command"
    label = "Git 命令执行失败"


# ---------------------------------------------------------------------------
# 分类规则：按「先特异性后一般性」排序，命中即返回。
# 每条规则是 (关键词序列, 异常类)；关键词为小写子串，任一命中即算匹配。
# ---------------------------------------------------------------------------
_RULES: tuple[tuple[tuple[str, ...], type[GitError]], ...] = (
    # —— 认证（必须在「权限」之前：`Permission denied (publickey)` 是认证问题）——
    (
        (
            "permission denied (publickey",
            "authentication failed",
            "could not read username",
            "could not read password",
            "invalid credentials",
            "terminal prompts disabled",
            "fatal: authentication",
            "no such identity",
            "the requested url returned error: 401",
            "the requested url returned error: 403",
            "http 401",
            "http 403",
        ),
        GitAuthError,
    ),
    # —— 网络 ——
    (
        (
            "could not resolve host",
            "unable to access",
            "failed to connect",
            "couldn't connect to server",
            "connection timed out",
            "operation timed out",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "remote end hung up",
            "recv failure",
            "send failure",
            "ssl certificate problem",
            "early eof",
            "rpc failed",
        ),
        GitNetworkError,
    ),
    # —— 仓库损坏 ——
    (
        (
            "object file is empty",
            "loose object",
            "index file corrupt",
            "corrupt",
            "bad object",
            "not a valid object name",
            "unable to read tree",
            "unable to read sha1 file",
            "broken link",
        ),
        GitRepositoryCorruptError,
    ),
    # —— 不是仓库（须早于权限：某些文案含 "cannot open"）——
    (
        (
            "not a git repository",
            "not a repository",
            "does not have a commit checked out",
        ),
        NotARepositoryError,
    ),
    # —— 权限 ——
    (
        (
            "permission denied",
            "access is denied",
            "operation not permitted",
            "cannot open",
            "unable to create file",
            "unable to unlink",
            "read-only file system",
        ),
        GitPermissionError,
    ),
    # —— 冲突（命令成功但结果需要人工处理；也可由调用方主动抛）——
    (
        (
            "conflict",
            "automatic merge failed",
            "fix conflicts",
            "unmerged files",
            "needs merge",
            "you have not concluded your merge",
        ),
        GitConflictError,
    ),
)


def classify_error(
    stderr: str,
    returncode: int | None = None,
    *,
    command: Sequence[str] | str = (),
) -> GitError:
    """根据 stderr 文案与退出码构造具体异常。

    Args:
        stderr: 子进程标准错误（也可传合并后的输出）。
        returncode: 退出码。
        command: 触发的命令（列表或已拼好的字符串）。
    """
    text = (stderr or "").lower()
    cmd = " ".join(command) if not isinstance(command, str) else command
    # 取最后若干行作为详情：git 的末尾几行通常才是真正原因
    detail = _tail(stderr or "", 12)
    for keywords, exc_type in _RULES:
        if any(k in text for k in keywords):
            return exc_type(
                f"{exc_type.label}",
                detail=detail,
                command=cmd,
                returncode=returncode,
            )
    return GitCommandError(
        "Git 命令执行失败",
        detail=detail,
        command=cmd,
        returncode=returncode,
    )


def _tail(text: str, lines: int) -> str:
    """取末尾 N 行并做长度裁剪，避免把整段 verbose 输出塞进 UI。"""
    stripped = text.strip()
    if not stripped:
        return ""
    parts = stripped.splitlines()
    tail = "\n".join(parts[-lines:])
    return tail if len(tail) <= 2000 else tail[-2000:]


# 类别 → （用户可读提示, 建议动作）。建议动作为空串表示无需额外引导。
_HINTS: dict[str, tuple[str, str]] = {
    "not_found": (
        "未检测到 Git，请先安装 Git 并确保其在 PATH 中。",
        "安装后可点击「重新检测」刷新。",
    ),
    "not_a_repository": (
        "当前目录不是 Git 仓库。",
        "可在 Git 面板中执行「初始化仓库」。",
    ),
    "network": (
        "无法连接到远端仓库，请检查网络或代理设置。",
        "可稍后重试，或改用「获取」观察连通性。",
    ),
    "auth": (
        "远端拒绝了本次认证，凭据可能缺失或已过期。",
        "请配置 SSH 公钥或凭据管理器后重试。",
    ),
    "permission": (
        "本地文件权限不足，操作被系统拒绝。",
        "请关闭占用文件的程序，或检查目录读写权限。",
    ),
    "conflict": (
        "存在合并冲突，需要人工解决后才能继续。",
        "打开冲突文件解决标记后，暂存该文件即可继续。",
    ),
    "corrupt": (
        "仓库对象或索引已损坏。",
        "可尝试 `git fsck` 定位损坏对象后从远端重新拉取。",
    ),
    "timeout": (
        "Git 操作超时（可能是网络挂起或需要交互式输入凭据）。",
        "请检查网络，或先在终端完成一次认证。",
    ),
    "command": ("Git 命令执行失败。", ""),
    "unknown": ("Git 操作失败。", ""),
}


def friendly_message(err: BaseException) -> str:
    """把异常转成一句话用户提示（含首条建议）。"""
    kind = getattr(err, "kind", "unknown")
    tip, advice = _HINTS.get(kind, _HINTS["unknown"])
    if isinstance(err, GitError) and err.detail:
        first = err.detail.strip().splitlines()
        if first:
            line = first[-1].strip()
            # 去掉 git 的 "fatal: " 前缀，避免与我们的提示重复
            if line.lower().startswith("fatal: "):
                line = line[7:]
            if line and line.lower() not in tip.lower():
                tip = f"{tip}（{line}）"
    return f"{tip} {advice}".strip()


def friendly_hint(err: BaseException) -> str:
    """返回建议动作文案（无建议时为空串，供 UI 决定是否显示）。"""
    kind = getattr(err, "kind", "unknown")
    return _HINTS.get(kind, _HINTS["unknown"])[1]
