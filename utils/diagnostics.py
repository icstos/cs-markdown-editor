"""运行时诊断：操作时间线 + 慢操作检测 + 卡顿看门狗。

依赖项：`utils.log`（写盘与现场快照）、标准库。

对外接口：
- mark(event, **fields)        往时间线打一个点（关键动作）
- timed(name, **fields)        `with` 计时段：自动记耗时、超阈值告警、异常必记
- beat()                       主线程心跳（由 asyncio 心跳任务每 1s 调用）
- start_watchdog(...)          启动卡顿看门狗线程
- snapshot(reason, extra)      手动写一份现场快照（诊断面板 / 用户反馈用）
- timeline(limit=None)         取格式化后的时间线（旧 → 新）
- current_operation()          主线程当前正在执行的操作名

为什么需要它（而不是「多打几条日志」）：

1. **日志是线性的，现场是立体的。** 出问题时人只翻到「最后一行」，但那行往往
   是「保存完成」之类无关内容。时间线把最近 64 个关键动作留在内存里，崩溃时
   整段 dump 出来，一眼看出「卡在哪一步、之前刚做了什么」。
2. **慢 ≠ 错。** 界面卡顿不会抛异常，只会「慢慢地」把体验毁掉。这里给每个操作
   加了耗时阈值，超了就自己现形（WARNING / ERROR），不必等用户来报「有点卡」。
3. **未响应必须能定位到行。** 看门狗盯着主线程心跳：心跳停了就说明主线程被
   同步任务占住，此刻的**线程栈**直接写进现场文件——这是「卡在哪一行」的唯一
   可靠证据。只在真的卡住时输出，空闲零噪音。

阈值取值的由来：人眼对 100ms 内的延迟无感，200ms 开始觉得「顿」，1s 以上会
认为「卡了」。故 `SLOW_MS=200` 记 INFO/WARNING，`VERY_SLOW_MS=1500` 记 ERROR。
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections import deque
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from utils import log as _log

logger = logging.getLogger(__name__)

# 慢操作阈值（毫秒）
SLOW_MS = 200
VERY_SLOW_MS = 1500

# 卡顿判定：主线程心跳中断超过该秒数即视为「未响应」
FREEZE_S = 5.0
CHECK_INTERVAL_S = 0.5

# 时间线容量：64 条足够覆盖「出事前那一小段时间」，内存占用可忽略
TIMELINE_SIZE = 64

_timeline: deque[tuple[float, str]] = deque(maxlen=TIMELINE_SIZE)

# 主线程操作栈（只有主线程写，看门狗只读；元组/列表赋值在 CPython 下原子）
_main_ops: list[tuple[str, float]] = []
_last_beat: float = 0.0
_armed = False
_watchdog: _Watchdog | None = None
_watchdog_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 时间线
# ---------------------------------------------------------------------------


def _describe(event: str, fields: dict[str, Any]) -> str:
    if not fields:
        return event
    parts = []
    for key, value in fields.items():
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        parts.append(f"{key}={text}")
    return f"{event} " + " ".join(parts)


def mark(event: str, **fields: Any) -> None:
    """往时间线打一个点。关键动作（打开/保存/切换/恢复）都该打。"""
    _timeline.append((time.time(), _describe(event, fields)))


def timeline(limit: int | None = None) -> list[str]:
    """返回格式化后的时间线（旧 → 新），带「距上一条」的增量秒数。"""
    items = list(_timeline)
    if limit is not None and limit > 0:
        items = items[-limit:]
    lines: list[str] = []
    prev: float | None = None
    for ts, text in items:
        stamp = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        delta = f"+{ts - prev:6.3f}s" if prev is not None else "        "
        lines.append(f"{stamp}  {delta}  {text}")
        prev = ts
    return lines


def _timeline_provider() -> list[str]:
    """供 `utils.log.crash_snapshot` 附加到现场文件。"""
    return timeline()


def install() -> None:
    """把时间线接到现场快照（幂等）。"""
    _log.register_context_provider(_timeline_provider)
    mark("诊断已就绪", 时间线容量=TIMELINE_SIZE)


# ---------------------------------------------------------------------------
# 慢操作检测
# ---------------------------------------------------------------------------


def current_operation() -> str | None:
    """主线程当前正在执行的操作名（无则 None）。"""
    return _main_ops[-1][0] if _main_ops else None


@contextlib.contextmanager
def timed(
    name: str,
    slow_ms: float = SLOW_MS,
    very_slow_ms: float = VERY_SLOW_MS,
    inflight: bool = True,
    **fields: Any,
) -> Iterator[None]:
    """计时段：记录耗时，超阈值告警，异常必记并原样抛出。

    用法::

        with diagnostics.timed("打开文件", path=p):
            ...

    设计取舍：**只在超阈值或异常时打日志**。正常路径仅往时间线打一个点（内存
    里一次 append，微秒级），所以它可以安全地放在「每次保存/每次切换」这类
    中频路径上，而不会把日志刷成流水账。

    Args:
        inflight: 是否把本段登记为「主线程当前操作」（看门狗报告卡住时的对象）。
            **跨 `await` 的计时段必须传 False**：等待后台线程 / IO 期间主线程其实
            是空闲的（心跳正常），若仍占着「当前操作」的位置，主线程随后卡在别处
            时看门狗就会指错人。纯同步耗时段保持默认 True。
    """
    on_main = inflight and threading.current_thread() is threading.main_thread()
    started = time.perf_counter()
    if on_main:
        _main_ops.append((name, started))
    finished = False
    try:
        yield
        finished = True
    except BaseException:
        # 异常必记（含 traceback），随后原样抛出——诊断层不改变控制流。
        elapsed = (time.perf_counter() - started) * 1000
        logger.error("操作失败 %s（耗时 %.0fms）", _describe(name, fields), elapsed, exc_info=True)
        mark(f"{name} ✗", 耗时=f"{elapsed:.0f}ms", **fields)
        raise
    finally:
        if on_main and _main_ops and _main_ops[-1][1] == started:
            _main_ops.pop()
        # `finally` 里不可用 `return`（会静默吞掉上面 raise 出去的异常），
        # 故用条件包裹而非提前返回。
        if finished:
            elapsed = (time.perf_counter() - started) * 1000
            if elapsed >= very_slow_ms:
                logger.error(
                    "操作极慢 %s（%.0fms，阈值 %.0fms）",
                    _describe(name, fields),
                    elapsed,
                    very_slow_ms,
                )
                mark(f"{name} ⚠", 耗时=f"{elapsed:.0f}ms", **fields)
            elif elapsed >= slow_ms:
                logger.warning("操作偏慢 %s（%.0fms）", _describe(name, fields), elapsed)
                mark(name, 耗时=f"{elapsed:.0f}ms", **fields)
            else:
                logger.debug("%s 完成 %.0fms", name, elapsed)
                mark(name, 耗时=f"{elapsed:.0f}ms", **fields)


# ---------------------------------------------------------------------------
# 卡顿看门狗
# ---------------------------------------------------------------------------


def beat() -> None:
    """主线程心跳。一次单调时钟读取 + 一次赋值，可放在任何地方。"""
    global _last_beat
    _last_beat = time.monotonic()


def arm() -> None:
    """允许看门狗开始判定（心跳任务就绪后调用）。

    未 arm 前不判定：应用启动早期主线程尚未进入事件循环，心跳天然为 0，
    此时报警全是误报。
    """
    global _armed
    _armed = True
    beat()


class _Watchdog(threading.Thread):
    """盯着主线程心跳；停止超过 `freeze_s` 就落一份现场快照。

    只在「真的卡住」时输出：恢复后重置，同一次卡顿不刷屏。
    """

    def __init__(self, freeze_s: float, interval_s: float) -> None:
        super().__init__(name="log-watchdog", daemon=True)
        self.freeze_s = freeze_s
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._reported = False
        self._freeze_started = 0.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.interval_s):
            # 看门狗自身绝不能把进程带走
            with contextlib.suppress(Exception):
                self._check()

    def _check(self) -> None:
        if not _armed or _last_beat <= 0:
            return
        now = time.monotonic()
        gap = now - _last_beat

        if gap >= self.freeze_s:
            if not self._reported:
                self._reported = True
                self._freeze_started = _last_beat
                self._report(gap)
        elif self._reported:
            self._reported = False
            duration = now - self._freeze_started
            logger.warning("界面已恢复：本次无响应约 %.1fs", duration)
            mark("界面恢复", 无响应历时=f"{duration:.1f}s")

    def _report(self, gap: float) -> None:
        # 卡在哪一行由现场文件里的「线程栈」给出（看门狗线程抓不到主线程的调用链，
        # 主线程栈由 utils.log.dump_thread_stacks 从 sys._current_frames() 直接读）。
        op = current_operation()
        where = f"操作={op}" if op else "操作=事件循环（无 in-flight 操作）"

        logger.critical(
            "界面无响应：主线程心跳已停 %.1fs（阈值 %.0fs）· %s", gap, self.freeze_s, where
        )
        path = _log.crash_snapshot(
            "freeze",
            extra=f"主线程心跳停止 {gap:.1f}s · {where}",
            timeline=timeline(),
            title="界面无响应（主线程被阻塞）",
        )
        if path:
            logger.critical("现场文件: %s", path)


def start_watchdog(freeze_s: float = FREEZE_S, interval_s: float = CHECK_INTERVAL_S) -> _Watchdog | None:
    """启动卡顿看门狗（幂等）。返回线程对象。"""
    global _watchdog
    with _watchdog_lock:
        if _watchdog is not None and _watchdog.is_alive():
            return _watchdog
        _watchdog = _Watchdog(freeze_s, interval_s)
        _watchdog.start()
        logger.debug("看门狗已启动：阈值 %.1fs / 间隔 %.1fs", freeze_s, interval_s)
        return _watchdog


def stop_watchdog() -> None:
    global _watchdog
    with _watchdog_lock:
        if _watchdog is not None:
            _watchdog.stop()
            _watchdog = None


# ---------------------------------------------------------------------------
# 手动快照
# ---------------------------------------------------------------------------


def snapshot(reason: str = "手动诊断", extra: str = "") -> str | None:
    """写一份现场快照（不含异常），返回文件路径。"""
    mark("导出诊断快照", 原因=reason)
    return _log.crash_snapshot(
        "manual", extra=extra, timeline=timeline(), title=f"手动诊断 · {reason}"
    )


def reset() -> None:
    """清空时间线与心跳（仅供测试）。"""
    global _last_beat, _armed
    _timeline.clear()
    _main_ops.clear()
    _last_beat = 0.0
    _armed = False
