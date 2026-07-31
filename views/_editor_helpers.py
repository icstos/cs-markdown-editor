"""编辑器纯计算助手：从 MarkdownEditor 闭包剥离的无状态函数。

依赖项：
- models（Line / BlockType 类型注解）
- utils.segment_helpers（is_fence / line_raw：行级判定与源码）
- views.pixel_layout（hit_test_line_x_raw：X 像素 → raw 偏移命中）

对外接口（均为内部助手，下划线前缀）：
- _snap_indent_up / _snap_indent_down：列表缩进 / 降级对齐到单位倍数
- _shift_cursor_off：前缀长度变化后平移光标，保留内容内相对位置
- _vline_off_at_x：视觉行上 X 像素命中 raw 偏移
- _table_cells：表格行 raw 拆分为单元格文本列表
- _rebuild_list_prefix：按缩进级别重建列表前缀（无序/有序/任务）
- _char_kind / _select_word_bounds：VSCode 风格词边界分类与扩展
- _build_highlight_map：向外选区高亮映射预计算
- _step_left / _step_right：raw 偏移步进（跨行跳过围栏块）
- _build_offset_prefix：行高前缀和数组构建
- make_snapshot：编辑器快照构造（撤销/重做栈元素）
- compute_delete_result：删除选区决策（新行列表 + 合并行 raw）

设计要点：
- 这些函数原本定义在 MarkdownEditor 组件闭包内（每次重渲染重新创建）或模块级，
  统一迁出到本模块后：①不再随渲染重建（输入响应微优化）；②可独立单元测试；
  ③为阶段 5 控制器封装提供无状态复用基础。
- 纯度判定：函数体内不读 cursor_li / cursor_off / document / *_ref.current / set_*，
  只通过参数进出。读取闭包状态的函数（如 _estimate_line_height / _get_cursor_row_col）
  不在此处，仍留在闭包内。
"""

import re

from core.history import EditorSnapshot
from models import BlockType, Line
from utils.segment_helpers import is_fence, line_raw
from views.pixel_layout import hit_test_line_x_raw

# 列表 / 任务标记正则：模块级预编译（与 editor.py 共享同一份定义）
_RE_TASK_MARKER = re.compile(r"^([-*+])\s+\[[ xX]\]\s+")  # 任务项前缀 - [ ] / - [x]
_RE_UO_MARKER = re.compile(r"^([-*+])\s+")  # 无序列表标记 - / * / +
_RE_O_MARKER = re.compile(r"^(\d+)\.\s+")  # 有序列表标记 N.


def _snap_indent_up(level: int, unit: int, limit: int) -> int:
    """缩进：向 unit 的倍数上取一级，钳制到 limit。"""
    return min(((level // unit) + 1) * unit, limit)


def _snap_indent_down(level: int, unit: int) -> int:
    """降级：向 unit 的倍数下取一级（不含当前），最小 0。"""
    if level <= 0:
        return 0
    return max(((level - 1) // unit) * unit, 0)


def _shift_cursor_off(cur_off: int, old_prefix_len: int, new_prefix_len: int, new_raw_len: int) -> int:
    """缩进 / 降级后光标位置：保留内容内相对偏移；光标在前缀内则落到内容首。

    仅前缀长度变化时同步平移光标，使光标在文字中的位置保持不变（自然丝滑）。
    """
    if cur_off >= old_prefix_len:
        off = cur_off + (new_prefix_len - old_prefix_len)
    else:
        off = new_prefix_len
    return max(0, min(off, new_raw_len))


def _fix_ime_doubling(value: str, last_value: str) -> str:
    """修正 Windows IME composing/commit text 完美翻倍（value = X + X）。

    Windows 五笔/拼音等输入法在特定 TextField 配置下，composing 或 commit text
    会被完美翻倍（value = X + X，如 "wqwq"、"你你"）。按 ASCII/非 ASCII 分策略：

    - ASCII（composing 编码，如 "wqwq"）：阈值 >= 4 才折叠。避免误伤 "ww"、"//"
      等快速连击——len=2 双叠更可能是用户连击而非 IME 翻倍，折叠会丢字
      （last_value 已含首字符，折叠后 value==last_value 触发 ignore 吞掉第二个）。
      len>=4 时再用 last_value 区分合法连续输入：连续输入相同字符时 value 逐步
      累积（每次 +1 字符），last_value 是 value 去掉末字符，满足
      len(value)==len(last_value)+1 且 value.startswith(last_value)；IME 翻倍时
      value=X+X，len(value)=2*len(X)，不满足该条件（如 "wqwq" len=4 != len("wq")+1=3）。

    - 非 ASCII（commit 上屏中文/日文等，如 "你你"）：阈值 >= 2，但用 last_value
      区分 IME 翻倍 vs 合法连续输入：
      - last_value != X → IME 翻倍（新会话单次上屏 / composing 转上屏翻倍）→ 折叠
      - last_value == X → 合法连续（上一次已上屏 X，本次再上屏 X）→ 不折叠

    既修复单次上屏 "你你" 翻倍，又不误伤 "好好" / "aaaa" 合法连续输入。
    """
    if len(value) < 2 or len(value) % 2 != 0:
        return value
    half = len(value) // 2
    if value[:half] != value[half:]:
        return value
    x = value[:half]
    if all(ord(c) < 128 for c in value):
        # ASCII composing 翻倍：阈值 >= 4
        if len(value) >= 4:
            # 合法连续输入（value = last_value + 1 字符）不折叠：
            # 连续输入相同字符时 value 逐步累积，last_value 是 value 去掉末字符，
            # 满足 len(value)==len(last_value)+1 且 value.startswith(last_value)。
            # IME 翻倍时 value=X+X，len(value)=2*len(X)，不满足该条件。
            if last_value and len(value) == len(last_value) + 1 and value.startswith(last_value):
                return value
            return x
        return value
    # 非 ASCII commit 翻倍：last_value 区分合法连续
    return x if last_value != x else value


def _detect_ime_compose(value: str, last_value: str) -> bool:
    """检测 IME 组合完成（composing ASCII 后缀被上屏非 ASCII 字符替换）。

    通用检测：value 与 last_value 共享已上屏前缀 P；last_value 的后缀 C 是
    composing ASCII（如 "vb"），value 的后缀 X 是上屏非 ASCII（如 "好"）。
    命中时 handle_char_input 走 replace 分支，把 [start_off, start_off+len(last_value)]
    区域整体替换为 value。

    背景：旧条件 `all(ord<128 for c in last_value)` 仅捕获首字上屏（last_value 为
    纯 ASCII composing，如 "wq"→"你"）。连续上屏第二字起 last_value 已含已上屏
    中文（如 "你vb"），条件失败 → 误走 append 把 "你好" 追加到 "你vb" 后，产生
    "你vb你好"（五笔输入 "你好啊" 错写成 "你vb你好kb你好啊" 的根因）。

    示例：
      ("你", "wq")        → True  （首字上屏：composing "wq" → "你"）
      ("你好", "你vb")    → True  （第二字上屏：composing "vb" → "好"）
      ("你好啊", "你好kb")→ True  （第三字上屏：composing "kb" → "啊"）
      ("你v", "你")       → False （composing 中：composing 后缀为空，属 append）
      ("好好", "好")      → False （合法连续：composing 后缀为空，属 append）
      ("abcd", "abc")     → False （ASCII 连续输入：committed 全 ASCII，属 append）
    """
    if not last_value:
        return False
    # 最长公共前缀长度（已上屏稳定前缀 P）
    cp = 0
    while cp < len(value) and cp < len(last_value) and value[cp] == last_value[cp]:
        cp += 1
    composing = last_value[cp:]  # 待替换的 composing ASCII 后缀
    committed = value[cp:]  # 上屏字符（非 ASCII）
    return bool(
        composing
        and all(ord(c) < 128 for c in composing)
        and any(ord(c) > 127 for c in committed)
    )


def _compute_composing_trim(value: str, last_value: str) -> str | None:
    """计算回车时未上屏 IME composing 的裁剪结果（on_submit 专用）。

    中文输入法 composing 期间（未上屏）按回车，IME 放弃 composing，TextField
    on_submit 的 value 仅含已上屏部分（value 是 last_value 的真前缀）。文档行
    区域 [start_off, start_off+len(last_value)] 当前为 last_value（含 composing
    英文），handle_char_input 的 ignore 分支（last_value.startswith(value)）不会
    回写移除，导致 composing 英文残留。本函数返回裁剪后的已上屏文本，供 on_submit
    替换文档区域移除废字符。

    返回值：
      - value 是 last_value 的真前缀（composing 取消）→ 返回 value（已上屏文本，
        可能为空串，表 composing 全部放弃）
      - value == last_value（无 composing / 直接输入 / composing 已上屏）→ None
      - value 非前缀（异常形态）→ None（保守不裁剪，避免误伤）

    与 handle_char_input ignore 分支的同形条件区别：ignore 在 on_change 期间触发
    （composing 可能仍活跃，保留文档）；本函数仅在 on_submit（回车）触发，此时
    IME 已放弃 composing，value 反映已上屏部分，裁剪是安全的。

    示例：
      ("你", "你vb")    → "你"  （composing "vb" 放弃，保留已上屏 "你"）
      ("", "vb")        → ""    （composing 全部放弃，无已上屏文本）
      ("你vb", "你vb")  → None  （无 composing，直接输入）
      ("abc", "abc")    → None  （ASCII 直接输入，不裁剪）
      ("你好", "你vb")  → None  （value 非前缀，保守不裁剪）
    """
    if not last_value or value == last_value:
        return None
    return value if last_value.startswith(value) else None


def _vline_off_at_x(visual_lines, vline_idx: int, x: float) -> int | None:
    """在指定视觉行上用 X 像素命中 raw 偏移（vline.start_raw + local_off）。"""
    if vline_idx < 0 or vline_idx >= len(visual_lines):
        return None
    vline = visual_lines[vline_idx]
    local_off = hit_test_line_x_raw(vline.offsets_x, x)
    return vline.start_raw + local_off


def _table_cells(line: Line) -> list[str]:
    """表格行 raw 拆分为单元格文本列表（去首尾 | 与空白后按 | 切分）。"""
    return [cell.strip() for cell in line.raw.strip().strip("|").split("|")]


# ============ 列表前缀重建 ============
def _rebuild_list_prefix(
    level: int, body: str, block_type: BlockType, task: bool, checked: bool,
    restart_num: bool,
) -> str:
    """按缩进级别重建列表前缀。

    restart_num=True 时有序列表序号重置为 1（嵌套子列表自然重新计数）。
    任务列表保留原标记符号与勾选状态。
    body 为去掉前导缩进的前缀原文（含标记符号），用于提取原 marker / 序号。
    """
    indent_sp = " " * level
    if task:
        marker = m.group(1) if (m := _RE_UO_MARKER.match(body)) else "-"
        return f"{indent_sp}{marker} [{'x' if checked else ' '}] "
    if block_type == BlockType.LIST_O:
        if restart_num:
            num = "1"
        else:
            num = m.group(1) if (m := _RE_O_MARKER.match(body)) else "1"
        return f"{indent_sp}{num}. "
    marker = m.group(1) if (m := _RE_UO_MARKER.match(body)) else "-"
    return f"{indent_sp}{marker} "


# ============ VSCode 风格词边界 ============
def _char_kind(ch: str) -> str:
    """字符类别分类（VSCode 风格词边界）。

    - space：空白字符
    - cjk：CJK 统一表意文字 + 日韩文（连续视为一词）
    - word：字母数字 + 下划线
    - punct：标点 / Markdown 语法字符等
    """
    if ch.isspace():
        return "space"
    cp = ord(ch)
    if (
        0x4E00 <= cp <= 0x9FFF  # CJK 统一表意
        or 0x3040 <= cp <= 0x30FF  # 平假名 + 片假名
        or 0xAC00 <= cp <= 0xD7AF  # 韩文音节
        or 0x3400 <= cp <= 0x4DBF  # CJK 扩展 A
        or 0xF900 <= cp <= 0xFAFF  # CJK 兼容表意
    ):
        return "cjk"
    if ch.isalnum() or ch == "_":
        return "word"
    return "punct"


def _select_word_bounds(raw: str, off: int) -> tuple[int, int] | None:
    """从 raw[off] 向左右扩展到同类词边界，返回 (start, end) 或 None。

    VSCode 风格：同类别连续区间。CJK 与 word 互相合并（中英混排词）。
    空白字符不选（改为选最近非空字符）；整行全空白返回 None。
    off 会被钳制到 [0, len(raw)]，off == len(raw) 时按 punct 处理（与原闭包一致）。
    """
    n = len(raw)
    if n == 0:
        return None
    off = max(0, min(off, n))
    kind = _char_kind(raw[off]) if off < n else "punct"
    if kind == "space":
        # 向左找首个非空
        left = off
        while left > 0 and raw[left - 1].isspace():
            left -= 1
        if left > 0 and not raw[left - 1].isspace():
            left -= 1
            kind = _char_kind(raw[left])
            off = left
        else:
            # 向右找首个非空
            right = off
            while right < n and raw[right].isspace():
                right += 1
            if right < n:
                off = right
                kind = _char_kind(raw[off])
            else:
                return None  # 整行全空白
    # 同类向左扩展
    start = off
    if kind in ("cjk", "word"):
        while start > 0 and _char_kind(raw[start - 1]) in ("cjk", "word"):
            start -= 1
    else:  # punct
        while start > 0 and _char_kind(raw[start - 1]) == "punct":
            start -= 1
    # 同类向右扩展
    end = off + 1
    if kind in ("cjk", "word"):
        while end < n and _char_kind(raw[end]) in ("cjk", "word"):
            end += 1
    else:  # punct
        while end < n and _char_kind(raw[end]) == "punct":
            end += 1
    return (start, end)


# ============ 向外选区高亮映射 ============
def _build_highlight_map(
    lines: list[Line], outward_sel: tuple[int, int, int, int] | None,
) -> dict[int, tuple[int, int]]:
    """预计算向外选区高亮映射 {li: (start_off, end_off)}。

    选区边界自动归一化（a <= b）。围栏行不参与（调用方保证 outward_sel 不跨围栏）。
    返回稳定 dict，供 LineView 的 @ft.memo 命中（身份比较稳定，避免逐行重算）。
    """
    if outward_sel is None:
        return {}
    a_li, a_off, b_li, b_off = outward_sel
    if (a_li, a_off) > (b_li, b_off):
        a_li, a_off, b_li, b_off = b_li, b_off, a_li, a_off
    n = len(lines)
    lo = max(a_li, 0)
    hi = min(b_li, n - 1)
    result: dict[int, tuple[int, int]] = {}
    for li in range(lo, hi + 1):
        line_raw_len = len(line_raw(lines[li]))
        if li == a_li and li == b_li:
            result[li] = (a_off, b_off)
        elif li == a_li:
            result[li] = (a_off, line_raw_len)
        elif li == b_li:
            result[li] = (0, b_off)
        else:
            result[li] = (0, line_raw_len)
    return result


# ============ raw 偏移步进 ============
def _step_left(lines: list[Line], li: int, off: int) -> tuple[int, int] | None:
    """向左步进一个 raw 偏移：行内左移，行首则跳上一行尾（跳过围栏块）。

    返回 None 表示已到文档起点。
    """
    if off > 0:
        return (li, off - 1)
    if li <= 0:
        return None
    prev = lines[li - 1]
    if is_fence(prev):
        return None
    return (li - 1, len(line_raw(prev)))


def _step_right(lines: list[Line], li: int, off: int) -> tuple[int, int] | None:
    """向右步进一个 raw 偏移：行内右移，行尾则跳下一行首（跳过围栏块）。

    返回 None 表示已到文档末尾。
    """
    if not (0 <= li < len(lines)):
        return None
    cur_raw = line_raw(lines[li])
    if off < len(cur_raw):
        return (li, off + 1)
    if li >= len(lines) - 1:
        return None
    nxt = lines[li + 1]
    if is_fence(nxt):
        return None
    return (li + 1, 0)


# ============ 行高前缀和 ============
def _build_offset_prefix(heights: list[float]) -> list[float]:
    """从行高列表构建前缀和数组：prefix[i] = sum(heights[0..i-1])。

    prefix[0] = 0.0，prefix[n] = 总高度。O(n) 一次性构建，后续 O(1) 查表。
    用于 _estimate_line_offset 的前缀和缓存。
    """
    prefix = [0.0] * (len(heights) + 1)
    for j, h in enumerate(heights):
        prefix[j + 1] = prefix[j] + h
    return prefix


# ============ 编辑器快照 ============
def _make_snapshot(
    cursor_li: int | None, cursor_off: int, raw_mode: bool,
    raw_draft: str, markdown: str,
) -> EditorSnapshot:
    """构造编辑器快照（撤销/重做栈元素）。

    markdown：序列化后的 Markdown 文本（raw_mode 下为 raw_draft）
    cursor_li：激活行号 | None（浏览态）
    cursor_off：行级 raw 偏移 0..len(line.raw)
    """
    return EditorSnapshot(
        markdown=markdown,
        cursor_li=cursor_li,
        cursor_off=cursor_off,
        raw_mode=raw_mode,
        raw_draft=raw_draft,
    )


# ============ 删除选区决策 ============
def _compute_delete_result(
    lines: list[Line], start_li: int, start_off: int,
    end_li: int, end_off: int,
) -> tuple[int, str, list[Line]] | None:
    """计算删除选区后的结果，返回 (合并行索引, 合并行 raw, 新行列表) 或 None。

    - 单行删除：新行列表 = 原 lines（同一引用，行数不变），仅合并行 raw 变化
    - 多行删除：新行列表 = lines[:start_li+1] + lines[end_li+1:]（行数减少）

    纯决策函数：不触发 reparse / mark_dirty / set_cursor，调用方负责执行副作用。
    边界无效（行号越界）时返回 None。
    """
    if start_li == end_li:
        if not (0 <= start_li < len(lines)):
            return None
        cur_raw = line_raw(lines[start_li])
        new_raw = cur_raw[:start_off] + cur_raw[end_off:]
        return (start_li, new_raw, lines)
    if not (0 <= start_li < len(lines) and 0 <= end_li < len(lines)):
        return None
    merged = line_raw(lines[start_li])[:start_off] + line_raw(lines[end_li])[end_off:]
    new_lines = lines[:start_li + 1] + lines[end_li + 1:]
    return (start_li, merged, new_lines)
