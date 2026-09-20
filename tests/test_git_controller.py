"""``app._git_controller`` 集成测试：装配槽位 + 端到端 Git 流程（真实临时仓库）。

控制器把仓储层（同步 git 子进程）统一交给 ``page.run_task`` 线程化执行。测试用
一个「手动泵」假页面（``_FakePage.run_task`` 只登记任务，不真正调度）把异步部分
变成确定性执行：``_FakePage.drain()`` 反复取出并用 ``asyncio.run`` 跑完，直到没有
新任务——不引入线程 / sleep，断言的是真实 git 命令的最终效果。

覆盖：
1. 探测（Git 可用性 / 仓库根 / 分支）与「非仓库」降级
2. 更改 → 暂存 → 提交 → 撤销提交的完整闭环
3. 差异视图打开（含元信息）与 Escape 覆盖层关闭
4. 面板入口 / 视图切换 / 单文件历史筛选
5. 失败上报（无远端推送）转成面板红条 + 状态栏 + SnackBar
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app._git_controller import build_git_controller
from services.git.repository import is_git_available
from services.git.runner import GitRunner

pytestmark = pytest.mark.skipif(not is_git_available(), reason="环境未安装 git")


# ---------------------------------------------------------------------------
# 假 ctx / 假页面
# ---------------------------------------------------------------------------


class _Ref:
    """最小 ref：只有 ``current``。"""

    def __init__(self, value=None) -> None:
        self.current = value


class _FakePage:
    """登记 ``run_task`` 提交的协程函数，由 ``drain`` 同步执行。"""

    def __init__(self) -> None:
        self.tasks: list[tuple] = []
        self.invocations = 0

    def run_task(self, fn, *args) -> None:
        self.tasks.append((fn, args))

    def drain(self, max_rounds: int = 40) -> int:
        """跑完当前及后续派生的全部任务（含 ``refresh`` 的 0.12s 去抖）。"""
        rounds = 0
        while self.tasks and rounds < max_rounds:
            pending, self.tasks = self.tasks, []
            rounds += 1
            for fn, args in pending:
                self.invocations += 1
                asyncio.run(fn(*args))
        return rounds


def _defaults() -> dict:
    """全新一套默认字段（ref 必须每个实例独享，不能跨测试共享可变对象）。"""
    return {
        "settings": {},
        "page_ref": _Ref(None),
        "tabs_ref": _Ref([]),
        "active_index_ref": _Ref(0),
        "git_service_ref": _Ref(None),
        "git_busy_ref": _Ref(False),
        "git_refresh_token": _Ref(0),
        "git_dialog_seq_ref": _Ref(0),
        "git_commit_message_ref": _Ref(""),
        "git_available": False,
        "git_version": "",
        "git_workspace": None,
        "git_root": None,
        "git_status": None,
        "git_error": None,
        "git_busy": False,
        "git_view": "changes",
        "git_branches": None,
        "git_history": None,
        "git_history_has_more": False,
        "git_history_loading": False,
        "git_history_filter": None,
        "git_commit_details": None,
        "git_expanded_commits": frozenset(),
        "git_commit_push": False,
        "git_commit_seq": 0,
        "git_active_path": None,
        "git_diff_open": False,
        "git_diff": None,
        "git_diff_meta": None,
        "git_diff_mode": "unified",
        "git_branch_menu_open": False,
    }


class FakeCtx:
    """按名自动生成 ``set_xxx`` 写入器的假 AppContext。

    控制器只依赖「读字段 + 调 ``set_<field>``」这一约定，因此这里不逐个手写
    23 个 setter：``__getattr__`` 见到 ``set_`` 前缀就返回一个把首个位置参数写进
    同名字段的闭包，等价于 ``use_state`` setter 的观测面。
    """

    def __init__(self, **overrides) -> None:
        object.__setattr__(self, "_store", {**_defaults(), **overrides})
        object.__setattr__(self, "snacks", [])
        object.__setattr__(self, "status_messages", [])
        object.__setattr__(self, "file_dialogs", [])
        object.__setattr__(self, "jumps", [])
        object.__setattr__(self, "fs_bumps", 0)
        object.__setattr__(self, "setting_writes", [])

    # -- 读取 / 自动 setter --
    def __getattr__(self, name: str):
        store = object.__getattribute__(self, "_store")
        if name in store:
            return store[name]
        if name.startswith("set_"):
            field = name[4:]

            def _setter(value=None, *_a, **_kw):
                store[field] = value

            return _setter
        raise AttributeError(name)

    # -- 跨控制器依赖（手工记录，便于断言）--
    def show_snack(self, msg: str) -> None:
        self.snacks.append(msg)

    def set_status_message(self, msg: str, kind: str = "info") -> None:
        self._store["status_message"] = msg
        self.status_messages.append((msg, kind))

    def update_setting(self, key: str, value) -> None:
        self.setting_writes.append((key, value))
        self._store["settings"] = {**self._store["settings"], key: value}

    def open_file_and_jump(self, path: str, li: int, off) -> None:
        self.jumps.append((path, li, off))

    def set_file_dialog(self, dialog) -> None:
        self.file_dialogs.append(dialog)

    def bump_fs_version(self) -> None:
        self.fs_bumps += 1


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _make_repo(root: Path) -> None:
    """初始化可提交的仓库（关闭签名 / 换行转换，保证跨平台确定性）。"""
    runner = GitRunner(cwd=str(root))
    runner.run(["init", "-b", "main"], timeout=30.0)
    runner.run(["config", "user.email", "tester@example.com"])
    runner.run(["config", "user.name", "Tester"])
    runner.run(["config", "commit.gpgsign", "false"])
    runner.run(["config", "core.autocrlf", "false"])


def _write(root: Path, rel: str, text: str) -> None:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _git(root: Path, *args: str) -> str:
    return GitRunner(cwd=str(root)).run(list(args), timeout=30.0).stdout


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


@pytest.fixture()
def workspace(tmp_path):
    """已提交一个 ``a.md`` 的仓库，作为工作区。"""
    root = tmp_path / "ws"
    root.mkdir()
    _make_repo(root)
    _write(root, "a.md", "# 标题\n\n初始内容\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: init")
    return root


@pytest.fixture()
def env(workspace):
    """(ctx, cbs, page) 三元组：控制器已按工作区装配完成。"""
    page = _FakePage()
    ctx = FakeCtx(
        page_ref=_Ref(page),
        tabs_ref=_Ref([{"file_path": str(workspace / "a.md")}]),
        settings={"workspace_folder": str(workspace), "git_history_page_size": 20},
    )
    cbs = build_git_controller(ctx)
    return ctx, cbs, page


def _refresh(env) -> None:
    ctx, cbs, page = env
    cbs["git_refresh"]()
    page.drain()
    assert ctx.git_root, "刷新后应探测到仓库根"


# ---------------------------------------------------------------------------
# 探测 / 降级
# ---------------------------------------------------------------------------


def test_controller_exposes_all_documented_slots(env):
    """槽位集合是 App 装配契约的一部分：缺一个就是「按钮点了没反应」。"""
    _ctx, cbs, _page = env
    expected = {
        "git_refresh", "git_open_panel", "git_init_repo", "git_set_view",
        "git_escape", "git_confirm_dialog_action",
        "git_stage", "git_unstage", "git_stage_all", "git_unstage_all",
        "git_discard", "git_discard_all",
        "git_open_diff", "git_close_diff", "git_set_diff_mode", "git_diff_jump",
        "git_diff_stage", "git_diff_unstage", "git_diff_discard", "git_open_in_editor",
        "git_commit", "git_toggle_commit_push", "git_undo_commit",
        "git_fetch", "git_pull", "git_push", "git_abort_operation",
        "git_open_branch_dialog", "git_close_branch_menu", "git_refresh_branches",
        "git_switch_branch", "git_create_branch", "git_delete_branch", "git_merge_branch",
        "git_jump_to_conflict",
        "git_load_history", "git_load_more_history", "git_set_history_filter",
        "git_clear_history_filter", "git_toggle_commit",
        "git_history_file_diff", "git_open_file_history",
    }
    assert expected <= set(cbs), sorted(expected - set(cbs))


def test_refresh_detects_repository_branch_and_clean_status(env, workspace):
    ctx, _cbs, _page = env
    _refresh(env)

    assert ctx.git_available is True
    assert ctx.git_version
    assert _same(ctx.git_root, str(workspace))
    assert _same(ctx.git_workspace, str(workspace))
    assert ctx.git_error is None
    assert ctx.git_status.is_clean
    assert [b.name for b in ctx.git_branches if b.current] == ["main"]


def test_refresh_on_non_repository_degrades_quietly(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _write(plain, "note.md", "hello\n")
    page = _FakePage()
    ctx = FakeCtx(
        page_ref=_Ref(page),
        tabs_ref=_Ref([{"file_path": str(plain / "note.md")}]),
        settings={"workspace_folder": str(plain)},
    )
    cbs = build_git_controller(ctx)
    cbs["git_refresh"]()
    page.drain()

    # Git 可用但没有仓库：不报错、不残留状态，面板应展示「初始化仓库」入口
    assert ctx.git_available is True
    assert ctx.git_root is None
    assert ctx.git_status is None
    assert ctx.git_error is None
    assert ctx.git_branches == []


def test_refresh_without_workspace_is_noop(tmp_path):
    """既没有工作区设置也没有已保存文件时，探测不应抛错（首次启动场景）。"""
    page = _FakePage()
    ctx = FakeCtx(
        page_ref=_Ref(page),
        tabs_ref=_Ref([{"file_path": None}]),
        settings={},
    )
    cbs = build_git_controller(ctx)
    cbs["git_refresh"]()
    page.drain()
    assert ctx.git_root is None
    assert ctx.git_status is None


# ---------------------------------------------------------------------------
# 更改 → 暂存 → 提交 → 撤销
# ---------------------------------------------------------------------------


def test_stage_commit_and_undo_roundtrip(env, workspace):
    ctx, cbs, page = env
    _refresh(env)

    # 工作区改动 → 未暂存分区
    _write(workspace, "a.md", "# 标题\n\n改过的内容\n")
    cbs["git_refresh"]()
    page.drain()
    assert [e.path for e in ctx.git_status.unstaged] == ["a.md"]
    assert ctx.git_status.staged == []

    # 单文件暂存 → 分区搬运
    cbs["git_stage"](["a.md"])
    page.drain()
    assert [e.path for e in ctx.git_status.staged] == ["a.md"]
    assert ctx.git_status.unstaged == []
    # 写操作结束后忙碌态必须释放（否则后续所有操作被并发保护拦下）
    assert ctx.git_busy is False
    assert ctx.git_busy_ref.current is False

    # 取消暂存 → 回到工作区
    cbs["git_unstage"](["a.md"])
    page.drain()
    assert [e.path for e in ctx.git_status.unstaged] == ["a.md"]

    # 提交
    cbs["git_stage_all"]()
    page.drain()
    ctx.git_commit_message_ref.current = "feat: 集成测试提交"
    cbs["git_commit"](False)
    page.drain()
    assert ctx.git_status.is_clean
    assert ctx.git_commit_seq == 1
    assert ctx.git_commit_message_ref.current == ""
    assert "feat: 集成测试提交" in _git(workspace, "log", "-1", "--pretty=%s")

    # 撤销上一次提交 → 更改回到工作区（内容未丢）
    cbs["git_undo_commit"]()
    page.drain()
    assert [e.path for e in ctx.git_status.unstaged] == ["a.md"]
    assert "改过的内容" in (workspace / "a.md").read_text(encoding="utf-8")
    assert "feat: 集成测试提交" not in _git(workspace, "log", "-1", "--pretty=%s")


def test_commit_without_message_only_warns(env):
    """空提交信息不发车：只提示，不占忙碌态、不产生提交。"""
    ctx, cbs, page = env
    _refresh(env)
    cbs["git_commit"](False)
    assert page.invocations == 0 or ctx.git_commit_seq == 0
    assert any("提交信息" in m for m, _k in ctx.status_messages)
    assert ctx.snacks  # 警告会额外弹 SnackBar


def test_discard_restores_tracked_file_and_removes_untracked(env, workspace):
    ctx, cbs, page = env
    _refresh(env)

    _write(workspace, "a.md", "乱改的内容\n")
    _write(workspace, "new.md", "新建的文件\n")
    cbs["git_refresh"]()
    page.drain()
    paths = {e.path for e in ctx.git_status.unstaged}
    assert {"a.md", "new.md"} <= paths

    # 破坏性操作先弹二次确认（文件对话框），确认后才真正执行
    cbs["git_discard"]("a.md")
    assert ctx.file_dialogs[-1]["action"] == "git_discard"
    assert ctx.file_dialogs[-1]["danger"] is True
    cbs["git_confirm_dialog_action"]("git_discard", "a.md")
    page.drain()
    assert "乱改的内容" not in (workspace / "a.md").read_text(encoding="utf-8")

    cbs["git_discard_all"]()
    assert ctx.file_dialogs[-1]["action"] == "git_discard_all"
    cbs["git_confirm_dialog_action"]("git_discard_all", None)
    page.drain()
    assert not (workspace / "new.md").exists()
    assert ctx.git_status.is_clean


def test_discard_staged_new_file_unstages_and_deletes(env, workspace):
    """已暂存的新增文件：丢弃 = 从索引移除 + 删盘（HEAD 里本就没有它）。"""
    ctx, cbs, page = env
    _refresh(env)
    _write(workspace, "b.md", "新文件\n")
    cbs["git_stage_all"]()
    page.drain()
    assert [e.path for e in ctx.git_status.staged] == ["b.md"]

    cbs["git_confirm_dialog_action"]("git_discard", "b.md")
    page.drain()
    assert not (workspace / "b.md").exists()
    assert ctx.git_status.is_clean


def test_discard_all_leaves_staged_section_untouched(env, workspace):
    """「丢弃所有工作区更改」只覆盖它所在的分区（未暂存 + 未跟踪）。"""
    ctx, cbs, page = env
    _refresh(env)
    _write(workspace, "staged.md", "已暂存\n")
    _write(workspace, "loose.md", "未跟踪\n")
    cbs["git_stage"](["staged.md"])
    page.drain()
    assert [e.path for e in ctx.git_status.staged] == ["staged.md"]

    cbs["git_discard_all"]()
    cbs["git_confirm_dialog_action"]("git_discard_all", None)
    page.drain()
    assert not (workspace / "loose.md").exists()  # 未跟踪被删
    assert (workspace / "staged.md").exists()  # 已暂存不动
    assert [e.path for e in ctx.git_status.staged] == ["staged.md"]


# ---------------------------------------------------------------------------
# 差异视图
# ---------------------------------------------------------------------------


def test_open_diff_fills_view_and_jump_target(env, workspace):
    ctx, cbs, page = env
    _refresh(env)

    _write(workspace, "a.md", "# 标题\n\n改过的内容\n")
    cbs["git_refresh"]()
    page.drain()

    cbs["git_open_diff"]("a.md", False)
    page.drain()
    assert ctx.git_diff_open is True
    assert ctx.git_active_path == "a.md"
    assert ctx.git_diff is not None
    added = [ln for h in ctx.git_diff.hunks for ln in h.lines if ln.kind.value == "added"]
    assert any("改过的内容" in ln.text for ln in added)
    # 文件在磁盘上 → 元信息提供跳转目标（差异行号 ↔ 编辑器行号联动的前提）
    assert _same(ctx.git_diff_meta["jump_path"], str(workspace / "a.md"))
    assert ctx.git_diff_meta["staged"] is False

    cbs["git_diff_jump"](3)
    assert ctx.jumps and ctx.jumps[-1][0] == str(workspace / "a.md")
    assert ctx.git_diff_open is False  # 跳转即关闭覆盖层

    cbs["git_close_diff"]()
    assert ctx.git_diff_open is False
    assert ctx.git_active_path is None


def test_diff_mode_persists_to_settings(env):
    ctx, cbs, _page = env
    cbs["git_set_diff_mode"]("split")
    assert ctx.git_diff_mode == "split"
    assert ("git_diff_mode", "split") in ctx.setting_writes
    cbs["git_set_diff_mode"]("nonsense")  # 非法值忽略
    assert ctx.git_diff_mode == "split"


def test_open_diff_for_deleted_file_disables_jump(env, workspace):
    ctx, cbs, page = env
    _refresh(env)
    os.remove(workspace / "a.md")
    cbs["git_refresh"]()
    page.drain()

    cbs["git_open_diff"]("a.md", False)
    page.drain()
    assert ctx.git_diff_open is True
    assert ctx.git_diff_meta["jump_path"] is None  # 磁盘上没有该文件

    cbs["git_diff_jump"](1)
    assert ctx.jumps == []
    assert any("无法在编辑器中定位" in m for m, _k in ctx.status_messages)


# ---------------------------------------------------------------------------
# 覆盖层 Escape / 面板入口 / 历史
# ---------------------------------------------------------------------------


def test_escape_closes_branch_menu_then_diff(env):
    """Escape 逐层关闭：栈序上分支面板在差异视图之上（见 _render 根 Stack）。"""
    ctx, cbs, page = env
    _refresh(env)

    cbs["git_open_branch_dialog"]()
    page.drain()
    assert ctx.git_branch_menu_open is True
    assert cbs["git_escape"]() is True
    assert ctx.git_branch_menu_open is False

    cbs["git_open_diff"]("a.md", True)
    page.drain()
    assert ctx.git_diff_open is True
    assert cbs["git_escape"]() is True
    assert ctx.git_diff_open is False

    # 没有覆盖层时不得吞掉 Escape（否则编辑器里的 Escape 语义会失效）
    assert cbs["git_escape"]() is False


def test_open_panel_writes_sidebar_settings_and_refreshes(env):
    ctx, cbs, page = env
    cbs["git_open_panel"]()
    page.drain()
    assert ctx.settings["sidebar_panel"] == "git"
    assert ctx.settings["sidebar_open"] is True
    assert ("sidebar_panel", "git") in ctx.setting_writes
    assert ctx.git_root  # 打开面板即刷新，不需用户手点刷新


def test_set_view_history_loads_log(env):
    ctx, cbs, page = env
    _refresh(env)
    cbs["git_set_view"]("history")
    page.drain()
    assert ctx.git_view == "history"
    assert ctx.git_history and ctx.git_history[0].summary == "chore: init"


def test_open_file_history_filters_by_relative_path(env, workspace):
    ctx, cbs, page = env
    _refresh(env)
    cbs["git_open_file_history"](str(workspace / "a.md"))
    page.drain()
    assert ctx.git_history_filter == {"path": "a.md"}
    assert ctx.git_view == "history"
    assert ctx.settings["sidebar_panel"] == "git"
    assert ctx.git_history and all(c.summary for c in ctx.git_history)


def test_open_file_history_outside_repo_warns(env, tmp_path):
    ctx, cbs, _page = env
    _refresh(env)
    outside = tmp_path / "outside.md"
    outside.write_text("x\n", encoding="utf-8")
    cbs["git_open_file_history"](str(outside))
    assert ctx.git_view == "changes"  # 未切换视图
    assert any("不在 Git 仓库" in m for m, _k in ctx.status_messages)


def test_history_filter_set_and_clear(env):
    ctx, cbs, page = env
    _refresh(env)
    cbs["git_set_history_filter"]("keyword", "init")
    assert ctx.git_history_filter["keyword"] == "init"
    cbs["git_load_history"]()
    page.drain()
    assert ctx.git_history_loading is False
    cbs["git_clear_history_filter"]()
    assert ctx.git_history_filter == {}


# ---------------------------------------------------------------------------
# 分支
# ---------------------------------------------------------------------------


def test_create_switch_and_refresh_branches(env, workspace):
    ctx, cbs, page = env
    _refresh(env)

    cbs["git_create_branch"]("feature/x")
    page.drain()
    assert ctx.git_root
    assert "feature/x" in _git(workspace, "branch", "--list", "--format=%(refname:short)")

    cbs["git_switch_branch"]("main")
    page.drain()
    assert [b.name for b in ctx.git_branches if b.current] == ["main"]

    cbs["git_refresh_branches"]()
    page.drain()
    names = {b.name for b in ctx.git_branches}
    assert {"main", "feature/x"} <= names


# ---------------------------------------------------------------------------
# 失败上报
# ---------------------------------------------------------------------------


def test_push_without_remote_reports_error(env):
    """无远端推送：转成面板红条 + 状态栏 + SnackBar，且忙碌态释放。"""
    ctx, cbs, page = env
    _refresh(env)
    cbs["git_push"]()
    page.drain()

    assert ctx.git_error and "推送失败" in ctx.git_error
    assert ctx.snacks
    assert ctx.git_busy is False
    assert ctx.git_busy_ref.current is False


def test_commit_push_toggle_persists(env):
    ctx, cbs, _page = env
    cbs["git_toggle_commit_push"]()
    assert ctx.git_commit_push is True
    assert ("git_commit_push", True) in ctx.setting_writes
    cbs["git_toggle_commit_push"]()
    assert ctx.git_commit_push is False


def test_stage_without_repository_is_noop(tmp_path):
    """未探测到仓库时的操作必须静默返回，不得冒异常（面板可能仍显示旧快照）。"""
    page = _FakePage()
    ctx = FakeCtx(page_ref=_Ref(page))
    cbs = build_git_controller(ctx)
    cbs["git_stage"](["a.md"])
    cbs["git_unstage"](["a.md"])
    cbs["git_stage_all"]()
    cbs["git_discard"]("a.md")
    assert page.tasks == []  # 没有派发任何异步任务
    assert ctx.git_error is None
    assert ctx.git_busy_ref.current is False


def test_confirm_dialog_action_routes_discard_and_delete_branch(env, workspace, tmp_path):
    """文件对话框确认后的回投：Git 语义在控制器内闭环。"""
    _ctx, cbs, page = env
    _refresh(env)
    _write(workspace, "a.md", "乱改\n")
    cbs["git_refresh"]()
    page.drain()

    cbs["git_confirm_dialog_action"]("git_discard", "a.md")
    page.drain()
    assert "乱改" not in (workspace / "a.md").read_text(encoding="utf-8")

    cbs["git_create_branch"]("temp/branch")
    page.drain()
    # create_branch 创建并切换过去；要删除它必须先切回 main（git 拒绝删除当前分支）
    cbs["git_switch_branch"]("main")
    page.drain()
    cbs["git_confirm_dialog_action"]("git_delete_branch", "temp/branch")
    page.drain()
    assert "temp/branch" not in _git(workspace, "branch", "--list", "--format=%(refname:short)")
