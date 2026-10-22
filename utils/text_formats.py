"""文本文件格式识别：扩展名 → 语法高亮语言、是否可在编辑器中打开。

分层定位：utils 叶子层，只依赖标准库（不 import models / parser / services / views）。

职责边界（两个问题，只回答这两个）：
1. 这个路径能不能当「文本」打开？——  ``is_supported_text_path``
2. 打开后用哪个语言标识做语法高亮？——  ``lang_for_path``

为什么不在这里判「是不是 Markdown」之外的类型：编辑器把非 Markdown 的文本
一律当作**一个代码块**来渲染（见 `parser.plain.parse_plain_text`），所以这里
只需要给出"语言标识"；空串表示纯文本（不做分词）。

扩展名表与 `views/code_block.py` 的 `_COMMON_LANGS` 保持同一套标识（Pygments
的 lexer 别名）。表里没有常用展示名的语言（如 `cmake`）也能正常高亮，代码块
头部的语言选择器会把当前值追加成末项，不会显示成空白。
"""

from __future__ import annotations

import os

# Markdown：编辑器的一等文档类型（行结构 + 所见即所得），不走代码块渲染
MARKDOWN_EXTS: tuple[str, ...] = (".md", ".markdown")

# 扩展名 → 语言标识（""=纯文本，不做分词）。
# 只收录"肉眼可读的纯文本"，二进制容器（.docx/.pdf/.png…）一律不收录 —— 它们
# 既不能被 UTF-8 之外的编码安全解码，也不该出现在编辑器里。
LANG_BY_EXT: dict[str, str] = {
    # ---- 纯文本 / 数据 ----
    ".txt": "",
    ".text": "",
    ".log": "",
    ".out": "",
    ".csv": "",
    ".tsv": "",
    ".env": "",
    ".gitignore": "",
    ".gitattributes": "",
    ".editorconfig": "ini",
    # ---- 脚本 / 编程语言 ----
    ".py": "python",
    ".pyw": "python",
    ".pyi": "python",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".ino": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".mts": "typescript",
    ".tsx": "tsx",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".scala": "scala",
    ".dart": "dart",
    ".jl": "julia",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".clj": "clojure",
    ".coffee": "coffeescript",
    ".f": "fortran",
    ".f90": "fortran",
    ".asm": "nasm",
    ".s": "nasm",
    ".groovy": "groovy",
    ".gradle": "groovy",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".tf": "terraform",
    ".vim": "vim",
    ".tex": "latex",
    ".rst": "rst",
    # ---- Shell ----
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".ksh": "bash",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".bat": "batch",
    ".cmd": "batch",
    # ---- 标记 / 配置 / 数据 ----
    ".htm": "html",
    ".html": "html",
    ".xhtml": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".xml": "xml",
    ".xsd": "xml",
    ".svg": "xml",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".properties": "ini",
    ".sql": "sql",
    ".cmake": "cmake",
    ".diff": "diff",
    ".patch": "diff",
}

# 无扩展名 / 固定文件名的语言（资源管理器直觉：Makefile、Dockerfile 不该显示成纯文本）
LANG_BY_NAME: dict[str, str] = {
    "makefile": "makefile",
    "gnumakefile": "makefile",
    "dockerfile": "dockerfile",
    "cmakelists.txt": "cmake",
    "gemfile": "ruby",
    "rakefile": "ruby",
    "vagrantfile": "ruby",
    "jenkinsfile": "groovy",
    "procfile": "",
    "license": "",
    "readme": "",
    "changelog": "",
}


def ext_of(path: str | None) -> str:
    """路径的小写扩展名（含点）；无扩展名 / 空路径返回空串。"""
    if not path:
        return ""
    return os.path.splitext(path)[1].lower()


def is_markdown_path(path: str | None) -> bool:
    """是否 Markdown 文档（唯一走行结构编辑的类型）。"""
    return ext_of(path) in MARKDOWN_EXTS


def lang_for_path(path: str | None) -> str | None:
    """路径对应的语法高亮语言标识。

    返回 ``None`` 表示**不是可当文本打开的类型**（调用方应拒绝或交系统程序）；
    返回空串表示纯文本（可打开、不做分词）。这两种含义必须区分开：``.txt`` 是
    合法的文本文件，只是没有语法。
    """
    if not path:
        return None
    if is_markdown_path(path):
        return "markdown"
    name = os.path.basename(path).lower()
    if name in LANG_BY_NAME:
        return LANG_BY_NAME[name]
    return LANG_BY_EXT.get(ext_of(path))


def is_supported_text_path(path: str | None) -> bool:
    """是否可在编辑器中打开（Markdown 或任一登记的纯文本类型）。"""
    return lang_for_path(path) is not None


def is_plain_text_path(path: str | None) -> bool:
    """是否「按代码块渲染」的纯文本文件（支持但不是 Markdown）。"""
    lang = lang_for_path(path)
    return lang is not None and not is_markdown_path(path)


def language_label(lang: str) -> str:
    """语言标识 → 头部展示名（供提示文案用；空标识即「纯文本」）。

    与 `views.code_block._lang_display` 同源语义，但这里只用于状态栏/提示，
    不参与代码块渲染（视图层有自己的一份展示名表）。
    """
    return {
        "cpp": "C++",
        "csharp": "C#",
        "jsx": "JSX",
        "tsx": "TSX",
    }.get(lang, lang.capitalize() if lang else "纯文本")
