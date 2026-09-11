"""目录（大纲）派生：从 Document 提取标题条目。

纯函数、无 UI 依赖：编辑器与各类大纲面板共用同一口径的标题派生。
"""

from models.document import BlockType, Document, SegType


def compute_toc(document: Document) -> list[tuple[int, int, str]]:
    """复用 editor.toc_entries 的派生逻辑：返回 [(line_idx, level, text), ...]。"""
    if document is None:
        return []
    result: list[tuple[int, int, str]] = []
    for i, line in enumerate(document.lines):
        if line.block_type != BlockType.HEADING:
            continue
        text = "".join(
            s.text for s in line.segments if s.seg_type != SegType.HEADING_PREFIX
        ).strip()
        if text:
            result.append((i, line.level, text))
    return result
