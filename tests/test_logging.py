"""日志与诊断基础设施的守卫测试。

覆盖三件事（对应「异常时能被定位」的三个必要条件）：
1. **日志真的落盘**，且每行自带时间 / 级别 / 模块:行号 / 线程；
2. **异常真的进日志**：全局钩子 + 崩溃现场文件（含 traceback / 时间线 / 线程栈）；
3. **卡顿真的能被判出**：慢操作告警、主线程心跳停摆被发现、恢复被记录。

设计取舍：断言尽量落在「文件内容」与「公开 API」上，而不是实现细节——这些
测试要保护的是「出事时查得到」，不是某段代码怎么写。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib
import re
import sys
import threading
import time

import pytest

from utils import diagnostics
from utils import log as ulog

# ---------------------------------------------------------------------------
# 夹具与工具
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_log(tmp_path):
    """把日志装到临时目录，结束后完整还原宿主环境的 logging 状态。

    日志是全局副作用（root handler / 异常钩子），不隔离就会污染其它测试。
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_hooks = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    # 先摘掉宿主（pytest）的 handler：日志拆卸会 close 它们，摘掉后才安全
    for handler in saved_handlers:
        root.removeHandler(handler)
    handle = ulog.setup(level="DEBUG", log_dir=str(tmp_path), console=False, force=True)
    diagnostics.reset()
    diagnostics.install()  # 真实行为：把时间线接到现场快照
    try:
        yield handle, tmp_path
    finally:
        diagnostics.stop_watchdog()
        diagnostics.reset()
        ulog.uninstall()
        root.setLevel(saved_level)
        for handler in saved_handlers:
            root.addHandler(handler)
        sys.excepthook, threading.excepthook, sys.unraisablehook = saved_hooks


def _wait_for(path: str | pathlib.Path, needle: str, timeout: float = 3.0) -> bool:
    """等异步写盘的日志出现（队列 → 后台线程，落盘有微小延迟）。"""
    p = pathlib.Path(path)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if p.exists() and needle in p.read_text(encoding="utf-8"):
            return True
        time.sleep(0.02)
    return p.exists() and needle in p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------


def test_setup_is_idempotent(clean_log):
    handle, _ = clean_log
    again = ulog.setup(log_dir=os.path.join("C:", "definitely", "missing"))
    assert again is handle, "重复安装必须复用同一句柄，否则会叠加 handler 造成重复日志"
    assert again.log_dir == handle.log_dir


def test_resolve_log_dir_env_override(monkeypatch, tmp_path):
    target = tmp_path / "custom-logs"
    monkeypatch.setenv(ulog.ENV_DIR, str(target))
    assert ulog.resolve_log_dir() == os.path.abspath(str(target))


def test_resolve_log_dir_dev_mode_is_project_local(monkeypatch):
    """开发态（有 pyproject.toml + .git）日志落在项目内 logs/，便于直接取证。"""
    monkeypatch.delenv(ulog.ENV_DIR, raising=False)
    root = ulog._project_root()
    if root is None:
        pytest.skip("当前不是完整仓库副本（缺 .git），跳过开发态路径断言")
    assert ulog.resolve_log_dir() == os.path.join(root, "logs")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("Warning", logging.WARNING),
        (logging.ERROR, logging.ERROR),
        ("不认识的级别", logging.INFO),
        (None, logging.INFO),
    ],
)
def test_resolve_level(raw, expected):
    assert ulog.resolve_level(raw) == expected


def test_env_level_beats_settings(clean_log, monkeypatch):
    handle, _ = clean_log
    monkeypatch.setenv(ulog.ENV_LEVEL, "ERROR")
    # 环境变量在场（「本次启动我就要 ERROR」）→ 设置文件的值不覆盖它
    assert ulog.apply_settings_level("DEBUG") == handle.level
    monkeypatch.delenv(ulog.ENV_LEVEL)
    assert ulog.apply_settings_level("ERROR") == logging.ERROR


def test_apply_settings_level_is_noop_when_not_installed(monkeypatch):
    """未安装日志（如测试/嵌入式场景）时不得去改宿主 root logger。"""
    monkeypatch.setattr(ulog, "_handle", None)
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    monkeypatch.delenv(ulog.ENV_LEVEL, raising=False)
    ulog.apply_settings_level("DEBUG")
    assert (root.level, list(root.handlers)) == before


# ---------------------------------------------------------------------------
# 落盘与格式
# ---------------------------------------------------------------------------


def test_line_has_time_level_module_thread(clean_log):
    handle, _ = clean_log
    ulog.get_logger("tests.probe").warning("格式化检查 %s", "值")
    assert _wait_for(handle.log_file, "格式化检查 值"), "日志未落盘"

    text = pathlib.Path(handle.log_file).read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "格式化检查" in ln)
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} ", line), f"缺时间戳: {line}"
    assert "[WARNING ]" in line, f"缺级别: {line}"
    assert "tests.probe:" in line, f"缺模块:行号: {line}"
    assert "(MainThread)" in line, f"缺线程名: {line}"


def test_file_handler_rotates_and_is_utf8(clean_log):
    handle, _ = clean_log
    file_handler = next(
        h for h in handle.listener.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    )
    assert file_handler.maxBytes == ulog.MAX_BYTES
    assert file_handler.backupCount == ulog.BACKUP_COUNT
    assert str(file_handler.encoding).lower().replace("-", "") == "utf8"


def test_exception_is_logged_with_traceback(clean_log):
    handle, _ = clean_log
    logger = ulog.get_logger("tests.raise")
    try:
        raise ValueError("落盘用的异常")
    except ValueError:
        logger.error("操作炸了", exc_info=True)
    assert _wait_for(handle.log_file, "ValueError: 落盘用的异常")


def test_hooks_are_installed(clean_log):
    assert sys.excepthook is ulog._excepthook
    assert threading.excepthook is ulog._thread_excepthook


# ---------------------------------------------------------------------------
# 崩溃现场
# ---------------------------------------------------------------------------


def test_crash_snapshot_carries_everything_needed(clean_log):
    """一份现场文件必须自足：说明 + 异常 + 时间线 + 线程栈。"""
    _, tmp = clean_log
    diagnostics.mark("出事前刚做了这个", 文件="a.md")
    try:
        raise KeyError("现场键")
    except KeyError:
        info = sys.exc_info()
        path = ulog.crash_snapshot("uncaught", exc_info=info, title="主线程未捕获异常")

    body = pathlib.Path(path).read_text(encoding="utf-8")
    assert "主线程未捕获异常" in body
    assert "KeyError: '现场键'" in body
    assert "出事前刚做了这个" in body
    assert "线程栈" in body and "MainThread" in body
    assert pathlib.Path(path).name.startswith("crash-")
    assert str(tmp) in path


def test_excepthook_writes_crash_file(clean_log, monkeypatch):
    _, tmp = clean_log
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: None)  # 免得 stderr 刷屏
    try:
        raise RuntimeError("钩子捕获的异常")
    except RuntimeError:
        ulog._excepthook(*sys.exc_info())

    files = list(pathlib.Path(tmp).glob("crash-*.log"))
    assert files, "未捕获异常必须留下现场文件"
    assert "钩子捕获的异常" in files[0].read_text(encoding="utf-8")


def test_thread_excepthook_writes_crash_file(clean_log):
    _, tmp = clean_log

    def boom():
        raise ValueError("子线程异常")

    thread = threading.Thread(target=boom, name="probe-boom")
    thread.start()
    thread.join()

    files = list(pathlib.Path(tmp).glob("crash-*.log"))
    assert files, "子线程未捕获异常同样必须留痕"
    assert "子线程异常" in files[0].read_text(encoding="utf-8")


def test_dump_thread_stacks_lists_all_threads(clean_log):
    release = threading.Event()

    def worker():
        release.wait(3)

    thread = threading.Thread(target=worker, name="probe-worker")
    thread.start()
    try:
        text = ulog.dump_thread_stacks()
    finally:
        release.set()
        thread.join()

    assert "MainThread" in text
    assert "probe-worker" in text


# ---------------------------------------------------------------------------
# 时间线与慢操作
# ---------------------------------------------------------------------------


def test_timeline_is_bounded_and_ordered(clean_log):
    for i in range(diagnostics.TIMELINE_SIZE * 3):
        diagnostics.mark("打点", i=i)
    lines = diagnostics.timeline()
    assert len(lines) == diagnostics.TIMELINE_SIZE, "时间线必须是有界环形缓冲"
    assert "i=" in lines[-1]


def test_timed_marks_normal_path(clean_log):
    with diagnostics.timed("正常操作", slow_ms=10_000):
        pass
    assert any("正常操作" in ln for ln in diagnostics.timeline())


def test_timed_warns_when_slow(clean_log, caplog):
    with (
        caplog.at_level(logging.WARNING, logger="utils.diagnostics"),
        diagnostics.timed("慢操作", slow_ms=1, very_slow_ms=10_000),
    ):
        time.sleep(0.02)
    assert any("操作偏慢" in r.getMessage() for r in caplog.records), "超过阈值必须告警"


def test_timed_errors_when_very_slow(clean_log, caplog):
    with (
        caplog.at_level(logging.ERROR, logger="utils.diagnostics"),
        diagnostics.timed("极慢操作", slow_ms=1, very_slow_ms=5),
    ):
        time.sleep(0.03)
    assert any("操作极慢" in r.getMessage() for r in caplog.records)


def test_timed_reraises_and_records_failure(clean_log, caplog):
    """诊断层只观察，不改变控制流——异常必须原样抛出。"""
    with (
        caplog.at_level(logging.ERROR, logger="utils.diagnostics"),
        pytest.raises(RuntimeError, match="故意失败"),
        diagnostics.timed("会失败的操作"),
    ):
        raise RuntimeError("故意失败")

    assert any("操作失败" in r.getMessage() for r in caplog.records)
    assert any("✗" in ln for ln in diagnostics.timeline()), "失败也要进时间线"


def test_timed_does_not_claim_slot_when_inflight_false(clean_log):
    """跨 await 的计时段不占「当前操作」——否则看门狗会指错人。"""
    with diagnostics.timed("后台等待", inflight=False):
        assert diagnostics.current_operation() is None
    with diagnostics.timed("前台操作"):
        assert diagnostics.current_operation() == "前台操作"
    assert diagnostics.current_operation() is None


# ---------------------------------------------------------------------------
# 卡顿看门狗
# ---------------------------------------------------------------------------


def test_watchdog_detects_freeze_then_recovery(clean_log, caplog):
    _, tmp = clean_log
    diagnostics.arm()
    diagnostics._last_beat = time.monotonic() - 10.0  # 伪造：心跳已停 10 秒
    watchdog = diagnostics._Watchdog(freeze_s=5.0, interval_s=0.05)

    with caplog.at_level(logging.CRITICAL):
        watchdog._check()
    assert watchdog._reported is True
    assert any("界面无响应" in r.getMessage() for r in caplog.records)
    freeze_files = list(pathlib.Path(tmp).glob("crash-*-freeze.log"))
    assert freeze_files, "卡死必须留下现场文件"
    assert "线程栈" in freeze_files[0].read_text(encoding="utf-8")

    # 同一次卡顿只报一次（不刷屏）
    caplog.clear()
    with caplog.at_level(logging.CRITICAL):
        watchdog._check()
    assert not any("界面无响应" in r.getMessage() for r in caplog.records)

    # 心跳恢复 → 记一条恢复日志，并允许下一次卡顿再次上报
    diagnostics.beat()
    with caplog.at_level(logging.WARNING):
        watchdog._check()
    assert watchdog._reported is False
    assert any("界面已恢复" in r.getMessage() for r in caplog.records)


def test_watchdog_silent_when_healthy(clean_log, caplog):
    """空闲（心跳正常）时零输出——这是它能长期开着的理由。"""
    diagnostics.arm()
    diagnostics.beat()
    watchdog = diagnostics._Watchdog(freeze_s=5.0, interval_s=0.05)
    with caplog.at_level(logging.DEBUG):
        watchdog._check()
    assert not caplog.records
    assert watchdog._reported is False


def test_watchdog_not_armed_means_silent(clean_log, caplog):
    """心跳任务尚未就绪（启动早期）时不得判定，否则全是误报。"""
    diagnostics.reset()  # _armed = False
    diagnostics._last_beat = time.monotonic() - 10.0
    watchdog = diagnostics._Watchdog(freeze_s=5.0, interval_s=0.05)
    with caplog.at_level(logging.DEBUG):
        watchdog._check()
    assert not caplog.records


def test_snapshot_writes_manual_report(clean_log):
    _handle, _tmp = clean_log
    path = diagnostics.snapshot("用户反馈", extra="复现步骤：打开大文件后滚动")
    assert path is not None
    body = pathlib.Path(path).read_text(encoding="utf-8")
    assert "用户反馈" in body
    assert "复现步骤：打开大文件后滚动" in body


def test_prune_is_fault_tolerant(tmp_path):
    """目录不存在 / 无权限时安静返回，绝不让清理本身抛进崩溃路径。"""
    assert ulog._prune_crash_files(str(tmp_path / "并不存在")) == 0


def test_crash_files_pruned_to_keep_limit(clean_log, monkeypatch):
    """现场文件有保留上限——否则崩溃循环能靠「每次新文件名」写满磁盘。

    注意 `app.log` 的轮转管不到现场文件：`RotatingFileHandler` 只轮转固定
    文件名，而现场文件为了不互相覆盖是**每次一个新名字**，天生不受轮转约束。
    """
    _handle, tmp = clean_log
    monkeypatch.setattr(ulog, "CRASH_KEEP", 5)
    for i in range(12):
        p = tmp / f"crash-20200101-0000{i:02d}-stale.log"
        p.write_text("旧现场\n", encoding="utf-8")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))  # 显式拉开 mtime

    path = ulog.crash_snapshot("probe", title="裁剪探针")
    assert path is not None

    remaining = sorted(p.name for p in pathlib.Path(tmp).glob("crash-*.log"))
    assert len(remaining) == 5, remaining
    assert pathlib.Path(path).name in remaining  # 刚写的这份必须在
    # 删掉的是最旧的一批，最新的一批留下
    assert remaining[0] == "crash-20200101-000008-stale.log", remaining


def test_setup_prunes_stale_crash_files_from_last_run(clean_log, monkeypatch):
    """每次启动顺手清一次：上次运行堆下的现场不会跨会话累积。"""
    _handle, tmp = clean_log
    monkeypatch.setattr(ulog, "CRASH_KEEP", 3)
    for i in range(10):
        p = tmp / f"crash-20200101-0000{i:02d}-old.log"
        p.write_text("x\n", encoding="utf-8")
        os.utime(p, (2_000_000 + i, 2_000_000 + i))

    ulog.setup(level="DEBUG", log_dir=str(tmp), console=False, force=True)

    remaining = sorted(p.name for p in pathlib.Path(tmp).glob("crash-*.log"))
    assert len(remaining) == 3, remaining
    assert remaining[-1] == "crash-20200101-000009-old.log", remaining
