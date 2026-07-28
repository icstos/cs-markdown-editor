# 文件对比重构为标签管理

## Context

当前文件对比是独立的全屏 `diff_mode` 状态，与多文档标签系统割裂——进入对比后无法同时查看/编辑其他文件标签，关闭对比靠 Esc 或头部关闭按钮，不符合桌面端"以标签管理文档"的直觉。

VSCode 的 diff editor 本身就是一个标签页（标题 `fileA ⟷ fileB`），可与其他文件标签并存、切换、关闭。本次重构把对比从"独占模式"改为"标签类型"，融入现有 tabs 系统，统一管理体验。

## 设计方案

### tab 数据结构扩展

为 tab 字典引入 `type` 字段，缺省 `"editor"`（向后兼容）：

```python
# 普通编辑标签（原有字段不变）
{"type": "editor", "document": Document, "file_path": str|None, "dirty": bool}

# 对比标签（新）
{"type": "diff", "left_path": str, "right_path": str,
 "left_doc": Document, "right_doc": Document,
 "left_dirty": bool, "right_dirty": bool}
```

### 统一辅助函数（减少散落分支）

在 main.py 引入两个辅助函数，所有需要"脏判断/路径列表"的函数统一调用，避免每处都写 `if type == "diff"`：

- `_tab_is_dirty(tab) -> bool`：diff 标签返回 `left_dirty or right_dirty`，否则 `tab["dirty"]`
- `_tab_paths(tab) -> list[str]`：diff 标签返回 `[left_path, right_path]`，否则 `[file_path]`（用于重命名同步、比较文本获取）

### 移除 diff_mode 状态

`diff_mode` / `set_diff_mode` / `diff_mode_ref` 全部移除。diff 数据改为从 `cur_tab` 读取。引入 `is_diff_tab`（当前标签是否 diff 类型）+ `is_diff_tab_ref` 镜像（供 `_get_active_nav`、KeyDispatcher 等闭包读取）。

## 修改清单

### main.py

**1. 状态与辅助**
- 移除 `diff_mode` / `set_diff_mode` / `diff_mode_ref`（L64-67）
- `cur_tab` 派生后新增 `is_diff_tab = cur_tab.get("type") == "diff"` + `is_diff_tab_ref`（L121-124 附近）
- 新增 `_tab_is_dirty` / `_tab_paths` 辅助函数（紧邻 `_doc_has_text`，L144 附近）

**2. 创建对比标签**
- `_compare_with_selected`（L472-497）：不再 `set_diff_mode(...)`，改为追加一个 `type:"diff"` 的 tab 并激活（复用空白标签逻辑同 `_open_file_by_path`），`set_diff_active_pane(0)`

**3. 编辑器区域渲染**（L1141 `if diff_mode:` 分支）
- 改为 `if is_diff_tab:`，diff 数据从 `cur_tab` 读（`left_doc`/`right_doc`/`left_path`/`right_path`）
- diff 编辑器的 `on_dirty_change` 改为按侧更新：左编辑器 `lambda d: _on_diff_dirty_change(0, d)`，右侧 `(1, d)`
- diff 编辑器的 key 改为 `f"diff-left-{active_index}"` / `f"diff-right-{active_index}"`，支持多 diff 标签独立实例
- 头部关闭按钮：`on_click` 改为 `close_tab(active_index)`（关闭当前标签）

**4. 新增 `_on_diff_dirty_change(side, d)`**
- 更新当前 diff 标签的 `left_dirty`/`right_dirty`（通过 `_update_active`）
- 触发 autosave（若启用）

**5. 脏状态/保存/关闭流程**（用辅助函数统一）
- `on_dirty_change`（L900）：仅 editor 标签走原逻辑（diff 标签由 `_on_diff_dirty_change` 处理，不触发此回调——diff 编辑器传专属回调）
- `save_doc`（L935）：diff 标签分支——保存两侧文档到各自 `left_path`/`right_path`，清两侧 dirty
- `_is_blank_untitled`（L156）：diff 标签直接返回 `False`
- `_do_close_many`（L183）/`_request_close`（L209）/`_save_and_close_pending`（L295）：`ts[i]["dirty"]` 改为 `_tab_is_dirty(ts[i])`
- `_autosave_enabled_for`（L629）/`_schedule_autosave`（L632）：diff 标签用 `_tab_is_dirty` + 任一侧有路径

**6. 路径相关同步**
- `_update_tab_for_renamed_file`（L358）：遍历 tab 时用 `_tab_paths(tab)` 匹配 old_path，diff 标签更新 left_path/right_path 及对应 doc.file_path
- `_get_text_for_compare`（L458）：遍历 tab 用 `_tab_paths(tab)` 匹配，diff 标签返回对应侧 serialize

**7. 其他**
- `export_doc`（L978）：diff 标签导出活跃侧（`diff_active_pane` 决定 left/right）的文档
- `_get_active_nav`（L794）/ KeyDispatcher（L929）：`diff_mode_ref.current is not None` 改为 `is_diff_tab_ref.current`
- footer（L1341）：`if diff_mode:` 改为 `if is_diff_tab:`，数据从 `cur_tab` 读
- Escape（L1055）：移除"退出 diff_mode"特殊分支；diff 标签下 Escape 无特殊行为（用 Ctrl+W 关标签）

### views/tab_bar.py

**TabBar 元数据扩展**：tabs 列表项支持 diff 类型
```python
# editor: {"type":"editor", "file_path":..., "dirty":...}
# diff:   {"type":"diff", "left_path":..., "right_path":..., "dirty": left_dirty or right_dirty}
```

**渲染**：
- diff 标签标题：`ft.Icon(COMPARE_ARROWS) + "left_name ⟷ right_name"`（文件名省略截断）
- dirty 标记：用传入的统一 `dirty` 字段
- 右键菜单：diff 标签显示简化菜单——「交换左右侧」「关闭」「关闭其他」「关闭全部」（无打开/复制路径/重命名等单文件操作）
- 新增 `on_context_action` 的 `"swap_diff"` action（交换左右侧文档与路径）

**main.py `_on_tab_context_action`**：新增 `"swap_diff"` 分支——交换当前 diff 标签的 left/right（doc/path/dirty），刷新 diff

## 不变项

- 同步滚动逻辑（`_sync_diff_scroll_to` / `_on_diff_left_scroll` 等）完全保留，仅当 is_diff_tab 时由 diff 编辑器的 `on_scroll_change` 触发
- diff 配色、diff_marks/diff_gaps 计算、行级着色均不变
- `compare_source` 状态保留（选择以进行比较的源）
- 多 diff 标签并存：文档内容在 tab 的 left_doc/right_doc 中保留；切换 diff 标签时光标重置（编辑器重新挂载），可接受

## 验证

1. `python -m py_compile main.py views/tab_bar.py` 语法检查
2. `python -c "import main"` 运行时导入
3. `python main.py` 启动，无报错
4. 交互验证：
   - 右键文件「选择以进行比较」→ 右键另一文件「与已选项目进行比较」→ 生成 diff 标签，标题显示双文件名
   - diff 标签可与其他文件标签并存、切换、关闭
   - diff 标签内双编辑器编辑、实时 diff 高亮、同步滚动正常
   - 关闭脏 diff 标签弹确认；保存两侧文件后 dirty 清除
   - diff 标签右键「交换左右侧」生效
   - Ctrl+W / 关闭按钮关闭 diff 标签
