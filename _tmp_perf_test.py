"""测量 Enter 换行关键路径耗时：serialize / parse_markdown / build_line_controls 构造。"""
import time
import parser
from models import Document, Line, BlockType, Segment, SegType


def make_doc(n: int) -> Document:
    lines = []
    for i in range(n):
        raw = f"这是第 {i} 行的文本内容，含一些字。"
        line = Line(block_type=BlockType.PARAGRAPH, raw=raw,
                    segments=[Segment(SegType.TEXT, raw, raw)])
        lines.append(line)
    return Document(lines=lines)


for n in (100, 500, 2000, 5000):
    doc = make_doc(n)
    # serialize
    t0 = time.perf_counter()
    for _ in range(10):
        s = parser.serialize(doc)
    t_ser = (time.perf_counter() - t0) / 10 * 1000
    # parse_markdown 单行（on_submit 创建新行）
    t0 = time.perf_counter()
    for _ in range(100):
        nl = parser.parse_markdown("续行内容").lines[0]
    t_parse = (time.perf_counter() - t0) / 100 * 1000
    # parse_markdown 全文（restore 路径）
    t0 = time.perf_counter()
    for _ in range(3):
        parser.parse_markdown(s)
    t_parse_full = (time.perf_counter() - t0) / 3 * 1000
    print(f"N={n:5d}: serialize={t_ser:6.2f}ms  parse_single={t_parse:.3f}ms  parse_full={t_parse_full:7.2f}ms  ser_len={len(s)}")
