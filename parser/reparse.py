"""行重解析：编辑提交后用新整行源码重新解析行（就地更新）。

依赖项：
- 标准库 copy
- parser._engine（_RE_CODE_FENCE / _RE_MATH_BLOCK）
- parser.block（_build_line）
- models（BlockType / Line / SegType / Segment）

对外接口：
- reparse_line(line, new_raw=None)：行重解析（编辑提交后调用）
- reparse_line_atomic(line, new_raw)：原子化重解析（仅 1 次 observable 通知，高频路径）
- segment_raw(segments)：段列表拼回行源码
- staging_reparse(line, new_raw)：返回新 Line（不修改原行，Typora 式实时渲染）
- line_to_raw(line)：行源码

设计要点：
- reparse_line_atomic 用 object.__setattr__ 绕过 Observable 逐字段 _notify，
  最后 line.notify() 触发唯一一次通知。专用于高频输入路径（handle_char_input /
  backspace_core / delete_core / handle_paste / indent_or_outdent）。
- staging_reparse 浅拷贝 + 重新赋值 segments，原 line.segments 不受影响。
"""

import copy

from models import BlockType, Line, SegType, Segment

from parser._engine import _RE_CODE_FENCE, _RE_MATH_BLOCK
from parser.block import _build_line


def _split_code_block(raw: str) -> tuple[str, str]:
    """从代码块 raw 中提取 (lang, body)。

    raw 形如 ```lang\\n...\\n```。围栏首行单独匹配，避免多行内容
    导致 `$` 锚点失效（曾引发"双重围栏"bug）。
    """
    first_line = raw.split("\n", 1)[0] if "\n" in raw else raw
    m = _RE_CODE_FENCE.match(first_line)
    if not m:
        return "", raw

    fence = m.group(2)
    lang = m.group(3)
    body = raw.split("\n", 1)[1] if "\n" in raw else ""
    # 去掉末行围栏
    tail = "\n" + fence[0] * len(fence)
    if body.endswith(tail):
        body = body[: -len(tail)]
    return lang, body


def reparse_line(line: Line, new_raw: str | None = None) -> None:
    """用新的整行源码重新解析该行（就地更新 block_type/level/segments）。

    保留代码块 / HR / MATH / TABLE 的特殊结构（整行为单位编辑，不拆段）。
    """
    if new_raw is not None:
        line.raw = new_raw
    raw = line.raw

    if line.block_type == BlockType.CODE:
        lang, body = _split_code_block(raw)
        line.lang = lang
        line.segments = [Segment(SegType.CODE, body, body)]
        return

    if line.block_type == BlockType.HR:
        line.segments = [Segment(SegType.TEXT, raw, raw)]
        return

    if line.block_type == BlockType.MATH:
        m = _RE_MATH_BLOCK.match(raw)
        content = m.group(1).strip() if m else raw
        line.segments = [Segment(SegType.MATH, content, content)]
        return

    if line.block_type == BlockType.TABLE:
        line.segments = [Segment(SegType.TEXT, raw, raw)]
        return

    # 普通块：完整重建
    rebuilt = _build_line(raw)
    line.block_type = rebuilt.block_type
    line.level = rebuilt.level
    line.lang = ""
    line.task = rebuilt.task
    line.checked = rebuilt.checked
    line.segments = rebuilt.segments


def reparse_line_atomic(line: Line, new_raw: str) -> None:
    """原子化重解析：所有字段批量更新，仅触发一次 observable 通知。

    用 object.__setattr__ 绕过 Observable.__setattr__ 的逐字段 _notify，
    最后 line.notify() 触发唯一一次通用通知。替代 reparse_line(line, new_raw)
    在高频编辑路径（handle_char_input / backspace_core / delete_core /
    handle_paste / indent_or_outdent）中的调用，将 2-7 次通知合并为 1 次。

    实现要点：
    - object.__setattr__ 仅更新 __dict__ 不触发 _notify（已验证 Flet Observable 源码）
    - segments 设为纯 list（不经 _wrap_if_collection 包裹为 ObservableList）：
      项目代码从不 append/extend/remove line.segments（仅替换引用），安全
    - line.notify() 是 Observable 的公共方法，触发 _notify(None) 通用通知

    与 reparse_line 的区别：后者保留用于低频路径（toggle_task / change_lang /
    撤销重做），通知次数不敏感。本函数专用于高频输入路径。
    """
    # 静默更新 raw（不触发通知）
    object.__setattr__(line, "raw", new_raw)
    raw = new_raw

    if line.block_type == BlockType.CODE:
        lang, body = _split_code_block(raw)
        object.__setattr__(line, "lang", lang)
        object.__setattr__(line, "segments", [Segment(SegType.CODE, body, body)])
        line.notify()
        return

    if line.block_type == BlockType.HR:
        object.__setattr__(line, "segments", [Segment(SegType.TEXT, raw, raw)])
        line.notify()
        return

    if line.block_type == BlockType.MATH:
        m = _RE_MATH_BLOCK.match(raw)
        content = m.group(1).strip() if m else raw
        object.__setattr__(line, "segments", [Segment(SegType.MATH, content, content)])
        line.notify()
        return

    if line.block_type == BlockType.TABLE:
        object.__setattr__(line, "segments", [Segment(SegType.TEXT, raw, raw)])
        line.notify()
        return

    # 普通块：完整重建（6 字段静默更新 + 1 次 notify）
    rebuilt = _build_line(raw)
    object.__setattr__(line, "block_type", rebuilt.block_type)
    object.__setattr__(line, "level", rebuilt.level)
    object.__setattr__(line, "lang", "")
    object.__setattr__(line, "task", rebuilt.task)
    object.__setattr__(line, "checked", rebuilt.checked)
    object.__setattr__(line, "segments", rebuilt.segments)
    line.notify()


def segment_raw(segments: list[Segment]) -> str:
    """由段列表拼回行源码。"""
    return "".join(s.raw for s in segments)


def staging_reparse(line: Line, new_raw: str) -> Line:
    """返回一个新的 Line（不修改原 line），用于 ActiveLineView 实时渲染。

    Typora 式 WYSIWYG：每次 on_change_draft 触发本地 staging reparse，
    避免 @ft.observable 的 document.line 被频繁修改导致整文档重渲染。
    提交时（blur/跨行/块操作）才由 editor.commit_active 写回 document.line。

    安全性：reparse_line 对 line.segments 是赋值新 list（line.segments = ...），
    不会修改原 line.segments 中的 Segment 对象。浅拷贝（copy.copy）足够隔离。
    """
    staging = copy.copy(line)        # 浅拷贝：line.segments 引用仍指向原 list
    staging.segments = []            # reparse_line 会赋新 list，原 line.segments 不受影响
    reparse_line(staging, new_raw)
    return staging


def line_to_raw(line: Line) -> str:
    """行的源码（直接取 raw，保证序列化稳定）。"""
    return line.raw
