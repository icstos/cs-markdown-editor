"""Git 控制器：版本管理全部动作的实现（App 侧独立控制器）。

设计要点（与 ``app/_file_io_ops.py`` / ``app/_backup_controller.py`` 同构）：

- **阻塞调用一律 ``asyncio.to_thread``**：git 是子进程调用，单次 0.1~0.3s，
  直接在事件循环里跑会让窗口卡顿（fetch/pull 这种网络操作更明显）。仓储层
  （``services.git.repository``）保持纯同步，控制器负责线程化。
- **忙碌态用 ref 而非 state**：``ctx.git_busy`` 是渲染快照。并发保护必须靠
  ``ctx.git_busy_ref``——同一事件里连点两次「拉取」，两次回调读到的
  ``ctx.git_busy`` 都还是 False（state 更新尚未回流），只有 ref 能立刻拦住。
- **统一任务包装** :func:`_run`：所有动作走同一条「忙碌 → 执行 → 异常上报 →
  收尾」路径，避免每个动作各写一遍 try/finally 而漏掉某个分支的收尾
  （漏掉 `_end_busy()` 会让整个 Git 面板永久卡在「正在执行」）。
- **刷新去抖**：切标签 / 文件系统事件都会触发状态刷新，用
  ``git_refresh_token`` 令牌 + 短睡眠合并，只让最后一次真正执行 git 调用。
- **错误分级**：仓储层把 stderr 分类成 ``GitError`` 子类（网络 / 权限 / 认证 /
  冲突 / 仓库损坏 / 非仓库 / 超时），这里统一转成「用户可读消息 + 处置建议」，
  写入 ``git_error`` 状态（面板顶部红条）并提示一次。
- **破坏性操作先确认**：丢弃单文件 / 丢弃全部工作区更改 / 删除分支都经
  ``set_file_dialog``（confirm 模式）二次确认，确认后由 ``_file_dialogs`` 回投
  :func:`confirm_dialog_action` 执行——复用既有删除文件对话框的交互与样式。

动作清单（共 34 个装配槽，键名与 AppContext 字段一致）：见文件末尾 ``return``。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable

import flet as ft

from app._contracts import GitEnv
from services.git.errors import GitError, friendly_hint, friendly_message
from services.git.models import ChangeKind, kind_label
from services.git.repository import (
    DEFAULT_PAGE_SIZE,
    GitRepository,
    GitService,
    discover_root,
    git_version,
    is_git_available,
)
from views.git_diff import MODE_SPLIT, MODE_UNIFIED

#: 状态消息种类（与 views/status_bar.py 的 _STATUS_COLOR 键一致）
_KIND_INFO, _KIND_SUCCESS, _KIND_WARN, _KIND_ERROR = "info", "success", "warn", "error"


def build_git_controller(ctx: GitEnv) -> dict:
    """构造 Git 控制器闭包组（返回 ``dict[str, Callable]``）。"""

    # ==================================================================
    # 基础工具
    # ==================================================================

    def _service() -> GitService:
        """取（惰性创建）GitService：按仓库根缓存 GitRepository 实例。"""
        svc = ctx.git_service_ref.current
        if svc is None:
            svc = GitService()
            ctx.git_service_ref.current = svc
        return svc

    def _repo() -> GitRepository | None:
        """当前仓库实例（尚未探测到仓库时返回 None）。"""
        root = ctx.git_root
        return _service().repository(root) if root else None

    def _workspace_dir() -> str | None:
        """工作区目录：显式工作区优先，否则取当前激活标签所在目录。"""
        wf = ctx.settings.get("workspace_folder")
        if wf and os.path.isdir(wf):
            return wf
        ts = ctx.tabs_ref.current
        ai = ctx.active_index_ref.current
        if 0 <= ai < len(ts):
            fp = ts[ai].get("file_path")
            if fp:
                return os.path.dirname(fp) or None
        return None

    def _notify(msg: str, kind: str = _KIND_INFO) -> None:
        """轻量提示：状态栏常驻 3 秒；警告/错误额外弹一次 SnackBar。"""
        ctx.set_status_message(msg, kind)
        if kind in (_KIND_WARN, _KIND_ERROR):
            ctx.show_snack(msg)

    def _report_error(err: Exception, op: str) -> None:
        """把异常转成用户可读消息：面板红条 + 状态栏 + SnackBar。"""
        if isinstance(err, GitError):
            text = f"{op}失败：{friendly_message(err)}"
            hint = friendly_hint(err)
            if hint:
                text = f"{text}（{hint}）"
        else:
            text = f"{op}失败：{err}"
        ctx.set_git_error(text)
        _notify(text, _KIND_ERROR)

    def _guard_operation() -> bool:
        """仓库处于合并/变基中时拒绝常规操作（VS Code 同款：先解决或中止）。"""
        st = ctx.git_status
        if st is not None and st.in_operation:
            _notify(f"{st.op_label}操作进行中，请先解决冲突或中止本次操作", _KIND_WARN)
            return True
        return False

    def _start_busy() -> bool:
        """尝试进入忙碌态；已在忙碌中则拒绝（拦掉并发操作）。"""
        if ctx.git_busy_ref.current:
            _notify("已有 Git 操作正在执行，请稍候", _KIND_WARN)
            return False
        ctx.git_busy_ref.current = True
        ctx.set_git_busy(True)
        return True

    def _end_busy() -> None:
        ctx.git_busy_ref.current = False
        ctx.set_git_busy(False)

    def _schedule(fn: Callable[..., Awaitable], *args) -> None:
        """提交协程**函数**到页面事件循环。

        注意 ``page.run_task`` 要求 ``inspect.iscoroutinefunction(handler)`` 为真
        ——传协程对象会被直接判为 TypeError（而且只在日志里冒出来，表现为「点了
        没反应」），所以这里一律传函数 + 位置参数。
        """
        page = ctx.page_ref.current
        if page is None:
            return
        with contextlib.suppress(Exception):  # 页面销毁竞态
            page.run_task(fn, *args)

    def _spawn(op: str, make_coro: Callable[[], Awaitable[None]]) -> None:
        """调度**只读**任务：不占用忙碌态。

        刷新 / 读历史 / 读提交详情这类操作不写仓库（不抢 ``index.lock``），
        若也占用忙碌态，切标签触发的自动刷新会把「正在执行 Git 操作…」闪出来，
        还会误拦用户紧接着的暂存/提交（提示「已有操作正在执行」）。
        """

        async def _wrapper() -> None:
            try:
                await make_coro()
            except Exception as e:  # 统一转用户可读消息
                _report_error(e, op)

        _schedule(_wrapper)

    def _run(op: str, make_coro: Callable[[], Awaitable[None]]) -> None:
        """调度**写仓库**任务：先占忙碌态，结束（含异常）必然释放。

        必须走这里而不是各写一遍 try/finally——漏掉 `_end_busy()` 会让面板永久
        停留在「正在执行 Git 操作…」，且之后所有操作都被并发保护拦下。
        """
        if not _start_busy():
            return

        async def _wrapper() -> None:
            try:
                await make_coro()
            except Exception as e:  # 统一转用户可读消息
                _report_error(e, op)
            finally:
                _end_busy()

        _schedule(_wrapper)

    def _dialog_seq() -> int:
        """确认对话框实例序号：key 变化让 FileActionDialog 重挂载、输入框复位。"""
        ctx.git_dialog_seq_ref.current += 1
        return ctx.git_dialog_seq_ref.current

    def _kind_of_diff(fd, fallback: ChangeKind = ChangeKind.MODIFIED) -> ChangeKind:
        """从 FileDiff 标志位推断变更类型（历史 diff 没有 status 记录可用）。"""
        if fd.is_new:
            return ChangeKind.ADDED
        if fd.is_deleted:
            return ChangeKind.DELETED
        if fd.is_renamed:
            return ChangeKind.RENAMED
        return fallback

    # ==================================================================
    # 探测 / 刷新
    # ==================================================================

    async def _detect() -> str | None:
        """探测 git 可用性 + 仓库根（后台线程），同步状态，返回仓库根。"""
        available = await asyncio.to_thread(is_git_available)
        version = await asyncio.to_thread(git_version) if available else ""
        workspace = _workspace_dir()
        root = (
            await asyncio.to_thread(discover_root, workspace)
            if available and workspace
            else None
        )
        ctx.set_git_available(available)
        ctx.set_git_version(version)
        ctx.set_git_workspace(workspace)
        ctx.set_git_root(root)
        return root

    async def _read_status(repo: GitRepository):
        """读状态并写回渲染槽，返回状态对象。"""
        status = await asyncio.to_thread(repo.status)
        ctx.set_git_status(status)
        return status

    async def _read_branches(repo: GitRepository):
        branches = await asyncio.to_thread(repo.branches)
        ctx.set_git_branches(branches)
        return branches

    async def _refresh_worker(*, history: bool, branches: bool, token: int) -> None:
        prev_root = ctx.git_root
        root = await _detect()
        if ctx.git_refresh_token.current != token:
            return  # 已被更新的刷新请求取代
        if not root:
            ctx.set_git_status(None)
            ctx.set_git_branches([])
            ctx.set_git_error(None)
            return
        repo = _service().repository(root)
        if prev_root != root or ctx.git_status is None:
            # 换了仓库：历史与展开态属于上一个仓库，必须清空
            ctx.set_git_history([])
            ctx.set_git_expanded_commits(frozenset())
            ctx.set_git_commit_details({})
        await _read_status(repo)
        if ctx.git_refresh_token.current != token:
            return
        if branches:
            await _read_branches(repo)
        if ctx.git_refresh_token.current != token:
            return
        ctx.set_git_error(None)
        if history:
            await _load_history_worker(repo, reset=True)

    async def _refresh_impl(*, history: bool = False, branches: bool = True) -> None:
        """刷新仓库状态（去抖：连续调用只执行最后一次）。"""
        ctx.git_refresh_token.current += 1
        token = ctx.git_refresh_token.current
        # 短睡眠合并连续触发（切标签连点 / 保存后的文件系统事件风暴）
        await asyncio.sleep(0.12)
        if ctx.git_refresh_token.current != token:
            return
        await _refresh_worker(history=history, branches=branches, token=token)

    def refresh(*, history: bool = False, branches: bool = True) -> None:
        """公开刷新入口（面板刷新按钮 / App 的 use_effect 触发）。"""
        _spawn("刷新 Git 状态", lambda: _refresh_impl(history=history, branches=branches))

    # ==================================================================
    # 面板入口 / 视图切换
    # ==================================================================

    def open_panel() -> None:
        """展开侧边栏并切到 Git 面板（状态栏分支名 / 菜单入口）。"""
        if not ctx.settings.get("sidebar_open", False):
            ctx.update_setting("sidebar_open", True)
        ctx.update_setting("sidebar_panel", "git")
        # 打开面板即刷新：保存文件不会递增 fs_version（那是给文件树用的信号），
        # 面板必须呈现磁盘当前状态而非上次快照。
        refresh()

    def set_view(view: str) -> None:
        ctx.set_git_view(view)
        if view == "history" and not ctx.git_history:
            load_history()

    # ==================================================================
    # 仓库初始化
    # ==================================================================

    def init_repo() -> None:
        """在（非仓库的）工作区目录执行 ``git init``。"""
        workspace = _workspace_dir()
        if not workspace:
            _notify("请先打开一个文件夹作为工作区", _KIND_WARN)
            return
        if not ctx.git_available:
            _notify("未检测到 Git，请先安装 Git 并确保其在 PATH 中", _KIND_ERROR)
            return

        async def _impl() -> None:
            repo = await asyncio.to_thread(GitRepository.init, workspace)
            _service().invalidate()
            ctx.set_git_root(repo.root)
            ctx.set_git_error(None)
            await _read_status(repo)
            ctx.bump_fs_version()
            _notify(f"已初始化仓库：{os.path.basename(workspace)}", _KIND_SUCCESS)

        _run("初始化仓库", _impl)

    # ==================================================================
    # 暂存 / 取消暂存
    # ==================================================================

    async def _stage_impl(paths: list[str] | None) -> None:
        repo = _repo()
        if repo is None:
            return
        if paths is None:
            await asyncio.to_thread(repo.stage_all)
        else:
            await asyncio.to_thread(repo.stage, paths)
        ctx.set_git_error(None)
        await _read_status(repo)

    def stage(paths: list[str]) -> None:
        if not paths or _repo() is None:
            return
        _run("暂存", lambda: _stage_impl(list(paths)))

    def stage_all() -> None:
        if _repo() is None:
            return
        _run("暂存全部", lambda: _stage_impl(None))

    async def _unstage_impl(paths: list[str] | None) -> None:
        repo = _repo()
        if repo is None:
            return
        if paths is None:
            await asyncio.to_thread(repo.unstage_all)
        else:
            await asyncio.to_thread(repo.unstage, paths)
        ctx.set_git_error(None)
        await _read_status(repo)

    def unstage(paths: list[str]) -> None:
        if not paths or _repo() is None:
            return
        _run("取消暂存", lambda: _unstage_impl(list(paths)))

    def unstage_all() -> None:
        if _repo() is None:
            return
        _run("取消全部暂存", lambda: _unstage_impl(None))

    # ==================================================================
    # 丢弃更改
    # ==================================================================

    def _discard_kind(entry) -> str:
        """把状态条目归到「丢弃」的三种处理路径之一。

        - ``untracked``：未跟踪的新文件，磁盘上存在但 HEAD / 索引里都没有
        - ``staged_new``：已暂存的新增文件（索引有、HEAD 没有）
        - ``tracked``：其余（有历史版本，可原地还原）
        """
        if entry is None:
            return "tracked"
        if entry.worktree_kind == ChangeKind.UNTRACKED and not entry.is_staged:
            return "untracked"
        if (
            entry.index_kind == ChangeKind.ADDED
            and entry.worktree_kind == ChangeKind.UNMODIFIED
        ):
            return "staged_new"
        return "tracked"

    async def _discard_impl(paths: list[str] | None) -> None:
        """丢弃工作区更改。

        三类文件必须分开处理，否则「丢弃」之后文件还在磁盘上（用户会以为操作
        失败），或者索引与磁盘状态互相矛盾：

        - 已跟踪 → ``discard_working``（``git restore --worktree``）原地还原
        - 未跟踪 → ``delete_untracked``（删盘；带仓库内 / 仅文件 / 内部路径保护）
        - 已暂存的新增文件 → ``unstage_and_delete``（HEAD 里本就不存在它）

        ``paths=None`` 对应「更改」分区标题栏的「丢弃所有工作区更改」：只覆盖该
        分区内容（未暂存 + 未跟踪），已暂存内容属于另一个分区，不在这里动。
        """
        repo = _repo()
        if repo is None:
            return
        status = ctx.git_status
        if paths is None:
            targets = [e.path for e in status.unstaged] if status is not None else []
        else:
            targets = list(paths)

        kinds = {
            p: _discard_kind(status.entry_for(p) if status is not None else None)
            for p in targets
        }
        untracked = [p for p in targets if kinds[p] == "untracked"]
        staged_new = [p for p in targets if kinds[p] == "staged_new"]
        tracked = [p for p in targets if kinds[p] == "tracked"]

        if paths is None:
            # 整树还原一条命令搞定，比逐文件 restore 少 N 次 git 进程
            await asyncio.to_thread(repo.discard_all)
            note = "已丢弃所有工作区更改"
        else:
            if tracked:
                await asyncio.to_thread(repo.discard_working, tracked)
            note = f"已丢弃 {len(targets)} 个文件的更改"
        if untracked:
            await asyncio.to_thread(repo.delete_untracked, untracked)
        if staged_new:
            await asyncio.to_thread(repo.unstage_and_delete, staged_new)

        ctx.set_git_error(None)
        await _read_status(repo)
        # 被丢弃文件若正在差异视图中显示，关掉它（内容已不存在）
        if ctx.git_diff_open and paths is not None and ctx.git_active_path in paths:
            ctx.set_git_diff_open(False)
        ctx.bump_fs_version()
        _notify(note, _KIND_SUCCESS)

    def discard(path: str) -> None:
        """丢弃单文件更改（先二次确认）。"""
        if _repo() is None or _guard_operation():
            return
        ctx.set_file_dialog({
            "mode": "confirm",
            "instance": _dialog_seq(),
            "title": "丢弃更改",
            "icon": ft.Icons.UNDO,
            "message": f"确定丢弃「{os.path.basename(path)}」的全部未暂存更改？\n"
                       "此操作不可撤销，文件将恢复为暂存区中的内容。",
            "confirm_label": "丢弃更改",
            "danger": True,
            "action": "git_discard",
            "target": path,
        })

    def discard_all() -> None:
        """丢弃所有工作区更改（先二次确认）。"""
        if _repo() is None or _guard_operation():
            return
        ctx.set_file_dialog({
            "mode": "confirm",
            "instance": _dialog_seq(),
            "title": "丢弃所有更改",
            "icon": ft.Icons.UNDO,
            "message": "确定丢弃工作区中的全部未暂存更改？\n"
                       "已跟踪文件将恢复为暂存区内容，未跟踪的新文件将被删除。\n"
                       "此操作不可撤销。",
            "confirm_label": "全部丢弃",
            "danger": True,
            "action": "git_discard_all",
            "target": None,
        })

    # ==================================================================
    # 差异视图
    # ==================================================================

    def _apply_diff(fd, *, path: str, staged: bool, sha: str | None,
                    repo: GitRepository) -> None:
        """把一份 FileDiff 装进差异视图的渲染槽（含元信息）。"""
        abs_path = repo.abspath(path)
        entry = ctx.git_status.entry_for(path) if (ctx.git_status and not sha) else None
        if entry is not None:
            kind = entry.kind
            title = entry.display_name
        else:
            kind = _kind_of_diff(fd)
            title = fd.display_path
        ctx.set_git_diff(fd)
        ctx.set_git_diff_meta({
            "path": path,
            "title": title,
            "label": kind_label(kind),
            "kind": kind,
            "staged": bool(staged),
            "history": bool(sha),
            "sha": sha or "",
            # 文件不在磁盘上（历史里已删除 / 尚未检出）时禁用行号跳转
            "jump_path": abs_path if os.path.isfile(abs_path) else None,
        })
        ctx.set_git_active_path(path)
        ctx.set_git_diff_open(True)
        ctx.set_git_error(None)

    async def _open_diff_impl(path: str, staged: bool, sha: str | None) -> None:
        repo = _repo()
        if repo is None:
            return
        if sha:
            fd = await asyncio.to_thread(repo.commit_file_diff, sha, path)
        else:
            fd = await asyncio.to_thread(repo.working_diff, path, staged=staged)
        _apply_diff(fd, path=path, staged=staged, sha=sha, repo=repo)

    def open_diff(path: str, staged: bool = False, history: bool = False,
                  sha: str | None = None) -> None:
        """打开内嵌差异视图（工作区 / 暂存区 / 历史提交三种来源）。"""
        if not path:
            return
        ctx.set_git_diff_mode(ctx.settings.get("git_diff_mode", MODE_UNIFIED))
        _spawn("打开差异", lambda: _open_diff_impl(path, staged, sha if history else None))

    def close_diff() -> None:
        ctx.set_git_diff_open(False)
        ctx.set_git_active_path(None)

    def set_diff_mode(mode: str) -> None:
        """切换统一 / 分栏视图（同时持久化到设置，下次打开沿用）。"""
        if mode not in (MODE_UNIFIED, MODE_SPLIT):
            return
        ctx.set_git_diff_mode(mode)
        ctx.update_setting("git_diff_mode", mode)

    def diff_jump(line: int) -> None:
        """差异行号 → 编辑器行号联动定位（打开文件并把光标落到该行）。"""
        target = (ctx.git_diff_meta or {}).get("jump_path")
        if not target or not line or line < 1:
            _notify("该差异行无法在编辑器中定位", _KIND_WARN)
            return
        close_diff()
        ctx.open_file_and_jump(target, line - 1, None)

    def open_in_editor() -> None:
        """在编辑器中打开差异所属文件（不定位行）。"""
        target = (ctx.git_diff_meta or {}).get("jump_path")
        if not target:
            _notify("该文件当前不在磁盘上，无法打开", _KIND_WARN)
            return
        close_diff()
        ctx.open_file_and_jump(target, None, None)  # type: ignore[arg-type]

    async def _diff_side_impl(path: str, *, to_staged: bool) -> None:
        """暂存 / 取消暂存后原地刷新差异（内容所在侧随之切换）。"""
        repo = _repo()
        if repo is None:
            return
        if to_staged:
            await asyncio.to_thread(repo.stage, [path])
        else:
            await asyncio.to_thread(repo.unstage, [path])
        status = await _read_status(repo)
        if status.entry_for(path) is None:
            # 该文件已无任何变更（例如新暂存后又取消），关闭差异视图
            ctx.set_git_diff_open(False)
            return
        fd = await asyncio.to_thread(repo.working_diff, path, staged=to_staged)
        _apply_diff(fd, path=path, staged=to_staged, sha=None, repo=repo)

    def diff_stage() -> None:
        path = (ctx.git_diff_meta or {}).get("path")
        if path:
            _run("暂存", lambda: _diff_side_impl(path, to_staged=True))

    def diff_unstage() -> None:
        path = (ctx.git_diff_meta or {}).get("path")
        if path:
            _run("取消暂存", lambda: _diff_side_impl(path, to_staged=False))

    def diff_discard() -> None:
        path = (ctx.git_diff_meta or {}).get("path")
        if path:
            discard(path)

    # ==================================================================
    # 提交 / 撤销 / 同步
    # ==================================================================

    async def _commit_impl(message: str, all_changes: bool) -> None:
        repo = _repo()
        if repo is None:
            return
        try:
            sha = await asyncio.to_thread(repo.commit, message, all_changes=all_changes)
        except Exception:
            # 提交失败（无暂存内容 / 钩子失败）保留提交框内容，用户不必重打；
            # 交由 _run 的统一上报，这里只补一句说明后抛出。
            raise
        ctx.set_git_error(None)
        # 清空提交框：序号递增驱动 Panel 的 use_effect，草稿 ref 同步归零
        ctx.set_git_commit_seq(ctx.git_commit_seq + 1)
        ctx.git_commit_message_ref.current = ""
        await _read_status(repo)
        if ctx.git_view == "history":
            await _load_history_worker(repo, reset=True)
        if ctx.git_commit_push:
            await _push_impl(repo)
            _notify(f"已提交并推送 {sha}", _KIND_SUCCESS)
        else:
            ctx.bump_fs_version()
            _notify(f"已提交 {sha}", _KIND_SUCCESS)

    def commit(all_changes: bool = False) -> None:
        """提交：``all_changes=True`` 等价 ``git commit -a``。"""
        message = (ctx.git_commit_message_ref.current or "").strip()
        if not message:
            _notify("请先填写提交信息", _KIND_WARN)
            return
        if _repo() is None:
            return
        _run("提交", lambda: _commit_impl(message, all_changes))

    def toggle_commit_push() -> None:
        """「提交后自动推送」开关（持久化到设置）。"""
        value = not ctx.git_commit_push
        ctx.set_git_commit_push(value)
        ctx.update_setting("git_commit_push", value)

    async def _undo_commit_impl() -> None:
        repo = _repo()
        if repo is None:
            return
        await asyncio.to_thread(repo.undo_last_commit)
        ctx.set_git_error(None)
        await _read_status(repo)
        if ctx.git_view == "history":
            await _load_history_worker(repo, reset=True)
        ctx.bump_fs_version()
        _notify("已撤销上一次提交，更改已回到工作区", _KIND_SUCCESS)

    def undo_commit() -> None:
        """撤销上一次提交（``reset --mixed HEAD~1``），更改回到工作区。"""
        if _repo() is None:
            return
        _run("撤销提交", _undo_commit_impl)

    async def _push_impl(repo: GitRepository) -> str:
        detail = await asyncio.to_thread(repo.push)
        ctx.set_git_error(None)
        await _read_status(repo)
        return detail

    async def _remote_impl(fn_name: str, op: str, *, refresh_after: bool) -> None:
        repo = _repo()
        if repo is None:
            return
        try:
            fn = getattr(repo, fn_name)
            detail = await asyncio.to_thread(fn)
        except Exception:
            # 失败也必须回读状态：拉取冲突会让工作区进入未合并状态，面板要立刻
            # 显示冲突文件，否则用户看不到究竟发生了什么。回读本身失败不再上报
            # （原始异常才是根因）。
            if repo is not None:
                with contextlib.suppress(Exception):  # 状态回读兜底
                    await _read_status(repo)
            raise
        ctx.set_git_error(None)
        await _read_status(repo)
        if refresh_after:
            ctx.bump_fs_version()
        tail = detail.splitlines()[-1] if detail else ""
        _notify(f"{op}完成" + (f"：{tail}" if tail else ""), _KIND_SUCCESS)

    def fetch() -> None:
        """``git fetch``：只更新远端跟踪分支，不动工作区。"""
        if _repo() is None:
            return
        _run("获取", lambda: _remote_impl("fetch", "获取", refresh_after=False))

    def pull() -> None:
        """``git pull``：拉取并合并远端更新。"""
        if _repo() is None or _guard_operation():
            return

        async def _impl() -> None:
            await _remote_impl("pull", "拉取", refresh_after=True)

        _run("拉取", _impl)

    def push() -> None:
        """``git push``：推送本地提交到远端。"""
        if _repo() is None or _guard_operation():
            return

        async def _impl() -> None:
            repo = _repo()
            if repo is None:
                return
            detail = await _push_impl(repo)
            tail = detail.splitlines()[-1] if detail else ""
            _notify("推送完成" + (f"：{tail}" if tail else ""), _KIND_SUCCESS)

        _run("推送", _impl)

    async def _abort_impl(op: str) -> None:
        repo = _repo()
        if repo is None:
            return
        fn = repo.abort_rebase if op == "rebase" else repo.abort_merge
        await asyncio.to_thread(fn)
        ctx.set_git_error(None)
        await _read_status(repo)
        ctx.bump_fs_version()
        _notify("已中止进行中的操作", _KIND_SUCCESS)

    def abort_operation() -> None:
        """中止进行中的合并 / 变基。"""
        st = ctx.git_status
        op = (st.op if st else None) or "merge"
        if _repo() is None:
            return
        _run("中止操作", lambda: _abort_impl(op))

    # ==================================================================
    # 分支
    # ==================================================================

    def open_branch_menu() -> None:
        ctx.set_git_branch_menu_open(True)
        refresh_branches()

    def close_branch_menu() -> None:
        ctx.set_git_branch_menu_open(False)

    def refresh_branches() -> None:
        repo = _repo()
        if repo is None:
            return

        async def _impl() -> None:
            await _read_branches(repo)
            ctx.set_git_error(None)

        _spawn("读取分支", _impl)

    async def _branch_impl(fn_name: str, args: tuple, done: str) -> None:
        repo = _repo()
        if repo is None:
            return
        try:
            fn = getattr(repo, fn_name)
            await asyncio.to_thread(fn, *args)
        except Exception:
            if repo is not None:
                with contextlib.suppress(Exception):  # 状态回读兜底
                    await _read_status(repo)
            raise
        ctx.set_git_error(None)
        await _read_status(repo)
        await _read_branches(repo)
        ctx.bump_fs_version()
        _notify(done, _KIND_SUCCESS)

    def switch_branch(name: str) -> None:
        if not name or _repo() is None or _guard_operation():
            return
        ctx.set_git_branch_menu_open(False)
        _run("切换分支", lambda: _branch_impl("switch_branch", (name,), f"已切换到 {name}"))

    def create_branch(name: str) -> None:
        if not name or _repo() is None or _guard_operation():
            return
        ctx.set_git_branch_menu_open(False)
        _run(
            "创建分支",
            lambda: _branch_impl("create_branch", (name,), f"已创建并切换到 {name}"),
        )

    def delete_branch(name: str) -> None:
        """删除分支（先二次确认；未合并分支由 git 拒绝，错误会上报）。"""
        if not name:
            return
        ctx.set_file_dialog({
            "mode": "confirm",
            "instance": _dialog_seq(),
            "title": "删除分支",
            "icon": ft.Icons.DELETE_OUTLINE,
            "message": f"确定删除分支「{name}」？\n"
                       "仅当该分支的提交已被其他分支包含时才能删除。",
            "confirm_label": "删除分支",
            "danger": True,
            "action": "git_delete_branch",
            "target": name,
        })

    def merge_branch(name: str) -> None:
        if not name or _repo() is None or _guard_operation():
            return
        ctx.set_git_branch_menu_open(False)
        _run("合并分支", lambda: _branch_impl("merge_branch", (name,), f"已合并 {name}"))

    # ==================================================================
    # 冲突
    # ==================================================================

    def jump_to_conflict(path: str) -> None:
        """打开冲突文件并跳到第一处冲突标记（``<<<<<<<``）。"""
        repo = _repo()
        if repo is None or not path:
            return

        async def _impl() -> None:
            markers = await asyncio.to_thread(repo.conflict_markers, path)
            line = markers.first_line
            target = repo.abspath(path)
            if line is None:
                _notify(f"{os.path.basename(path)} 未找到冲突标记（可能已解决）",
                        _KIND_WARN)
                ctx.open_file_and_jump(target, None, None)  # type: ignore[arg-type]
            else:
                _notify(f"已定位到第 {line} 行冲突标记")
                ctx.open_file_and_jump(target, line - 1, None)

        _spawn("定位冲突", _impl)

    # ==================================================================
    # 历史记录
    # ==================================================================

    def _page_size() -> int:
        try:
            return max(1, int(ctx.settings.get("git_history_page_size", DEFAULT_PAGE_SIZE)))
        except (TypeError, ValueError):
            return DEFAULT_PAGE_SIZE

    def _filters() -> dict:
        f = ctx.git_history_filter or {}
        return {
            "author": (f.get("author") or "").strip() or None,
            "keyword": (f.get("keyword") or "").strip() or None,
            "path": (f.get("path") or "").strip() or None,
        }

    async def _load_history_worker(repo: GitRepository, *, reset: bool) -> None:
        """加载一页历史（``reset=False`` 时追加，供「加载更多」）。"""
        offset = 0 if reset else len(ctx.git_history)
        f = _filters()
        ctx.set_git_history_loading(True)
        try:
            page = await asyncio.to_thread(
                repo.log,
                limit=_page_size(),
                offset=offset,
                author=f["author"],
                keyword=f["keyword"],
                path=f["path"],
            )
            ctx.set_git_history(page if reset else [*ctx.git_history, *page])
            ctx.set_git_history_has_more(len(page) >= _page_size())
            ctx.set_git_error(None)
        finally:
            ctx.set_git_history_loading(False)

    def load_history() -> None:
        """重新加载历史首页（筛选条件变化后调用）。

        历史筛选文字由面板局部持有并即时写入 ``git_history_filter``，这里读 App
        侧副本执行查询——每次按键都跑 git log 会让输入明显掉帧。
        """
        repo = _repo()
        if repo is None:
            ctx.set_git_history([])
            ctx.set_git_history_has_more(False)
            return
        _spawn("加载提交历史", lambda: _load_history_worker(repo, reset=True))

    def load_more_history() -> None:
        repo = _repo()
        if repo is None or ctx.git_history_loading:
            return
        _spawn("加载提交历史", lambda: _load_history_worker(repo, reset=False))

    def set_history_filter(field: str, value: str) -> None:
        if field not in ("author", "keyword", "path"):
            return
        ctx.set_git_history_filter({**(ctx.git_history_filter or {}), field: value})

    def clear_history_filter() -> None:
        ctx.set_git_history_filter({})
        load_history()

    def toggle_commit(sha: str) -> None:
        """展开 / 折叠提交的变更文件列表（展开时按需加载该提交详情）。"""
        expanded = set(ctx.git_expanded_commits)
        if sha in expanded:
            expanded.discard(sha)
            ctx.set_git_expanded_commits(frozenset(expanded))
            return
        expanded.add(sha)
        ctx.set_git_expanded_commits(frozenset(expanded))
        if sha in (ctx.git_commit_details or {}):
            return
        repo = _repo()
        if repo is None:
            return

        async def _impl() -> None:
            files = await asyncio.to_thread(repo.commit_files, sha)
            ctx.set_git_commit_details({**(ctx.git_commit_details or {}), sha: files})

        _spawn("加载提交详情", _impl)

    def history_file_diff(sha: str, path: str) -> None:
        """历史提交中的单文件差异（``git show <sha> -- <path>``）。"""
        if not sha or not path:
            return
        ctx.set_git_diff_mode(ctx.settings.get("git_diff_mode", MODE_UNIFIED))
        _spawn("打开差异", lambda: _open_diff_impl(path, False, sha))

    def open_file_history(path: str) -> None:
        """「单文件专属修改历史」：切到历史视图并按该文件过滤。

        编辑器标签右键 / 文件树右键菜单的入口。path 为绝对路径，转成仓库相对
        路径后写入筛选条件（git log 的 ``-- <path>`` 只认相对路径）。
        """
        repo = _repo()
        if repo is None or not path:
            _notify("该文件不在 Git 仓库中", _KIND_WARN)
            return
        # 必须用 contains 判边界：os.path.relpath 对仓库外的路径不会报错，只会给出
        # "..\\..\\x" 这种相对路径，靠异常判「不在仓库内」会全部漏判。
        if not repo.contains(path):
            _notify("该文件不在 Git 仓库中", _KIND_WARN)
            return
        rel = repo.relpath(path)
        ctx.set_git_history_filter({**(ctx.git_history_filter or {}), "path": rel})
        ctx.set_git_view("history")
        open_panel()
        load_history()

    # ==================================================================
    # 破坏性操作的确认回投（由 app/_file_dialogs 转调）
    # ==================================================================

    def confirm_dialog_action(action: str, target) -> None:
        """``set_file_dialog`` 确认后的执行入口。

        ``_file_dialogs.on_file_dialog_confirm`` 只负责关闭对话框并转发，具体 Git
        语义留在这里，避免文件对话框控制器反向依赖 Git。
        """
        if action == "git_discard" and target:
            _run("丢弃更改", lambda: _discard_impl([target]))
        elif action == "git_discard_all":
            _run("丢弃更改", lambda: _discard_impl(None))
        elif action == "git_delete_branch" and target:
            _run(
                "删除分支",
                lambda: _branch_impl("delete_branch", (target,), f"已删除分支 {target}"),
            )

    def escape() -> bool:
        """Escape 关闭最上层 Git 覆盖层，返回 True 表示按键已消费。

        供 KeyDispatcher 在常规分发之前调用：差异视图与分支面板都是全窗口遮蔽
        式覆盖层，此刻"前台"是它们而不是编辑器，Escape 语义应落在覆盖层上。
        尤其分支面板内的「新建分支」输入框挂在原生输入域，走常规门控会被原生
        输入框静默吞掉（面板永远关不掉）。

        栈序（``_render.py`` 根 Stack）是 diff → branch_menu，故分支面板更靠上，
        先关它；只有 diff 打开时才关 diff。
        """
        if ctx.git_branch_menu_open:
            close_branch_menu()
            return True
        if ctx.git_diff_open:
            close_diff()
            return True
        return False

    # ==================================================================
    # 装配
    # ==================================================================

    return {
        # 入口 / 刷新
        "git_refresh": refresh,
        "git_escape": escape,
        "git_open_panel": open_panel,
        "git_init_repo": init_repo,
        "git_set_view": set_view,
        "git_confirm_dialog_action": confirm_dialog_action,
        # 暂存 / 取消暂存 / 丢弃
        "git_stage": stage,
        "git_unstage": unstage,
        "git_stage_all": stage_all,
        "git_unstage_all": unstage_all,
        "git_discard": discard,
        "git_discard_all": discard_all,
        # 差异视图
        "git_open_diff": open_diff,
        "git_close_diff": close_diff,
        "git_set_diff_mode": set_diff_mode,
        "git_diff_jump": diff_jump,
        "git_diff_stage": diff_stage,
        "git_diff_unstage": diff_unstage,
        "git_diff_discard": diff_discard,
        "git_open_in_editor": open_in_editor,
        # 提交 / 同步
        "git_commit": commit,
        "git_toggle_commit_push": toggle_commit_push,
        "git_undo_commit": undo_commit,
        "git_fetch": fetch,
        "git_pull": pull,
        "git_push": push,
        "git_abort_operation": abort_operation,
        # 分支
        "git_open_branch_dialog": open_branch_menu,
        "git_close_branch_menu": close_branch_menu,
        "git_refresh_branches": refresh_branches,
        "git_switch_branch": switch_branch,
        "git_create_branch": create_branch,
        "git_delete_branch": delete_branch,
        "git_merge_branch": merge_branch,
        # 冲突
        "git_jump_to_conflict": jump_to_conflict,
        # 历史
        "git_load_history": load_history,
        "git_load_more_history": load_more_history,
        "git_toggle_commit": toggle_commit,
        "git_set_history_filter": set_history_filter,
        "git_clear_history_filter": clear_history_filter,
        "git_history_file_diff": history_file_diff,
        "git_open_file_history": open_file_history,
    }
