"""备份控制器：定时备份 / 自动保存 / 外部修改检测 / 崩溃恢复 / 启动扫描。

闭包组：start_backup_loop / trigger_autosave_now / trigger_backup_now /
write_exit_sentinel / scan_recoverable / scan_recent_backups /
open_backup_in_new_tab / delete_backup / cleanup_expired_backups

跨组依赖（通过 ctx 装配槽，调用时读取）：
- file_io_ops 组：save_doc（自动保存触发）
- file_dialogs 组：show_snack（恢复提示）
- tab_management 组：update_active（恢复时新建标签载入备份内容）
- settings_controller 组：update_setting（清理周期等）

设计要点：
- 定时器模型：start_backup_loop 启动三个独立循环——
  ① 自动保存循环：每 auto_save_interval 分钟扫描脏标签写回原文件
  ② 备份循环：每 backup_interval 分钟对所有打开标签生成完整备份
  ③ 外部修改检测循环：基于 watchfiles 事件驱动，监控所有打开标签的文件路径，
    检测到外部修改时弹出重载确认对话框（实现「编辑期间监听原文件变动」需求）。
    watchfiles 基于 OS 原生通知（inotify/ReadDirectoryChangesW/FSEvents），
    零 CPU 轮询开销，文件变化即时响应。watchfiles 不可用时回退到 5 秒轮询。
  三个循环独立运行，自动保存关闭时不影响备份和外部修改检测。
- 自我写入过滤：save_doc 写入文件后立即更新 _last_known_mtime，利用 asyncio
  单线程特性，watchfiles 事件在 save_doc 完成后才被处理，mtime 比较相等 → 忽略。
- 大文件优化：单文档 >10MB 时降低备份频率至 15 分钟（关闭高频监听）。
- 退出 / 崩溃钩子：on_disconnect / window_event 触发 exit_backup + 哨兵写入。
- 启动扫描：scan_recoverable 读取上次会话哨兵，返回可恢复草稿列表。
- 恢复交互：open_backup_in_new_tab 在新标签页打开备份内容，由用户主动
  选择是否保存（不强制覆盖原文件）。

依赖项：
- asyncio / os / typing
- watchfiles（awatch / Change：外部修改检测，可选，不可用时回退轮询）
- parser（恢复时解析备份正文为 Document）
- app._tab_helpers（doc_has_text）
- app.autosave（AutosaveContext / autosave_all_dirty）
- services.backup（write_backup / cleanup_old_backups / delete_backup / is_large_content）
- services.recovery（find_recoverable_on_startup / find_recent_backups /
  load_backup_content / write_last_session_sentinel）
"""

import asyncio
import os
from typing import Any

import parser
from app._tab_helpers import doc_has_text
from app.autosave import AutosaveContext, autosave_all_dirty
from services.backup import (
    cleanup_old_backups,
    delete_backup as _delete_backup_file,
    is_large_content,
    write_backup,
)
from services.recovery import (
    find_recoverable_on_startup,
    find_recent_backups,
    load_backup_content,
    write_last_session_sentinel,
)


def build_backup_controller(ctx):
    """构造备份控制器闭包组。

    返回 dict[str, Callable]：
    start_backup_loop / trigger_autosave_now / trigger_backup_now /
    write_exit_sentinel / scan_recoverable / scan_recent_backups /
    open_backup_in_new_tab / delete_backup / cleanup_expired_backups
    """

    # 本会话产生的备份路径清单（退出时写入哨兵，供下次启动恢复）
    session_backup_paths: list[str] = []
    # 备份循环任务句柄（启动后保存，stop_backup_loop 取消）
    _loop_task_holder: dict[str, Any] = {"task": None}

    def _make_autosave_ctx() -> AutosaveContext:
        """构造 AutosaveContext，读取 ctx 最新槽位（避免闭包捕获渲染期快照）。"""
        return AutosaveContext(
            settings=ctx.settings,
            page_ref=ctx.page_ref,
            tabs_ref=ctx.tabs_ref,
            save_doc_fn=ctx.save_doc,
            set_status_fn=ctx.set_status_message,
        )

    def _backup_all_tabs() -> int:
        """对所有打开标签生成完整备份（无论是否脏、是否已保存）。

        返回本次成功写入的备份数量。失败静默忽略（备份不打断编辑流程）。
        空白标签（无文本）跳过，避免无意义空文件堆积。
        """
        if not ctx.settings.get("backup_enabled", True):
            return 0
        ts = ctx.tabs_ref.current or []
        count = 0
        for tab in ts:
            # 对比标签两侧各自备份
            if tab.get("type") == "diff":
                for side in ("left_doc", "right_doc"):
                    doc = tab.get(side)
                    if doc is None:
                        continue
                    content = parser.serialize(doc)
                    if not content.strip():
                        continue
                    path = write_backup(ctx.settings, tab, content)
                    if path:
                        session_backup_paths.append(path)
                        count += 1
                continue
            # 普通编辑标签
            doc = tab.get("document")
            if doc is None:
                continue
            content = parser.serialize(doc)
            if not content.strip():
                continue
            path = write_backup(ctx.settings, tab, content)
            if path:
                session_backup_paths.append(path)
                count += 1
        return count

    def _effective_backup_interval(content: str | None = None) -> float:
        """计算生效的备份间隔（秒）。

        - 默认 backup_interval 分钟
        - 大文件优化：单文档 >10MB 时降至 15 分钟
        """
        interval = int(ctx.settings.get("backup_interval", 10))
        if content and is_large_content(content):
            interval = max(interval, 15)
        return max(60.0, interval * 60.0)

    def _effective_autosave_interval() -> float:
        """计算生效的自动保存间隔（秒）。"""
        interval = int(ctx.settings.get("auto_save_interval", 5))
        interval = max(1, min(30, interval))
        return max(30.0, interval * 60.0)

    async def _backup_loop():
        """后台备份循环：每 backup_interval 分钟全量备份所有标签。

        循环独立于自动保存（自动保存关闭时备份仍运行），确保崩溃场景
        数据可恢复。捕获所有异常避免循环退出（静默继续下一轮）。
        """
        while True:
            try:
                await asyncio.sleep(_effective_backup_interval())
                _backup_all_tabs()
                # 顺带清理过期备份（低频，每轮一次）
                try:
                    cleanup_old_backups(ctx.settings)
                except Exception:
                    pass
            except asyncio.CancelledError:
                # 正常停止（应用退出）
                raise
            except Exception:
                # 异常不退出循环，等下个 tick 重试
                continue

    async def _autosave_loop():
        """后台自动保存循环：每 auto_save_interval 分钟扫描脏标签写回原文件。

        仅在 auto_save=True 时实际保存；关闭时循环仍运行但 autosave_all_dirty
        内部 early return（零开销）。
        """
        while True:
            try:
                await asyncio.sleep(_effective_autosave_interval())
                autosave_all_dirty(_make_autosave_ctx())
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def _external_check_loop():
        """基于 watchfiles 的外部修改检测循环。

        实现「编辑期间监听原文件变动」需求，采用操作系统原生文件通知
        （Linux inotify / Windows ReadDirectoryChangesW / macOS FSEvents），
        零 CPU 轮询开销，文件变化即时响应。

        工作流程：
        1. 收集所有打开标签的文件路径（普通编辑标签 + 对比标签两侧）
        2. 启动 watchfiles.awatch 监控这些路径
        3. 路径集合变化时（打开/关闭标签）自动重启 watcher
        4. 收到 modified/deleted 事件时弹出重载确认对话框

        自我写入过滤：save_doc 写入文件后立即更新 _last_known_mtime，
        利用 asyncio 单线程特性（save_doc 无 await），watchfiles 事件
        在 save_doc 完成后才被处理，此时 mtime 已更新 → 比较相等 → 忽略。
        """
        try:
            from watchfiles import Change  # noqa: PLC0415
        except ImportError:
            # watchfiles 不可用 → 回退到 5 秒轮询
            await _external_poll_loop()
            return

        current_paths: frozenset[str] = frozenset()
        watch_task: asyncio.Task | None = None
        stop_event: asyncio.Event | None = None

        while True:
            try:
                new_paths = _collect_watchable_paths()

                # 路径集合变化时重启 watcher
                if new_paths != current_paths:
                    # 停止旧 watcher
                    if stop_event is not None:
                        stop_event.set()
                    if watch_task is not None:
                        watch_task.cancel()
                        try:
                            await watch_task
                        except (asyncio.CancelledError, Exception):
                            pass

                    current_paths = new_paths
                    stop_event = asyncio.Event()

                    if not new_paths:
                        # 无文件可监控，等待下次检查
                        await asyncio.sleep(2.0)
                        continue

                    # 启动新 watcher 子任务
                    watch_task = asyncio.create_task(
                        _run_watcher(new_paths, stop_event, Change)
                    )

                # 定期检查路径集合是否变化（打开/关闭标签）
                await asyncio.sleep(2.0)

            except asyncio.CancelledError:
                if stop_event is not None:
                    stop_event.set()
                if watch_task is not None:
                    watch_task.cancel()
                raise
            except Exception:
                await asyncio.sleep(5.0)
                continue

    async def _external_poll_loop():
        """轮询回退方案（watchfiles 不可用时使用）。

        每 5 秒检查激活标签的文件 mtime，逻辑与原实现一致。
        """
        while True:
            try:
                await asyncio.sleep(5.0)
                if not ctx.settings.get("detect_external_changes", True):
                    continue
                if ctx.file_dialog is not None:
                    continue
                ts = ctx.tabs_ref.current or []
                active_idx = ctx.active_index_ref.current
                if not (0 <= active_idx < len(ts)):
                    continue
                tab = ts[active_idx]
                if tab.get("type") == "diff":
                    continue
                path = tab.get("file_path")
                if not path:
                    continue
                last_mtime = tab.get("_last_known_mtime")
                if last_mtime is None:
                    continue
                try:
                    current_mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if current_mtime > last_mtime:
                    _show_external_change_dialog(path, active_idx)
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def _run_watcher(
        paths: frozenset[str], stop_event: asyncio.Event, Change
    ):
        """运行 watchfiles 监控器，将文件变化事件分发到处理函数。

        watchfiles.awatch 是异步生成器，stop_event 被设置时退出。
        退出后由 _external_check_loop 的主循环重启（路径集合变化时）。
        """
        from watchfiles import awatch  # noqa: PLC0415

        try:
            async for changes in awatch(*paths, stop_event=stop_event):
                _process_file_changes(changes, Change)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _collect_watchable_paths() -> frozenset[str]:
        """收集所有需要监控的文件路径。

        包含普通编辑标签的 file_path 和对比标签的 left_path/right_path。
        返回绝对路径集合（frozenset 便于比较变化）。
        未命名草稿（无路径）跳过。
        """
        paths: set[str] = set()
        ts = ctx.tabs_ref.current or []
        for tab in ts:
            if tab.get("type") == "diff":
                for side in ("left_path", "right_path"):
                    p = tab.get(side)
                    if p:
                        paths.add(os.path.abspath(p))
                continue
            p = tab.get("file_path")
            if p:
                paths.add(os.path.abspath(p))
        return frozenset(paths)

    def _process_file_changes(changes, Change):
        """处理 watchfiles 产生的文件变化事件。

        - 仅处理 modified / deleted 事件（忽略 added 等无关事件）
        - 通过 mtime 比较过滤自己保存产生的事件（save_doc 已更新 mtime）
        - 已有对话框打开时跳过（避免重复弹出）
        - 一次只弹一个对话框（找到第一个匹配即返回）

        自我写入过滤（os.replace 兼容）：
        write_text_atomic 使用 os.replace 原子替换原文件，在 Windows 上触发
        deleted + added 事件序列（原文件被"删除"再"重新添加"）。对 deleted 事件
        需检查文件是否真的被删除——若文件仍存在且 mtime 未变，说明是 os.replace
        的副作用，应忽略。
        """
        if not ctx.settings.get("detect_external_changes", True):
            return
        if ctx.file_dialog is not None:
            return  # 已有对话框打开，不重复弹出

        for change_type, path in changes:
            # 仅关心修改和删除事件
            if change_type != Change.modified and change_type != Change.deleted:
                continue

            abs_path = os.path.abspath(path)

            # 找到对应的 tab（仅普通编辑标签，对比标签不弹对话框）
            tab_index, tab = _find_editor_tab_by_path(abs_path)
            if tab is None:
                continue

            # 自我写入过滤：通过 mtime 比较判断是否为外部修改
            last_mtime = tab.get("_last_known_mtime")
            if last_mtime is not None:
                if change_type == Change.deleted:
                    # os.replace 在 Windows 上触发 deleted + added 事件序列：
                    # 文件实际仍存在，只是被原子替换。若文件存在且 mtime 未变，
                    # 说明是自己的保存触发的事件，应忽略。只有文件真的不存在时
                    # 才视为外部删除。
                    if os.path.isfile(abs_path):
                        try:
                            current_mtime = os.path.getmtime(abs_path)
                            if current_mtime <= last_mtime:
                                continue  # os.replace 副作用，忽略
                        except OSError:
                            pass  # 读取 mtime 失败，继续弹出对话框
                    # 文件真的不存在 → 外部删除，弹出对话框
                elif change_type == Change.modified:
                    try:
                        current_mtime = os.path.getmtime(abs_path)
                        if current_mtime <= last_mtime:
                            continue  # 是我们自己保存的，忽略
                    except OSError:
                        pass  # 文件可能已被删除，继续弹出对话框

            _show_external_change_dialog(abs_path, tab_index)
            return  # 一次只弹一个对话框

    def _find_editor_tab_by_path(path: str):
        """根据文件路径找到对应的普通编辑标签。

        返回 (tab_index, tab) 或 (None, None)。
        对比标签不参与外部修改检测（重载逻辑更复杂，暂不支持）。
        """
        ts = ctx.tabs_ref.current or []
        abs_path = os.path.abspath(path)
        for i, tab in enumerate(ts):
            if tab.get("type") == "diff":
                continue
            p = tab.get("file_path")
            if p and os.path.abspath(p) == abs_path:
                return i, tab
        return None, None

    def _show_external_change_dialog(path: str, tab_index: int):
        """弹出外部修改重载确认对话框。"""
        ctx.set_file_dialog({
            "mode": "confirm",
            "title": "文件已被外部修改",
            "message": f"{os.path.basename(path)} 已在外部被修改。\n是否重新加载最新内容？\n（选择「保留本地版本」将用本地内容覆盖外部修改）",
            "confirm_label": "重新加载",
            "cancel_label": "保留本地版本",
            "action": "reload_external",
            "target": path,
            "target_tab_index": tab_index,
        })

    def start_backup_loop():
        """use_effect 回调：启动自动保存 + 备份 + 外部修改检测三个后台循环。

        返回 cleanup 函数，应用退出 / 组件卸载时取消任务。
        三循环设计：
        - 自动保存可被用户关闭（settings.auto_save=False）
        - 备份始终运行（settings.backup_enabled 默认 True）
        - 外部修改检测始终运行（settings.detect_external_changes 默认 True）
        """
        page = ctx.page_ref.current
        if page is None:
            return lambda: None

        async def _runner():
            tasks = [
                asyncio.create_task(_autosave_loop()),
                asyncio.create_task(_backup_loop()),
                asyncio.create_task(_external_check_loop()),
            ]
            _loop_task_holder["task"] = tasks
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                pass

        try:
            page.run_task(_runner)
        except Exception:
            pass

        def _cleanup():
            tasks = _loop_task_holder.get("task")
            if tasks:
                for t in tasks:
                    try:
                        t.cancel()
                    except Exception:
                        pass
                _loop_task_holder["task"] = None

        return _cleanup

    def trigger_autosave_now():
        """即时触发自动保存（窗口失焦 / 最小化时调用）。

        仅在 auto_save_on_blur=True 时实际执行。扫描所有脏标签写回原文件。
        """
        if not ctx.settings.get("auto_save_on_blur", True):
            return
        if not ctx.settings.get("auto_save", False):
            return
        autosave_all_dirty(_make_autosave_ctx())

    def trigger_backup_now():
        """即时触发全量备份（退出 / 关闭前 / 崩溃钩子调用）。

        无论 backup_enabled 是否开启都尝试写入（崩溃场景数据安全优先）。
        静默执行，不抛异常。
        """
        try:
            _backup_all_tabs()
        except Exception:
            pass

    def write_exit_sentinel():
        """退出前写入会话哨兵（记录本次会话产生的备份路径）。

        供下次启动时 find_recoverable_on_startup 读取。无备份时清掉旧哨兵，
        避免下次启动误提示。失败静默忽略（不影响退出流程）。
        """
        try:
            # 退出前再做一次全量备份，确保最后状态可恢复
            _backup_all_tabs()
            write_last_session_sentinel(ctx.settings, session_backup_paths)
        except Exception:
            pass

    def scan_recoverable():
        """启动时扫描可恢复草稿（基于上次会话哨兵）。

        返回 BackupInfo 列表（按时间降序，同文档去重）。无则空。
        哨兵读取后即清除（启动提示只触发一次）。
        """
        try:
            return find_recoverable_on_startup(ctx.settings)
        except Exception:
            return []

    def scan_recent_backups():
        """手动恢复入口：扫描最近 N 天全量备份（不限于上次会话）。

        按文档 ID 去重，每个文档保留最新备份。失败返回空列表。
        """
        try:
            return find_recent_backups(ctx.settings)
        except Exception:
            return []

    def open_backup_in_new_tab(backup_path: str):
        """在新标签页打开备份内容（用户主动恢复）。

        - 读取备份正文（含元数据，但仅载入正文到文档）
        - 新建标签页载入，不强制保存（由用户决定）
        - 标签 file_path=None（未命名草稿），dirty=True（有内容需用户确认）
        - 失败时 show_snack 提示
        - 恢复后关闭恢复面板（用户已选择操作，面板不再需要遮挡）
        """
        result = load_backup_content(backup_path)
        if result is None:
            ctx.show_snack("恢复失败：备份文件已损坏或不存在")
            return
        body, _cursor, _scroll = result
        doc = parser.parse_markdown(body)
        # 新建标签载入备份内容（file_path=None 表示未命名草稿）
        new_tabs = list(ctx.tabs_ref.current)
        new_tabs.append({
            "document": doc,
            "file_path": None,
            "dirty": doc_has_text(doc),
        })
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs
        new_idx = len(new_tabs) - 1
        ctx.set_active_index(new_idx)
        ctx.active_index_ref.current = new_idx
        ctx.set_session(ctx.session + 1)
        ctx.set_recovery_open(False)
        ctx.show_snack(f"已恢复草稿（共 {len(body.splitlines())} 行），请确认是否保存")

    def delete_backup(backup_path: str):
        """删除指定备份文件。删除后刷新恢复列表。"""
        if _delete_backup_file(backup_path):
            # 从会话清单中移除（避免退出哨兵写入已删除路径）
            try:
                session_backup_paths.remove(backup_path)
            except ValueError:
                pass
            # 刷新恢复列表：过滤掉已删除的项
            current = ctx.recovery_list or []
            ctx.set_recovery_list([
                b for b in current if b.backup_path != backup_path
            ])

    def cleanup_expired_backups():
        """清理过期备份（启动时与定时触发）。返回删除数。"""
        try:
            return cleanup_old_backups(ctx.settings)
        except Exception:
            return 0

    return {
        "start_backup_loop": start_backup_loop,
        "trigger_autosave_now": trigger_autosave_now,
        "trigger_backup_now": trigger_backup_now,
        "write_exit_sentinel": write_exit_sentinel,
        "scan_recoverable": scan_recoverable,
        "scan_recent_backups": scan_recent_backups,
        "open_backup_in_new_tab": open_backup_in_new_tab,
        "delete_backup": delete_backup,
        "cleanup_expired_backups": cleanup_expired_backups,
    }
