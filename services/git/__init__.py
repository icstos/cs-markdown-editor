"""Git 版本管理服务层（纯 Python，不依赖 flet / views / app）。

分层（单向依赖，禁止反向）：

    models   ← 数据模型（状态 / 提交 / 差异 / 分支）
    errors   ← 错误分类（网络 / 权限 / 冲突 / 认证 / 损坏 / 超时）
    runner   ← 子进程执行 + 超时 + 编码 + 非交互
    porcelain← git 机器可读输出 → 数据模型（纯函数解析）
    difftext ← 统一 diff 文本 → 结构化差异 + 分栏对齐 + 冲突标记扫描
    repository ← 高层门面（GitRepository / GitService / 仓库探测）

视图层（``views/git_*``）与控制器（``app/_git_controller``）只使用本包导出的
公共符号，不直接拼 git 命令、不解析 git 输出。

典型用法::

    from services.git import GitService, discover_root, friendly_message

    root = discover_root("/path/to/workspace")
    repo = GitService().repository(root)
    st = repo.status()
    repo.stage([entry.path for entry in st.unstaged])
"""

from services.git.difftext import (
    MAX_DIFF_LINES,
    build_split_rows,
    detect_binary,
    parse_single_file_diff,
    parse_unified_diff,
    scan_merge_markers,
    synthetic_added_diff,
)
from services.git.errors import (
    GitAuthError,
    GitCommandError,
    GitConflictError,
    GitError,
    GitNetworkError,
    GitNotFoundError,
    GitPermissionError,
    GitRepositoryCorruptError,
    GitTimeoutError,
    NotARepositoryError,
    classify_error,
    friendly_hint,
    friendly_message,
)
from services.git.models import (
    BranchInfo,
    ChangeKind,
    CommitDetail,
    CommitInfo,
    DiffHunk,
    DiffLine,
    DiffLineKind,
    FileChange,
    FileDiff,
    GitStatus,
    MergeMarkers,
    SplitRow,
    StatusEntry,
    kind_label,
    status_letter,
)
from services.git.porcelain import (
    parse_branches,
    parse_log,
    parse_name_status,
    parse_numstat,
    parse_status,
)
from services.git.repository import (
    DEFAULT_PAGE_SIZE,
    GitRepository,
    GitService,
    discover_root,
    git_version,
    is_git_available,
    is_repository,
)
from services.git.runner import GitResult, GitRunner, find_git_executable

# 按「模型 → 错误 → 命令 → 仓储」分层罗列而非字母序：这份清单也是包对外契约的
# 说明文档，读者应按层理解，故显式豁免 RUF022。
__all__ = [  # noqa: RUF022
    # 模型
    "BranchInfo",
    "ChangeKind",
    "CommitDetail",
    "CommitInfo",
    "DiffHunk",
    "DiffLine",
    "DiffLineKind",
    "FileChange",
    "FileDiff",
    "GitStatus",
    "MergeMarkers",
    "SplitRow",
    "StatusEntry",
    "kind_label",
    "status_letter",
    # 错误
    "GitError",
    "GitNotFoundError",
    "NotARepositoryError",
    "GitPermissionError",
    "GitNetworkError",
    "GitAuthError",
    "GitConflictError",
    "GitRepositoryCorruptError",
    "GitTimeoutError",
    "GitCommandError",
    "classify_error",
    "friendly_message",
    "friendly_hint",
    # 解析
    "parse_status",
    "parse_log",
    "parse_name_status",
    "parse_numstat",
    "parse_branches",
    # 差异
    "MAX_DIFF_LINES",
    "build_split_rows",
    "detect_binary",
    "parse_single_file_diff",
    "parse_unified_diff",
    "scan_merge_markers",
    "synthetic_added_diff",
    # 仓库
    "DEFAULT_PAGE_SIZE",
    "GitRepository",
    "GitService",
    "discover_root",
    "git_version",
    "is_git_available",
    "is_repository",
    # 运行器
    "GitResult",
    "GitRunner",
    "find_git_executable",
]
