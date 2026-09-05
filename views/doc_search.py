"""文档内搜索匹配计算（浮层搜索专用，纯函数无 UI）。

复用 views.sidebar 的行级正则匹配（_build_query_regex / _match_lines）：
- 匹配基于每行 raw 文本（与侧边栏当前文档搜索同口径），返回 (li, s, e) raw 区间；
- 编辑器渲染侧按 raw→flat 映射把区间落到可见文本做装饰高亮（装饰层，不改文档）。

对外接口：
- compute_doc_matches(document, query, case_sensitive, regex) -> list[(li, s, e)]
  按行号 → 行内起点的字典序排列；正则非法/空查询返回 []。
"""

from models import Document
from views.sidebar import _build_query_regex, _match_lines

# 单次渲染可接受的高亮区间上限（超长文档保护；超限截断并在 UI 层展示总数截断）
_MAX_DOC_MATCHES = 10000


def compute_doc_matches(
    document: Document | None,
    query: str,
    case_sensitive: bool,
    regex: bool,
    limit: int = _MAX_DOC_MATCHES,
) -> list[tuple[int, int, int]]:
    """计算当前文档的匹配列表 [(li, s, e), ...]（li / s 升序）。

    - 空查询 / document 为空 / 正则非法（_build_query_regex 返回 None）→ []
    - 超过 limit 时截断（保前 limit 条，行号升序）
    """
    if document is None or not query.strip():
        return []
    pattern = _build_query_regex(query, case_sensitive, False, regex)
    if pattern is None:
        return []
    results = _match_lines(document, pattern)
    flat: list[tuple[int, int, int]] = []
    for li, matches in results:
        for s, e in matches:
            flat.append((li, s, e))
            if len(flat) >= limit:
                return flat
    return flat


def group_matches_by_line(
    matches: list[tuple[int, int, int]],
) -> dict[int, list[tuple[int, int]]]:
    """把扁平匹配列表分组为 {li: [(s, e), ...]}（渲染侧按行查色）。"""
    grouped: dict[int, list[tuple[int, int]]] = {}
    for li, s, e in matches:
        grouped.setdefault(li, []).append((s, e))
    return grouped


def clamp_index(idx: int, total: int) -> int:
    """把当前索引归一到 [0, total)；total==0 时返回 -1（无匹配）。"""
    if total <= 0:
        return -1
    return idx % total
