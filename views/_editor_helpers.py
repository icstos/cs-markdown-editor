"""编辑器纯计算助手：从 MarkdownEditor 闭包剥离的无状态函数。

依赖项：
- models（Line 类型注解）
- views.pixel_layout（hit_test_line_x_raw：X 像素 → raw 偏移命中）

对外接口（均为内部助手，下划线前缀）：
- _snap_indent_up / _snap_indent_down：列表缩进 / 降级对齐到单位倍数
- _shift_cursor_off：前缀长度变化后平移光标，保留内容内相对位置
- _vline_off_at_x：视觉行上 X 像素命中 raw 偏移
- _table_cells：表格行 raw 拆分为单元格文本列表

设计要点：
- 这些函数原本定义在 MarkdownEditor 组件闭包内（每次重渲染重新创建）或模块级，
  统一迁出到本模块后：①不再随渲染重建（输入响应微优化）；②可独立单元测试；
  ③为阶段 5 控制器封装提供无状态复用基础。
- 纯度判定：函数体内不读 cursor_li / cursor_off / document / *_ref.current / set_*，
  只通过参数进出。读取闭包状态的函数（如 _estimate_line_height / _get_cursor_row_col）
  不在此处，仍留在闭包内。
"""

from models import Line
from views.pixel_layout import hit_test_line_x_raw


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
