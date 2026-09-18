"""代码块语法高亮（纯逻辑，服务层）。

职责单一：把一段代码文本切成「逻辑行 → [(文本, 语义类别)]」，供视图层按当前
主题把语义类别映射成颜色。

为什么返回语义类别而不是颜色：配色属于主题（styles.py），分词属于服务层。
亮/暗两套主题各自提供一份「类别 → 颜色」映射，同一份分词结果可复用。

分词器用 Pygments（惰性导入：首次出现代码块才付出导入成本，与项目既有的
"重依赖首次使用时导入"策略一致）。以下情况一律回退纯文本（只失去上色，
内容与行数完全不变）：
- 语言为空 / plaintext / 未知（Pygments 无对应词法）
- Pygments 未安装
- 超长代码（避免病态输入让分词成为卡顿源）

**不变式**：`highlight_lines(code, lang)` 的行数恒等于 `code.split("\\n")` 的行数，
且每行拼接结果恒等于原文对应行。行号、光标行坐标、边界跳出都依赖这一条。
"""

from __future__ import annotations

# 语义类别（视图层据此取色；未收录的类别回退代码块正文色）
DEFAULT = ""
KEYWORD = "kw"
STRING = "str"
COMMENT = "com"
NUMBER = "num"
FUNCTION = "fn"
TYPE = "type"
VARIABLE = "var"
OPERATOR = "op"
ERROR = "err"

# 单次分词的最大代码长度：超过则只做纯文本渲染（防御病态输入）
_MAX_LEX_CHARS = 200_000
# 分词结果缓存容量（键为 (lang, code)，FIFO 淘汰）
_CACHE_LIMIT = 48

# 语言名 → 视为纯文本（无需分词）
_PLAIN_LANGS = frozenset({"", "text", "txt", "plain", "plaintext", "none"})

_cache: dict[tuple[str, str], tuple[tuple[tuple[str, str], ...], ...]] = {}
_pygments = None
_lexer_cache: dict[str, object] = {}


def _pygments_module():
    """惰性导入 Pygments 顶层包；不可用时返回 None（回退纯文本）。"""
    global _pygments
    if _pygments is None:
        try:
            import pygments as _mod
        except Exception:  # pragma: no cover - 环境缺依赖时回退
            _pygments = False
        else:
            _pygments = _mod
    return _pygments or None


def _get_lexer(lang: str):
    """取得语言对应的词法分析器；未知语言返回 None。"""
    key = lang.strip().lower()
    if key in _lexer_cache:
        return _lexer_cache[key]
    lexer = None
    mod = _pygments_module()
    if mod is not None:
        try:
            from pygments.lexers import get_lexer_by_name

            # stripnl/ensurenl=False：不得增删首尾换行，否则行数与原文不符
            lexer = get_lexer_by_name(key, stripnl=False, ensurenl=False)
        except Exception:
            lexer = None
    _lexer_cache[key] = lexer
    return lexer


def _kind_of(token_type) -> str:
    """Pygments token 类型 → 语义类别（就近归类，未知归默认色）。"""
    mod = _pygments_module()
    if mod is None:  # pragma: no cover - 调用前已保证
        return DEFAULT
    from pygments.token import Token

    if token_type in Token.Error:
        return ERROR
    if token_type in Token.Comment:
        return COMMENT
    if token_type in Token.Keyword:
        return KEYWORD
    if token_type in Token.Literal.String:
        return STRING
    if token_type in Token.Literal.Number:
        return NUMBER
    if token_type in Token.Name.Function or token_type in Token.Name.Builtin:
        return FUNCTION
    if (
        token_type in Token.Name.Class
        or token_type in Token.Name.Namespace
        or token_type in Token.Name.Decorator
    ):
        return TYPE
    if token_type in Token.Name.Attribute or token_type in Token.Name.Constant:
        return VARIABLE
    if token_type in Token.Operator or token_type in Token.Punctuation:
        return OPERATOR
    return DEFAULT


def _append(line: list[list], text: str, kind: str) -> None:
    """向行内追加片段，与上一个片段同色时合并（减少 TextSpan 数量）。"""
    if not text:
        return
    if line and line[-1][1] == kind:
        line[-1][0] += text
    else:
        line.append([text, kind])


def _lex_lines(code: str, lang: str) -> tuple[tuple[tuple[str, str], ...], ...] | None:
    """分词成逻辑行；语言不支持 / 分词失败返回 None（调用方回退纯文本）。"""
    lexer = _get_lexer(lang)
    mod = _pygments_module()
    if lexer is None or mod is None:
        return None
    try:
        tokens = list(mod.lex(code, lexer))
    except Exception:
        return None
    lines: list[list] = [[]]
    for token_type, text in tokens:
        if not text:
            continue
        kind = _kind_of(token_type)
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if i:
                lines.append([])
            _append(lines[-1], part, kind)
    return tuple(tuple((t, k) for t, k in line) for line in lines)


def _plain_lines(code: str) -> tuple[tuple[tuple[str, str], ...], ...]:
    """纯文本回退：每行一个默认色片段（空行给空元组）。"""
    return tuple(((line, DEFAULT),) if line else () for line in code.split("\n"))


def highlight_lines(code: str, lang: str) -> tuple[tuple[tuple[str, str], ...], ...]:
    """把代码切成逻辑行 → ((文本, 语义类别) ...)。

    返回值的行数恒等于 ``code.split("\\n")`` 的行数（见模块文档不变式）。
    结果按 (语言, 代码) 缓存；文档反复重渲染时不会重复分词。
    """
    if not code:
        return ((),)
    key = (lang or "", code)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    result = None
    if (lang or "").strip().lower() not in _PLAIN_LANGS and len(code) <= _MAX_LEX_CHARS:
        result = _lex_lines(code, lang)

    expected = code.count("\n") + 1
    if result is None or len(result) != expected:
        # 分词行数与原文不符（极少数字法会吞换行）→ 宁可不上色也不破坏行坐标
        result = _plain_lines(code)

    if len(_cache) >= _CACHE_LIMIT:
        # FIFO 淘汰（dict 保序）：代码块内容多变但同一屏内稳定，无需 LRU 精度
        _cache.pop(next(iter(_cache)))
    _cache[key] = result
    return result


def clear_highlight_cache() -> None:
    """清空分词缓存（测试用；正常渲染无需调用）。"""
    _cache.clear()
    _lexer_cache.clear()
