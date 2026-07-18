"""验证 Step 1-2: raw_to_visible_spans 拼接一致性 + staging_reparse 不污染原 line。"""
import sys
sys.path.insert(0, ".")

import parser
from parser import staging_reparse
from views.segment_view import raw_to_visible_spans, _split_seg_for_display
from models import BlockType, SegType

# 测试样本（含各类语法）
SAMPLE = r"""# 标题**加粗**

普通段落 *斜体* `代码` ~~删除==高亮==~~ [链接](http://x) ![图](a.png) $x^2$

- 无序**列表**
- [x] 任务
1. 有序

> 引用**加粗**

---

$$
x = y
$$

| a | b |
|---|---|
| 1 | 2 |
"""

doc = parser.parse_markdown(SAMPLE)

# C.1: raw_to_visible_spans 拼接 == line.raw
print("=== C.1: raw_to_visible_spans 拼接一致性 ===")
fail = 0
for i, line in enumerate(doc.lines):
    for cursor in [None, 0, len(line.raw) // 2, len(line.raw)]:
        spans = raw_to_visible_spans(line, 16, cursor_raw_offset=cursor,
                                     heading_level=line.level if line.block_type == "heading" else 0)
        joined = "".join(s.text for s in spans)
        if joined != line.raw:
            print(f"  FAIL line {i} (bt={line.block_type}, cursor={cursor}): {joined!r} != {line.raw!r}")
            fail += 1
print(f"  {'PASS' if fail == 0 else f'FAIL ({fail})'}: 拼接一致性")

# C.2: _split_seg_for_display 拼接 == seg.raw
print("=== C.2: _split_seg_for_display 拼接一致性 ===")
fail2 = 0
for line in doc.lines:
    for seg in line.segments:
        pieces = _split_seg_for_display(seg)
        joined = "".join(p[0] for p in pieces)
        if joined != seg.raw:
            print(f"  FAIL seg_type={seg.seg_type} raw={seg.raw!r}: {joined!r}")
            fail2 += 1
print(f"  {'PASS' if fail2 == 0 else f'FAIL ({fail2})'}: 段拆分拼接")

# C.3: staging_reparse 不污染原 line
print("=== C.3: staging_reparse 不污染原 line ===")
line = parser.parse_markdown("**bold**").lines[0]
orig_segs_id = id(line.segments)
orig_raw = line.raw
staging = staging_reparse(line, "*italic*")
ok1 = id(line.segments) == orig_segs_id
ok2 = line.raw == orig_raw
ok3 = staging.raw == "*italic*"
ok4 = id(staging.segments) != id(line.segments)
print(f"  原 line.segments id 不变: {ok1}")
print(f"  原 line.raw 不变: {ok2} ({line.raw!r})")
print(f"  staging.raw 正确: {ok3} ({staging.raw!r})")
print(f"  staging.segments 是新 list: {ok4}")
print(f"  {'PASS' if all([ok1, ok2, ok3, ok4]) else 'FAIL'}")

# C.4: staging_reparse 改变块类型（如段落 -> 标题）
print("=== C.4: staging_reparse 块类型切换 ===")
line2 = parser.parse_markdown("普通段落").lines[0]
print(f"  原: bt={line2.block_type}, raw={line2.raw!r}")
staging2 = staging_reparse(line2, "# 标题")
print(f"  staging: bt={staging2.block_type}, raw={staging2.raw!r}, segs={len(staging2.segments)}")
print(f"  原 line 不变: bt={line2.block_type}, raw={line2.raw!r}")
print(f"  {'PASS' if staging2.block_type == 'heading' and line2.block_type == 'paragraph' else 'FAIL'}")

# C.5: Parser roundtrip 不回归
print("=== C.5: Parser roundtrip ===")
serialized = parser.serialize(doc)
ok = serialized == SAMPLE
if not ok:
    for i, (a, b) in enumerate(zip(serialized.split("\n"), SAMPLE.split("\n"))):
        if a != b:
            print(f"  diff line {i}: {a!r} != {b!r}")
print(f"  {'PASS' if ok else 'FAIL'}: roundtrip")

# C.6: staging_reparse 块类型切换不污染原 line（ActiveLineView 关键依赖）
print("=== C.6: staging_reparse 块类型切换不污染 ===")
line_p = parser.parse_markdown("普通段落").lines[0]
orig_bt = line_p.block_type
orig_raw = line_p.raw
staging_h = staging_reparse(line_p, "# 标题")
ok6 = (
    staging_h.block_type == BlockType.HEADING
    and line_p.block_type == orig_bt
    and line_p.raw == orig_raw
)
print(f"  原 line 不变: bt={line_p.block_type}, raw={line_p.raw!r}")
print(f"  staging 切换: bt={staging_h.block_type}, raw={staging_h.raw!r}")
print(f"  {'PASS' if ok6 else 'FAIL'}: staging 切换块类型，原 line 不变")

# C.7: raw_to_visible_spans 对所有块类型拼接一致（含 CODE/MATH/HR/TOC 回退）
print("=== C.7: raw_to_visible_spans 全块类型拼接 ===")
fail7 = 0
for i, line in enumerate(doc.lines):
    for cursor in [None, 0, len(line.raw)]:
        spans = raw_to_visible_spans(
            line, 16,
            cursor_raw_offset=cursor,
            heading_level=line.level if line.block_type == BlockType.HEADING else 0,
        )
        joined = "".join(s.text for s in spans)
        if joined != line.raw:
            print(f"  FAIL line {i} (bt={line.block_type}, cursor={cursor}): {joined!r} != {line.raw!r}")
            fail7 += 1
print(f"  {'PASS' if fail7 == 0 else f'FAIL ({fail7})'}: 全块类型拼接")

# C.8: ActiveLineView 已移除，段级编辑基础结构可 import
print("=== C.8: 段级编辑基础结构 import ===")
try:
    from views.line_view import LineView, _active_field, _spans_for
    # ActiveLineView 应已删除
    try:
        from views.line_view import ActiveLineView  # type: ignore
        ok8 = False  # 不应可 import
        print(f"  FAIL: ActiveLineView 仍可 import（应已删除）")
    except ImportError:
        ok8 = True
        print(f"  PASS: ActiveLineView 已删除，段级组件可 import")
except Exception as e:
    print(f"  FAIL: {e}")
    ok8 = False


# 段级编辑辅助函数：editor.py 中 _reconstruct_line_raw 是闭包，此处内联复制用于测试
def _reconstruct_line_raw(line, seg_idx, seg_draft):
    before_raw = "".join(s.raw for s in line.segments[:seg_idx])
    after_raw = "".join(s.raw for s in line.segments[seg_idx + 1:])
    return before_raw + seg_draft + after_raw


# C.9: _reconstruct_line_raw 拼接一致性
# 仅对 inline 块验证（段落/标题/列表/引用/HR/TOC/BLANK）。
# CODE/MATH 块的 segments[0].raw 仅存内容不含围栏，line.raw 含 ```...```/$$...$$，
# 段级重构不适用于这两类块（它们走多行编辑路径，不参与段级编辑）。
print("=== C.9: _reconstruct_line_raw 拼接一致性 ===")
fail9 = 0
for i, line in enumerate(doc.lines):
    if not line.segments:
        continue
    if line.block_type in (BlockType.CODE, BlockType.MATH):
        continue  # 多行围栏块不参与段级重构
    for seg_idx in range(len(line.segments)):
        seg_draft = line.segments[seg_idx].raw
        recon = _reconstruct_line_raw(line, seg_idx, seg_draft)
        if recon != line.raw:
            print(f"  FAIL line {i} seg {seg_idx}: {recon!r} != {line.raw!r}")
            fail9 += 1
print(f"  {'PASS' if fail9 == 0 else f'FAIL ({fail9})'}: 段级重构拼接")

# C.10: _locate_seg_by_raw_offset 往返一致性
print("=== C.10: _locate_seg_by_raw_offset 往返 ===")
from views.editor import _locate_seg_by_raw_offset
fail10 = 0
for line in doc.lines:
    if not line.segments:
        continue
    for target in [0, len(line.raw) // 2, len(line.raw)]:
        seg_idx, seg_off = _locate_seg_by_raw_offset(line, target)
        expected_base = sum(len(line.segments[j].raw) for j in range(seg_idx))
        actual = expected_base + seg_off
        # 容差：末段尾部允许超出（_locate_seg_by_raw_offset 末段返回 seg.raw 长度）
        last_seg = len(line.segments) - 1
        if seg_idx == last_seg and seg_off == len(line.segments[last_seg].raw):
            # 末段尾部回退，target 应等于 len(line.raw)
            if target != len(line.raw) and actual != target:
                print(f"  FAIL target={target}: seg={seg_idx} off={seg_off} actual={actual}")
                fail10 += 1
        elif actual != target:
            print(f"  FAIL target={target}: seg={seg_idx} off={seg_off} actual={actual}")
            fail10 += 1
print(f"  {'PASS' if fail10 == 0 else f'FAIL ({fail10})'}: 段定位往返")

# C.11: 段级 commit 模拟（_reconstruct_line_raw + reparse_line）
print("=== C.11: 段级 commit 模拟 ===")
import copy
fail11 = 0
for line in doc.lines:
    if not line.segments or line.block_type in (
        BlockType.CODE, BlockType.MATH, BlockType.TABLE, BlockType.HR, BlockType.TOC
    ):
        continue
    for seg_idx in range(len(line.segments)):
        # 模拟段内字符追加
        seg_draft = line.segments[seg_idx].raw + "X"
        full_raw = _reconstruct_line_raw(line, seg_idx, seg_draft)
        # reparse_line 副作用：直接修改 line，需克隆
        test_line = copy.deepcopy(line)
        try:
            parser.reparse_line(test_line, full_raw)
        except Exception as e:
            print(f"  FAIL bt={line.block_type} seg={seg_idx}: reparse 抛出 {e}")
            fail11 += 1
            continue
        if "X" not in test_line.raw:
            print(f"  FAIL bt={line.block_type} seg={seg_idx}: 追加字符未保留 raw={test_line.raw!r}")
            fail11 += 1
print(f"  {'PASS' if fail11 == 0 else f'FAIL ({fail11})'}: 段级 commit 模拟")

print("\n=== 全部完成 ===")
