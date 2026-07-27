# 优化任务列表支持

## Context（背景）

当前任务列表（`- [ ]` / `- [x]`）实现仅处于"能用"阶段，存在多处短板：

1. **视觉割裂**：`ft.Checkbox` 用默认 Material 蓝填充 + 默认 2px 圆角，与编辑器主题色（亮/暗两套）和设计 token（Radius/Spacing）完全不联动；编辑器其它元素都已主题感知，唯独 Checkbox 仍残留 Material 默认外观。
2. **状态不可见**：已勾选项的文字与未勾选完全一致，没有删除线/muted 色区分。GitHub/Typora/VS Code 三大参考实现都用"删除线 + 灰色文字"标识已完成任务——这是用户判断任务状态的唯一视觉线索。
3. **键盘流缺失**：用户必须用鼠标点 Checkbox 才能切换勾选状态；也没有把普通行/列表行转为任务行的快捷键。这违背"操作自然顺畅"。
4. **焦点矩形（用户记忆中的"左侧横线"）**：Flet Checkbox 默认 `focus_color` 在 FOCUSED 态绘制一个矩形 overlay，视觉上像 Checkbox 左侧延伸出的横线，破坏清爽观感。

本次优化目标：让任务列表视觉融入主题（美观、科学）、状态一目了然（清晰）、可纯键盘操作（自然顺畅），且不破坏现有光标命中数学与撤销/重做链路。

---

## Approach（总体方案）

四项必需改动 + 两项可选打磨。所有改动沿用现有"busting prop / theme 感知 / EditorActions 桥接"模式，不引入新依赖。

| # | 改动 | 必需性 | 触达目标 |
|---|------|------|---------|
| A | 主题感知 Checkbox（颜色/圆角/焦点透明） | 必需 | 美观 |
| B | 已勾选项删除线 + muted 文字色 | 必需 | 科学、清晰 |
| C | `Alt+C` 切换当前任务勾选状态 | 必需 | 自然顺畅 |
| D | `Ctrl+Shift+T` 把当前行转为任务项 | 必需 | 自然顺畅 |
| E | 空任务占位符 | 可选 | 清晰 |
| F | 工具栏任务按钮 | 可选 | 可发现性 |

---

## File-by-file Implementation Plan

### 1. `services/shortcuts.py` — 注册两个新动作

在 `ACTION_REGISTRY`（约 80-138 行）追加两项 `ActionDef`：
- `ActionDef("toggle_task", "切换任务状态", "both", "编辑", "勾选/取消勾选当前任务列表项。", {"browse": "alt+c", "edit": "alt+c"})` — scope 用 `"both"`，浏览态与编辑态均可触发（任务勾选是天然跨态操作）。
- `ActionDef("format_task", "任务列表", "edit", "格式", "将当前行切换为任务列表项（- [ ]）。", {"edit": "ctrl+shift+t"})`

在 `DEFAULT_SHORTCUTS`（18-63 行）：
- `browse` 字典追加 `"toggle_task": "alt+c"`
- `edit` 字典追加 `"toggle_task": "alt+c"` 和 `"format_task": "ctrl+shift+t"`

已确认两键位在 DEFAULT_SHORTCUTS 两层均未占用，无冲突。`alt+c` 是 VS Code Markdown All-in-One 扩展的社区事实标准键位，与 `alt+z`（toggle_word_wrap）形成 alt 系列"切换类"语义一致性。

### 2. `views/rendered_line.py` — A + B + E 视觉核心

**A. Checkbox 主题化**（替换 373-376 行的裸 `ft.Checkbox`）：

```python
ft.Checkbox(
    value=line.checked,
    on_change=lambda e: on_toggle_task(line_idx) if on_toggle_task else None,
    active_color=c.link,
    check_color=ft.Colors.WHITE,
    fill_color={
        ft.ControlState.SELECTED: c.link,
        ft.ControlState.DEFAULT: c.surface,
    },
    overlay_color={
        ft.ControlState.HOVERED: ft.Colors.with_opacity(0.06, c.text),
        ft.ControlState.FOCUSED: ft.Colors.TRANSPARENT,  # 消除"左侧横线"焦点矩形
    },
    border_side=ft.BorderSide(1.5, c.muted),
    shape=ft.RoundedRectangleBorder(radius=Radius.SM),
    tristate=False,
    splash_radius=0,
)
```

API 已验证：`Checkbox` 支持 `fill_color` / `overlay_color` 为 `dict[ControlState, ...]`，`shape` 接受 `RoundedRectangleBorder`，`ft.ControlState.FOCUSED/SELECTED/DEFAULT/HOVERED` 全部存在于顶层 `ft` 命名空间。

**B. 已勾选删除线 + muted**：扩展 `_spans_with_highlight`（549 行）签名加 `checked: bool = False`；返回 spans 前若 `checked=True`，对每个 span 重写 style：
- `decoration` 与原值取并集（保留已有 underline 等），追加 `ft.TextDecoration.LINE_THROUGH`
- `color = c.muted`（覆盖原色）
- 保留原 `bgcolor`（选区高亮底色不丢）

任务行调用处（363 行）传 `checked=line.checked`。

**始终应用**（不区分浏览态/编辑态）：编辑态光标位置由独立的 cursor overlay 标识，与文字样式解耦；GitHub/Typora/VS Code 在编辑已勾选项时也保留删除线，符合"状态指示器应稳定显示"的语义。

**E. 空任务占位符**（365-366 行 `else` 分支）：
- 浏览态（`cursor_off is None`）：渲染 `ft.TextSpan("待办事项...", style=ft.TextStyle(color=c.muted, italic=True, size=base))`
- 编辑态：保留现有 `ft.TextSpan(" ", style=style)`（光标可见可输入）

### 3. `views/editor.py` — C + D 交互核心

**D. 扩展 `set_block`**（1070 行）：签名改为 `def set_block(block_type: BlockType, level: int = 0, task: bool = False):`；在 `LIST_UO` 分支（1088-1090 行）按 `task` 选前缀：
```python
elif block_type == BlockType.LIST_UO:
    indent_sp = " " * line.level if line.block_type in (BlockType.LIST_UO, BlockType.LIST_O) else ""
    prefix = "- [ ] " if task else "- "
    new_raw = indent_sp + prefix + content
```
所有现有调用方（key_bindings.py:440-448, editor.py 工具栏 lambda）均不传 `task`，默认 False，行为完全不变 ✓。现有 cursor 重定位逻辑（1120-1132 行）基于 `new_prefix_len` 自动重算，`- ` → `- [ ] ` 前缀 2 字符变 6 字符自动处理 ✓。

**C. 新增 `toggle_task_at_cursor()`**：在 `toggle_task`（1322 行）旁新增：
```python
def toggle_task_at_cursor():
    li = cursor_li if cursor_li is not None else cursor_line
    if li is None or not (0 <= li < len(document.lines)):
        return
    line = document.lines[li]
    if not line.task:
        return  # 非任务行静默忽略
    toggle_task(li)
```
浏览态用 `cursor_line`（最近交互行）兜底，编辑态用 `cursor_li`。

**新增 `format_task()`**：
```python
def format_task():
    set_block(BlockType.LIST_UO, task=True)
```

**EditorActions 上报**（2414-2444 行附近）：追加两个字段：
```python
toggle_task_at_cursor=toggle_task_at_cursor,
format_task=format_task,
```

**工具栏回调（F）**：在 `_tool_area` 的 `Toolbar(...)` 调用（2685 行附近）追加 `on_task=lambda: set_block(BlockType.LIST_UO, task=True)`。

### 4. `core/actions.py` — EditorActions 桥接

`EditorActions` dataclass 追加两个无参 callable 字段：
```python
toggle_task_at_cursor: Callable[[], None]
format_task: Callable[[], None]
```
放在 `set_block`（82 行）之后，与"块级切换"语义聚类。两个字段都是必填（无默认值），保持现有"必填即校验"约定。

### 5. `views/key_bindings.py` — 快捷键分发

在 `_dispatch`（约 440-525 行）：

**Browse 层**（458-495 行 `if layer == "browse":` 块内），追加：
```python
elif matches(combo, shortcuts.get("toggle_task", "alt+c")):
    if actions is not None:
        actions.toggle_task_at_cursor()
```

**Edit 层**（496 行后 `# edit 层` 块内），追加：
```python
elif matches(combo, shortcuts.get("toggle_task", "alt+c")):
    if actions is not None:
        actions.toggle_task_at_cursor()
elif matches(combo, shortcuts.get("format_task", "ctrl+shift+t")):
    if actions is not None and not self._native_field_focused(actions):
        actions.format_task()
```
`format_task` 加 `_native_field_focused` 守卫，避免在代码块/表格单元格编辑中误触发；`toggle_task` 不加守卫——任务勾选不修改文本内容，与原生 field 编辑无冲突，反而便于在编辑代码块时切换外部任务状态。

### 6. `views/toolbar.py` — 工具栏任务按钮（F，可选）

接收 `on_task` 回调，在列表按钮（`on_list`）旁追加：
```python
ft.IconButton(
    icon=ft.Icons.CHECK_BOX_OUTLINE_BLANK,
    tooltip="任务列表  Ctrl+Shift+T",
    on_click=lambda e: (on_task or _noop)(),
    ...
)
```
图标 `ft.Icons.CHECK_BOX_OUTLINE_BLANK` 已验证存在于当前 Flet 版本。语义贴近"未勾选任务"，与转换后产生的 `- [ ]` 视觉一致。

---

## Critical Files

- [services/shortcuts.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/shortcuts.py) — 注册 `toggle_task` / `format_task` 动作 + 默认键位
- [views/rendered_line.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/rendered_line.py) — A 主题化 Checkbox / B 删除线注入 / E 占位符
- [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) — D 扩展 `set_block` / C 新增 `toggle_task_at_cursor` + `format_task` / 工具栏回调
- [core/actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/actions.py) — EditorActions 字段
- [views/key_bindings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py) — 快捷键分发分支
- [views/toolbar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/toolbar.py) — 工具栏按钮（可选）

---

## Reuse Existing Utilities

- `_current_colors()` [styles.py:131](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/styles.py#L131) — 主题感知取色
- `Colors` dataclass [styles.py:34](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/styles.py#L34) — `link`/`muted`/`surface`/`text`/`code_bg`
- `Radius.SM`/`Spacing` design tokens [styles.py:303](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/styles.py#L303)
- `ShortcutManager` / `ACTION_REGISTRY` [services/shortcuts.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/shortcuts.py)
- `_push_history` / `_reparse_atomic` / `mark_dirty` — 复用 `toggle_task` 既有撤销链路
- `_native_field_focused` 守卫模式 [key_bindings.py:447](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py#L447)

---

## Out of Scope（明确不做）

- **Checkbox 尺寸随 `body_font_size` 缩放**：会破坏 `_prefix_width_px`（[rendered_line.py:196-215](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/rendered_line.py#L196-L215)）的 raw 前缀文本像素宽度近似，导致光标命中偏移。保持默认 ~24px，由 Row `spacing` 弹性吸收偏差。如后续确需缩放，须重构 `_prefix_width_px` 接收实际 Checkbox 宽度参数。
- **新增 `BlockType.TASK`**：任务在模型层就是 `LIST_UO + task=True`（[document.py:121](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/models/document.py#L121)），新增枚举值会污染 parser/line_view/segment_view 多处，违反最小侵入。
- **任务行拖拽选区命中精度修复**：`_hit_raw_off` 在任务行激活态用浏览态 offsets 命中（[rendered_line.py:224-227](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/rendered_line.py#L224-L227)），边界像素偏 1-2px。属预存在问题，与本计划无关。
- **批量勾选/取消**：超出本次优化范围。

---

## Verification（验证步骤）

1. **语法/导入校验**：
   ```powershell
   python -m py_compile views/rendered_line.py views/editor.py services/shortcuts.py views/key_bindings.py core/actions.py views/toolbar.py
   python -m pytest tests/ -q
   ```

2. **视觉验证**（启动应用，新建文档输入）：
   - 输入 `- [ ] 任务一` / `- [x] 任务二` / `- 普通列表`
   - 切换亮色/暗色主题（Ctrl+Shift+L），确认 Checkbox 颜色随主题变化（亮色用 #1677FF 蓝、暗色用 #58A6FF 蓝）、未勾选边框用 muted 色、圆角 4px
   - 已勾选项文字呈删除线 + muted 色；未勾选保持正常色
   - 点击 Checkbox 不出现"左侧横线"焦点矩形
   - 空任务行（`- [ ]` 后无内容）浏览态显示淡灰"待办事项..."占位符

3. **交互验证**：
   - 光标在某任务行内容中，按 `Alt+C` → 勾选状态切换；再按 → 切回
   - 浏览态下点击某任务行后按 `Alt+C` → 切换该行勾选
   - 在普通段落行按 `Ctrl+Shift+T` → 行变为 `- [ ] 段落内容`，光标保持内容位置
   - 在代码块编辑器内按 `Ctrl+Shift+T` → 无反应（被 `_native_field_focused` 守卫拦截）
   - 工具栏点击任务图标 → 同 `Ctrl+Shift+T` 效果
   - 撤销（Ctrl+Z）能恢复勾选切换 / 转任务操作

4. **快捷键设置面板**：
   - 打开设置（Ctrl+,），确认"切换任务状态"和"任务列表"两个动作出现，键位正确显示
   - 修改键位后立即生效（复用现有 ShortcutManager 即时生效链路）

5. **冲突检测**：
   - 设置面板尝试把 `format_task` 改为 `ctrl+b`（与 format_bold 冲突），应弹出冲突提示
