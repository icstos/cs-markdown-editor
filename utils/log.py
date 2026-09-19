"""统一日志基础设施：分级、轮转、异步落盘、全局异常钩子、崩溃现场取证。

依赖项：仅标准库。**刻意不依赖项目内任何模块**——日志必须能在 import 序列的
最早期安装，用来捕获 `flet` / `app` 导入期与启动期的故障；若它反过来依赖项目
模块，被依赖方的导入错误就永远记不下来。

对外接口：
- setup(...) -> LogHandle     安装日志（幂等；重复调用只生效一次）
- get_logger(name) -> Logger  取模块级 logger（各模块 `get_logger(__name__)`）
- set_level(level)            运行期调级（环境变量 / 设置面板）
- log_dir() / log_file()      日志位置（供 UI 展示、Agent 取证）
- dump_thread_stacks()        抓取全线程调用栈
- crash_snapshot(reason, ...) 同步写「崩溃 / 卡死现场」文件
- register_context_provider() 注册现场附加上下文（由 utils.diagnostics 注入时间线）

设计要点：
1. **双路径落盘**：常规日志经后台线程异步写（调用方只入队，O(1) 不阻塞 UI）；
   崩溃 / 卡死现场**同步**直写，保证进程异常终止时仍然落盘——异步队列在
   `os._exit` / 原生崩溃时是留不住东西的。
2. **取证路径可预测**：`CS_MD_LOG_DIR` > 开发态（项目内 `logs/`）> 平台标准
   目录。三条规则覆盖开发与打包两种形态，Agent / 人工按固定路径取日志即可。
3. **轮转**：单文件 2MB × 3 备份，长期运行不撑爆磁盘。
4. **安装幂等 + 失败降级**：重复调用只生效一次；日志系统自身的故障（目录不可
   写、handler 构造失败）必须降级而不能把应用一起带走。
5. 安装 root handler 后，`flet` 启动时的 `logging.basicConfig()` 会自动成为
   no-op（root 已有 handler），因此第三方库与 flet 内部的错误同样进日志。

格式（固定列宽，便于人读与 grep）::

    2026-09-19 14:40:12.345 [INFO    ] app._file_io_ops:235 (MainThread) 打开文件 path=...
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

APP_NAME = "CS-Markdown-Editor"
LOG_FILENAME = "app.log"
CRASH_GLOB = "crash-*.log"
CRASH_KEEP = 50              # 现场文件最多保留份数（见 _prune_crash_files）

MAX_BYTES = 2 * 1024 * 1024  # 单文件 2MB
BACKUP_COUNT = 3             # 保留 3 个轮转备份

ENV_DIR = "CS_MD_LOG_DIR"
ENV_LEVEL = "CS_MD_LOG_LEVEL"
ENV_CONSOLE = "CS_MD_LOG_CONSOLE"

_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d [%(levelname)-8s] %(name)s:%(lineno)d "
    "(%(threadName)s) %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 现场快照分隔线宽度（纯观感，Agent 也按 `---` 分段解析）
_RULE = "=" * 72
_SUB_RULE = "-" * 72


# ---------------------------------------------------------------------------
# 安装状态
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_handle: LogHandle | None = None
_context_provider: Callable[[], list[str]] | None = None


@dataclass
class LogHandle:
    """一次日志安装的句柄（同一进程内幂等复用）。"""

    log_dir: str
    log_file: str
    session: str
    level: int
    console: bool
    started_at: float
    listener: Any = field(default=None)  # logging.handlers.QueueListener | None

    def as_dict(self) -> dict[str, Any]:
        """给 UI / 诊断面板用的只读快照。"""
        return {
            "log_dir": self.log_dir,
            "log_file": self.log_file,
            "session": self.session,
            "level": logging.getLevelName(self.level),
            "console": self.console,
        }


# ---------------------------------------------------------------------------
# 位置解析
# ---------------------------------------------------------------------------


def _project_root() -> str | None:
    """开发态的项目根：同时存在 `pyproject.toml` 与 `.git` 才算。

    两者结合可可靠区分「源码运行」与「flet build 打包产物」——打包时 `.git`
    在 `[tool.flet.app] exclude` 里被排除，不会随包分发。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "pyproject.toml")) and os.path.isdir(
        os.path.join(root, ".git")
    ):
        return root
    return None


def resolve_log_dir() -> str:
    """解析日志目录：环境变量 > 开发态（项目内 logs/）> 平台标准目录。"""
    env = os.environ.get(ENV_DIR)
    if env:
        return os.path.abspath(env)

    root = _project_root()
    if root is not None:
        return os.path.join(root, "logs")

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME, "logs")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Logs", APP_NAME)
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state"
    )
    return os.path.join(base, "cs-markdown-editor", "logs")


def _console_default() -> bool:
    """打包态（GUI 应用，无控制台）默认不开 stderr handler。"""
    env = os.environ.get(ENV_CONSOLE)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "")
    return _project_root() is not None


def resolve_level(level: int | str | None = None) -> int:
    """解析级别：显式入参 > `CS_MD_LOG_LEVEL` > INFO。"""
    raw: Any = level if level is not None else os.environ.get(ENV_LEVEL)
    if raw is None:
        return logging.INFO
    if isinstance(raw, int):
        return raw
    name = str(raw).strip().upper()
    resolved = logging.getLevelName(name)
    if isinstance(resolved, int):
        return resolved
    return logging.INFO


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)


def _build_sink_formatter() -> logging.Formatter:
    """listener 端 handler 用：记录已被 QueueHandler 渲染过，只需透传。"""
    return logging.Formatter("%(message)s")


def setup(
    level: int | str | None = None,
    log_dir: str | None = None,
    console: bool | None = None,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
    force: bool = False,
) -> LogHandle:
    """安装日志系统（幂等）。

    Args:
        level: 级别（int 或名称），None 时取环境变量 / INFO。
        log_dir: 日志目录，None 时用 `resolve_log_dir()`。
        console: 是否输出到 stderr，None 时按开发态自动判定。
        max_bytes / backup_count: 轮转阈值与备份数。
        force: 强制重装（测试或运行期切换目录用）。

    Returns:
        LogHandle：日志目录、文件路径、会话号、实际级别。
    """
    global _handle
    with _lock:
        if _handle is not None and not force:
            if level is not None:
                set_level(level)
            return _handle
        if _handle is not None:
            _teardown()

        resolved_level = resolve_level(level)
        target_dir = log_dir or resolve_log_dir()
        use_console = _console_default() if console is None else console
        session = datetime.now().strftime("%Y%m%d-%H%M%S")

        root = logging.getLogger()
        root.setLevel(resolved_level)

        file_path = os.path.join(target_dir, LOG_FILENAME)
        listener = None
        try:
            os.makedirs(target_dir, exist_ok=True)
            _prune_crash_files(target_dir)
            formatter = _build_formatter()
            sink_formatter = _build_sink_formatter()

            handlers: list[logging.Handler] = []
            file_handler = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(sink_formatter)
            file_handler.setLevel(resolved_level)
            handlers.append(file_handler)

            if use_console and sys.stderr is not None:
                stream_handler = logging.StreamHandler(sys.stderr)
                stream_handler.setFormatter(sink_formatter)
                stream_handler.setLevel(resolved_level)
                handlers.append(stream_handler)

            # 异步：UI 线程只入队，写盘在后台线程完成。
            # QueueHandler 必须先 format 再入队（否则 traceback 会丢），
            # 故它的 formatter 与文件端一致，listener 端只需透传 message。
            log_queue: Any = _make_queue()
            queue_handler = logging.handlers.QueueHandler(log_queue)
            queue_handler.setFormatter(formatter)
            queue_handler.setLevel(resolved_level)
            listener = logging.handlers.QueueListener(
                log_queue, *handlers, respect_handler_level=True
            )
            listener.start()
            root.addHandler(queue_handler)
        except Exception as exc:  # 日志装不上不能拖垮应用
            _fallback(exc)
            resolved_level = logging.WARNING
            use_console = sys.stderr is not None

        _handle = LogHandle(
            log_dir=target_dir,
            log_file=file_path,
            session=session,
            level=resolved_level,
            console=use_console,
            started_at=datetime.now().timestamp(),
            listener=listener,
        )
        _install_excepthooks()
        atexit.register(_shutdown)
        return _handle


def _make_queue() -> Any:
    """无界 SimpleQueue：`put_nowait` 永不阻塞调用方。"""
    import queue

    return queue.SimpleQueue()


def _fallback(exc: BaseException) -> None:
    """日志系统自身故障：只往 stderr 吐一行，不做任何可能再抛的事。"""
    with contextlib.suppress(Exception):
        sys.stderr.write(f"[log] 日志系统安装失败，降级为仅 stderr：{exc!r}\n")


def _teardown() -> None:
    """拆掉现有 handler（仅供 `force=True` 重装时调用）。"""
    global _handle
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()
    if _handle is not None and _handle.listener is not None:
        with contextlib.suppress(Exception):
            _handle.listener.stop()
    _handle = None


def _shutdown() -> None:
    """进程退出：先冲干净队列再关文件。"""
    if _handle is not None and _handle.listener is not None:
        with contextlib.suppress(Exception):
            _handle.listener.stop()
    with contextlib.suppress(Exception):
        logging.shutdown()


def uninstall() -> None:
    """拆除日志系统（测试隔离 / 运行期换目录用）。"""
    with _lock:
        _teardown()


def is_installed() -> bool:
    return _handle is not None


def get_logger(name: str) -> logging.Logger:
    """取模块级 logger（`get_logger(__name__)`）。"""
    return logging.getLogger(name)


def apply_settings_level(level: int | str | None) -> int:
    """按「环境变量 > 设置文件」的优先级应用级别。

    环境变量表达的是「本次启动我就要 DEBUG」，设置面板改的是常态偏好，
    因此环境变量在场时不覆盖它。`level` 为空表示设置里没这一项。
    """
    if _handle is None or level is None or os.environ.get(ENV_LEVEL):
        # 未安装日志（如测试环境）时不做任何事：设置 root handler 级别这类副作用
        # 不该由一个「应用设置」入口在宿主环境里悄悄执行。
        return _handle.level if _handle is not None else logging.INFO
    return set_level(level)


def set_level(level: int | str) -> int:
    """运行期调级：root 与所有 handler 同步调整。"""
    resolved = resolve_level(level)
    root = logging.getLogger()
    root.setLevel(resolved)
    for handler in root.handlers:
        handler.setLevel(resolved)
    if _handle is not None:
        _handle.level = resolved
    return resolved


def log_dir() -> str | None:
    return _handle.log_dir if _handle is not None else None


def log_file() -> str | None:
    return _handle.log_file if _handle is not None else None


def session_id() -> str:
    return _handle.session if _handle is not None else "-"


def session_header(extra: str = "") -> str:
    """会话头：一次性把「这台机器 / 这个版本 / 这次启动」写进日志。

    定位问题时最浪费时间的往往是「不知道用户跑的是哪个版本、什么环境」，
    所以启动第一条日志就把这些固定下来。
    """
    import platform

    lines = [
        _RULE,
        f"会话 {session_id()} 启动 · {APP_NAME}",
        _SUB_RULE,
        f"Python  : {sys.version.split()[0]} ({platform.platform()})",
        f"flet    : {_safe_version('flet')}",
        f"进程    : pid={os.getpid()}  cwd={os.getcwd()}",
        f"日志    : {log_file()}",
        f"级别    : {logging.getLevelName(_handle.level) if _handle else 'INFO'}"
        f"  stderr={_handle.console if _handle else False}",
        f"命令行  : {' '.join(sys.argv)}",
    ]
    if extra:
        lines.append(f"备注    : {extra}")
    lines.append(_RULE)
    return "\n".join(lines)


def _safe_version(pkg: str) -> str:
    try:
        import importlib.metadata as md

        return md.version(pkg)
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# 全局异常钩子
# ---------------------------------------------------------------------------


def _install_excepthooks() -> None:
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    sys.unraisablehook = _unraisablehook


def _report(slug: str, title: str, exc_info: Any, where: str = "") -> None:
    """统一出口：写日志 + 落一份现场文件。

    `slug` 只用于文件名（必须 ASCII，Windows 路径与 shell 都友好），
    `title` 是给人看的中文说明，写在现场文件首行。
    """
    logger = logging.getLogger("crash")
    detail = f"（{where}）" if where else ""
    logger.critical("%s%s", title, detail, exc_info=exc_info)
    logger.critical("现场文件: %s", crash_snapshot(slug, exc_info=exc_info, title=title + detail))


def _excepthook(exc_type: Any, exc: BaseException, tb: Any) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    _report("uncaught", "主线程未捕获异常", (exc_type, exc, tb))
    sys.__excepthook__(exc_type, exc, tb)


def _thread_excepthook(args: Any) -> None:
    name = getattr(args.thread, "name", "?")
    _report(
        f"thread-{name}",
        f"子线程 {name} 未捕获异常",
        (args.exc_type, args.exc_value, args.exc_traceback),
    )


def _unraisablehook(args: Any) -> None:
    """`__del__` / 弱引用回调里抛出的、无法传播的异常。"""
    _report(
        "unraisable",
        f"不可传播异常（{args.err_msg or 'unraisable'}）",
        (args.exc_type, args.exc_value, args.exc_traceback),
        where=getattr(args.object, "__qualname__", "") or "",
    )


def attach_asyncio(loop: Any) -> None:
    """挂 asyncio 事件循环的异常处理（Flet 的 `ft.run` 内部循环）。

    事件循环里被静默吞掉的 future 异常只有在这里才看得到。
    """

    def _on_loop_exception(loop_: Any, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        message = context.get("message", "")
        if isinstance(exc, BaseException):
            _report("asyncio", "事件循环异常", (type(exc), exc, exc.__traceback__), where=message)
        else:
            logging.getLogger("crash").error("asyncio 异常：%s", message)

    with contextlib.suppress(Exception):
        loop.set_exception_handler(_on_loop_exception)


def attach_flet(page: Any) -> None:
    """挂 Flet 的两条异常通道。

    1. `page.on_error`：控件事件回调里逃逸的异常；
    2. `page.run_task`：异步任务里逃逸的异常。

    第 2 条为什么必须单独包装：flet 的 `run_task` 把任务异常 `raise` 在
    `concurrent.futures` 的完成回调里，最终由那个模块的 logger 打一条 ERROR。
    异常不会丢，但**没有现场文件**、logger 也不叫 `crash`——排查时极易被跳过。
    这里补一份带时间线与线程栈的现场。`page.run_task` 是本项目所有异步操作的
    唯一入口（打开文件 / 备份 / 启动扫描…），所以这一处覆盖了绝大多数异步异常。
    """
    _attach_on_error(page)
    _instrument_run_task(page)


def _attach_on_error(page: Any) -> None:
    """控件回调异常：`page.on_error`。"""
    prev = getattr(page, "on_error", None)

    def _on_error(e: Any) -> None:
        exc = getattr(e, "error", None) or getattr(e, "message", None) or e
        logging.getLogger("crash").error("Flet 回调异常：%s", exc)
        crash_snapshot("flet-callback", extra=str(exc), title="Flet 回调异常")
        if callable(prev):
            with contextlib.suppress(Exception):
                prev(e)

    with contextlib.suppress(Exception):
        page.on_error = _on_error


def _instrument_run_task(page: Any) -> None:
    """包装 `page.run_task`，为异步任务补现场（幂等）。"""
    original = getattr(page, "run_task", None)
    if original is None or getattr(original, "_cs_instrumented", False):
        return

    def _run_task(handler: Any, *args: Any, **kwargs: Any) -> Any:
        future = original(handler, *args, **kwargs)
        with contextlib.suppress(Exception):
            future.add_done_callback(lambda f: _report_task_failure(handler, f))
        return future

    _run_task._cs_instrumented = True
    with contextlib.suppress(Exception):
        page.run_task = _run_task


def _report_task_failure(handler: Any, future: Any) -> None:
    """异步任务结束时的失败上报（在事件循环线程执行）。"""
    try:
        if future.cancelled():
            return
        exc = future.exception()
    except Exception:
        return
    if exc is None:
        return
    where = getattr(handler, "__qualname__", None) or repr(handler)
    logging.getLogger("crash").error("异步任务异常 task=%s", where, exc_info=exc)
    crash_snapshot(
        "task",
        exc_info=(type(exc), exc, exc.__traceback__),
        title=f"异步任务异常 · {where}",
    )


# ---------------------------------------------------------------------------
# 现场取证
# ---------------------------------------------------------------------------


def register_context_provider(provider: Callable[[], list[str]]) -> None:
    """注册现场附加上下文（由 utils.diagnostics 注入「最近操作时间线」）。"""
    global _context_provider
    _context_provider = provider


def _unique_path(directory: str, stem: str, suffix: str) -> str:
    """同秒内重复崩溃不互相覆盖：`stem.log`、`stem-2.log`……"""
    path = os.path.join(directory, stem + suffix)
    index = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem}-{index}{suffix}")
        index += 1
    return path


def _prune_crash_files(directory: str, keep: int | None = None) -> int:
    """只保留最新的 `keep` 份现场文件（默认 `CRASH_KEEP`），返回删除数量。

    `keep` 刻意不用默认参数写死：默认参数在**函数定义时**求值，那样运行期调
    `CRASH_KEEP` 或测试里 monkeypatch 都不会生效。

    `app.log` 有 `RotatingFileHandler` 兜底，现场文件却是「防覆盖」命名的（每次崩溃
    产出新文件名、从不覆盖），不裁剪迟早堆满磁盘——**崩溃循环**（例如心跳 / 看门狗 /
    自动保存循环里周期性抛错）能在几分钟内产出上千份几 KB 的文件。

    失败一律吞掉：清理永远不该妨碍记录现场，更不能把异常带进崩溃路径。
    """
    limit = CRASH_KEEP if keep is None else keep
    try:
        paths = [p for p in Path(directory).glob(CRASH_GLOB) if p.is_file()]
    except Exception:
        return 0
    if len(paths) <= limit:
        return 0

    def sort_key(p: Path) -> tuple[float, str]:
        try:
            return (p.stat().st_mtime, p.name)
        except OSError:
            return (0.0, p.name)

    removed = 0
    for path in sorted(paths, key=sort_key)[: len(paths) - limit]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass  # 正被占用 / 权限不足：留到下次，不阻塞
    return removed


def dump_thread_stacks(main_first: bool = True) -> str:
    """抓取当前所有线程的调用栈——定位「卡住 / 未响应」的决定性证据。

    普通日志只能告诉你「卡之前发生了什么」，栈能告诉你「此刻卡在哪一行」。
    """
    names = {t.ident: t.name for t in threading.enumerate()}
    main_ident = threading.main_thread().ident
    frames = sys._current_frames()
    idents = list(frames)
    if main_first:
        idents.sort(key=lambda i: (i != main_ident, str(names.get(i, i))))

    blocks: list[str] = []
    for ident in idents:
        name = names.get(ident, f"Thread-{ident}")
        try:
            stack = "".join(traceback.format_stack(frames[ident])).rstrip()
        except Exception as exc:
            stack = f"(栈抓取失败：{exc!r})"
        blocks.append(f"--- 线程 {name} (id={ident}) ---\n{stack}")
    return "\n\n".join(blocks)


def crash_snapshot(
    reason: str,
    exc_info: Any = None,
    extra: str = "",
    timeline: list[str] | None = None,
    title: str | None = None,
) -> str | None:
    """同步写一份「崩溃 / 卡死现场」文件，返回路径（失败返回 None）。

    `reason` 是 ASCII 短标签（进文件名），`title` 是给人看的中文说明（进文件首行）。

    刻意不走异步队列：崩溃路径上队列里的东西大概率留不住，而这份现场正是最
    需要留下来的。内容自足——只看这一个文件就能还原现场。
    """
    handle = _handle
    if handle is None:
        return None

    now = datetime.now()
    stamp = now.strftime("%Y%m%d-%H%M%S")
    # 文件名只用 ASCII：中文/空格/冒号在 Windows 路径与 shell 里都是麻烦
    slug = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "-_")) else "-" for ch in reason)
    slug = "-".join(p for p in slug.split("-") if p)[:32] or "crash"
    path = _unique_path(handle.log_dir, f"crash-{stamp}-{slug}", ".log")

    lines: list[str] = [
        _RULE,
        f"现场快照 · {title or reason}",
        _SUB_RULE,
        f"时间    : {now.strftime(_DATE_FORMAT)} (会话 {handle.session})",
        f"进程    : pid={os.getpid()}",
        f"应用    : {APP_NAME} · Python {sys.version.split()[0]} · flet {_safe_version('flet')}",
        f"日志    : {handle.log_file}",
    ]
    if extra:
        lines.append(f"附加    : {extra}")

    if exc_info is not None:
        lines += [_SUB_RULE, "[异常]", "".join(traceback.format_exception(*exc_info)).rstrip()]

    if timeline:
        lines += [_SUB_RULE, f"[最近操作时间线]（{len(timeline)} 条，旧 → 新）", *timeline]
    elif _context_provider is not None:
        try:
            ctx = _context_provider()
            if ctx:
                lines += [_SUB_RULE, f"[最近操作时间线]（{len(ctx)} 条，旧 → 新）", *ctx]
        except Exception:
            pass

    lines += [_SUB_RULE, "[线程栈]（全线程）", dump_thread_stacks(), _RULE]

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        return None
    # 写成功后再裁剪：崩溃循环下最多留 CRASH_KEEP 份，不会写满磁盘
    _prune_crash_files(handle.log_dir)
    return path
