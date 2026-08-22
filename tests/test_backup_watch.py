"""外部修改检测（watchdog 桥接）集成测试。

覆盖 app._backup_controller：
- _FileChange：事件类型枚举（与 watchfiles.Change 同语义）
- start_backup_loop → _external_check_loop → _run_watcher 端到端：
  外部修改文件 → 弹重载确认对话框；外部删除 → 弹对话框；
  自我写入（mtime 未变）→ 静默不弹

真实文件系统事件（watchdog Observer + 后台线程 → asyncio.Queue 桥接），
用 tmp_path 隔离；时序断言用 _wait_for 条件等待（8s 超时兜底）。
watchdog 缺失时自动落到 5 秒轮询回退，对话框同样到达（行为一致），
故本测试在有无 watchdog 的环境下均可通过。
"""

import asyncio
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app._backup_controller import _FileChange, build_backup_controller


class _FakePage:
    """最小 page 替身：run_task 直接在当前事件循环调度协程。"""

    def run_task(self, coro):
        return asyncio.get_running_loop().create_task(coro())


def _make_ctx(tmp_path, dialogs, last_mtime):
    """构造最小 ctx（仅外部修改检测路径需要的槽位）。"""
    f = tmp_path / "a.md"
    tabs = [
        {
            "type": "editor",
            "file_path": str(f),
            "_last_known_mtime": last_mtime,
        }
    ]
    return types.SimpleNamespace(
        settings={"detect_external_changes": True},
        file_dialog=None,
        set_file_dialog=dialogs.append,
        tabs_ref=types.SimpleNamespace(current=tabs),
        active_index_ref=types.SimpleNamespace(current=0),
        page_ref=types.SimpleNamespace(current=_FakePage()),
        settings_ref=None,
    )


async def _wait_for(cond, timeout: float = 8.0) -> bool:
    """条件等待：每 20ms 轮询一次 cond，超时返回 False（测试兜底）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


# ---- _FileChange 枚举 ----


def test_file_change_enum_semantics():
    """事件类型与 watchfiles.Change 同语义（added/modified/deleted）。"""
    assert _FileChange.added == 1
    assert _FileChange.modified == 2
    assert _FileChange.deleted == 3
    assert _FileChange.modified != _FileChange.deleted
    assert _FileChange.added != _FileChange.modified


# ---- 端到端：watchdog 事件 → 重载对话框 ----


def test_external_modify_triggers_reload_dialog(tmp_path):
    """外部修改已打开文件 → 弹出重载确认对话框（指向正确标签）。"""
    f = tmp_path / "a.md"
    f.write_text("v1", encoding="utf-8")
    dialogs: list = []
    ctx = _make_ctx(tmp_path, dialogs, last_mtime=f.stat().st_mtime)

    async def run():
        ctrl = build_backup_controller(ctx)
        cleanup = ctrl["start_backup_loop"]()
        try:
            # 等待 watcher 就绪
            assert await _wait_for(lambda: True, timeout=1.0)
            await asyncio.sleep(1.2)
            f.write_text("v2", encoding="utf-8")
            assert await _wait_for(lambda: len(dialogs) > 0), "未收到外部修改对话框"
        finally:
            cleanup()

    asyncio.run(run())
    assert dialogs[0]["mode"] == "confirm"
    assert dialogs[0]["action"] == "reload_external"
    assert dialogs[0]["target"] == str(f)
    assert dialogs[0]["target_tab_index"] == 0


def test_external_delete_triggers_reload_dialog(tmp_path):
    """外部删除已打开文件 → 弹出重载确认对话框（文件确实不存在时）。"""
    f = tmp_path / "a.md"
    f.write_text("v1", encoding="utf-8")
    dialogs: list = []
    ctx = _make_ctx(tmp_path, dialogs, last_mtime=f.stat().st_mtime)

    async def run():
        ctrl = build_backup_controller(ctx)
        cleanup = ctrl["start_backup_loop"]()
        try:
            await asyncio.sleep(1.2)
            f.unlink()
            assert await _wait_for(lambda: len(dialogs) > 0), "未收到外部删除对话框"
        finally:
            cleanup()

    asyncio.run(run())
    assert dialogs[0]["action"] == "reload_external"
    assert dialogs[0]["target"] == str(f)


def test_self_write_filter_silent_then_external_trigger(tmp_path):
    """自我写入（mtime 未变）静默；随后真实外部修改仍正常弹框。"""
    f = tmp_path / "a.md"
    f.write_text("v1", encoding="utf-8")
    dialogs: list = []
    # _last_known_mtime 设为未来 → 任何事件 mtime 均 <= 它 → 视为自我写入
    ctx = _make_ctx(tmp_path, dialogs, last_mtime=time.time() + 3600)
    ctrl = build_backup_controller(ctx)

    async def run():
        cleanup = ctrl["start_backup_loop"]()
        try:
            await asyncio.sleep(1.2)
            f.write_text("v2", encoding="utf-8")  # 模拟自我写入后的事件
            await asyncio.sleep(2.0)
            assert dialogs == [], "自我写入不应弹出重载对话框"

            # 更新 mtime 基准（save_doc 写入后更新 _last_known_mtime 的等价动作）
            ctx.tabs_ref.current[0]["_last_known_mtime"] = f.stat().st_mtime
            f.write_text("v3", encoding="utf-8")  # 真实外部修改
            assert await _wait_for(lambda: len(dialogs) > 0), "外部修改未触发对话框"
        finally:
            cleanup()

    asyncio.run(run())
    assert dialogs[0]["target"] == str(f)
