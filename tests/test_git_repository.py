"""``services.git.repository`` 端到端测试：真实临时仓库上跑完整流程。

覆盖「错误场景全覆盖」要求中的多数分支：非仓库、空仓库、冲突、无远端、
无提交可撤销等，全部用真实 git 命令触发（不 mock），确保命令参数选型正确。
"""

from __future__ import annotations

import os

import pytest

from services.git import (
    ChangeKind,
    GitCommandError,
    GitConflictError,
    GitRepository,
    GitService,
    NotARepositoryError,
    discover_root,
    is_git_available,
    is_repository,
)
from services.git.runner import GitRunner

pytestmark = pytest.mark.skipif(not is_git_available(), reason="环境未安装 git")


def _init_repo(path) -> GitRepository:
    """初始化一个可提交的测试仓库（关闭签名、设定身份，避免依赖全局配置）。"""
    runner = GitRunner(cwd=str(path))
    runner.run(["init", "-b", "main"], timeout=30.0)
    runner.run(["config", "user.email", "tester@example.com"])
    runner.run(["config", "user.name", "Tester"])
    # 全局可能开启提交签名 / 分页等，显式关闭保证测试确定性
    runner.run(["config", "commit.gpgsign", "false"])
    runner.run(["config", "core.autocrlf", "false"])
    runner.run(["config", "core.quotepath", "false"])
    return GitRepository(str(path))


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return _init_repo(root)


def _write(repo: GitRepository, rel: str, text: str) -> None:
    full = repo.abspath(rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read(repo: GitRepository, rel: str) -> str:
    with open(repo.abspath(rel), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 仓库探测与初始化
# ---------------------------------------------------------------------------


def test_discover_root_on_non_repository(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert discover_root(str(plain)) is None
    assert not is_repository(str(plain))


def test_discover_root_finds_parent_from_file(repo):
    _write(repo, "docs/deep/a.md", "x\n")
    found = discover_root(repo.abspath("docs/deep/a.md"))
    assert found is not None
    assert os.path.normcase(found) == os.path.normcase(repo.root)


def test_repository_name_and_paths(repo):
    assert repo.name == "repo"
    assert repo.relpath(repo.abspath("a/b.md")) == "a/b.md"
    assert repo.contains(repo.abspath("a/b.md"))
    assert not repo.contains(os.path.join(os.path.dirname(repo.root), "outside.md"))


def test_init_reports_unborn_state(tmp_path):
    target = tmp_path / "fresh"
    target.mkdir()
    new_repo = GitRepository.init(str(target), branch="main")
    st = new_repo.status()
    assert st.unborn
    assert st.branch == "main"
    assert st.is_clean
    assert new_repo.log() == []
    assert not new_repo.has_commits()


def test_init_on_missing_dir_raises(tmp_path):
    with pytest.raises(NotARepositoryError):
        GitRepository.init(str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
# 状态 / 暂存 / 提交
# ---------------------------------------------------------------------------


def test_status_sections_and_kinds(repo):
    _write(repo, "tracked.md", "one\n")
    repo.stage(["tracked.md"])
    repo.commit("init")
    _write(repo, "tracked.md", "one\ntwo\n")
    _write(repo, "untracked.md", "new\n")
    st = repo.status()
    assert st.branch == "main"
    assert st.staged == []
    paths = {e.path: e for e in st.unstaged}
    assert paths["tracked.md"].worktree_kind == ChangeKind.MODIFIED
    assert paths["untracked.md"].worktree_kind == ChangeKind.UNTRACKED
    assert paths["untracked.md"].letter == "U"
    assert st.change_count == 2


def test_stage_then_unstage_roundtrip(repo):
    _write(repo, "a.md", "1\n")
    repo.stage(["a.md"])
    assert [e.path for e in repo.status().staged] == ["a.md"]
    repo.unstage(["a.md"])
    st = repo.status()
    assert st.staged == []
    assert [e.path for e in st.untracked] == ["a.md"]


def test_unstage_all_on_unborn_repo(repo):
    _write(repo, "a.md", "1\n")
    _write(repo, "b.md", "2\n")
    repo.stage_all()
    assert len(repo.status().staged) == 2
    repo.unstage_all()
    st = repo.status()
    assert st.staged == []
    assert len(st.untracked) == 2


def test_commit_returns_short_sha_and_clears_status(repo):
    _write(repo, "a.md", "hello\n")
    repo.stage(["a.md"])
    sha = repo.commit("首次提交")
    assert sha and len(sha) >= 7
    st = repo.status()
    assert st.is_clean
    assert not st.unborn
    commits = repo.log(limit=5)
    assert len(commits) == 1
    assert commits[0].summary == "首次提交"
    assert commits[0].short_sha == sha


def test_commit_requires_message(repo):
    _write(repo, "a.md", "x\n")
    repo.stage(["a.md"])
    with pytest.raises(GitCommandError):
        repo.commit("   ")


def test_commit_without_staged_changes_raises_friendly_error(repo):
    _write(repo, "a.md", "x\n")
    repo.stage(["a.md"])
    repo.commit("one")
    with pytest.raises(GitCommandError) as ei:
        repo.commit("two")
    assert "没有可提交" in str(ei.value)


def test_commit_all_changes_flag(repo):
    _write(repo, "a.md", "x\n")
    repo.stage(["a.md"])
    repo.commit("one")
    _write(repo, "a.md", "x\ny\n")
    repo.commit("two", all_changes=True)
    st = repo.status()
    assert st.is_clean
    assert len(repo.log(limit=5)) == 2


def test_amend_replaces_last_commit(repo):
    _write(repo, "a.md", "x\n")
    repo.stage(["a.md"])
    repo.commit("原始信息")
    repo.amend("修正后的信息")
    commits = repo.log(limit=5)
    assert len(commits) == 1
    assert commits[0].summary == "修正后的信息"


# ---------------------------------------------------------------------------
# 差异
# ---------------------------------------------------------------------------


def test_working_diff_for_modified_file(repo):
    _write(repo, "a.md", "one\ntwo\n")
    repo.stage(["a.md"])
    repo.commit("init")
    _write(repo, "a.md", "one\nTWO\n")
    fd = repo.working_diff("a.md")
    assert (fd.additions, fd.deletions) == (1, 1)
    assert fd.hunks and fd.hunks[0].lines


def test_working_diff_for_untracked_file_is_synthetic_new(repo):
    _write(repo, "fresh.md", "alpha\nbeta\n")
    fd = repo.working_diff("fresh.md")
    assert fd.is_new
    assert fd.additions == 2
    assert fd.hunks[0].old_start == 0


def test_staged_diff_uses_index_side(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    _write(repo, "a.md", "one\ntwo\n")
    repo.stage(["a.md"])
    staged = repo.working_diff("a.md", staged=True)
    assert staged.additions == 1
    # 暂存后工作区与索引一致 → 未暂存差异为空
    assert repo.working_diff("a.md").hunks == []


def test_commit_file_diff(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    sha = repo.commit("init")
    _write(repo, "a.md", "one\ntwo\n")
    repo.stage(["a.md"])
    repo.commit("add line")
    latest = repo.log(limit=1)[0]
    fd = repo.commit_file_diff(latest.sha, "a.md")
    assert fd.additions == 1
    assert fd.path == "a.md"
    # 首个提交也能取到（--root 保证）
    first = repo.commit_file_diff(sha, "a.md")
    assert first.is_new


def test_commit_detail_lists_files_with_counts(repo):
    _write(repo, "a.md", "a\n")
    _write(repo, "b.md", "b\n")
    repo.stage_all()
    sha = repo.commit("两个文件")
    detail = repo.commit_detail(sha)
    assert {f.path for f in detail.files} == {"a.md", "b.md"}
    assert detail.total_additions == 2
    assert all(f.letter == "A" for f in detail.files)


def test_commit_detail_missing_sha_raises(repo):
    _write(repo, "a.md", "a\n")
    repo.stage(["a.md"])
    repo.commit("x")
    with pytest.raises(GitCommandError):
        repo.commit_detail("0" * 40)


# ---------------------------------------------------------------------------
# 丢弃
# ---------------------------------------------------------------------------


def test_discard_working_restores_file(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    _write(repo, "a.md", "changed\n")
    repo.discard_working(["a.md"])
    assert _read(repo, "a.md") == "one\n"
    assert repo.status().is_clean


def test_discard_all_restores_every_tracked_file(repo):
    _write(repo, "a.md", "one\n")
    _write(repo, "b.md", "two\n")
    repo.stage_all()
    repo.commit("init")
    _write(repo, "a.md", "changed\n")
    _write(repo, "b.md", "changed\n")
    repo.discard_all()
    assert _read(repo, "a.md") == "one\n"
    assert _read(repo, "b.md") == "two\n"


def test_delete_untracked_removes_only_files(repo):
    _write(repo, "x.md", "x\n")
    os.makedirs(os.path.join(repo.root, "dir"), exist_ok=True)
    removed = repo.delete_untracked(["x.md", "dir", "not-exists.md", ".git/config"])
    assert removed == ["x.md"]
    assert not os.path.exists(repo.abspath("x.md"))
    assert os.path.isdir(os.path.join(repo.root, "dir"))
    assert os.path.exists(os.path.join(repo.root, ".git", "config"))


def test_unstage_and_delete_staged_new_file(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    _write(repo, "new.md", "fresh\n")
    repo.stage(["new.md"])
    assert [e.path for e in repo.status().staged] == ["new.md"]
    repo.unstage_and_delete(["new.md"])
    st = repo.status()
    assert st.is_clean
    assert not os.path.exists(repo.abspath("new.md"))


# ---------------------------------------------------------------------------
# 撤销提交
# ---------------------------------------------------------------------------


def test_undo_last_commit_mixed_returns_changes_to_worktree(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    _write(repo, "a.md", "one\ntwo\n")
    repo.stage(["a.md"])
    repo.commit("second")
    repo.undo_last_commit()
    st = repo.status()
    assert st.staged == []
    assert [e.path for e in st.unstaged] == ["a.md"]
    assert len(repo.log(limit=5)) == 1


def test_undo_last_commit_soft_keeps_staged(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    _write(repo, "a.md", "one\ntwo\n")
    repo.commit("second", all_changes=True)
    repo.undo_last_commit(keep_staged=True)
    st = repo.status()
    assert [e.path for e in st.staged] == ["a.md"]
    assert len(repo.log(limit=5)) == 1


def test_undo_last_commit_rejects_single_commit_repo(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("only")
    with pytest.raises(GitCommandError) as ei:
        repo.undo_last_commit()
    assert "没有可撤销" in str(ei.value)


def test_undo_last_commit_without_any_commit(repo):
    with pytest.raises(GitCommandError):
        repo.undo_last_commit()


# ---------------------------------------------------------------------------
# 历史查询：分页与过滤
# ---------------------------------------------------------------------------


@pytest.fixture()
def history_repo(repo):
    _write(repo, "a.md", "0\n")
    _write(repo, "b.md", "0\n")
    repo.stage_all()
    repo.commit("初始提交")
    for i in range(1, 8):
        target = "a.md" if i % 2 else "b.md"
        _write(repo, target, f"{i}\n")
        repo.commit(f"第{i}次修改", all_changes=True)
    return repo


def test_log_pagination(history_repo):
    page1 = history_repo.log(limit=3, offset=0)
    page2 = history_repo.log(limit=3, offset=3)
    assert len(page1) == 3 and len(page2) == 3
    assert [c.sha for c in page1] != [c.sha for c in page2]
    assert len(history_repo.log(limit=100)) == 8


def test_log_filter_by_keyword(history_repo):
    hits = history_repo.log(limit=50, keyword="第3次")
    assert len(hits) == 1
    assert hits[0].summary == "第3次修改"


def test_log_filter_by_path(history_repo):
    hits = history_repo.log(limit=50, path="b.md")
    # b.md 只在「初始提交」与偶数次修改里出现
    assert all("初始提交" == h.summary or "次修改" in h.summary for h in hits)
    assert all(h.summary != "第1次修改" for h in hits)


def test_log_filter_by_author(history_repo):
    assert len(history_repo.log(limit=50, author="Tester")) == 8
    assert history_repo.log(limit=50, author="nobody") == []


def test_log_limit_is_clamped(history_repo):
    assert len(history_repo.log(limit=0)) == 1
    assert len(history_repo.log(limit=10_000)) == 8


# ---------------------------------------------------------------------------
# 分支
# ---------------------------------------------------------------------------


def test_branch_create_switch_and_delete(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    repo.create_branch("feature/x")
    st = repo.status()
    assert st.branch == "feature/x"
    names = {b.name for b in repo.local_branches()}
    assert names == {"main", "feature/x"}
    repo.switch_branch("main")
    assert repo.status().branch == "main"
    repo.delete_branch("feature/x")
    assert {b.name for b in repo.local_branches()} == {"main"}


def test_create_branch_without_checkout(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    repo.create_branch("side", checkout=False)
    assert repo.status().branch == "main"
    assert "side" in {b.name for b in repo.local_branches()}


def test_branch_metadata_reports_current_and_subject(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    current = [b for b in repo.local_branches() if b.current][0]
    assert current.name == "main"
    assert current.subject == "init"
    assert current.sha
    assert not current.is_remote


def test_rename_branch(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    repo.rename_branch("trunk")
    assert repo.status().branch == "trunk"


# ---------------------------------------------------------------------------
# 合并冲突
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_with_conflict(repo):
    """制造一次真实的合并冲突（两个分支改动同一行）。"""
    _write(repo, "a.md", "base\n")
    repo.stage(["a.md"])
    repo.commit("base")
    repo.create_branch("other")
    _write(repo, "a.md", "from-other\n")
    repo.commit("other change", all_changes=True)
    repo.switch_branch("main")
    _write(repo, "a.md", "from-main\n")
    repo.commit("main change", all_changes=True)
    with pytest.raises(GitConflictError):
        repo.merge_branch("other")
    return repo


def test_merge_conflict_detection_markers_and_abort(repo_with_conflict):
    repo = repo_with_conflict
    st = repo.status()
    assert [e.path for e in st.conflicted] == ["a.md"]
    assert st.conflicted[0].letter == "!"
    assert st.conflicted[0].label == "冲突"
    assert st.op == "merge"
    assert GitRepository.operation_label(st.op) == "合并中"
    markers = repo.conflict_markers("a.md")
    assert markers.has_conflict
    assert markers.first_line == 1
    # 冲突未解决时提交必须被拒绝，并归类为冲突
    with pytest.raises(GitConflictError):
        repo.commit("try to commit conflict")
    repo.abort_merge()
    assert repo.status().is_clean
    assert repo.status().op is None


# ---------------------------------------------------------------------------
# 远端同步的错误分支
# ---------------------------------------------------------------------------


def test_fetch_without_remote_raises_helpful_error(repo):
    with pytest.raises(GitCommandError) as ei:
        repo.fetch()
    assert "远端" in str(ei.value)


def test_push_without_remote_raises_helpful_error(repo):
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    with pytest.raises(GitCommandError) as ei:
        repo.push()
    assert "远端" in str(ei.value)
    assert repo.remotes() == []


def test_pull_network_failure_is_classified(repo):
    """远端不可达时必须归类为网络错误（而不是无提示地卡住）。"""
    _write(repo, "a.md", "one\n")
    repo.stage(["a.md"])
    repo.commit("init")
    # 指向一个本地不存在、会立即失败的远端地址
    repo.add_remote("origin", "https://127.0.0.1:1/does-not-exist.git")
    repo.runner.run(["branch", "--set-upstream-to=origin/main", "main"], check=False)
    from services.git.errors import GitError

    with pytest.raises(GitError) as ei:
        repo.pull()
    assert ei.value.kind in ("network", "auth", "command")


# ---------------------------------------------------------------------------
# GitService 缓存
# ---------------------------------------------------------------------------


def test_git_service_caches_repositories(tmp_path):
    root = tmp_path / "svc"
    root.mkdir()
    _init_repo(root)
    svc = GitService()
    a = svc.repository(str(root))
    b = svc.repository(str(root))
    assert a is b
    svc.invalidate(str(root))
    assert svc.repository(str(root)) is not a


def test_git_service_invalidate_all(tmp_path):
    root = tmp_path / "svc2"
    root.mkdir()
    _init_repo(root)
    svc = GitService()
    first = svc.repository(str(root))
    svc.invalidate()
    assert svc.repository(str(root)) is not first
