# 优化表格支持：创建入口 + 修复 Tab 路由 Bug + 方向键导航

## Context（背景）

当前表格的**渲染与编辑已相当完善**：基于 DataTable2（`views/table_view.py`）实现了单元格编辑、Tab/Enter 导航、增删行列、右键菜单（ft.ContextMenu）、主题适配、斑马纹/hover/边框/圆角/阴影。视觉层面无需改动。

但存在两个明显缺口：

1. **无创建表格入口**：`set_block` 无 TABLE 分支、`shortcuts.py` 无 `format_table` 动作、`toolbar.py` 无表格按钮、`key_bindings.py` 无分发。用户无法通过快捷键或工具栏创建表格。
2. **`table_focus_ref.current` 从未被赋值**（已存在 Bug）：在 `views/editor.py:225` 声明 `ft.use_ref(None)`，仅在 2481 传出、2754 检查，但全代码库无任何 `.current =` 赋值。导致：
   - `editor.py:2754` 的 Tab/Escape 路由守卫恒为 False → **现有 Tab 导航实为死代码**
   - `key_bindings.py:155` `_native_field_focused` 对表格恒返回 False → 编辑表格时全局快捷键未正确屏蔽

本方案在补齐创建入口的同时修复此 Bug，让 Tab/Escape/Arrow 路由真正生效，并增加方向键单元格导航（Excel 行为）。

## 实现步骤

### 1. `core/actions.py` — 新增 format_table 接口字段
在 `format_task` 字段（L85）之后增加：
```python
format_table: Callable[[], None]  # Ctrl+Alt+T：当前行转为 2×2 表格
```
必填字段（与 `format_task` 同级），缺失立即报错。全代码库仅 `editor.py:2442` 一处用关键字参数构造 EditorActions，安全。

### 2. `views/editor.py` — set_block TABLE 分支 + format_table 函数 + table_focus_li state + 方向键路由

**(a) table_focus_li state（修复 Bug）** — 在 `math_focus_li` 声明（L230-232）旁新增：
```python
table_focus_li, set_table_focus_li = ft.use_state(None)
table_focus_ref.current = table_focus_li  # 镜像 state 到 ref，修复从未赋值 Bug
```
`on_table_blur`（L1581）增加 `set_table_focus_li(None)`；`on_table_focus` 不设 state（避免 cell 切换反复触发）。`set_table_focus_li(li)` 仅在 set_block(TABLE) 后调用。

**(b) set_block TABLE 分支** — 在 `elif block_type == BlockType.HR:`（L1102）之前插入：
- 取 `content = _inline_content(line)`（L92）作为表头第一列
- 构造 3 个 Line 对象（2 列 × 2 行，默认左对齐）：
  - header: `_join_row([content, ""])` → `| {content} |  |`
  - sep: `_join_row(["---", "---"])` → `| --- | --- |`
  - data: `_join_row(["", ""])` → `|  |  |`
  - 每个 Line 用 `Line(block_type=BlockType.TABLE, raw=xxx)` + `segments=[Segment(SegType.TEXT, xxx, xxx)]`（与 `on_table_op` 的 `_rebuild_table_line` L1513-1516 一致）
- 切片替换：`document.lines = lines[:li] + [header, sep, data] + lines[li+1:]`
- **early return**（跳过函数末尾的 `_reparse_atomic` + `_set_cursor` 重定位，TABLE 多行结构不适用）
- 焦点处理（参考 MATH 分支 L1111-1118）：`set_cursor_line(li)` → `set_cursor_li(None)` → `set_table_focus_li(li)`
- 边界守卫：当前行已是 TABLE 时静默返回

**(c) format_table 函数** — 紧跟 `format_task`（L1356-1362）：
```python
def format_table():
    """Ctrl+Alt+T：当前行转为 2×2 表格。复用 set_block 的 TABLE 分支。"""
    set_block(BlockType.TABLE)
```

**(d) EditorActions 实例化**（L2478-2479 附近）增加 `format_table=format_table`

**(e) _on_key_down 方向键路由**（L2754-2760 表格路由块）在 Escape 分支后增加：
```python
nk = key.replace(" ", "")  # Flet 方向键可能返回 "Arrow Up" 带空格
if nk == "arrowup":
    table_nav_ref.current("up"); return
if nk == "arrowdown":
    table_nav_ref.current("down"); return
```

**(f) TableView 调用**（L2604-2626）增加 `auto_focus_li=table_focus_li`

### 3. `views/table_view.py` — auto_focus prop + 方向键导航

**(a) 函数签名**（L131-159）增加 `auto_focus_li: int | None = None`

**(b) auto_focus use_effect** — 在 `_resolve_pending_nav` use_effect（L339）旁新增：
```python
def _auto_focus_first_cell():
    if auto_focus_li is not None and auto_focus_li == line_idx and edit_cell is None:
        _start_edit(header_idx, 0)
ft.use_effect(_auto_focus_first_cell, [auto_focus_li])
```

**(c) _move_up 函数** — 紧跟 `_move_down`（L267-282），首行不动作（Excel 行为）

**(d) ArrowDown 末行不新增行** — `_move_down` 当前末行会 `on_table_op("add_row")`。方向键 ArrowDown 不应新增行。在 `_navigate` 中新增 `"down"` 分支调用不新增行的下行逻辑（提取 `_move_down_no_add` 或传 flag），**仅 Enter 保留末行新增**

**(e) _navigate 扩展**（L319-323）增加 `"up"`/`"down"` 分支

### 4. `services/shortcuts.py` — 注册 format_table 快捷键
- **DEFAULT_SHORTCUTS**：browse 和 edit 层均增加 `"format_table": "ctrl+alt+t"`（两层通用，与 format_math_block 一致）
- **ACTION_REGISTRY**（L128 format_hr 附近）增加：
```python
ActionDef("format_table", "表格", "both", "格式",
          "将当前行切换为 2×2 表格（1 表头 + 1 数据行），并进入表格编辑态。",
          {"browse": "ctrl+alt+t", "edit": "ctrl+alt+t"}),
```
快捷键选 `Ctrl+Alt+T`：T for Table 直观；Alt 与 `Alt+C`（任务切换）形成 Alt 系列认知；避开已占用的 `Ctrl+Shift+T`（任务列表）、`Ctrl+\`（拆分）。设置面板会自动渲染此动作行。

### 5. `views/toolbar.py` — 增加表格按钮
- 函数签名（L80 `on_math_block` 后）增加 `on_table: Callable[[], None]`
- 按钮 Row（L117 公式块按钮后）增加：`_btn(ft.Icons.TABLE_CHART, _tip("表格", "format_table"), on_table)`
- tooltip 用 `_tip` 动态读取自定义键位（与 format_task 一致）

### 6. `views/key_bindings.py` — 分发 format_table
在 `_handle_shortcuts` 的 `format_task` 分支（L453-456）后插入：
```python
if matches(combo, shortcuts.get("format_table", "ctrl+alt+t")):
    if actions is not None and not self._native_field_focused(actions):
        actions.format_table()
    return
```
两层通用，`_native_field_focused` 守卫（修复 Bug 后正确识别表格聚焦）确保在表格内不重复触发。

### 7. `views/editor.py` — Toolbar 调用传 on_table
在 `Toolbar(...)` 调用（L2712 `on_task` 附近）增加 `on_table=lambda: set_block(BlockType.TABLE)`

## 实施顺序
1. core/actions.py（接口先行）
2. editor.py（set_block TABLE + format_table + table_focus_li state + _on_key_down 路由 + Toolbar 调用 + EditorActions 实例化）
3. table_view.py（auto_focus prop + use_effect + _move_up + _navigate 扩展）
4. toolbar.py（按钮）
5. shortcuts.py（注册）
6. key_bindings.py（分发）

## 验证方法
1. **语法校验**：对 6 个修改文件运行 `python -m py_compile`
2. **测试**：`python -m pytest tests/ -q` 确保无回归
3. **手动验证**：
   - 普通段落按工具栏表格按钮 / Ctrl+Alt+T → 生成 2×2 表格，光标自动落入表头第一格
   - 把有内容的行转为表格 → 表头第一列含原文本
   - Tab/Shift+Tab 在单元格间导航（修复 Bug 后路由真正生效）
   - ArrowUp/Down 同列上下移动（首行 Up / 末行 Down 不动作）
   - Enter 末行新增行；Escape 退出表格编辑
   - 表格内按 Ctrl+S → 触发保存（验证 _native_field_focused 正确屏蔽全局快捷键）
   - 撤销 Ctrl+Z → 恢复原段落
   - 设置面板 → 看到"表格"动作行，可改键
