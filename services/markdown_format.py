"""Markdown 文档格式化器（纯函数，无项目内依赖）。

对外接口：
- format_markdown(text: str) -> str：格式化整篇 Markdown 文本

格式化规则（Shift+Alt+F 触发）：
1. 清理行尾多余空格；文件末尾统一保留一个换行
2. 行内代码统一用反引号包裹，反引号与内容间无多余空格
   （内容含反引号时自动升级为多反引号包裹）
3. 任务列表 `- [ ]` / `- [x]` 语法与空格规范统一（含引用行内的任务列表）
4. 引用：统一 `>` 后加空格；多层嵌套引用的 `>` 合并为连续前缀、
   数量与层级对应（`> > text` → `>> text`）
5. 中英文混排加空格：中文与英文、数字、行内代码之间统一加半角空格

实现要点：
- 逐行处理；围栏块（``` 代码块 / 文档首行 --- frontmatter）内部原样保留
  （代码缩进与行尾空格有意义），围栏线本身仅做行尾清理
- 行内代码先提取为占位符再处理中英文空格，还原后代码内容不被改动
- 纯文本/行级规则，不引入完整 AST 解析，保证对任意文本健壮
"""

import re

__all__ = ["format_markdown"]

# CJK 基本区 + 扩展 A（常用汉字覆盖）
_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"

# 行内代码：反引号围栏 + 内容（不跨行，防贪婪）
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(.+?)(?<!`)\1(?!`)")

# 引用前缀：缩进 + 连续/带空格的 > 序列
_QUOTE_RE = re.compile(r"^(\s*)((?:>[ \t]*)+)(.*)$")

# 任务列表：可选引用前缀 + 列表标记 + [ ]/[x]/[X] + 剩余
_TASK_RE = re.compile(
    r"^(\s*(?:>[ \t]*)?[-*+][ \t]*)\[([ xX]?)\](.*)$"
)


def _normalize_inline_code(line: str) -> str:
    """统一行内代码：反引号紧贴内容；内容含反引号时升级分隔符。"""

def _leading_backticks(s: str) -> int:
    """字符串开头的连续反引号数。"""
    return len(s) - len(s.lstrip("`"))


def _trailing_backticks(s: str) -> int:
    """字符串末尾的连续反引号数。"""
    return len(s) - len(s.rstrip("`"))


def _normalize_inline_code(line: str) -> str:
    """统一行内代码：反引号紧贴内容；内容首/尾含反引号时升级分隔符并保留空格。

    内容首尾含反引号时，围栏与内容间必须保留空格才能被解析器合法包裹
    （如 `` `code` ``，内容为 `code`）；若紧贴，内容首尾反引号会与围栏
    粘连成更长的连续反引号串，解析时内容被吞。普通内容则收紧为紧贴形态。
    """

    def repl(m: re.Match) -> str:
        fence = m.group(1)
        content = m.group(2).strip()
        if not content:
            return fence
        if content[0] == "`" or content[-1] == "`":
            need = max(_leading_backticks(content), _trailing_backticks(content))
            fence = "`" * (need + 1)
            return f"{fence} {content} {fence}"
        return f"{fence}{content}{fence}"

    return _INLINE_CODE_RE.sub(repl, line)


def _normalize_quote(line: str) -> str:
    """引用规范化：合并 `> >` 为连续前缀，`>` 与内容间统一一个空格。"""
    m = _QUOTE_RE.match(line)
    if not m:
        return line
    indent = m.group(1)
    depth = m.group(2).count(">")
    rest = m.group(3).lstrip()
    prefix = ">" * depth
    if not rest:
        return f"{indent}{prefix}"
    return f"{indent}{prefix} {rest}"


def _normalize_task(line: str) -> str:
    """任务列表规范化：`[x]`/`[X]` → `[x]`，空 → `[ ]`，`]` 后统一一个空格。"""
    m = _TASK_RE.match(line)
    if not m:
        return line
    prefix = m.group(1)
    mark = "x" if m.group(2).strip().lower() == "x" else " "
    rest = m.group(3).lstrip()
    if not rest:
        return f"{prefix}[{mark}]"
    return f"{prefix}[{mark}] {rest}"


def _extract_inline_code(line: str) -> tuple[str, list[str]]:
    """提取行内代码为占位符，返回 (保护后文本, 代码列表)。"""
    spans: list[str] = []

    def keep(m: re.Match) -> str:
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    return _INLINE_CODE_RE.sub(keep, line), spans


def _add_cjk_spacing(line: str) -> str:
    """中英文混排加空格：中文与英文/数字/行内代码之间插半角空格。

    行内代码先占位保护：代码内容不被改动，代码两侧（与中文相邻时）
    正确插入空格。
    """
    protected, spans = _extract_inline_code(line)
    # 先处理代码占位符边界（\x00 视为“代码”）
    out = re.sub(rf"(?<=[{_CJK}])(?=\x00)", " ", protected)
    out = re.sub(rf"(?<=\x00)(?=[{_CJK}])", " ", out)
    # 中文 ↔ 字母/数字 边界
    out = re.sub(rf"(?<=[{_CJK}])(?=[A-Za-z0-9])", " ", out)
    out = re.sub(rf"(?<=[A-Za-z0-9])(?=[{_CJK}])", " ", out)
    for i, span in enumerate(spans):
        out = out.replace(f"\x00{i}\x00", span)
    return out


def _format_content_line(line: str) -> str:
    """正文行（非围栏内）格式化：引用 → 任务列表 → 行内代码 → 中英文空格。"""
    line = _normalize_quote(line)
    line = _normalize_task(line)
    line = _normalize_inline_code(line)
    line = _add_cjk_spacing(line)
    return line


def _fence_flags(lines: list[str]) -> list[bool]:
    """返回每行是否位于围栏块内部（True=内部内容行；围栏线本身为 False）。"""
    flags = [False] * len(lines)
    in_fence = False
    marker: str | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not in_fence:
            if stripped.startswith("```"):
                in_fence, marker = True, "```"
            elif i == 0 and stripped == "---":
                in_fence, marker = True, "---"
            flags[i] = False
        else:
            if (marker == "```" and stripped.startswith("```")) or (
                marker == "---" and stripped == "---"
            ):
                in_fence = False
            flags[i] = in_fence
    return flags


def format_markdown(text: str) -> str:
    """格式化整篇 Markdown 文本，返回规范化结果。"""
    lines = text.split("\n")
    fence = _fence_flags(lines)
    for i, line in enumerate(lines):
        if fence[i]:
            continue  # 围栏内内容行：原样保留（代码缩进/行尾空格有意义）
        lines[i] = _format_content_line(line).rstrip()
    # 规则 1：末尾统一保留一个换行（清掉全部末尾空行）
    while lines and not lines[-1].strip():
        lines.pop()
    result = "\n".join(lines)
    return result + "\n" if result else ""
