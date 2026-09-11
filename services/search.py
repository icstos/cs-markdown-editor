"""搜索与替换领域服务：查询正则、行级匹配、跨文件搜索、反向引用替换。

从 views/sidebar.py 迁出：搜索面板的 UI 与匹配/替换算法原先混在同一文件，
且 views/doc_search.py 需反向导入侧边栏私有函数。算法层独立后可被文档内
搜索浮层、侧边栏搜索与替换面板共用，且不依赖任何视图模块。
"""

import os
import re

from models.document import Document

MAX_RESULTS = 200  # 当前文档搜索结果上限，防止超长文档卡顿
# 跨文件搜索性能保护
MAX_CROSS_FILES = 500  # 最多扫描文件数
MAX_PER_FILE = 50  # 每文件结果上限
MAX_CROSS_TOTAL = 1000  # 跨文件总结果上限
MAX_FILE_SIZE = 1_000_000  # 跳过 >1MB 的文件（getsize 先判断，不读盘）
MAX_LINE_LEN = 2000  # 超长行只取首匹配（防 minified 文件卡 finditer）
PREVIEW_RADIUS = 30  # 预览窗口：匹配位前后字符数


def build_query_regex(
    query: str,
    case_sensitive: bool,
    whole_word: bool,
    regex: bool,
) -> re.Pattern | None:
    """从查询词 + 4 选项构造编译后的正则。

    4 种模式由 re.escape + \\b + re.IGNORECASE 组合表达，单一代码路径避免分支：
    - regex=False：re.escape 转义字面量（"a.b" 不被当模式）
    - regex=True：query 即模式
    - whole_word=True：用 \\b...\\b 包裹
    - case_sensitive=False：加 re.IGNORECASE

    无效正则返回 None（调用方提示"正则表达式无效"）。
    """
    q = query.strip()
    if not q:
        return None
    pattern_str = q if regex else re.escape(q)
    if whole_word:
        # 用 (?:...) 非捕获组包裹，确保 \b 边界作用于整个模式
        # 否则 \bcat|dog\b 会被解析为 (\bcat) | (dog\b)，中间分支无边界保护
        pattern_str = rf"\b(?:{pattern_str})\b"
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern_str, flags)
    except re.error:
        return None


def match_lines(
    document: Document,
    pattern: re.Pattern | None,
    limit: int = MAX_RESULTS,
) -> list[tuple[int, list[tuple[int, int]]]]:
    """行级正则匹配，返回 [(line_idx, [(start, end), ...]), ...]。

    每行返回所有匹配区间（VSCode 风格：行内多匹配均高亮）；超长行（> MAX_LINE_LEN）
    只取首匹配，防 minified 文件卡 finditer。pattern 为 None 时返回 []。
    """
    if document is None or pattern is None:
        return []
    results: list[tuple[int, list[tuple[int, int]]]] = []
    for i, line in enumerate(document.lines):
        raw = line.raw or ""
        if len(raw) > MAX_LINE_LEN:
            m = pattern.search(raw)
            if m:
                results.append((i, [(m.start(), m.end())]))
        else:
            matches = [(m.start(), m.end()) for m in pattern.finditer(raw)]
            if matches:
                results.append((i, matches))
        if len(results) >= limit:
            break
    return results


def search_in_file(
    path: str,
    pattern: re.Pattern,
    max_per_file: int = MAX_PER_FILE,
) -> list[tuple[int, list[tuple[int, int]]]]:
    """读取单文件并按行匹配。读取失败 / 超大文件返回 []。

    不解析成 Document：跨文件只需 line_idx + raw 做匹配，text.split("\\n") 已足够；
    parser.parse_markdown 会构建完整段树，对搜索场景是不必要开销。
    line_idx 按 \\n 切分索引，与编辑器打开后 document.lines[i].raw 一致。
    """
    try:
        if os.path.getsize(path) > MAX_FILE_SIZE:
            return []
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    results: list[tuple[int, list[tuple[int, int]]]] = []
    for i, raw in enumerate(text.split("\n")):
        if len(raw) > MAX_LINE_LEN:
            m = pattern.search(raw)
            if m:
                results.append((i, [(m.start(), m.end())]))
        else:
            matches = [(m.start(), m.end()) for m in pattern.finditer(raw)]
            if matches:
                results.append((i, matches))
        if len(results) >= max_per_file:
            break
    return results


def flatten_matches(
    search_results: list[tuple[int, list[tuple[int, int]]]],
) -> list[tuple[int, int, int]]:
    """将当前文档搜索结果扁平化为 [(li, s, e), ...]。

    供"替换当前"按索引取匹配、导航上下翻、计数显示用。
    """
    flat: list[tuple[int, int, int]] = []
    for li, matches in search_results:
        for s, e in matches:
            flat.append((li, s, e))
    return flat


def flatten_cross_matches(
    cross_results: list[tuple[str, str, list[tuple[int, list[tuple[int, int]]]]]],
) -> list[tuple[str, int, int, int]]:
    """将跨文件搜索结果扁平化为 [(path, li, s, e), ...]。"""
    flat: list[tuple[str, int, int, int]] = []
    for path, _name, hits in cross_results:
        for li, matches in hits:
            for s, e in matches:
                flat.append((path, li, s, e))
    return flat


def convert_vscode_backrefs(replace_text: str) -> str:
    r"""将 VSCode 风格 $N 反向引用转换为 Python \g<N> 语法。

    match.expand() 仅识别 \\1 / \\g<1>（Python re 语法），VSCode/Typora 用户
    习惯 $1 / $2 写法。此处做一次性转换，两种语法并存：
    - $$ → 字面量 $
    - $N（N 为 1 位或多位数字）→ \\g<N>
    - $ 后非数字 → 字面量 $（如 $abc 保持不变）
    - \\1 / \\g<1> 等 Python 语法原样保留，match.expand() 原生处理
    """
    # $$ → 临时占位符（避免被 $N 规则误匹配）
    result = replace_text.replace("$$", "\x00")
    # $N → \g<N>
    result = re.sub(r"\$(\d+)", r"\\g<\1>", result)
    # 恢复字面量 $
    return result.replace("\x00", "$")


def expand_replacement(
    match: re.Match,
    replace_text: str,
    regex_mode: bool,
) -> str:
    r"""展开替换文本中的反向引用。

    regex 模式：同时支持 VSCode 风格 $1/$2 与 Python 风格 \1/\g<1>。
    先把 $N 转为 \g<N>，再交 match.expand() 统一展开。
    非 regex 模式：$ 和 \ 为字面量，直接返回 replace_text 不做展开。
    """
    if regex_mode:
        try:
            return match.expand(convert_vscode_backrefs(replace_text))
        except (re.error, ValueError):
            return replace_text
    # 非 regex：$ 和 \ 无特殊含义，直接返回字面量
    return replace_text


def find_match_at(
    pattern: re.Pattern,
    raw: str,
    start: int,
    end: int,
) -> re.Match | None:
    """在 raw 中查找起始/结束位置与 (start, end) 匹配的 re.Match 对象。

    供 expand_replacement 需要完整 Match（含捕获组）时使用。
    """
    for m in pattern.finditer(raw):
        if m.start() == start and m.end() == end:
            return m
        if m.start() > start:
            break
    return None


def replace_in_string(
    raw: str,
    pattern: re.Pattern,
    spans: list[tuple[int, int]],
    replace_text: str,
    regex_mode: bool,
) -> tuple[str, int]:
    """行内替换所有匹配区间，右→左处理保偏移。返回 (new_raw, count)。

    regex 模式用 pattern.finditer 重建 Match 做反向引用展开；
    非 regex 模式直接用 replace_text 字面量替换。
    """
    if not spans:
        return raw, 0
    # regex 模式：预建 (start,end)→Match 映射，供 expand 使用
    match_map: dict[tuple[int, int], re.Match] = {}
    if regex_mode:
        for m in pattern.finditer(raw):
            match_map[(m.start(), m.end())] = m

    new_raw = raw
    count = 0
    # 右→左：左侧替换不破坏右侧偏移
    for s, e in sorted(spans, key=lambda t: t[0], reverse=True):
        if s < 0 or e > len(new_raw) or s > e:
            continue
        if regex_mode and (s, e) in match_map:
            replacement = expand_replacement(match_map[(s, e)], replace_text, True)
        else:
            replacement = replace_text
        new_raw = new_raw[:s] + replacement + new_raw[e:]
        count += 1
    return new_raw, count


def replace_in_file_text(
    text: str,
    pattern: re.Pattern,
    replace_text: str,
    regex_mode: bool,
) -> tuple[str, int]:
    """跨文件单文件文本替换：按 \\n 切行逐行替换。返回 (new_text, count)。

    与 search_in_file 的行切分方式一致（text.split("\\n")），保证 line_idx 对齐。
    """
    lines = text.split("\n")
    total = 0
    for i, raw in enumerate(lines):
        spans = [(m.start(), m.end()) for m in pattern.finditer(raw)]
        if spans:
            new_raw, count = replace_in_string(raw, pattern, spans, replace_text, regex_mode)
            lines[i] = new_raw
            total += count
    return "\n".join(lines), total
