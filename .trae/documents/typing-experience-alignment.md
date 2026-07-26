# 深度对齐 Typora/VSCode 编辑体验

## Context

用户要求所有原生编辑行为自然可用，包括光标系统、选区系统、基础编辑操作（输入/删除/剪贴板/撤销重做），以及所有快捷键支持在设置页自定义。

经过对 [editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)、[key_bindings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py)、[shortcuts.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/shortcuts.py)、[actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/actions.py) 的完整审查，**绝大多数功能已实现**：

- ✅ 光标系统：方向键、点击跳转、Home/End、Ctrl+Home/End、PageUp/PageDown、记忆列、自动滚动
- ✅ 选区系统：拖拽选区、Shift+方向键扩展、Shift+点击定位末端、选区高亮
- ✅ 输入：IME 友好 3 分支模型、Enter 续行（列表/引用/标题）
- ✅ 删除：Backspace/Delete（含行合并）、选区批量删除
- ✅ 剪贴板：Ctrl+X 剪切（含 outward_sel）、Ctrl+V 多行粘贴
- ✅ 撤销重做：50 快照栈、去重、粒度控制

**确认存在 5 个缺口**需要修补。

## 缺口分析与修复方案

### 缺口 1：Ctrl+C 在 outward_sel 激活时不复制选区文本 ❌

**现状**：[key_bindings.py:154-190](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py#L154-L190) 的 outward_sel 拦截块处理了 Backspace/Delete/Ctrl+X/Escape/Shift+Arrow，但**遗漏了 Ctrl+C**。Ctrl+C 落入 `_handle_shortcuts`，因 `cursor_li is None` 调用 `_do_copy`，而 `_do_copy` 读取 `selection_text_ref`（SelectionArea 来源）——outward_sel 激活时 SelectionArea 无选区，结果为空。

**修复**：
- `core/actions.py`：EditorActions 新增 `handle_outward_copy: Callable[[], Awaitable[None]] | None`
- `views/editor.py`：实现 `handle_outward_copy()`——提取 outward_sel 范围文本（复用 `handle_outward_cut` 的文本提取逻辑，但不删除），写入剪贴板
- `views/key_bindings.py`：outward_sel 拦截块新增 `if combo == "ctrl+c":` 分支，路由到 `handle_outward_copy`

### 缺口 2：Ctrl+V 在 outward_sel 激活时不替换选区 ❌

**现状**：outward_sel 激活时 `cursor_li is None`，`_handle_shortcuts` 的 Ctrl+V 分支要求 `cursor_li is not None`，结果什么也不做。

**修复**：
- `views/key_bindings.py`：outward_sel 拦截块新增 `if combo == "ctrl+v":` 分支——先调用 `handle_outward_delete()` 删除选区，再异步调用 `_do_paste_check()` 在删除点粘贴
- `views/editor.py`：`handle_outward_delete` 已在删除后调用 `_set_cursor(start_li, start_off)` 恢复光标，粘贴会在此位置插入

### 缺口 3：Ctrl+A 全选未实现 ❌

**现状**：Ctrl+A 仅在原生控件（代码块/表格）聚焦时放行（`_NATIVE_CLIPBOARD_COMBO`），文档编辑器无全选功能。

**修复**：
- `core/actions.py`：EditorActions 新增 `select_all: Callable[[], None]`
- `views/editor.py`：实现 `select_all()`——若文档为空返回；否则设 `cursor_li=None`，设 `outward_sel=(0, 0, last_li, len(last_line_raw))`，suppress_blur
- `views/key_bindings.py`：在行内格式快捷键判断之后、layer 判定之前，新增 `if combo == "ctrl+a":` 分支（两层均生效），调用 `actions.select_all()`

### 缺口 4：Ctrl+C/X/V/A 不在设置页可自定义 ⚠️

**现状**：[shortcuts.py:66-109](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/shortcuts.py#L66-L109) ACTION_REGISTRY 无 copy/cut/paste/select_all 条目；[key_bindings.py:372-409](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py#L372-L409) 硬编码 `combo == "ctrl+c"` 等判断。

**修复**：
- `services/shortcuts.py`：
  - `DEFAULT_SHORTCUTS["edit"]` 新增 `"copy": "ctrl+c"`, `"cut": "ctrl+x"`, `"paste": "ctrl+v"`, `"select_all": "ctrl+a"`
  - `ACTION_REGISTRY` 新增 4 个 ActionDef（scope="both"，category="编辑"）
- `views/key_bindings.py`：将硬编码的 `combo == "ctrl+c"` 等改为 `matches(combo, shortcuts.get("copy", "ctrl+c"))` 等，从当前层快捷键表读取

**注意**：基本导航键（方向键/Backspace/Delete/Tab/Home/End/PageUp/PageDown）保持硬编码，不放入 registry——这些是编辑器底层行为，自定义会破坏可用性（与 VSCode 默认行为一致，VSCode 也不建议改这些）。

### 缺口 5：Home 键不够智能（无"先内容首、再行首"行为）⚠️

**现状**：[editor.py:625-632](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py#L625-L632) `move_home` 直接跳到 raw 0（前缀之前）。

**修复**（Smart Home，对齐 VSCode）：
- `views/editor.py`：新增 `last_home_li_ref = ft.use_ref(-1)`、`last_home_off_ref = ft.use_ref(-1)` 跟踪上次 Home 落点
- 修改 `move_home()`：
  1. 计算 content_start（跳过 HEADING_PREFIX/LIST_PREFIX/QUOTE_PREFIX 段 0 的长度）
  2. 若当前 cursor_off != content_start → 跳到 content_start
  3. 若当前 cursor_off == content_start → 跳到 raw 0
  4. 更新 last_home_li_ref / last_home_off_ref
- End 已是 raw end（视觉行尾），无需 Smart End

## 实现步骤

### 步骤 1：扩展 EditorActions 接口
**文件**：`core/actions.py`
- 新增 `handle_outward_copy: Callable[[], Awaitable[None]] | None = None`
- 新增 `select_all: Callable[[], None] | None = None`

### 步骤 2：实现 editor.py 新动作
**文件**：`views/editor.py`
- 实现 `async def handle_outward_copy()`：复用 `handle_outward_cut` 的文本提取逻辑，仅写剪贴板不删除
- 实现 `def select_all()`：全文档 outward_sel
- 修改 `move_home()` 实现 Smart Home
- 在 EditorActions 构造处（约 L1492-1539）补充 `handle_outward_copy=handle_outward_copy, select_all=select_all`

### 步骤 3：注册剪贴板快捷键到 registry
**文件**：`services/shortcuts.py`
- `DEFAULT_SHORTCUTS["browse"]` 新增 `"copy": "ctrl+c"`, `"cut": "ctrl+x"`, `"paste": "ctrl+v"`, `"select_all": "ctrl+a"`
- `DEFAULT_SHORTCUTS["edit"]` 同上
- `ACTION_REGISTRY` 新增 4 个 ActionDef（copy/cut/paste/select_all，scope="both"，category="编辑"）

### 步骤 4：KeyDispatcher 路由更新
**文件**：`views/key_bindings.py`
- outward_sel 拦截块（L154-190）新增：
  - `if combo == "ctrl+c":` → `page.run_task(actions.handle_outward_copy)`
  - `if combo == "ctrl+v":` → `actions.handle_outward_delete()` 后 `page.run_task(self._do_paste_check)`
- 在行内格式快捷键之后、layer 判定之前新增：
  - `if combo == "ctrl+a":` → `actions.select_all()`（非原生控件聚焦时）
- `_handle_shortcuts` 中将硬编码 `combo == "ctrl+c"` 等改为 `matches(combo, shortcuts.get("copy", "ctrl+c"))` 等

### 步骤 5：设置页验证
**文件**：`views/settings_dialog.py`
- 无需修改——`_action_rows` 已通过 `actions_for_layer` 自动渲染新增的 4 个动作

## 验证方案

### 自动化验证
1. `python -m py_compile core/actions.py views/editor.py services/shortcuts.py views/key_bindings.py`
2. `python -c "import main; print('OK')"`
3. 验证 ACTION_REGISTRY 新增条目：`python -c "from services.shortcuts import ACTION_REGISTRY; ids=[a.id for a in ACTION_REGISTRY]; print('copy' in ids, 'cut' in ids, 'paste' in ids, 'select_all' in ids)"`

### 手动验证（启动应用）
1. **Ctrl+A 全选**：打开文档按 Ctrl+A，应选中全部文本（高亮）；再按任意方向键取消
2. **Ctrl+C 复制选区**：Shift+方向键选中一段 → Ctrl+C → 点击别处定位光标 → Ctrl+V，应粘贴选中的文本
3. **Ctrl+V 替换选区**：选中一段 → Ctrl+V，应删除选区并粘贴剪贴板内容
4. **Smart Home**：在 `# 标题` 行点击"标"字之后 → 按 Home → 光标跳到"标"前（内容首）；再按 Home → 光标跳到 `#` 前（行首）
5. **设置页**：打开设置 → 高级 tab，应看到"复制/剪切/粘贴/全选"4 个新动作行，可修改快捷键
6. **冲突检测**：在设置页把"复制"改成 Ctrl+S，应显示冲突提示

## 不在本次范围

- 像素级垂直导航（当前字符偏移方式对比例字体有微小偏差，但实际体验可接受）
- 选区的纯文本复制转 Markdown（已有 `compute_markdown_from_text`，outward_sel 复制暂用纯文本，与选区删除共用同一文本源保证一致性）
- 基本导航键（方向键/Backspace/Delete/Tab）的自定义（保持硬编码，避免破坏编辑器底层行为）
