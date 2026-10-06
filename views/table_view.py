"""表格视图：基于 DataTable2 的 Typora/Word 风格表格渲染与编辑。

表格作为独立可编辑岛屿（与代码块的 Flet 原生编辑框同构）：
- 单击单元格进入编辑模式（TextField 替换 Text）
- Tab/Shift+Tab/Enter 单元格间导航（Tab 在末格新增行）
- 工具栏 + 右键菜单支持行列增删、对齐设置、删除整表
- on_change_cell 原地更新行模型（不触发 observable 重渲染，避免光标跳动）
- on_table_op 结构操作触发重渲染
- table_nav_ref 供 editor.py _on_key_down 调用 Tab/Escape 导航

四条几何/交互不变量（由 tests/test_table_view_native.py 逐条钉死）：

1. **三态文字同 x**：表头 / 数据格 / 编辑框的**字形左缘必须落在同一条竖线上**
   （真机复核：浏览态 474/1003 ↔ 编辑态 474/1003，物理 px，DPR 1.5）。三态共用
   `_CELL_PAD_H` 即可 —— 注意**前提是编辑框 `filled=False`**：带填充的
   `InputDecorator` 会自己多让出一段行首内缩（实测 4 逻辑 px），那时就得额外做补偿
   （本模块曾为此维护过 `_EDIT_LEAD_INSET`）。**表头内不得放常驻装饰控件**——曾经
   放过一个对齐图标，它把表头文字右推 16 逻辑 px，进编辑态又跳回来（真机实测表头
   墨迹左缘比数据格右移 21 物理 px）。列对齐状态改由工具栏承载。
2. **编辑态铺满整格**：进入编辑时，单元格底面必须**铺满单元格矩形**（宽、高都要），
   不能是"格子里浮着一个小输入框"。两个条件缺一不可：① 底面 `Container` 必须显式
   设 `alignment` —— 只有设了它才会吃满父级给的宽高，否则贴住子项，而
   `ft.TextField` 的固有高只有 24 逻辑 px（真机实测改前填充 36 物理 px，而单元格
   是 59 物理 px，上下各空 7.5 逻辑 px）；② 输入框自身不能再上色
   （`filled=False`），底色由那层底面**一处**提供 —— 否则两层叠加处会重新叠出
   一个"格中格"的小方块。
3. **网格行高恒定**：数据行恒为 `_DATA_ROW_H`，单元格文本**单行 + 省略号**，
   完整内容走 tooltip。行高随内容浮动会让"进出编辑"产生高度跳动（读态两行、
   编辑框只有一行），也让表格总高失估（`views/editor/_scroll.py` 按行偏移前缀和
   算滚动范围）。
4. **工具栏行高 = `_TB_H`**：行高由**最高子项**决定，压内边距无效，故工具栏内
   不得出现 Material 固有尺寸控件（`ft.IconButton` 40 / `ft.TextButton` 36 /
   `ft.Dropdown` 48）。统一用固定高度的 `Container(ink=True)` 与自绘触发器，
   与 `views/code_block.py` 的头部、`views/status_bar.py` 同一套做法。
5. **操作目标显式**：行/列级操作（删行、删列、列对齐）在**没有活动单元格**时
   置灰——原先无选区时默认作用在"最后一个数据行的最后一列"，点一下就删掉一列，
   与用户意图无关。
"""

import asyncio
from collections.abc import Callable

import flet as ft

import parser
from models.document import BlockType, Line
from services.clipboard import copy_code_to_clipboard
from styles import (
    FONT_MAIN,
    FONT_MONO,
    Elevation,
    Radius,
    Spacing,
    _current_colors,
    card_shadow,
)
from utils.table_helpers import ALIGN_RE, split_row
from views.segment_view import segment_to_span

# flet_datatable2 只在真正渲染表格时才需要：该包导入成本高（~54ms，连带大量
# flet 控件子模块）。首启空白文档 / 不含表格的文档可完全跳过，故改为首次用到
# 时惰性导入（缺失时回退 ft.DataTable）。
_DataTable2 = None


def _data_table2_cls():
    """惰性获取 DataTable2 类（导入失败回退 ft.DataTable）。"""
    global _DataTable2
    if _DataTable2 is None:
        try:
            from flet_datatable2 import DataTable2
        except Exception:  # pragma: no cover
            from flet import DataTable as DataTable2
        _DataTable2 = DataTable2
    return _DataTable2


# ---------------------------------------------------------------------------
# 几何常量（单元格三态共用，禁止各处另写数字）
# ---------------------------------------------------------------------------

# 单元格内边距：表头 / 数据格 / 编辑框**三态同值**，文字左缘才会落在同一条竖线上
# （点进编辑不跳字，表头也不会与数据列错位）。刻意保留 10（不是 4px 网格上的
# Spacing.LG=8）：8 会让单元格在视觉上过分局促，而列间距（column_spacing=12）是按
# 10 调的。
_CELL_PAD_H = 10
_CELL_PAD_V = 6
_CELL_FONT = 14  # 单元格字号（表头 / 数据 / 编辑框同值）

# 编辑态单元格的底色（`link` 的不透明度）。一档取自"活动单元格 0.04 / 活动行 0.07 /
# 活动列表头 0.10"之上，让"这一格正在打字"和"我在这一行/列"一眼分得开；也与
# `views/code_block.py` 活动行号色带（0.26）同属"焦点用浓色"的口径。
# 底面铺满整格（见模块 docstring 不变量 2），故这个值直接决定编辑态的观感权重。
_EDIT_CELL_TINT = 0.20

# 说明：这里曾有一条 `_EDIT_LEAD_INSET = 4`（"编辑框要比浏览态少 4 逻辑 px"的补偿），
# 因为当时输入框是 `filled=True` —— 真机实测带填充的 InputDecorator 会**自己**在行首
# 多让出一段内缩（6 物理 px / DPR 1.5 = 4 逻辑 px）。改成"底面统一着色 + 输入框
# `filled=False`"之后，那段内缩随之消失（同一结构下把 `filled` 改回 True 复现，
# 文字立刻右移 6 物理 px），补偿也就必须去掉：三态内边距重新同值。
# 结论：**行首内缩是 `filled` 的属性，不是 `TextField` 的固有几何。**

# 行高：数据行固定 40（网格化、紧凑、行高恒定）；表头 36，比数据行略矮，
# 让表头像"标题条"而不是又一行数据。
_DATA_ROW_H = 40
_HEAD_ROW_H = 36

# 工具栏行高：与 views/code_block.py 的 _HEADER_H 同一量级。工具栏内出现
# Material 固有尺寸控件时会被顶到 36~48（真机实测改前 67 物理 px / DPR1.5 ≈ 45
# 逻辑 px），故所有子项都必须显式定高。
_TB_H = 22

# 单元格 tooltip 的触发门槛（可见字符数）。表头的单元格宽度由 DataTable2 在
# 客户端按内容分配，Python 侧拿不到真实列宽，无法精确判断"是否会被省略号裁掉"；
# 故取一个**偏保守**的字符数门槛：宁可多挂一个 tooltip（用户不悬停就没有成本），
# 也不要漏掉真正被裁掉的单元格。20 个 CJK 字 ≈ 280px，已超过多数文档表格的列宽。
_TOOLTIP_MIN_CHARS = 20


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_table_lines(
    lines: list[Line], start_idx: int
) -> tuple[int, int, list[int], list[list[str]], list[str]]:
    """解析连续 TABLE 行。

    返回 (header_idx, sep_idx, row_indices, rows, aligns)。
    row_indices 不含 header 和 separator。aligns 为对齐标记原始字符串。
    """
    row_indices: list[int] = []
    rows: list[list[str]] = []
    aligns: list[str] = []
    i = start_idx
    header_idx = start_idx
    sep_idx = -1
    seen_header = False
    while i < len(lines) and lines[i].block_type == BlockType.TABLE:
        cells = [c.strip() for c in lines[i].raw.strip().strip("|").split("|")]
        # 分隔行判定：单元格必须非空且匹配 :?-{3,}:?。原先 `c or "---"` 会把空
        # 单元格回退成 "---"，导致新增的空数据行 |  |  | 被误判为分隔行，从而
        # 不进入 row_indices，表格渲染时丢失该行（"增加行按钮无效"的根因）。
        if all(c and ALIGN_RE.fullmatch(c) for c in cells):
            aligns = cells
            sep_idx = i
        elif not seen_header:
            header_idx = i
            rows.append(cells)
            seen_header = True
        else:
            row_indices.append(i)
            rows.append(cells)
        i += 1
    return header_idx, sep_idx, row_indices, rows, aligns


def _normalize_rows(rows: list[list[str]]) -> list[list[str]]:
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def _cell_text(cell: str) -> str:
    return cell.strip() or "\u00a0"  # 不换行空格：确保空单元格有可测量宽度


def _render_cell_spans(cell_text: str, base_size: int = 14) -> list[ft.TextSpan]:
    """把单元格文本解析为行内 markdown 并渲染为 TextSpan 列表（Typora 式）。

    复用 parse_inline + segment_to_span：**bold** *italic* `code` ~~strike~~
    ==hl== [link](url) 等行内语法正确渲染样式，标记折叠仅显示内容文本。
    - 链接段保留 on_click 打开 URL（DataCell.on_tap 不冲突：TextSpan 点击优先）
    - 其余段不绑定点击（on_activate=None），由 DataCell.on_tap 进入编辑
    - 空单元格返回单个不换行空格 span，确保空格可点击进入编辑
    """
    text = cell_text.strip()
    if not text:
        return [ft.TextSpan(text="\u00a0", style=ft.TextStyle(size=base_size))]
    segs = parser.parse_inline(text)
    spans: list[ft.TextSpan] = []
    for i, seg in enumerate(segs):
        spans.append(segment_to_span(seg, i, on_activate=None, base_size=base_size))
    return spans or [ft.TextSpan(text=text, style=ft.TextStyle(size=base_size))]


def _cell_tooltip(spans: list[ft.TextSpan]) -> str | None:
    """单元格被省略号裁切时的悬停提示文本；内容短到不会裁切时返回 None。

    取**渲染后**的可见文本（markdown 标记已折叠、链接显示 label），与用户看到的
    一致；不换行空格（空单元格的占位符）要剥掉，否则空单元格也会挂一个 tooltip。

    门槛见 `_TOOLTIP_MIN_CHARS`：这是刻意的**启发式**（真实列宽只在客户端才知道），
    方向是"宁可多挂"——挂上而没被悬停零成本，漏挂则用户再也看不到被裁掉的内容。
    不挂 tooltip 的单元格也不丢内容：点击进编辑态后编辑框里有完整文本。
    """
    visible = "".join(s.text or "" for s in spans).replace("\u00a0", "").strip()
    return visible if len(visible) >= _TOOLTIP_MIN_CHARS else None


def _safe_color(color: str, opacity: float) -> str:
    return ft.Colors.with_opacity(opacity, color)


def _align_of(sep_cell: str) -> str:
    s = sep_cell.strip()
    if s.startswith(":") and s.endswith(":"):
        return "center"
    if s.endswith(":"):
        return "right"
    return "left"


def _align_text_align(align: str) -> ft.TextAlign:
    return {
        "left": ft.TextAlign.LEFT,
        "center": ft.TextAlign.CENTER,
        "right": ft.TextAlign.RIGHT,
    }.get(align, ft.TextAlign.LEFT)


def _align_container(align: str) -> ft.Alignment:
    """Container 内容对齐：控制 Text 块在单元格内的水平位置。

    Text 无 expand=True 时只占内容宽度，text_align 无空间生效。
    通过 Container.alignment 把 Text 块整体左/中/右对齐。
    """
    return {
        "left": ft.Alignment.CENTER_LEFT,
        "center": ft.Alignment.CENTER,
        "right": ft.Alignment.CENTER_RIGHT,
    }.get(align, ft.Alignment.CENTER_LEFT)


def _align_icon(align: str) -> str:
    return {
        "left": ft.Icons.FORMAT_ALIGN_LEFT,
        "center": ft.Icons.FORMAT_ALIGN_CENTER,
        "right": ft.Icons.FORMAT_ALIGN_RIGHT,
    }.get(align, ft.Icons.FORMAT_ALIGN_LEFT)


# ---------------------------------------------------------------------------
# TableView 组件
# ---------------------------------------------------------------------------


@ft.memo
@ft.component
def TableView(
    lines: list[Line],
    line_idx: int,
    content_width: float | None = None,
    clipboard_ref: ft.Ref | None = None,
    on_change_cell: Callable[[int, int, str], None] | None = None,
    on_table_op: Callable[[str, dict], None] | None = None,
    on_table_focus: Callable[[], None] | None = None,
    on_table_blur: Callable[[], None] | None = None,
    table_nav_ref: ft.Ref | None = None,
    is_current_line: bool = False,
    # set_block(TABLE) 创建后传入 table_focus_li，触发 use_effect 自动聚焦表头首格
    auto_focus_li: int | None = None,
    # 版本号触发 prop：on_table_op 通过 document.lines = lines 重新赋值已能
    # 触发 memo 检测（lines 引用变化）。但 on_change_cell 就地修改 line.raw
    # 不替换 lines 引用，ft.memo 浅比较 lines 引用未变会误判未刷新。
    # 表格内部 edit_draft state 已驱动编辑态显示，on_change_cell 无需外部
    # 重渲染；但保留版本号 prop 作为结构性变更（add_row/delete_col 等）的
    # 兜底触发，确保 lines 引用未变但内容已变的边缘场景也能刷新。
    # TableView 内部不读取这两个值，仅作 memo 触发用。
    lines_version: int = 0,
    first_line_raw_version: int = 0,
    # 主题失效 prop：切换主题时 lines/版本号/回调均不变，ft.memo 会复用缓存
    # 跳过函数体执行，导致 _current_colors() 与 is_dark 判定停留在旧主题，
    # 单元格背景、表头色、边框色不刷新。通过 theme_mode 变化触发 memo 失效，
    # 重新执行函数体取色。TableView 内部不读取此值（直接读 page.theme_mode，
    # 已由 App 在渲染期同步写入），仅作 memo 触发用。
    theme_mode: ft.ThemeMode = ft.ThemeMode.LIGHT,
):
    """自管理的表格编辑组件（独立岛屿，不使用 active/draft 系统）。

    memo 化：表格作为独立岛屿，prop 集合稳定（lines/line_idx/content_width
    + 回调 + is_current_line + 版本号 prop）。cursor_line 移动到表格外时
    is_current_line 由 True→False 触发 memo 刷新（移除高亮边框）；
    on_table_op 通过 document.lines = lines 重赋值触发 lines 引用变化，
    memo 检测到后重新解析表格结构。on_change_cell 由内部 edit_draft state
    驱动显示，无需外部重渲染，memo 跳过以保 IME/编辑流畅。
    """
    c = _current_colors()
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK
    header_idx, sep_idx, row_indices, rows, aligns = _parse_table_lines(lines, line_idx)
    if not rows:
        return ft.Container()

    normalized = _normalize_rows(rows)
    col_count = len(normalized[0]) if normalized else 0
    if col_count == 0:
        return ft.Container()

    aligns = [
        _align_of(aligns[i]) if i < len(aligns) else "left" for i in range(col_count)
    ]
    header_row = normalized[0]
    body_rows = normalized[1:] if len(normalized) > 1 else []

    # ---- 内部状态 ----
    edit_cell, set_edit_cell = ft.use_state(None)  # (line_idx, col_idx) | None
    edit_draft, set_edit_draft = ft.use_state("")
    edit_draft_ref = ft.use_ref("")
    # 复制按钮反馈：复制成功后图标切换为 ✓ 1.2s 后复位（与代码块复制按钮一致）
    copied, set_copied = ft.use_state(False)
    edit_draft_ref.current = edit_draft
    pending_blur_ref = ft.use_ref(False)
    nav_seq, set_nav_seq = ft.use_state(0)
    pending_nav, set_pending_nav = ft.use_state(None)  # ("new_row", col_idx) | None
    # 导航守卫：_start_edit 切换单元格时设为 True，阻止旧 TextField 卸载触发的
    # on_blur 退出编辑模式（Tab/Enter 导航、点击新单元格均会触发重渲染→旧 field 卸载）。
    # 0.1s 后自动复位，允许真正的失焦（点击表格外部）正常退出。
    nav_guard_ref = ft.use_ref(False)

    # ---- 辅助方法 ----
    def _cell_value(li: int, ci: int) -> str:
        if 0 <= li < len(lines):
            cells = split_row(lines[li].raw)
            if ci < len(cells):
                return cells[ci]
        return ""

    def _start_edit(li: int, ci: int):
        """进入单元格编辑模式。"""
        if edit_cell is not None and edit_cell != (li, ci):
            _commit_current()
        # 设导航守卫：阻止 set_edit_cell 触发重渲染后旧 TextField 卸载的 on_blur
        nav_guard_ref.current = True
        pending_blur_ref.current = False
        text = _cell_value(li, ci)
        edit_draft_ref.current = text
        set_edit_draft(text)
        set_edit_cell((li, ci))
        set_nav_seq(nav_seq + 1)
        if on_table_focus is not None:
            on_table_focus()
        # 延迟复位守卫：等待 on_blur 触发窗口过去后恢复
        page = ft.context.page
        if page is not None:

            async def _reset_guard():
                await asyncio.sleep(0.1)
                nav_guard_ref.current = False

            page.run_task(_reset_guard)

    def _commit_current():
        """提交当前编辑单元格的草稿到行模型（on_change_cell 已实时同步，此处兜底）。"""
        if edit_cell is not None and on_change_cell is not None:
            on_change_cell(edit_cell[0], edit_cell[1], edit_draft_ref.current)

    def _exit_edit():
        """退出编辑模式。"""
        nav_guard_ref.current = False
        pending_blur_ref.current = False
        _commit_current()
        set_edit_cell(None)
        if on_table_blur is not None:
            on_table_blur()

    # ---- 导航 ----
    def _move_cell(delta: int):
        """Tab(1)/Shift+Tab(-1)：移动到下一/上一格。末格 Tab 新增行。"""
        _commit_current()
        current = edit_cell or (header_idx, 0)
        all_rows = [header_idx] + row_indices
        try:
            row_idx = all_rows.index(current[0])
        except ValueError:
            row_idx = 0
        ci = current[1]
        next_ci = ci + delta
        next_row_idx = row_idx
        if next_ci >= col_count:
            next_ci = 0
            next_row_idx = row_idx + 1
        elif next_ci < 0:
            next_ci = col_count - 1
            next_row_idx = row_idx - 1
        if 0 <= next_row_idx < len(all_rows):
            _start_edit(all_rows[next_row_idx], next_ci)
        elif next_row_idx >= len(all_rows) and on_table_op is not None:
            on_table_op("add_row", {"after_li": all_rows[-1], "col_count": col_count})
            set_pending_nav(("new_row", 0))

    def _move_down(add: bool = True):
        """移动到下一行同列。

        add=True（Enter）：末行新增行（Typora/Word 行为）。
        add=False（ArrowDown）：末行不动作（Excel 行为），避免方向键误增行。
        """
        _commit_current()
        current = edit_cell or (header_idx, 0)
        all_rows = [header_idx] + row_indices
        try:
            row_idx = all_rows.index(current[0])
        except ValueError:
            row_idx = 0
        ci = current[1]
        next_row_idx = row_idx + 1
        if next_row_idx < len(all_rows):
            _start_edit(all_rows[next_row_idx], ci)
        elif add and on_table_op is not None:
            on_table_op("add_row", {"after_li": all_rows[-1], "col_count": col_count})
            set_pending_nav(("new_row", ci))

    def _move_up():
        """ArrowUp：移动到上一行同列。首行不动作（Excel 行为）。"""
        _commit_current()
        current = edit_cell or (header_idx, 0)
        all_rows = [header_idx] + row_indices
        try:
            row_idx = all_rows.index(current[0])
        except ValueError:
            row_idx = 0
        ci = current[1]
        next_row_idx = row_idx - 1
        if next_row_idx >= 0:
            _start_edit(all_rows[next_row_idx], ci)
        # 首行 ArrowUp：不动作

    # ---- TextField 事件 ----
    def _on_change_draft(value: str):
        edit_draft_ref.current = value
        set_edit_draft(value)
        if edit_cell is not None and on_change_cell is not None:
            on_change_cell(edit_cell[0], edit_cell[1], value)

    def _on_blur():
        """延迟失焦：允许点击另一单元格时在新 TextField 聚焦前取消清除。

        导航守卫（nav_guard_ref）为 True 时直接返回：Tab/Enter/点击新单元格
        触发的重渲染会卸载旧 TextField 产生 on_blur，这不是真正的失焦。
        """
        if nav_guard_ref.current:
            return
        pending_blur_ref.current = True

        async def _deferred():
            await asyncio.sleep(0.05)
            if pending_blur_ref.current:
                pending_blur_ref.current = False
                _commit_current()
                set_edit_cell(None)
                if on_table_blur is not None:
                    on_table_blur()

        page = ft.context.page
        if page is not None:
            page.run_task(_deferred)

    def _on_submit(e):
        """Enter 键：移动到下一行。"""
        _move_down()

    # ---- 编辑态底面（表头与数据格共用一份实现）----
    def _cell_edit_surface(ci: int, *, key: str, bold: bool = False) -> ft.Container:
        """单元格编辑态：一张**铺满整格**的底面 + 一个透明输入框。

        表头与数据格只差字重与 key 前缀，故共用一份实现 —— 三态对齐/铺满这两条
        不变量都靠"只有一处可改"来保证，避免只修了其中一支（改前两支就是各写各的）。

        底面 `alignment=ft.Alignment.CENTER` 身兼两职：

        1. **铺满整格**。`Container` 只有设了 `alignment` 才会吃满父级给的宽高；
           没有它时贴住子项，而 `ft.TextField` 的固有高只有 ~24 逻辑 px —— 真机实测
           改前填充 y=462..497（36 物理 px），而单元格是 y=450..508（59 物理 px），
           上下各空 7.5 逻辑 px，看起来是"单元格里嵌了个输入控件"。
        2. **文字仍落在浏览态的那条基线上**。铺满的是底面，输入框自身只有一行高、
           被 `alignment` 居中，且三态内边距同值（`_CELL_PAD_H`），故表头读态 /
           数据读态 / 编辑态**同 x 同 y**。

        注意 `filled=False` 同时是上面第 2 条和"三态文字同 x"的前提：`ft.TextField`
        一旦 `filled=True`，它的 InputDecorator 会在行首**自己**多让出一段内缩
        （真机实测 4 逻辑 px），文字立刻右移，这里就得再补一条减法。
        """
        return ft.Container(
            content=ft.TextField(
                key=key,
                value=edit_draft,
                autofocus=True,
                border=ft.NoInputBorder(),
                # **输入框自身不上色**：底色只由外层底面一处提供。这里若再叠一层
                # fill，两层叠加处会重新变成一个"格中格"的小方块（正是本轮的 bug）。
                # 与 views/code_block.py 的编辑框同一做法（filled=False + 透明底）。
                filled=False,
                bgcolor=ft.Colors.TRANSPARENT,
                dense=True,
                # 内边距与表头/数据格的浏览态**同值**（`_CELL_PAD_*`）：三态字形
                # 左缘落在同一条竖线上，点进编辑不跳字。
                content_padding=ft.Padding.symmetric(
                    horizontal=_CELL_PAD_H, vertical=_CELL_PAD_V
                ),
                text_style=ft.TextStyle(
                    font_family=FONT_MAIN,
                    color=c.text,
                    size=_CELL_FONT,
                    weight=ft.FontWeight.W_600 if bold else ft.FontWeight.NORMAL,
                ),
                text_align=_align_text_align(aligns[ci]),
                cursor_color=c.link,
                selection_color=_safe_color(c.link, 0.18),
                on_change=lambda e: _on_change_draft(e.control.value),
                on_submit=_on_submit,
                on_blur=lambda e: _on_blur(),
                on_focus=lambda e: on_table_focus() if on_table_focus else None,
            ),
            alignment=ft.Alignment.CENTER,
            bgcolor=_safe_color(c.link, _EDIT_CELL_TINT),
            # 不留圆角：单元格是网格里的一格，"铺满"才是这一格的语义；圆角会在四角
            # 露出底色，又变回"一格里的一个色块"。表格自身有 border_radius +
            # ANTI_ALIAS 裁剪，最外侧一格的四角不会越出表格边框。
            padding=0,
        )

    # ---- 导航回调（供 editor.py _on_key_down 通过 table_nav_ref 调用）----
    def _navigate(action: str, delta: int = 0):
        if action == "tab":
            _move_cell(delta)
        elif action == "escape":
            _exit_edit()
        elif action == "up":
            _move_up()
        elif action == "down":
            _move_down(add=False)  # ArrowDown 末行不新增行（Excel 行为）

    if table_nav_ref is not None:
        table_nav_ref.current = _navigate

    # ---- 结构变更后定位新行 ----
    def _resolve_pending_nav():
        if pending_nav is None:
            return
        kind, col_idx = pending_nav
        if kind == "new_row":
            _, _, new_row_indices, _, _ = _parse_table_lines(lines, line_idx)
            if new_row_indices:
                _start_edit(new_row_indices[-1], col_idx)
        set_pending_nav(None)

    ft.use_effect(_resolve_pending_nav, [pending_nav])

    # ---- 表格创建后自动聚焦表头首格（set_block(TABLE) 触发）----
    def _auto_focus_first_cell():
        # auto_focus_li == line_idx：仅当前表格匹配创建行；edit_cell is None：
        # 避免用户已点进某格后又因 auto_focus 抢焦
        if (
            auto_focus_li is not None
            and auto_focus_li == line_idx
            and edit_cell is None
        ):
            _start_edit(header_idx, 0)

    ft.use_effect(_auto_focus_first_cell, [auto_focus_li])

    # ---- 当前选中行/列（工具栏操作目标）----
    sel = edit_cell or ((row_indices[-1] if row_indices else header_idx, col_count - 1))
    sel_li, sel_ci = sel

    # ---- 活动单元格（定位高亮用）----
    # 与 `sel` 刻意分开：`sel` 是"工具栏的操作目标"（无选区时兜底到最后一行/列），
    # 而定位高亮**只在真的有活动单元格时**出现。若共用 `sel`，表格没被点过也会
    # 出现"最后一行最后一列"的高亮，等于向用户谎报选区。
    active_li = edit_cell[0] if edit_cell is not None else None
    active_ci = edit_cell[1] if edit_cell is not None else None
    has_active = edit_cell is not None

    # ---- 工具栏操作 ----
    def _do_add_row():
        _commit_current()
        target_li = (
            sel_li
            if sel_li in row_indices
            else (row_indices[-1] if row_indices else sep_idx)
        )
        if on_table_op is not None:
            on_table_op("add_row", {"after_li": target_li, "col_count": col_count})
            set_pending_nav(("new_row", sel_ci))

    def _do_delete_row():
        if not row_indices:
            return
        target_li = sel_li if sel_li in row_indices else row_indices[-1]
        if len(row_indices) <= 1:
            if on_table_op is not None:
                on_table_op("clear_row", {"li": target_li})
            return
        _commit_current()
        if on_table_op is not None:
            on_table_op("delete_row", {"li": target_li})
        set_edit_cell(None)

    def _do_add_col():
        if on_table_op is not None:
            on_table_op("add_col", {"table_start": line_idx, "col_idx": sel_ci + 1})

    def _do_delete_col():
        if col_count <= 1:
            return
        if on_table_op is not None:
            on_table_op("delete_col", {"table_start": line_idx, "col_idx": sel_ci})
        set_edit_cell(None)

    def _do_set_align(align: str):
        if on_table_op is not None:
            on_table_op(
                "set_align",
                {
                    "table_start": line_idx,
                    "col_idx": sel_ci,
                    "align": align,
                },
            )

    def _do_delete_table():
        """删除整张表（多行结构，交给 on_table_op 一处处理）。

        表格是**多行**结构，删掉后原位置没有任何可落光标的块，故 op 里会用一行
        空段落替换（见 views/editor/_fence.py 的 delete_table 分支）。
        撤销：on_table_op 开头统一 `push_history()`，Ctrl+Z 可整体还原。
        """
        if on_table_op is not None:
            on_table_op("delete_table", {"table_start": line_idx})
        set_edit_cell(None)

    # ---- 右键菜单 ----
    def _cell_context_items(li: int, ci: int, is_header: bool) -> list:
        items: list = []
        if not is_header:
            items.extend(
                [
                    ft.PopupMenuItem(
                        content="上方插入行",
                        on_click=lambda e: (
                            on_table_op(
                                "add_row", {"after_li": li - 1, "col_count": col_count}
                            )
                            if on_table_op
                            else None
                        ),
                    ),
                    ft.PopupMenuItem(
                        content="下方插入行",
                        on_click=lambda e: (
                            on_table_op(
                                "add_row", {"after_li": li, "col_count": col_count}
                            )
                            if on_table_op
                            else None
                        ),
                    ),
                    ft.PopupMenuItem(
                        content="删除行",
                        on_click=lambda e: (
                            on_table_op("delete_row", {"li": li})
                            if on_table_op
                            else None
                        ),
                    ),
                    ft.PopupMenuItem(),
                ]
            )
        items.extend(
            [
                ft.PopupMenuItem(
                    content="左侧插入列",
                    on_click=lambda e: (
                        on_table_op("add_col", {"table_start": line_idx, "col_idx": ci})
                        if on_table_op
                        else None
                    ),
                ),
                ft.PopupMenuItem(
                    content="右侧插入列",
                    on_click=lambda e: (
                        on_table_op(
                            "add_col", {"table_start": line_idx, "col_idx": ci + 1}
                        )
                        if on_table_op
                        else None
                    ),
                ),
                ft.PopupMenuItem(
                    content="删除列",
                    on_click=lambda e: (
                        on_table_op(
                            "delete_col", {"table_start": line_idx, "col_idx": ci}
                        )
                        if on_table_op
                        else None
                    ),
                ),
                ft.PopupMenuItem(),
                ft.PopupMenuItem(
                    content="左对齐",
                    on_click=lambda e: (
                        on_table_op(
                            "set_align",
                            {
                                "table_start": line_idx,
                                "col_idx": ci,
                                "align": "left",
                            },
                        )
                        if on_table_op
                        else None
                    ),
                ),
                ft.PopupMenuItem(
                    content="居中对齐",
                    on_click=lambda e: (
                        on_table_op(
                            "set_align",
                            {
                                "table_start": line_idx,
                                "col_idx": ci,
                                "align": "center",
                            },
                        )
                        if on_table_op
                        else None
                    ),
                ),
                ft.PopupMenuItem(
                    content="右对齐",
                    on_click=lambda e: (
                        on_table_op(
                            "set_align",
                            {
                                "table_start": line_idx,
                                "col_idx": ci,
                                "align": "right",
                            },
                        )
                        if on_table_op
                        else None
                    ),
                ),
                ft.PopupMenuItem(),
                # 删除整表：与工具栏的垃圾桶按钮同一入口。放在**最末**并用分隔线
                # 隔开——它是唯一会一次删掉整块内容的菜单项，不能与"删一行/删一列"
                # 混在相邻位置（误点代价最大）。
                ft.PopupMenuItem(
                    content="删除表格",
                    on_click=lambda e: (
                        on_table_op("delete_table", {"table_start": line_idx})
                        if on_table_op
                        else None
                    ),
                ),
            ]
        )
        return items

    # ---- 渲染：列头 ----
    columns: list = []
    for ci in range(col_count):
        is_editing = edit_cell == (header_idx, ci)
        if is_editing:
            # 表头编辑态与数据格共用同一张底面（只差字重），三态对齐/铺满两条
            # 不变量因此只有一处可改。
            label = _cell_edit_surface(ci, key=f"th-edit-{nav_seq}", bold=True)
        else:
            inner = ft.Container(
                # 表头**只有文字**：曾经此处前置了一个常驻对齐图标（12px + 4px 间距），
                # 它把表头文字右推 16 逻辑 px，而数据格只缩进 `_CELL_PAD_H` —— 真机
                # 实测表头"名称"的墨迹左缘比数据"Alpha"右移 21 物理 px（14 逻辑 px），
                # 点击进编辑（编辑框按 `_CELL_PAD_H` 缩进）时又跳回来，三态两处错位。
                # 列对齐状态改由工具栏承载：当前列的图标 + 下拉都在那条 22px 的行上。
                content=ft.Text(
                    value=_cell_text(header_row[ci]),
                    style=ft.TextStyle(
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_600,
                        color=c.text,
                        size=_CELL_FONT,
                    ),
                    text_align=_align_text_align(aligns[ci]),
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                # 与数据格同一套对齐方式（Container.alignment）：Text 无 expand 时
                # text_align 无空间生效，数据行靠这一层做左/中/右。
                alignment=_align_container(aligns[ci]),
                padding=ft.Padding.symmetric(
                    horizontal=_CELL_PAD_H, vertical=_CELL_PAD_V
                ),
                border_radius=Radius.MD,
                # 活动列的表头 = 一颗色片：宽表里"我在哪一列"由它回答（"在哪一行"
                # 由整行底色回答，见 data_rows 的 color）。列头不做逐格底色——单元格
                # 之间有 column_spacing 的缝隙，逐格铺色会断成一串小方块，看不出是一列。
                bgcolor=_safe_color(c.link, 0.10) if ci == active_ci else None,
            )
            label = ft.ContextMenu(
                content=ft.GestureDetector(
                    content=inner,
                    on_tap=lambda e, ci=ci: _start_edit(header_idx, ci),
                    mouse_cursor=ft.MouseCursor.CLICK,
                ),
                secondary_items=_cell_context_items(header_idx, ci, is_header=True),
            )
        columns.append(ft.DataColumn(label=label))

    # ---- 渲染：数据行 ----
    data_rows: list[ft.DataRow] = []
    for ri, source_li in enumerate(row_indices):
        row = normalized[ri + 1] if ri + 1 < len(normalized) else [""] * col_count
        cells: list[ft.DataCell] = []
        for ci in range(col_count):
            is_editing = edit_cell == (source_li, ci)
            if is_editing:
                content = _cell_edit_surface(ci, key=f"td-edit-{nav_seq}")
            else:
                spans = _render_cell_spans(row[ci], base_size=_CELL_FONT)
                inner = ft.Container(
                    content=ft.Text(
                        # 行内 markdown 渲染：**bold** *italic* `code` ~~strike~~
                        # ==hl== [link](url) 等语法渲染为带样式 TextSpan，标记折叠
                        # 仅显示内容（Typora 式）。外层 style 作为基础样式兜底，
                        # segment_style 对 TEXT 段仅设 size+color，font_family 由此继承。
                        spans=spans,
                        style=ft.TextStyle(
                            font_family=FONT_MAIN,
                            color=c.text,
                            size=_CELL_FONT,
                        ),
                        text_align=_align_text_align(aligns[ci]),
                        # **单行 + 省略号**：行高恒定（= _DATA_ROW_H），进出编辑态不产生
                        # 高度跳动。曾经是 max_lines=4 —— 40px 的行只放得下一行半，第二行
                        # 被横向切半（真机截图里"请求分发"只剩上半个字），既不是完整内容
                        # 也没有省略号，比直接裁掉更难看。完整内容由 tooltip 承载。
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    # Container.alignment：控制 Text 块在单元格内的水平位置。
                    # Text 无 expand=True 只占内容宽度，text_align 无空间生效，
                    # 仅靠 text_align 数据行永远左对齐。表头与数据格都走这一层，
                    # 两者的文字左缘才会在同一条竖线上。
                    alignment=_align_container(aligns[ci]),
                    padding=ft.Padding.symmetric(
                        horizontal=_CELL_PAD_H, vertical=_CELL_PAD_V
                    ),
                    border_radius=Radius.MD,
                    bgcolor=_safe_color(c.link, 0.04) if ci == active_ci else None,
                    # 被省略号裁掉的单元格靠悬停看全文（不破坏网格、不改行高，
                    # 也比"点进去才能看"更符合数据网格的直觉）。
                    tooltip=_cell_tooltip(spans),
                )
                # 用 DataCell.on_tap 而非 GestureDetector 包裹：DataCell 的 on_tap
                # 由 Flutter InkWell 拦截，命中区域覆盖整个单元格（不受 content
                # 尺寸影响）；GestureDetector 只命中 inner Container（Text+padding），
                # 空单元格 Text 为 " " 极窄，几乎无法点击进入编辑。
                content = ft.ContextMenu(
                    content=inner,
                    secondary_items=_cell_context_items(source_li, ci, is_header=False),
                )
            cells.append(
                ft.DataCell(
                    content=content,
                    # 编辑态不绑 on_tap：避免点击当前编辑单元格时重复触发
                    # _start_edit 导致 TextField 重建（key 变化）、光标跳动。
                    on_tap=None
                    if is_editing
                    else (lambda e, li=source_li, ci=ci: _start_edit(li, ci)),
                )
            )
        data_rows.append(
            ft.DataRow(
                cells=cells,
                # **行底色用状态映射**，不是单一颜色：
                # `ft.DataRow.color` 是 `ControlStateValue`，Flutter 侧按状态取值，
                # 且它**优先于** DataTable 的 `data_row_color`——原先这里给的是单一
                # 颜色（奇数行 1.5% 斑马、偶数行 0% 而非 None），恒为非 None，把
                # `data_row_color` 里的 HOVERED / PRESSED 整个遮住了：悬停高亮从来没
                # 生效过（死配置）。现在斑马纹 + 悬停 + 当前行三种语义都写在这一处，
                # 单一来源，不再依赖被遮住的表级配置。
                color={
                    # 当前行（光标所在行）：整条行带染淡蓝 —— 宽表里"我在哪一行"
                    # 由它回答，"在哪一列"由表头色片回答。
                    ft.ControlState.DEFAULT: (
                        _safe_color(c.link, 0.07)
                        if source_li == active_li
                        else _safe_color(
                            c.text, 0.015 if ri % 2 == 0 else 0.0
                        )
                    ),
                    ft.ControlState.HOVERED: _safe_color(
                        c.link, 0.11 if source_li == active_li else 0.05
                    ),
                    ft.ControlState.PRESSED: _safe_color(
                        c.link, 0.14 if source_li == active_li else 0.08
                    ),
                },
            )
        )

    # ---- 工具栏 ----
    # 行高硬锁 `_TB_H`：`Row` 的高度 = **最高子项**，而 Material 的固有尺寸压不动
    # （`ft.TextButton` 36 / `ft.IconButton` 40 / `ft.Dropdown` 48，`visual_density`、
    # 内边距、`height=` 都只能改外框不能改固有高，强压还会裁切内部文字）。改前这里
    # 是 4×`TextButton` + `Dropdown` + `IconButton`，真机实测整条工具栏 **67 物理 px**
    # （DPR 1.5）≈ 45 逻辑 px —— 表格卡片里最大的一块空白，也是与代码块头部（22px）
    # 最不一致的地方。现在统一用固定高度的 `Container(ink=True)`：水波与悬停由
    # Material 在客户端完成，不产生任何服务端往返（与 `views/code_block.py` 的
    # `_header_icon` 同一套做法）。
    def _tb_btn(
        label: str,
        on_click,
        *,
        icon: str | None = None,
        tooltip: str = "",
        enabled: bool = True,
        color: str | None = None,
    ) -> ft.Control:
        """紧凑工具栏按钮（图标 / 文字 / 图标+文字 通用）。

        `enabled=False`：不挂 `on_click` 且 `ink=False`（Material 的"禁用"语义 =
        无水波、无悬停、文字降透明度），但**保留 tooltip** —— 用它解释"为什么点不了"，
        这比一个哑掉的按钮清楚得多。
        """
        fg = color or c.text
        if not enabled:
            fg = _safe_color(c.muted, 0.55)
        parts: list[ft.Control] = []
        if icon is not None:
            parts.append(ft.Icon(icon, size=13, color=fg))
        if label:
            parts.append(ft.Text(label, size=12, color=fg))
        return ft.Container(
            height=_TB_H,
            border_radius=Radius.SM,
            padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=0),
            alignment=ft.Alignment.CENTER,
            ink=enabled,
            on_click=(lambda e: on_click()) if enabled else None,
            tooltip=tooltip or label,
            content=ft.Row(
                controls=parts,
                spacing=Spacing.XS,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _tb_sep() -> ft.Control:
        """组间细分隔条：把"加"与"减"两族分开，避免四个按钮连成一片。"""
        return ft.Container(
            width=1, height=12, bgcolor=_safe_color(c.border, 0.9)
        )

    # 列对齐：自绘 `PopupMenuButton` 触发器（当前列的对齐图标 + ▾）。不用
    # `ft.Dropdown` —— 它恒为 48px，是工具栏行高的唯一决定项（见上方说明）。
    # 无活动单元格时置灰：对齐作用于"光标所在列"，没有当前列就不该能点。
    current_align = aligns[sel_ci] if sel_ci < len(aligns) else "left"
    align_button = ft.PopupMenuButton(
        height=_TB_H,
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=0),
        tooltip=(
            "设置当前列对齐"
            if has_active
            else "先点击一个单元格，再设置它所在列的对齐"
        ),
        disabled=not has_active,
        # UNDER：菜单向下展开，不遮住表头本身（默认 OVER 会盖住正在看的那一行）
        menu_position=ft.PopupMenuPosition.UNDER,
        shape=ft.RoundedRectangleBorder(radius=Radius.MD),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            padding=ft.Padding.all(0),
            # 淡底 pill：让"当前列对齐"在紧凑工具栏里成为一个可点的实体
            # （VSCode 的 language mode 指示器同理），不是一段悬空的图标
            bgcolor=ft.Colors.with_opacity(0.05, c.text),
            overlay_color=ft.Colors.with_opacity(0.10, c.text),
        ),
        content=ft.Row(
            controls=[
                ft.Icon(
                    _align_icon(current_align),
                    size=13,
                    color=c.text if has_active else _safe_color(c.muted, 0.55),
                ),
                ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=15, color=c.muted),
            ],
            spacing=0,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        items=[
            ft.PopupMenuItem(
                # 压缩项高（默认 48）：三项的菜单不必铺满一屏
                height=28,
                content=ft.Text(value=text, size=12),
                # 勾选当前列的对齐：菜单一打开就能确认状态，无需记忆
                checked=key == current_align,
                on_click=lambda e, k=key: _do_set_align(k),
            )
            for key, text in (("left", "左对齐"), ("center", "居中"), ("right", "右对齐"))
        ],
    )

    # ---- 复制 / 删除整表 ----
    # 复制整张表格的 markdown 源码（连续 TABLE 行的 raw 拼接），粘贴到其他
    # markdown 编辑器可保持表格格式。复用 services.clipboard.copy_code_to_clipboard
    # 的剪贴板写入 + 图标反馈逻辑（✓ 1.2s 后复位）。
    table_end = line_idx
    while table_end < len(lines) and lines[table_end].block_type == BlockType.TABLE:
        table_end += 1
    table_md = "\n".join(lines[i].raw for i in range(line_idx, table_end))
    copy_btn = _tb_btn(
        "",
        lambda: (
            page.run_task(copy_code_to_clipboard, clipboard_ref, table_md, set_copied)
            if page is not None and not copied and clipboard_ref is not None
            else None
        ),
        icon=ft.Icons.CHECK if copied else ft.Icons.CONTENT_COPY,
        tooltip="已复制" if copied else "复制表格 Markdown（可直接粘到别处）",
        color=ft.Colors.GREEN if copied else c.muted,
    )
    # 删除整表：此前**完全没有入口** —— 只能切到原文模式手动删（工具栏只有删行/删列，
    # 右键菜单也没有）。删掉多行后光标无处可落，故 op 里用一行空段落替换整张表。
    delete_btn = _tb_btn(
        "",
        _do_delete_table,
        icon=ft.Icons.DELETE_OUTLINE,
        tooltip="删除整张表格（可 Ctrl+Z 撤销）",
        color=c.muted,
    )

    toolbar = ft.Row(
        controls=[
            ft.Icon(ft.Icons.TABLE_ROWS_ROUNDED, size=14, color=c.muted),
            ft.Text(
                # 显式标注"行/列"：原来的 `4 × 4` 两个数字同形，读者无法判断哪个是
                # 行、哪个是列；行数**含表头行**（tooltip 里说明）。
                f"{len(body_rows) + 1} 行 × {col_count} 列",
                size=11,
                color=c.muted,
                font_family=FONT_MONO,
                tooltip="表格尺寸（行数含表头行）",
            ),
            ft.Container(expand=True),
            _tb_btn(
                "行", _do_add_row, icon=ft.Icons.ADD, tooltip="在表格末尾新增一行"
            ),
            _tb_btn(
                "列",
                _do_add_col,
                icon=ft.Icons.ADD,
                tooltip="在当前列右侧插入一列（未选中单元格时追加到末尾）",
            ),
            _tb_sep(),
            _tb_btn(
                "行",
                _do_delete_row,
                icon=ft.Icons.REMOVE,
                tooltip="删除光标所在行（仅剩一行数据时清空其内容）"
                if has_active
                else "先点击一个单元格，再删除它所在的行",
                enabled=has_active,
            ),
            _tb_btn(
                "列",
                _do_delete_col,
                icon=ft.Icons.REMOVE,
                tooltip="删除光标所在列"
                if has_active and col_count > 1
                else (
                    "表格至少保留一列"
                    if col_count <= 1
                    else "先点击一个单元格，再删除它所在的列"
                ),
                enabled=has_active and col_count > 1,
            ),
            _tb_sep(),
            align_button,
            ft.Container(expand=True),
            copy_btn,
            delete_btn,
        ],
        spacing=Spacing.XS,
        height=_TB_H,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ---- DataTable2 ----
    # min_width 不支持 float("inf")：word_wrap=False 时 content_width=inf，
    # JSON 序列化为 "Infinity"（非标准），Flutter 无法解析导致渲染异常
    # （如只显示首列）。转为 None 让 DataTable2 自适应内容宽度。
    _min_w = (
        content_width if (content_width and content_width != float("inf")) else None
    )
    table = _data_table2_cls()(
        columns=columns,
        rows=data_rows,
        column_spacing=12,
        horizontal_margin=8,
        data_row_height=_DATA_ROW_H,
        heading_row_height=_HEAD_ROW_H,
        divider_thickness=1,
        horizontal_lines=ft.BorderSide(1, _safe_color(c.border, 0.08)),
        vertical_lines=ft.BorderSide(1, _safe_color(c.border, 0.06)),
        border=ft.TableBorder.all(
            1,
            _safe_color(c.border, 0.10),
        )
        if hasattr(ft, "TableBorder")
        else ft.Border.all(
            1,
            _safe_color(c.border, 0.10),
        ),
        border_radius=12,
        show_bottom_border=True,
        # 表头底色：4% → 6%。4% 时表头与数据行的色差只有 (245,249,254) vs
        # (254,254,254)，真机缩略图里几乎看不出这是"表头"；6% 让表头读起来是一条
        # 标题带，而不是碰巧颜色略深的一行数据。
        heading_row_color=_safe_color(c.link, 0.06),
        # 刻意**不设** `data_row_color`：`ft.DataRow.color` 恒为非 None（斑马纹），
        # Flutter 侧它的优先级高于表级的 `data_row_color`，HOVERED / PRESSED 会被
        # 整个遮住 —— 改前这里配了悬停色，却从未生效过（死配置）。行底的
        # 斑马 / 悬停 / 当前行三种语义现在统一写在 `ft.DataRow.color` 的状态映射里，
        # 单一来源（见上方 data_rows 的组装）。
        bgcolor=_safe_color(
            getattr(c, "surface", c.code_bg),
            0.96,
        ),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        show_checkbox_column=False,
        fixed_top_rows=1,
        fixed_left_columns=0,
        min_width=_min_w,
    )

    container_bg = _safe_color(c.code_bg, 0.55)
    container_border = _safe_color(c.border, 0.08)

    # is_current_line 高亮
    # 顶部操作区紧凑：移除工具栏与表格间的固定间隔器（Column spacing=0 即贴合），
    # 外层垂直 padding 由 Spacing.LG(8) 收紧至 Spacing.SM(4)，整体顶部高度更紧凑。
    content = ft.Container(
        content=ft.Column(
            controls=[toolbar, table],
            spacing=0,
        ),
        width=float("inf"),
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.XS),
        bgcolor=container_bg,
        border_radius=Radius.XXL,
        border=ft.Border.all(1, container_border),
        shadow=card_shadow(Elevation.LOW, is_dark),
    )

    if is_current_line:
        content = ft.Container(
            content=content,
            border=ft.Border.all(
                2,
                _safe_color(c.link, 0.20),
            ),
            border_radius=Radius.XXXL,
            padding=ft.Padding.all(1),
        )

    return content
