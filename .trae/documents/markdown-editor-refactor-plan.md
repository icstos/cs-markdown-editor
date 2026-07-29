# cs-markdown-editor 架构优化与现代化重构计划

## Context（背景与目标）

项目是一个基于 Flet 0.86.2 的 Typora 式段级所见即所得 Markdown 编辑器（Python 3.12+）。整体分层已清晰（`models → utils/styles → parser → core → services → config → views → main.py`，无循环依赖），Python 3.12 特性使用较好。但存在两个核心架构债务：

1. **`views/editor.py`（约 2780 行）**：`MarkdownEditor` 单 `@ft.component` 内集中 80+ 闭包函数，共享 `cursor_li/cursor_off/document/*_ref/set_*`，是维护与扩展的瓶颈。
2. **`main.py`（约 1500 行）**：`App` 单 `@ft.component` 内集中 50+ 闭包函数（标签/文件/diff/设置/快捷键/自动保存）。

用户决策：**全量拆分为包 + 全面补测试 + 全方位性能提升**（IME 延迟、光标导航/选区、大文档）。硬约束：**保持所有用户可见行为不变**。

### 不可破坏的硬约束（来自 `重构架构说明书.md` / `core/actions.py` docstring）

- `cursor_ref` 必须是 `ft.use_ref`（非 state），避免重渲染打断 IME
- 透明 cursor TextField **不设 value 属性**；value 清空由 `use_effect([clear_value_seq])` 异步执行
- `nav_seq` 仅在撤销/重做时递增（同行输入不递增以保 IME 组合态）
- `EditorActions` 必填字段构造期校验
- `cursor_li=None` 表浏览态；`cursor_off` 为行级 raw 偏移 0..len(line.raw)
- 所有 `use_*` hook 必须在组件函数体顶层顺序调用（Flet 0.86 约束）
- IME 热路径必须用 `_reparse_atomic = parser.reparse_line_atomic`（仅 1 次 observable 通知），**禁止**直接调 `reparse_line`（2-7 次通知）

## 总体策略

按"测试网 → 纯函数抽取 → 独立模块 → 大文件拆分 → 性能优化 → 收尾"六阶段推进。每阶段独立可验证、可回滚。**阶段 0+1 是大文件拆分的不可压缩前置**（没有测试网就直接拆分 = 盲飞）。

---

## 阶段 0：测试安全网（前置必做）

为所有可独立测试的纯逻辑补单测，**不触碰 editor.py / main.py 主结构**。

### 新增测试文件
- `tests/test_history.py` — `EditHistory.push` 去重 / `pop_undo` / `pop_redo` / 容量限制 / `LineEditSnapshot` vs `EditorSnapshot` 不相等判定
- `tests/test_cursor.py` — `CursorState.reset` / `extend` / `normalize`
- `tests/test_shortcuts.py` — `ShortcutManager.get` / `first_conflict_target` / `inline_format_combos` / `matches` / `normalize`
- `tests/test_file_io.py` — `read_text` / `write_text` + `file_ops` 新建/重命名/删除/副本（用 `tmp_path`）
- `tests/test_utils_helpers.py` — `segment_helpers` / `table_helpers` / `file_helpers` 全覆盖
- `tests/test_key_bindings.py` — `_combo` / `_extract_printable_char` / `KeyDispatcher.handle`（注入 mock `actions_ref` + `page_ref` + `shortcut_mgr`，断言分发路径）
- `tests/test_text_layout.py` — `measure_text_width` 缓存命中 / `image_fit_size` 边界

### 验证
`pytest` 全绿；纯逻辑覆盖率 > 80%。

---

## 阶段 1：现代化清扫 + 纯函数抽取

### 1.1 现代化清扫（零行为变更）

- 删除 5 处冗余 `from __future__ import annotations`：`views/table_view.py`、`views/pixel_layout.py`、`tests/test_task_smoke.py`、`tests/test_editor_helpers.py`、`tests/test_table_smoke.py`
- 统一重复定义：`views/editor.py:93 _ALIGN_RE_TABLE` 与 `views/table_view.py:36 _ALIGN_RE` → 统一用 `utils/table_helpers.ALIGN_RE`；`views/editor.py:91 _FENCE_BLOCKS` 与 `views/pixel_layout.py:52 _FENCE_BLOCK_TYPES` → 提取到 `utils/segment_helpers.FENCE_BLOCK_TYPES` 单一来源
- `views/editor.py` 内联 `re.match` 8 处（行 126/128/130/743/1033/1040/1043/1452）→ 模块级 `re.compile` 常量
- `@dataclass` 加 `slots=True`：`core/actions.py:EditorActions`、`views/pixel_layout.py:LineLayout`（`models/document.py` 的 `@ft.observable` dataclass **不动**，需 `__dict__`）
- 参数化约 20 处裸 `tuple/list/dict` 注解
- 新增 PEP 695 `type` 别名：`core/actions.py` 加 `type OutwardSel = tuple[int, int, int, int] | None` 与 `type ScrollState = tuple[float, float, float]`；`config/settings.py` 加 `type Settings = dict[str, Any]`；`main.py` 加 `type Tab = dict[str, Any]`
- 4 处 `re.match + if m` 模式补海象运算符 `:=`
- 扩展 `pyproject.toml` ruff lint 规则：加 `B`（bugbear）、`C4`、`SIM`、`RUF`，跑 `ruff check --fix`

### 1.2 从闭包抽取纯函数（在拆分前完成，配单测）

闭包还在同文件时抽取风险最低，且能立刻补测试验证行为不变。模式：**决策与执行分离**——纯函数接收参数返回结果，闭包保留执行（调 `set_*` / `ref.current = ...`）。

迁移到 `views/_editor_helpers.py`（已有 6 个纯函数的同层）：
- `_rebuild_list_prefix(level, body, block_type, task, checked, restart_num)` ← editor.py 约 1024-1045 行
- `_char_kind(ch)` + `_select_word_bounds(raw, off) -> (start, end)` ← `_select_word_at` 约 1992-2076 行
- `_build_highlight_map(lines, outward_sel)` ← 约 2534-2554 行（改为接收参数）
- `_step_left(lines, li, off)` / `_step_right(lines, li, off)` ← 约 1849-1870 行（改为接收 `lines`）
- `_offset_prefix_build(heights)` ← `_estimate_line_offset` 内循环构造
- `make_snapshot(cursor_li, cursor_off, raw_mode, raw_draft, document) -> EditorSnapshot` ← `_make_snapshot`
- `compute_delete_result(lines, start_li, start_off, end_li, end_off)` ← `_delete_raw_range` 决策部分

迁移到 `parser/selection.py`：
- `_extract_outward_text(lines, a_li, a_off, b_li, b_off)` ← 约 2131-2148 行

迁移到 `app/_tab_helpers.py`（新建）：
- `_doc_has_text` / `_is_blank_untitled` / `_tab_display_name` / `_tab_is_dirty` / `_tab_paths` ← main.py 纯函数

### 验证
`pytest` 全绿；`ruff check` 无新告警；手动冒烟（IME 输入 / 光标导航 / 向外选区 / 撤销重做 / 缩进 / 表格）行为不变。

---

## 阶段 2：抽取独立模块（中等风险切片）

### 2.1 `app/diff_scroll_sync.py`（新建）

把 main.py 约 894-963 行的 4-ref + 60ms 异步追赶状态机封装为 `DiffScrollSync` 类：
- 实例属性：`_syncing` / `_direction` / `_pending_target` / `_pending_offset`（原为 `ft.use_ref`，类内改为普通属性）
- 构造参数：`page_ref`、`diff_nav_left`、`diff_nav_right`
- 方法：`sync_to(side, offset)` / `_after_sync()` / `on_left_scroll(offset, max_scroll, viewport_h)` / `on_right_scroll(...)`
- main.py 用 `ft.use_memo(lambda: DiffScrollSync(page_ref, diff_nav_left, diff_nav_right), [])` 实例化一次
- 单测：注入 fake `page_ref`（记录 `run_task` 协程，用 `asyncio` 测试工具推进 60ms）+ fake nav（记录 `scroll_to_offset` 调用序列），断言单次同步 / syncing 期间被动侧忽略 / 主动侧累积 pending / 追赶 / direction 切换

### 2.2 `app/autosave.py`（新建）

迁移 `_autosave_enabled_for` / `_schedule_autosave`（main.py 约 689-720 行）为函数，接收 ctx 参数。

### 验证
diff 对比模式滚动同步行为与原版一致；自动保存行为不变。

---

## 阶段 3：拆分 `views/editor.py` → `views/editor/` 包

前置：阶段 0 + 阶段 1 完成。

### 架构模式：EditorContext + 工厂

`EditorContext` 是**双区 dataclass，每次渲染整体重建**：
- 稳定区：所有 `use_ref` 对象 + 所有 `set_*` setter（身份跨渲染不变）+ `document`（@ft.observable 引用稳定）
- 快照区：当次渲染的 state 值（`cursor_li` / `cursor_off` / `outward_sel` / `raw_mode` / `viewport_w` 等）

工厂签名统一：`build_xxx(ctx: EditorContext) -> dict[str, Callable]`。工厂内**禁止任何 `use_*` hook**（硬性规则）。跨工厂调用通过 ctx 装配槽（普通属性赋值，非 hook）。

### 包结构

```
views/editor/
├── __init__.py          # MarkdownEditor 组件：hooks → 镜像 → ctx → 工厂调用 → _cb_ref 装配 → 稳定包装器 → use_effect → EditorActions 写入 → 渲染
├── _context.py          # EditorContext dataclass（非 frozen）
├── _helpers.py          # _make_stable_cb / _noop / _build_diff_gap / _is_fence / _line_raw / _inline_content / _next_line_raw / _FENCE_BLOCKS / 模块级 re.compile
├── _history.py          # build_history(ctx): _make_snapshot / _push_history / _push_line_edit / _restore_snapshot / undo / redo
├── _cursor.py           # build_cursor(ctx): _set_cursor / _end_input_session / _cursor_base / _on_tap_line / handle_char_input / handle_paste / backspace_core / delete_core / on_submit  [IME 核心组，紧耦合不拆散]
├── _navigation.py       # build_navigation(ctx): move_left/right/home/end/up/down/doc_start/end / _move_vline / page_up/down / _ensure_visible / _safe_scroll_to
├── _indent.py           # build_indent(ctx): indent_or_outdent
├── _blocks.py           # build_blocks(ctx): set_block / new_line_after / toggle_task / toggle_task_at_cursor / format_task / format_table / change_lang
├── _inline_format.py    # build_inline_format(ctx): apply_inline_format / _apply_outward_wrap / handle_outward_type_char
├── _outward_selection.py# build_outward(ctx): _step_left/right/up/down / _step_vline / _start_outward_from_point / _extend_outward* / _select_word_at / on_extend_outward / _delete_raw_range / handle_outward_cut/copy/delete / select_all / cut_current_line
├── _clipboard.py        # build_clipboard(ctx): compute_markdown_from_text / handle_delete_selection / handle_cut / on_selection_area_change / apply_inline_format_to_selection
├── _fence_handlers.py   # build_fence(ctx): on_change_code / on_code_focus / on_code_blur / on_change_math / on_math_focus / on_math_blur / on_change_cell / on_table_op / on_table_focus / on_table_blur
├── _scroll.py           # build_scroll(ctx): _on_scroll / _get_scroll_state / _scroll_to_offset / _on_content_resize / on_line_size_change / _estimate_line_* / _hit_test_* / _get_layout_cache / jump_to
├── _raw_mode.py         # build_raw_mode(ctx): toggle_raw / toggle_focus_mode / on_blur / on_cursor_focus / _on_raw_change
├── _actions.py          # build_actions(ctx, callbacks) -> EditorActions  [装配 37 字段]
└── _render.py           # build_line_controls(ctx, stable_cbs, ...) -> list[ft.Control]  [行循环 + 表格合并 + diff 间隙]
```

### 关键实施约束

1. **hook 仅在 `__init__.py` 顶层**：所有 `use_state` / `use_ref` / `use_effect` / `use_memo` 调用顺序与原 editor.py 一致
2. **state→ref 镜像集中在 ctx 构造前**：`outward_sel_ref.current = outward_sel` / `table_focus_ref.current = table_focus_li` / `math_focus_ref.current = math_focus_li` 等，在所有 hook 之后、ctx 构造之前单一编排点
3. **稳定回调包装器留在 `__init__.py`**：`_cb_ref.current = {**cursor_cbs, **nav_cbs, ...}` → `_make_stable_cb(ref, key)` + `use_memo(lambda: {...}, [])` 产出 `_stable_cbs`，传给 LineView/TableView/ToolArea
4. **`_STABLE_CB_KEYS` 改为从工厂输出 keys 自动派生**（单一真相源，避免漂移），或加单测断言"工厂输出 keys ⊇ STABLE_KEYS"
5. **每个工厂模块顶部统一 `_reparse_atomic = parser.reparse_line_atomic` 别名**，函数体一律用别名
6. **IME 核心组（`_cursor.py`）不拆散**：`_set_cursor` / `_end_input_session` / `handle_char_input` / `handle_paste` 互相调用且都触及 `input_session_ref` / `cursor_ref`，必须同模块
7. **跨工厂调用走 ctx 装配槽**：例如 `_navigation._move_vline` 需调 `_cursor._cursor_base` → 在 `__init__.py` 装配段 `ctx.cursor_base = cursor_cbs["cursor_base"]` 后再调 `build_navigation(ctx)`
8. **`main.py` 导入路径不变**：`from views.editor import MarkdownEditor` 仍可用（package `__init__.py` 暴露）

### 验证
全量回归测试 + 手动冒烟清单：IME 中文/英文/混合输入、光标所有方向键 + Home/End/PageUp/Down、向外选区（拖拽/Shift+Click/Shift+方向键）、Ctrl+C/X/V、Tab/Shift+Tab 缩进、Ctrl+0~6 标题、Ctrl+Shift+T 任务、Ctrl+Alt+T 表格、Ctrl+Z/Y、Ctrl+/ 原文模式、代码块/公式块/表格岛屿编辑、链接 Ctrl+Click。

---

## 阶段 4：拆分 `main.py` → `app/` 包

前置：阶段 3 完成。

### 架构模式：AppContext + 控制器

同阶段 3 的 ctx 模式。控制器接收 `AppContext`，返回 `dict[str, Callable]`。`app_component.py` 是 App 组件，调用所有 hooks、构造 ctx、装配控制器、组装渲染。

### 打破 `shortcut_mgr ↔ update_setting` 前向引用循环

main.py 约 667 行 `ShortcutManager(settings, lambda k, v: update_setting(k, v))` 用 lambda 捕获 `update_setting`。控制器抽取后用 **holder-ref 模式**：

```python
update_setting_ref = ft.use_ref(None)  # App 顶层
shortcut_mgr = ShortcutManager(settings, lambda k, v: update_setting_ref.current(k, v))
# 控制器装配后：
update_setting_ref.current = settings_cbs["update_setting"]
```

### 包结构

```
app/
├── __init__.py              # 重新导出 App + main_sync + main
├── app_component.py         # App 组件：hooks → 镜像 → ctx → 控制器装配 → KeyDispatcher → render/ 组装
├── _context.py              # AppContext dataclass
├── _tab_helpers.py          # 纯函数（阶段 1 已迁入）
├── tab_management.py        # select_tab / _cycle_tab / _do_close_many / _request_close / close_tab / new_doc / _save_and_close_pending / _close_without_save / _cancel_close / _update_active / _update_tab / _cur_tab
├── file_io_ops.py           # open_doc / save_doc / export_doc / _open_file_by_path / _push_recent_file / _copy_path / _update_tab_for_renamed_file / _close_tabs_for_path
├── file_dialogs.py          # _on_file_dialog_confirm / _open_input_dialog / _open_delete_dialog + 合并 _on_tab_context_action 与 _on_sidebar_context_action 重复分发逻辑
├── diff_controller.py       # _get_text_for_compare / _select_for_compare / _compare_with_selected / _set_diff_active_pane / _on_diff_dirty_change
├── diff_scroll_sync.py      # DiffScrollSync 类（阶段 2 已建）
├── settings_controller.py   # update_setting / reset_settings / reset_shortcuts / export_shortcuts / import_shortcuts / toggle_theme / open_settings / close_settings / select_settings_tab / _on_capture / _on_cancel_capture / toggle_sidebar / toggle_word_wrap / change_sidebar_panel / change_sidebar_width
├── autosave.py              # _autosave_enabled_for / _schedule_autosave（阶段 2 已建）
├── split_editor.py          # toggle_split_editor / _set_active_pane
├── focus_router.py          # _get_active_nav / jump_to_line / on_dirty_change
├── keyboard.py              # KeyDispatcher 构造 + _bind_keyboard + _handler + _cleanup + dispatcher_ref 管理
└── render/
    ├── __init__.py
    ├── sidebar_view.py      # sidebar_container 构造
    ├── editor_area.py       # editor_area 三路条件渲染（diff / split / single）
    ├── footer_view.py       # StatusBar 条件渲染
    ├── tab_bar_view.py      # TabBar 构造
    └── dialogs_view.py      # settings_view / confirm_dialog / file_dialog_view + 顶层 Stack 组装
```

### 关键实施约束

1. 所有 `use_state` / `use_ref` / `use_effect` 保留在 `app_component.py` 顶层
2. `tabs_ref` / `active_index_ref` 镜像约定保留（多个 set_tabs 后立即 `tabs_ref.current = new_tabs`，autosave 等异步读取者依赖）
3. `session` 与 tab 创建/切换/关闭强绑定（8 处 `set_session(session + 1)`）不能漏
4. `shortcut_mgr` 每次渲染重建是设计（无状态读取器），保持
5. 渲染期副作用（`page.theme_mode` 同步写入、`dispatcher_ref.current = dispatcher`）保留在 `app_component.py` 渲染期，不挪到 `use_effect`
6. `save_doc ↔ _push_recent_file ↔ update_setting` 链通过 ctx 装配槽解耦，**控制器间不相互 import**

### 验证
全量回归 + 手动冒烟：新建/打开/保存/另存、标签切换/关闭/批量关闭、对比模式（选择比较 + 双向滚动同步）、拆分编辑器（Ctrl+\）、设置页所有项、快捷键捕获/重置/导入/导出、自动保存、最近文件、侧边栏三面板（文件/大纲/搜索）。

---

## 阶段 5：性能优化

按"影响/风险比"排序，行为稳定后做（需稳定基线测量）。

### P0（最大瓶颈）
- **`line_controls` 虚拟化**（`views/editor/_render.py`）：当前每次渲染 O(n) 构造所有 LineView 控件。改为：小文档（<500 行）保持 `Column(scroll=AUTO)` 精确稳定；大文档切到 `ListView(build_controls_on_demand=True)`，配合现有 `_offset_prefix_ref` 前缀和缓存做 `scroll_to_line` 精确补偿，用现有 `_safe_scroll_to` 两步滚动（先估算触发构建，等 150ms 后用实测高度补偿）吸收 `maxScrollExtent` 抖动。**这是大文档场景唯一根本性优化。**

### P1（已就位，验证保持）
- 稳定回调（`_make_stable_cb` + `use_memo([])`）
- `_estimate_line_offset` 前缀和缓存（O(1) 查表）
- `reparse_line_atomic`（每次按键 1 次通知）
- `LineLayoutCache`（Y 二分命中 O(log n)）
- `_char_width_cache`（高频 X 偏移 O(1)）

### P2（中等收益）
- `_toc_sig` 改增量哈希：当前 `"|".join(...)` 全标题 O(n) 拼接每次渲染都跑，改 `use_memo` 触发条件为标题数 + 末标题 raw 长度签名
- `ShortcutManager` 用 `use_memo([shortcuts_sig])` 缓存（当前每次渲染重建）
- `_estimate_line_height` fallback 路径的 `measure_text_width` 缓存命中验证

### P3（小收益）
- editor.py 内联 `re.match` 已在阶段 1 编译为模块级常量
- `apply_inline_format_to_selection` Phase 2 stub 补全（见阶段 6）

### 验证
用 `_typing_test.py` 对比优化前后打字延迟；手动测试 1k+ 行文档滚动/编辑/光标导航流畅度；IME 中文连续输入无翻倍无延迟。

---

## 阶段 6：收尾

### 6.1 补全 Phase 2 stubs
- `handle_delete_selection`（`_clipboard.py`）：实现 raw 偏移映射的多行选区删除
- `apply_inline_format_to_selection`（`_clipboard.py`）：实现 SelectionArea 选区的行内格式包裹

### 6.2 文档同步
- 更新 `README.md` 架构图与模块说明
- 更新 `重构架构说明书.md` / `优化说明.md` 记录本轮包化拆分
- 在 `core/actions.py` docstring 补充 EditorContext 模式说明

### 6.3 视觉/交互微调（保持功能不变前提下）
- 验证用户偏好项保持：ft.ContextMenu 右键、置顶项背景色、段级编辑、光标自然导航、checkbox 无左横线、行内代码/公式选区变色、无序标记 `•`、代码块明暗主题、编辑区高度匹配、预览模式隐藏语法、大纲同级左对齐 + 彩色细竖线、Ctrl+1-6 光标不动、Ctrl+X 无选区切行、引用块左侧浅灰、内容自适应宽度

---

## 验证策略（贯穿所有阶段）

1. **每阶段结束**：`pytest` 全绿 + `ruff check` 无新告警 + 手动冒烟对应清单
2. **阶段 3/4 拆分时**：用 `git stash` + 临时插桩对比拆分前后同一操作序列的 ref 状态快照
3. **阶段 5 性能**：`_typing_test.py` 基线对比 + 大文档（1k+ 行）手动流畅度
4. **回归守护**：每个阶段一个独立 commit，便于 bisect 定位行为漂移

## Top 风险与缓解

| 风险 | 缓解 |
|------|------|
| hook 调用顺序破坏（最高危） | 硬性规则：所有 `use_*` 仅在 `__init__.py` / `app_component.py` 顶层，工厂/控制器内禁用 |
| 稳定回调 key 集合漂移导致 memo 失效 | `_STABLE_CB_KEYS` 从工厂输出 keys 自动派生（单一真相源），或加单测断言 |
| `shortcut_mgr` lambda 前向引用断裂 | holder-ref 模式（阶段 4）；拆分前先写回归测试 |
| state→ref 镜像时序错位 | 镜像集中在 ctx 构造前单一编排点；拆分后用插桩对比 ref 状态 |
| `reparse_line_atomic` 误用为 `reparse_line` | 各工厂顶部统一 `_reparse_atomic` 别名；代码审查 grep `reparse_line\b` 确保无直接调用 |

## 关键文件

- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\views\editor.py`（拆分源）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\main.py`（拆分源）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\core\actions.py`（EditorActions，37 字段）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\core\history.py`（已用 PEP 695 `type` 别名）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\views\key_bindings.py`（KeyDispatcher，通过 actions_ref 解耦，拆分不影响）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\views\_editor_helpers.py`（纯函数抽取目标层）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\views\pixel_layout.py`（LineLayoutCache，性能关键）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\pyproject.toml`（ruff 配置）
- `c:\Users\aigcs\CSTOS\projects\Tools\cs-markdown-editor\重构架构说明书.md`（硬约束出处）
