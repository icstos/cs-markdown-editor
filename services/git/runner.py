"""Git 子进程运行器：唯一的「与 git 对话」出口。

职责边界：
- 只负责拼参数、起进程、收输出、判定成败、超时熔断；**不做任何业务解析**
  （解析在 porcelain / difftext）。
- 失败一律抛 ``services.git.errors`` 里的具体异常（分类见 classify_error），
  因此上层永远不需要看 returncode。

关键工程决策（桌面 GUI 场景下踩过坑的点）：
- ``GIT_TERMINAL_PROMPT=0`` + ``GIT_ASKPASS=echo`` + ``GCM_INTERACTIVE=never``：
  禁止 git 弹出交互式凭据输入。GUI 应用没有终端，一旦 git 等待输入就会
  永久挂起（表现为「界面卡住」），宁可让它立刻失败并提示用户去终端认证。
- Windows 下 ``CREATE_NO_WINDOW``：否则每次调用都会闪出一个控制台黑框。
- 强制 UTF-8 + ``errors="replace"``：git 在中文 Windows 上默认按 GBK 输出，
  混用会抛 UnicodeDecodeError 或产生乱码路径。
- ``-c core.quotepath=false``：让非 ASCII 路径按原样输出（而不是八进制转义），
  否则中文文件名会显示成 ``\346\226\207\344\273\266``。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from services.git.errors import (
    GitError,
    GitNotFoundError,
    GitTimeoutError,
    classify_error,
)

log = logging.getLogger(__name__)

__all__ = ["GitResult", "GitRunner", "find_git_executable", "reset_git_cache"]

#: 默认超时（秒）：本地命令应当毫秒级完成，5 秒内没结束说明磁盘/仓库异常
DEFAULT_TIMEOUT = 20.0
#: 网络命令超时（秒）：fetch / pull / push 需要容忍慢链路
NETWORK_TIMEOUT = 180.0

#: 始终前置的 git 配置覆盖（保证跨平台输出稳定可解析）
_BASE_ARGS: tuple[str, ...] = ("-c", "core.quotepath=false", "-c", "color.ui=false")

_GIT_CACHE: str | None = None
_GIT_CACHE_DONE = False


def find_git_executable() -> str | None:
    """定位 git 可执行文件；找不到返回 None。

    结果在进程内缓存（首次调用做一次 PATH 扫描）。测试可通过
    ``reset_git_cache()`` 清空，或经环境变量 ``CS_MD_GIT_PATH`` 指定。
    """
    global _GIT_CACHE, _GIT_CACHE_DONE
    if _GIT_CACHE_DONE:
        return _GIT_CACHE
    override = os.environ.get("CS_MD_GIT_PATH")
    if override and os.path.isfile(override):
        _GIT_CACHE = override
    else:
        _GIT_CACHE = shutil.which("git")
        if _GIT_CACHE is None and sys.platform == "win32":
            # GUI 进程的 PATH 可能不含 Git 安装目录（从资源管理器启动时偶发），
            # 兜底探测常见安装位置。
            for cand in (
                r"C:\Program Files\Git\cmd\git.exe",
                r"C:\Program Files (x86)\Git\cmd\git.exe",
                os.path.expanduser(r"~\AppData\Local\Programs\Git\cmd\git.exe"),
            ):
                if os.path.isfile(cand):
                    _GIT_CACHE = cand
                    break
    _GIT_CACHE_DONE = True
    return _GIT_CACHE


def reset_git_cache() -> None:
    """清空可执行文件探测缓存（测试用）。"""
    global _GIT_CACHE, _GIT_CACHE_DONE
    _GIT_CACHE = None
    _GIT_CACHE_DONE = False


@dataclass(slots=True)
class GitResult:
    """一次 git 调用的结果。"""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        """合并输出（诊断用）。"""
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()

    @property
    def command_text(self) -> str:
        return "git " + " ".join(self.args)


class GitRunner:
    """在固定工作目录下执行 git 命令。

    实例是**无状态可复用**的：同一仓库的所有操作共用一个 runner 即可。
    """

    def __init__(
        self,
        cwd: str | None = None,
        *,
        git_exe: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.cwd = cwd
        self._git_exe = git_exe
        self.timeout = timeout

    # ---- 可执行文件 ----
    @property
    def git_exe(self) -> str:
        exe = self._git_exe or find_git_executable()
        if not exe:
            raise GitNotFoundError(
                "未检测到 Git",
                detail="在 PATH 中找不到 git 可执行文件",
                command="git",
            )
        return exe

    def available(self) -> bool:
        """git 是否可用（不抛异常，供 UI 决定是否展示面板）。"""
        try:
            _ = self.git_exe  # 触发探测：未安装 Git 时抛 GitNotFoundError
        except GitNotFoundError:
            return False
        return True

    def version(self) -> str:
        """返回 git 版本字符串（失败返回空串）。"""
        try:
            res = self.run(["--version"], check=False, timeout=5.0)
        except GitError:
            return ""
        return res.stdout.strip()

    # ---- 执行 ----
    def run(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        cwd: str | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> GitResult:
        """同步执行 git 命令。

        Args:
            args: 不含 "git" 本身的参数序列。
            check: 失败时是否抛异常（False 时返回结果由调用方判定）。
            cwd: 覆盖工作目录。
            timeout: 覆盖超时（秒）。
            input_text: 传给 stdin 的内容（如 ``git commit -F -``）。

        Raises:
            GitNotFoundError: git 不可用。
            GitTimeoutError: 超时。
            GitError: 其余失败（按 stderr 分类）。
        """
        exe = self.git_exe
        argv = [exe, *_BASE_ARGS, *args]
        work_dir = cwd or self.cwd or os.getcwd()
        env = self._env()
        effective_timeout = self.timeout if timeout is None else timeout
        log.debug("git 调用: %s (cwd=%s)", " ".join(args), work_dir)
        try:
            proc = subprocess.run(
                argv,
                cwd=work_dir,
                env=env,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
                creationflags=_creation_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitTimeoutError(
                "Git 操作超时",
                detail=f"命令在 {effective_timeout:.0f} 秒内未结束：{' '.join(args)}",
                command="git " + " ".join(args),
            ) from exc
        except FileNotFoundError as exc:
            raise GitNotFoundError(
                "未检测到 Git",
                detail=str(exc),
                command="git " + " ".join(args),
            ) from exc
        except OSError as exc:
            raise GitError(
                "无法启动 Git 进程",
                detail=str(exc),
                command="git " + " ".join(args),
            ) from exc

        result = GitResult(
            args=tuple(args),
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
        if check and not result.ok:
            raise classify_error(
                result.stderr or result.stdout,
                result.returncode,
                command=("git", *args),
            )
        return result

    async def run_async(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        cwd: str | None = None,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> GitResult:
        """在线程池中执行（不阻塞 UI 事件循环）。

        项目内的 UI 交互一律走本方法：git 是同步阻塞 IO，直接调用会让
        界面卡死（尤其 fetch/push）。
        """
        return await asyncio.to_thread(
            self.run,
            args,
            check=check,
            cwd=cwd,
            timeout=timeout,
            input_text=input_text,
        )

    # ---- 内部 ----
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        # 禁止任何交互式输入：GUI 没有终端，等待输入等于永久挂起
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"
        env["GCM_INTERACTIVE"] = "never"
        env.setdefault("GIT_PAGER", "cat")
        env["LC_ALL"] = "C.UTF-8"
        env["LANG"] = "C.UTF-8"
        return env


def _creation_flags() -> int:
    """Windows 下隐藏子进程控制台窗口；其他平台返回 0。"""
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0
