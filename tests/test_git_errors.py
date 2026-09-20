"""``services.git.errors`` 测试：错误分类与用户提示。

分类错误会让 UI 给出误导性的建议（例如把「认证失败」提示成「检查网络」），
因此每条规则都用真实 git 输出片段钉住。
"""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "fatal: not a git repository (or any of the parent directories): .git",
            NotARepositoryError,
        ),
        (
            "fatal: unable to access 'https://github.com/x/y.git/': "
            "Could not resolve host: github.com",
            GitNetworkError,
        ),
        (
            "fatal: Authentication failed for 'https://example.com/repo.git/'",
            GitAuthError,
        ),
        ("git@github.com: Permission denied (publickey).", GitAuthError),
        (
            "error: unable to create file docs/a.md: Permission denied",
            GitPermissionError,
        ),
        (
            "error: Your local changes would be overwritten; merge conflict detected",
            GitConflictError,
        ),
        (
            "error: object file .git/objects/ab/cdef is empty\nfatal: loose object is corrupt",
            GitRepositoryCorruptError,
        ),
    ],
)
def test_classify_by_stderr(stderr, expected):
    err = classify_error(stderr, 128, command=("git", "x"))
    assert isinstance(err, expected)
    assert err.kind == expected.kind
    assert err.detail
    assert err.command == "git x"


def test_classify_auth_before_permission():
    """`Permission denied (publickey)` 必须归为认证而不是本地权限。"""
    err = classify_error("Permission denied (publickey)", 128)
    assert isinstance(err, GitAuthError)


def test_classify_unknown_falls_back_to_command_error():
    err = classify_error("fatal: something entirely unexpected", 1)
    assert type(err) is GitCommandError
    assert err.kind == "command"


def test_network_beats_permission_when_both_present():
    """远端不可达时 git 常同时输出 unable to access 与权限字样，应判网络。"""
    err = classify_error(
        "fatal: unable to access 'http://h/r.git/': Failed to connect: connection refused",
        128,
    )
    assert isinstance(err, GitNetworkError)


def test_classify_empty_stderr():
    err = classify_error("", 1)
    assert isinstance(err, GitCommandError)
    assert err.detail == ""


def test_detail_is_truncated_to_tail_lines():
    text = "\n".join(f"line{i}" for i in range(40)) + "\nfatal: not a git repository"
    err = classify_error(text, 128)
    assert err.detail.count("\n") <= 11
    assert "not a git repository" in err.detail
    assert "line0" not in err.detail


@pytest.mark.parametrize(
    "err",
    [
        GitNotFoundError("x"),
        NotARepositoryError("x"),
        GitNetworkError("x"),
        GitAuthError("x"),
        GitPermissionError("x"),
        GitConflictError("x"),
        GitRepositoryCorruptError("x"),
        GitTimeoutError("x"),
        GitCommandError("x"),
    ],
)
def test_every_error_has_friendly_message_and_optional_hint(err):
    msg = friendly_message(err)
    assert msg and msg.endswith(("。", "。 "))
    assert isinstance(friendly_hint(err), str)


def test_friendly_message_embeds_stderr_tail():
    err = classify_error("fatal: unable to access 'http://h/': Could not resolve host: h", 128)
    msg = friendly_message(err)
    assert "网络" in msg


def test_exception_is_picklable_attributes():
    err = GitError("boom", detail="d", command="git status", returncode=9)
    assert str(err) == "boom"
    assert err.returncode == 9
    assert err.label == "Git 操作失败"


def test_timeout_error_label():
    assert GitTimeoutError("x").label == "操作超时"
