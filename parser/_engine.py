"""parser 包基础设施：mistune 解析器实例（惰性）+ 块级正则 + 行内包裹器表。

依赖项：
- 标准库 re、functools
- mistune（行内 AST / HTML 解析，延迟到首次解析时才 import）
- models（SegType）

对外接口：
- _get_md() / _get_html_md()：惰性构造的 mistune 实例（lru_cache 单例）
- _RE_* 正则常量：块级前缀识别
- _INLINE_WRAPPERS：行内 AST 节点 → (SegType, 包裹器) 映射
- _INLINE_PLUGINS：mistune 行内插件名列表

设计要点：
- mistune 实例与模块本身均延迟加载：import parser 不触发 `import mistune`，
  首次 _get_md() / _get_html_md() 调用时才 import mistune + 构造实例。
  to_html 仅导出时用 → 启动期完全不加载 mistune 的 footnotes/task_lists 插件链。
- 本模块是 parser 包依赖图的叶子（仅依赖 models + 标准库），
  不导入包内任何子模块，避免循环。
"""

import functools
import re

from models import SegType

# 行内解析插件：删除线 / 高亮 / 上下标（支持组合语法 ***加粗斜体*** 等）
_INLINE_PLUGINS = ["strikethrough", "mark", "superscript", "subscript"]


@functools.lru_cache(maxsize=1)
def _get_md():
    """行内 AST 解析器（惰性单例）。

    首次调用时才 import mistune 并构造实例。用于 parse_inline。
    """
    import mistune

    return mistune.create_markdown(renderer="ast", plugins=_INLINE_PLUGINS + ["table"])


@functools.lru_cache(maxsize=1)
def _get_html_md():
    """HTML 渲染解析器（惰性单例）。

    首次调用时才 import mistune 并构造实例。仅 to_html（导出）时调用，
    启动期不加载 footnotes/task_lists 插件链。
    """
    import mistune

    return mistune.create_markdown(
        renderer="html",
        plugins=_INLINE_PLUGINS + ["table", "footnotes", "task_lists"],
    )


# ---- 正则：块级前缀识别 ----
_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_RE_UO_LIST = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_RE_O_LIST = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_RE_QUOTE = re.compile(r"^>\s?(.*)$")
_RE_HR = re.compile(r"^(\s*)([-*_])\2\2+\s*$")  # --- ** ___ 等
_RE_CODE_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$")
_RE_TASK = re.compile(r"^(\s*)([-*+])\s+\[( |x|X)\]\s+(.*)$")
_RE_MATH_BLOCK = re.compile(r"^\$\$(.+?)\$\$\s*$", re.DOTALL)
_RE_MATH_FENCE = re.compile(r"^\$\$\s*$")  # 块级公式围栏：$$ 独占一行开/闭
_RE_INLINE_MATH = re.compile(r"\$([^$\n]+?)\$")
_RE_TOC = re.compile(r"^\[toc\]\s*$", re.IGNORECASE)


# 行内 AST 节点类型 -> (SegType, 包裹器) 映射；codespan/link/image 单独处理
# 这些包裹器可任意嵌套组合（如 ***加粗斜体*** = emphasis→strong→text）
_INLINE_WRAPPERS: dict[str, tuple[SegType, str]] = {
    "strong": (SegType.STRONG, "**"),
    "emphasis": (SegType.EMPHASIS, "*"),
    "strikethrough": (SegType.STRIKE, "~~"),
    "mark": (SegType.HIGHLIGHT, "=="),
    "superscript": (SegType.SUPERSCRIPT, "^"),
    "subscript": (SegType.SUBSCRIPT, "~"),
}
