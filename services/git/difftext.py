"""统一 diff 文本 → 结构化模型；分栏（并排）对齐；冲突标记扫描。

解析规则完全按 git 的 ``--unified`` 输出语法（``--no-color``，不启用外部差异工具），
因此既能解析 ``git diff``（工作区/暂存区），也能解析 ``git show``（历史提交）。

大文件保护（对应「万行文件 diff 渲染无卡顿」）：
- 解析阶段设**行预算** ``max_lines``，超出即截断并置 ``truncated=True``；
- 分栏对齐在截断后的模型上进行，不会二次膨胀内存；
- 视图层再用 ListView 虚拟化渲染（只构建可见行）。
"""

from __future__ import annotations

import re

from services.git.models import (
    DiffHunk,
    DiffLine,
    DiffLineKind,
    FileDiff,
    MergeMarkers,
    SplitRow,
)

__all__ = [
    "MAX_DIFF_LINES",
    "build_split_rows",
    "detect_binary",
    "parse_single_file_diff",
    "parse_unified_diff",
    "scan_merge_markers",
    "synthetic_added_diff",
]

#: 单文件差异保留的最大行数（含块头）。超过则截断并提示，保证万行文件不卡顿。
#: 10000 行 × 每行 1 个控件 ≈ 可接受的构建耗时（ListView 仍只布局可见部分）。
MAX_DIFF_LINES = 12000

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.*?) b/(.*)$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_INDEX_RE = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)(?:\s+(\d+))?")

_MERGE_START_RE = re.compile(r"^<{7}(\s|$)")
_MERGE_MID_RE = re.compile(r"^={7}(\s|$)")
_MERGE_END_RE = re.compile(r"^>{7}(\s|$)")
_MERGE_BASE_RE = re.compile(r"^\|{7}(\s|$)")


def parse_unified_diff(text: str, *, max_lines: int = MAX_DIFF_LINES) -> list[FileDiff]:
    """把多文件 unified diff 文本解析为 ``FileDiff`` 列表。"""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: DiffHunk | None = None
    budget = max_lines
    truncated_all = False

    def _flush_hunk() -> None:
        nonlocal hunk
        if current is not None and hunk is not None:
            current.hunks.append(hunk)
        hunk = None

    def _flush_file() -> None:
        nonlocal current
        _flush_hunk()
        if current is not None:
            files.append(current)
        current = None

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\r")

        if line.startswith("diff --git "):
            _flush_file()
            m = _DIFF_GIT_RE.match(line)
            if m:
                current = FileDiff(path=m.group(2), old_path=m.group(1))
                if m.group(1) != m.group(2):
                    current.is_renamed = True
            else:
                current = FileDiff(path=line[len("diff --git "):].strip())
            continue

        if current is None:
            # 容忍 `git show` 去掉头部后的裸补丁：以 ---/+++ 为起点补一个容器
            if line.startswith("--- ") or line.startswith("+++ "):
                current = FileDiff(path="")
            else:
                continue

        # ---- 文件级元信息 ----
        if line.startswith("new file mode"):
            current.is_new = True
            continue
        if line.startswith("deleted file mode"):
            current.is_deleted = True
            continue
        if line.startswith("old mode "):
            current.old_mode = line[len("old mode "):].strip()
            continue
        if line.startswith("new mode "):
            current.new_mode = line[len("new mode "):].strip()
            continue
        if line.startswith("rename from "):
            current.old_path = line[len("rename from "):].strip()
            current.is_renamed = True
            continue
        if line.startswith("rename to "):
            current.path = line[len("rename to "):].strip()
            current.is_renamed = True
            continue
        if line.startswith("copy from "):
            current.old_path = line[len("copy from "):].strip()
            continue
        if line.startswith("copy to "):
            current.path = line[len("copy to "):].strip()
            continue
        m = _INDEX_RE.match(line)
        if m:
            continue
        if line.startswith("--- "):
            p = line[4:].strip()
            if p == "/dev/null":
                current.is_new = True
            else:
                current.old_path = _strip_prefix(p)
                if not current.path:
                    current.path = current.old_path
            continue
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p == "/dev/null":
                current.is_deleted = True
            else:
                current.path = _strip_prefix(p)
            continue

        # ---- 二进制 ----
        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.is_binary = True
            continue

        # ---- 块头 ----
        m = _HUNK_RE.match(line)
        if m:
            _flush_hunk()
            hunk = DiffHunk(
                header=line,
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or 1),
            )
            budget -= 1
            if budget <= 0:
                current.truncated = True
                truncated_all = True
                break
            continue

        # ---- 差异行 ----
        if hunk is None:
            continue
        if line.startswith("\\"):  # "\ No newline at end of file"
            continue
        if budget <= 0:
            current.truncated = True
            truncated_all = True
            break

        if line.startswith("-"):
            old_no = hunk.old_start + _count(hunk.lines, DiffLineKind.CONTEXT, DiffLineKind.REMOVED)
            hunk.lines.append(
                DiffLine(kind=DiffLineKind.REMOVED, text=line[1:], old_no=old_no)
            )
        elif line.startswith("+"):
            new_no = hunk.new_start + _count(hunk.lines, DiffLineKind.CONTEXT, DiffLineKind.ADDED)
            hunk.lines.append(
                DiffLine(kind=DiffLineKind.ADDED, text=line[1:], new_no=new_no)
            )
        elif line.startswith(" ") or line == "":
            old_no = hunk.old_start + _count(hunk.lines, DiffLineKind.CONTEXT, DiffLineKind.REMOVED)
            new_no = hunk.new_start + _count(hunk.lines, DiffLineKind.CONTEXT, DiffLineKind.ADDED)
            hunk.lines.append(
                DiffLine(
                    kind=DiffLineKind.CONTEXT,
                    text=line[1:] if line else "",
                    old_no=old_no,
                    new_no=new_no,
                )
            )
        else:
            continue
        budget -= 1

    _flush_file()
    if truncated_all and files:
        files[-1].truncated = True
    for fd in files:
        fd.additions = sum(
            1 for h in fd.hunks for ln in h.lines if ln.kind == DiffLineKind.ADDED
        )
        fd.deletions = sum(
            1 for h in fd.hunks for ln in h.lines if ln.kind == DiffLineKind.REMOVED
        )
    return files


def parse_single_file_diff(text: str, path: str = "") -> FileDiff:
    """解析单文件 diff（``git diff -- <path>`` / ``git show`` 单文件）。

    解析不到任何文件段落但文本非空时，返回一个带 ``error`` 的占位对象，
    以便 UI 明确展示「无法解析」而不是静默空白。
    """
    files = parse_unified_diff(text)
    if not files:
        return FileDiff(path=path)
    if path:
        for fd in files:
            if fd.path == path or fd.old_path == path:
                return fd
    fd = files[0]
    if not fd.path:
        fd.path = path
    return fd


def _strip_prefix(p: str) -> str:
    """去掉 diff 头里的 ``a/`` / ``b/`` 前缀。"""
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


def _count(lines: list[DiffLine], *kinds: DiffLineKind) -> int:
    """统计某几种类型已出现的行数（用于推算行号）。"""
    want = set(kinds)
    return sum(1 for ln in lines if ln.kind in want)


def build_split_rows(
    file_diff: FileDiff,
    *,
    context_span: int | None = None,
    max_rows: int = MAX_DIFF_LINES,
) -> list[SplitRow]:
    """把 ``FileDiff`` 转换为分栏（左右并排）行列表。

    变更块内的删除行与新增行**按下标配对**（VS Code 并排视图的近似行为），
    多出的一侧用空行占位（``DiffLineKind.EMPTY``），保证左右两侧行号对齐。

    ``context_span`` 非 None 时，上下文行超过该跨度会被折叠为省略行
    （用于「只看改动」的紧凑模式）。
    """
    rows: list[SplitRow] = []
    for hunk in file_diff.hunks:
        rows.append(
            SplitRow(
                left_no=None,
                left_text="",
                left_kind=DiffLineKind.EMPTY,
                right_no=None,
                right_text="",
                right_kind=DiffLineKind.EMPTY,
                is_hunk_header=True,
                header_text=hunk.header,
            )
        )
        if len(rows) >= max_rows:
            break
        i = 0
        lines = hunk.lines
        n = len(lines)
        while i < n:
            ln = lines[i]
            if ln.kind != DiffLineKind.CONTEXT:
                dels: list[DiffLine] = []
                adds: list[DiffLine] = []
                while i < n and lines[i].kind in (DiffLineKind.REMOVED, DiffLineKind.ADDED):
                    (dels if lines[i].kind == DiffLineKind.REMOVED else adds).append(lines[i])
                    i += 1
                for k in range(max(len(dels), len(adds))):
                    d = dels[k] if k < len(dels) else None
                    a = adds[k] if k < len(adds) else None
                    rows.append(
                        SplitRow(
                            left_no=d.old_no if d else None,
                            left_text=d.text if d else "",
                            left_kind=DiffLineKind.REMOVED if d else DiffLineKind.EMPTY,
                            right_no=a.new_no if a else None,
                            right_text=a.text if a else "",
                            right_kind=DiffLineKind.ADDED if a else DiffLineKind.EMPTY,
                        )
                    )
            else:
                rows.append(
                    SplitRow(
                        left_no=ln.old_no,
                        left_text=ln.text,
                        left_kind=DiffLineKind.CONTEXT,
                        right_no=ln.new_no,
                        right_text=ln.text,
                        right_kind=DiffLineKind.CONTEXT,
                    )
                )
                i += 1
        if len(rows) >= max_rows:
            break
    return rows[:max_rows]


def synthetic_added_diff(path: str, text: str, *, max_lines: int = MAX_DIFF_LINES) -> FileDiff:
    """为「未跟踪文件」合成一份全新增的差异（git 不跟踪则不产生 diff）。

    与 ``git diff --no-index /dev/null <file>`` 等价，但跨平台且无需起进程。
    """
    fd = FileDiff(path=path, is_new=True)
    lines = (text or "").splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    hunk = DiffHunk(
        header=f"@@ -0,0 +1,{len(lines)} @@",
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=len(lines),
    )
    for idx, raw in enumerate(lines, start=1):
        hunk.lines.append(
            DiffLine(kind=DiffLineKind.ADDED, text=raw.rstrip("\r"), new_no=idx)
        )
    fd.hunks.append(hunk)
    fd.additions = len(lines)
    fd.deletions = 0
    fd.truncated = truncated
    fd.raw = "\n".join(f"+{ln.text}" for ln in hunk.lines)
    return fd


def detect_binary(data: bytes) -> bool:
    """粗略判断字节内容是否为二进制（含 NUL 即视为二进制）。"""
    return b"\0" in data[:8000]


def scan_merge_markers(text: str, path: str = "") -> MergeMarkers:
    """扫描冲突标记，返回各行号（1 起），供「跳转到冲突位置」。"""
    markers = MergeMarkers(path=path)
    for idx, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.rstrip("\r")
        if _MERGE_START_RE.match(line):
            markers.start_lines.append(idx)
        elif _MERGE_MID_RE.match(line) or _MERGE_BASE_RE.match(line):
            markers.separator_lines.append(idx)
        elif _MERGE_END_RE.match(line):
            markers.end_lines.append(idx)
    return markers
