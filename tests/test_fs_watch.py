"""外部文件系统变化轮询监测测试（零依赖 watcher）。

覆盖 views.sidebar：
- _tree_signature：树内容 → 签名（文件增删/嵌套目录可区分、无变化签名稳定）
- poll_fs_changes：外部创建/删除文件触发 on_change、稳定后静默、
  基准预置（应用内重扫同步）不重复上报、should_stop 优雅退出

时序测试用极短 interval（0.05s）+ _wait_for 条件等待（5s 超时兜底），
避免固定 sleep 在慢机器上的 flake；async 协程用 asyncio.run 直接驱动
（与 test_open_folder 一致，不引入 pytest-asyncio）。
"""

import asyncio
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from views.sidebar import _scan_files, _tree_signature, poll_fs_changes  # noqa: E402


# ---- _tree_signature：签名与树内容一一对应 ----


def test_signature_stable_without_change(tmp_path):
    """无文件变化时两次扫描签名一致。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    sig1 = _tree_signature(_scan_files(str(tmp_path)))
    sig2 = _tree_signature(_scan_files(str(tmp_path)))
    assert sig1 == sig2


def test_signature_distinguishes_create(tmp_path):
    """新增文件 → 签名变化。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    sig1 = _tree_signature(_scan_files(str(tmp_path)))
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    sig2 = _tree_signature(_scan_files(str(tmp_path)))
    assert sig1 != sig2


def test_signature_distinguishes_delete(tmp_path):
    """删除文件 → 签名变化。"""
    a = tmp_path / "a.md"
    a.write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("y", encoding="utf-8")
    sig1 = _tree_signature(_scan_files(str(tmp_path)))
    a.unlink()
    sig2 = _tree_signature(_scan_files(str(tmp_path)))
    assert sig1 != sig2


def test_signature_covers_nested_dirs(tmp_path):
    """嵌套目录与其中文件均进入签名（目录相对路径累积）。"""
    d = tmp_path / "sub"
    d.mkdir()
    (d / "a.md").write_text("x", encoding="utf-8")
    sig = _tree_signature(_scan_files(str(tmp_path)))
    assert "/sub" in sig
    assert "a.md" in sig


def test_signature_distinguishes_rename(tmp_path):
    """重命名（= 删旧 + 建新）→ 签名变化。"""
    p = tmp_path / "old.md"
    p.write_text("x", encoding="utf-8")
    sig1 = _tree_signature(_scan_files(str(tmp_path)))
    os.rename(p, tmp_path / "new.md")
    sig2 = _tree_signature(_scan_files(str(tmp_path)))
    assert sig1 != sig2


# ---- poll_fs_changes：轮询协程行为 ----


async def _wait_for(cond, timeout: float = 5.0) -> bool:
    """条件等待：每 20ms 轮询一次 cond，超时返回 False（测试兜底）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def _drive_poll(root, base, changes, stop, checks):
    """公共驱动：启动轮询任务 → 依次执行 checks（异步回调）→ 收尾退出。"""
    task = asyncio.create_task(
        poll_fs_changes(
            root,
            0.05,
            base,
            lambda: changes.append(1),
            lambda: stop["flag"],
        )
    )
    try:
        for check in checks:
            assert await check(), "条件等待超时"
    finally:
        stop["flag"] = True
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            task.cancel()


def test_poll_detects_external_create_and_delete(tmp_path):
    """外部创建 → 上报一次；外部删除 → 再上报；稳定后静默。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    base = types.SimpleNamespace(current=None)
    changes: list[int] = []
    stop = {"flag": False}

    async def run():
        async def stable_no_change():
            # 基准初始化后观察 3 个周期以上，无变化不应上报
            if not await _wait_for(lambda: base.current is not None):
                return False
            await asyncio.sleep(0.2)
            return changes == []

        async def create_reported():
            (tmp_path / "new.md").write_text("n", encoding="utf-8")
            return await _wait_for(lambda: len(changes) == 1)

        async def delete_reported():
            (tmp_path / "new.md").unlink()
            return await _wait_for(lambda: len(changes) == 2)

        async def back_to_silent():
            await asyncio.sleep(0.2)
            return changes == [1, 1]

        await _drive_poll(
            str(tmp_path), base, changes, stop,
            [stable_no_change, create_reported, delete_reported, back_to_silent],
        )

    asyncio.run(run())


def test_poll_preset_base_silent(tmp_path):
    """基准已预置（应用内重扫同步场景）：内容未变时轮询静默。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    base = types.SimpleNamespace(
        current=_tree_signature(_scan_files(str(tmp_path)))
    )
    changes: list[int] = []
    stop = {"flag": False}

    async def run():
        async def silent():
            await asyncio.sleep(0.25)
            return changes == []

        await _drive_poll(str(tmp_path), base, changes, stop, [silent])

    asyncio.run(run())


def test_poll_reports_after_preset_base_changes(tmp_path):
    """基准预置后外部仍变化 → 正常上报（预置不会漏报真实外部变更）。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    base = types.SimpleNamespace(
        current=_tree_signature(_scan_files(str(tmp_path)))
    )
    changes: list[int] = []
    stop = {"flag": False}

    async def run():
        async def external_change_reported():
            (tmp_path / "ext.md").write_text("e", encoding="utf-8")
            return await _wait_for(lambda: len(changes) == 1)

        await _drive_poll(str(tmp_path), base, changes, stop, [external_change_reported])

    asyncio.run(run())
