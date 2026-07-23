# 表格彻底重构方案

## Context

当前表格编辑体验存在以下问题：
1. 表格编辑深度耦合 `active/draft` 段级编辑系统，逻辑复杂且易出 bug
2. 需要双击才能进入单元格编辑（用户期望单击即编辑）
3. 缺少行列增删、对齐方式设置等核心表格操作
4. Tab/Enter 导航逻辑分散在 editor.py 中，与段级编辑逻辑交织

参考代码块重构的成功经验（CodeEditor 独立岛屿模式），将表格重构为**自管理的独立可编辑岛屿**：TableView 内部管理编辑状态，通过回调与 editor.py 交互，不再使用 active/draft 系统。

## 核心设计：表格作为独立可编辑岛屿

### 架构对比

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| 编辑状态 | editor.py 的 active/draft/table_cell | TableView 内部 use_state |
| 单元格编辑 | 双击进入 | 单击进入 |
| Tab/Enter 导航 | editor.py `_table_move`/`_table_enter` | TableView 内部处理 |
| 行列操作 | 无 | 工具栏 + 右键菜单 |
| 对齐设置 | 无 | 列头右键菜单 |
| 按键保护 | active != None 时拦截 | table_focus_ref 守卫 |

## 实现步骤

### 1. 重写 `views/table_view.py`（核心）

**组件签名（精简，移除所有 active/draft 相关参数）：**

```python
@ft.component
def TableView(
    lines: list[Line],
    line_idx: int,              # 表格起始行索引
    content_width: float | None = None,
    clipboard_ref: ft.Ref | None = None,
    on_change_cell: Callable[[int, int, str], None] | None = None,   # (line_idx, cell_idx, value)
    on_table_op: Callable[[str, dict], None] | None = None,          # 结构操作
    on_table_focus: Callable[[], None] | None = None,
    on_table_blur: Callable[[], None] | None = None,
    is_current_line: bool = False,
):
```

**内部状态：**
- `edit_cell`: `(line_idx, col_idx) | None` — 当前编辑单元格
- `edit_draft`: `str` — 编辑中文本
- `field_ref`: `ft.use_ref(None)` — TextField 引用，用于 autofocus

**交互模型（Typora/Word 风格）：**
- **单击单元格** → 进入编辑模式（TextField 替换 Text，autofocus + 光标定位）
- **Tab** → 下一格（末格时调用 `on_table_op("add_row")` 新增行）
- **Shift+Tab** → 上一格
- **Enter** → 下一行同列（末行时新增行）
- **Escape** → 退出编辑
- **点击表格外部 / blur** → 提交并退出编辑

**工具栏（表格上方，参考代码块 header）：**
```
[表格图标] [N×M]  ...  [+ 行] [+ 列] [删行] [删列] [对齐 ▾]
```
- `+ 行`：在选中行下方新增空行（无选中则末尾新增）
- `+ 列`：在选中列右侧新增空列（无选中则末尾新增）
- `删行`：删除选中行（保护表头+分隔行，至少保留 1 数据行）
- `删列`：删除选中列（保护至少 1 列）
- `对齐 ▾`：下拉菜单 左/居中/右，作用于选中列

**右键菜单（ft.ContextMenu，遵循用户偏好）：**
- 单元格右键：上方插入行 / 下方插入行 / 删除行 / 左侧插入列 / 右侧插入列 / 删除列 / 对齐左/中/右
- 列头右键：左侧插入列 / 右侧插入列 / 删除列 / 对齐左/中/右

**DataTable2 配置：**
- `fixed_top_rows=1`（粘性表头）
- `show_checkbox_column=False`
- `data_row_height=48`、`heading_row_height=44`
- 奇偶行条纹、hover 高亮
- `min_width=content_width`（窄表格撑满，宽表格横向滚动）
- 单元格 TextField：`border=NONE`、`filled=True`、`dense=True`、光标色为主题 link 色

**内部回调（调用 editor.py）：**
- 单元格文本变化 → `on_change_cell(line_idx, col_idx, draft)`（原地更新，不重渲染）
- 结构操作 → `on_table_op(op_name, {params})`（触发重渲染）
- 聚焦 → `on_table_focus()`；失焦 → `on_table_blur()`

**内部导航逻辑（Tab/Enter/Escape）：**
- TableView 的 TextField `on_submit` 处理 Tab/Enter 导航
- 通过 `ft.use_ref` 持有 lines 引用，避免闭包过期
- 跨行导航需要知道行索引列表（从 `_parse_table_lines` 获取）

### 2. 修改 `views/editor.py`

**移除：**
- `table_cell` / `set_table_cell` state（L189）
- `table_selected_cell` / `set_table_selected_cell` state（L190）
- `_table_move` 函数（L1198-1236）
- `_table_tab` 函数（L1238-1239）
- `_table_enter` 函数（L1241-1266）
- `commit_active` 中 TABLE 分支（L422-427）
- `_draft_for` 中 TABLE 分支（L376-377）
- `_goto` 中 `table_cell_idx` 参数及 `is_new_table_cell` 逻辑（L445-457）
- `activate` 中 TABLE 分支（L480-487）
- `_on_key_down` 中 Tab/Enter 的 TABLE 分支（L2286-2295）
- BackSpace/Delete 中 `line.block_type == BlockType.TABLE` 守卫（保留，但改为 `_is_fence` 统一处理）
- TableView 调用中所有 active/draft 相关参数（L2130-2147）

**新增：**
- `table_focus_ref = ft.use_ref(None)` — 跟踪聚焦的表格（类似 code_focus_ref）
- `on_change_cell(li, cell_idx, value)` — 原地更新单元格文本（不触发 observable 重渲染）：
  ```python
  def on_change_cell(li, cell_idx, value):
      line = document.lines[li]
      cells = _table_cells(line)
      if cell_idx < len(cells):
          cells[cell_idx] = value
      new_raw = _join_row(cells)
      line.raw = new_raw
      if line.segments:
          line.segments[0].text = new_raw
          line.segments[0].raw = new_raw
      _maybe_push_draft_history()
      if not document.dirty:
          mark_dirty()
  ```
- `on_table_op(op, params)` — 结构操作（add_row / delete_row / add_col / delete_col / set_align），修改 document.lines 后 `document.lines = list(document.lines)` 触发重渲染
- `on_table_focus()` / `on_table_blur()` — 设置/清除 table_focus_ref
- 更新 TableView 调用，传入新的精简参数集

**on_table_op 实现要点：**
- `add_row`: 在指定行后插入 `| | | ...` 新行（`parser.parse_markdown` 解析为 Line），需要知道列数
- `delete_row`: 移除指定行（保护表头+分隔行，至少保留 1 数据行；若删除后无数据行，保留空数据行）
- `add_col`: 遍历表格所有行（含分隔行），在指定位置插入空单元格（分隔行插入 `---`）
- `delete_col`: 遍历所有行，移除指定列（保护至少 1 列）
- `set_align`: 修改分隔行对应列（`:---` / `---:` / `:---:`）
- 所有操作前 `_push_history()`，操作后 `mark_dirty()`

### 3. 修改 `state/actions.py`

- 新增 `table_focus_ref: ft.Ref` 字段到 `EditorActions` dataclass

### 4. 修改 `views/key_bindings.py`

- 在 `code_focus_ref` 守卫块旁新增 `table_focus_ref` 守卫：
  ```python
  if (
      actions is not None
      and getattr(actions, "table_focus_ref", None) is not None
      and actions.table_focus_ref.current is not None
  ):
      # 表格聚焦时：文本编辑键 + 剪贴板组合交由 TableView 内部 TextField 处理
      if not (e.ctrl or e.meta or e.alt):
          if norm in ("backspace", "delete", "enter", "tab", "home", "end",
                       "arrowleft", "arrowright", "arrowup", "arrowdown"):
              return
      if combo in ("ctrl+c", "ctrl+x", "ctrl+v", "ctrl+a"):
          return
  ```

### 5. 修改 `views/editor.py` 的 `_on_key_down`

- 移除 Tab/Enter 的 TABLE 分支
- 新增 table_focus_ref 守卫（表格聚焦时提前返回，类似 code 的 `if active is None: return` 逻辑）

## 关键约束

1. **保护表格结构完整性**：不能删除表头行、分隔行；不能删除最后一列；删除最后一个数据行时保留一个空数据行
2. **原地更新避免光标跳动**：`on_change_cell` 直接修改 line.raw 不触发 observable，与代码块的 `on_change_code` 模式一致
3. **结构操作触发重渲染**：行列增删、对齐修改通过 `document.lines = list(document.lines)` 触发
4. **撤销/重做合并**：`on_table_focus` 推基线快照，`on_table_blur` 标记 pending，单次聚焦会话合并为一步
5. **ft.ContextMenu**：右键菜单使用 `ft.ContextMenu`（用户偏好，非 `PopupMenuButton`）
6. **Flet 版本兼容**：Button 用位置参数传 label，不用 `text=` 参数

## 验证方案

1. `python -m py_compile views/table_view.py views/editor.py views/key_bindings.py state/actions.py` 确认无语法错误
2. 启动应用，确认示例文档中的表格正确渲染（表头、对齐、条纹）
3. 测试单击单元格进入编辑 → 输入 → Tab 导航 → Enter 换行
4. 测试工具栏按钮：+行、+列、删行、删列、对齐设置
5. 测试右键菜单：插入行/列、删除行/列、对齐
6. 测试保护逻辑：不能删表头/分隔行/最后一列
7. 测试 Tab 在末格创建新行
8. 测试撤销/重做（Ctrl+Z/Y）
9. 测试表格聚焦时全局快捷键被跳过
10. 测试表格内容导出为 Markdown（保存后重新打开确认格式正确）
