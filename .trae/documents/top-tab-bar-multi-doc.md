# 顶部多文档标签栏

## Context（背景）

当前 `main.py` 的 `App` 组件用 4 个独立 `use_state`（`document` / `file_path` / `dirty` / `session`）管理**单个**文档，编辑器通过 `key=str(session)` 在文档切换时整体重挂载。用户希望像 VS Code / Typora 那样在顶部增加多文档标签栏：每个标签显示文件名，未保存修改以星号标记；支持新建标签、关闭标签、点击切换标签。

核心思路：把单文档状态重构为「标签列表 + 激活索引」模型，每个标签是一个 `{document, file_path, dirty}` 字典；新增 `TabBar` 视图组件挂在 `body` 之上；新增关闭确认弹层；接入 `Ctrl+W` / `Ctrl+Tab` 快捷键。编辑器内部逻辑（`views/editor.py`）无需改动——仍接收单个 `document` / `file_path`，靠 `key=session` 重挂载来切换。

## 设计决策（已与用户确认）

1. **打开/新建行为**：当前标签为「未命名 + 空内容 + 未修改」时，复用该空标签加载新文档；否则追加新标签。打开已打开的文件时，切换到已存在标签而非开重复标签。
2. **关闭脏标签**：弹出确认对话框「保存并关闭 / 不保存 / 取消」。保存可被用户在保存对话框中取消（取消则不关闭）。
3. **快捷键**：`Ctrl+W` 关闭当前标签；`Ctrl+Tab` / `Ctrl+Shift+Tab` 前后切换标签（接入可配置快捷键系统）。
4. **右键菜单**：用 `ft.ContextMenu`（符合用户既定偏好）提供「关闭 / 关闭其他 / 关闭全部 / 复制路径」。

## 实现步骤

### 1. 新建 `views/tab_bar.py`

新增 `TabBar` 组件 + 内部 `_Tab` 子组件（`_Tab` 用自身 `use_state` 管理悬停态，避免父级整体重渲染）。

- **Props**：`tabs: list[dict]`（每项含 `file_path`、`dirty`）、`active_index: int`、`theme_mode`、`on_select(i)`、`on_close(i)`、`on_new()`、`on_context_action(action, i)`（供右键菜单的「关闭其他/全部」）。
- **布局**：`ft.Container`（底边框、`toolbar_bg` 背景）内含 `ft.Row`：左侧 `ft.Row(scroll=AUTO, expand=True)` 承载标签 + 右侧固定的「+」新建按钮。
- **单个标签 `_Tab`**：
  - 激活态：`surface` 背景 + 底部 2px `primary` 强调条 + `text` 色 + `W_600` 字重；
  - 非激活：透明背景 + `muted` 色，悬停时 `hover` 背景；
  - **脏标记**：文件名前加 `*`（`#FF9F0A` 警告色）；
  - **关闭按钮**：`ft.IconButton(ft.Icons.CLOSE, icon_size=14)`，`on_click` 调 `on_close(i)` 并 `e.control.stop_propagation()` 防止触发选中；鼠标悬停标签时关闭按钮变亮（`muted → text`）。
  - 文件名 `max_lines=1, overflow=ELLIPSIS`，`tooltip` 显示完整路径。
  - 包裹 `ft.ContextMenu`，菜单项：关闭、关闭其他、关闭全部、复制路径（`file_path` 为空时禁用复制路径）。
- 复用 `styles.get_colors(theme_mode)`、`FONT_MAIN`、`only_border`；文件名派生复用 `main._file_name` 的同款逻辑（`os.path.basename`，空则 `未命名.md`）——把该工具函数提到 `styles.py` 或在 `tab_bar.py` 内联一份小函数，避免循环依赖（`main.py` 导入 `tab_bar`，故 `tab_bar` 不应反向导入 `main`）。

### 2. 重构 `main.py` 的 `App` 状态

替换：

```python
document, set_document = ft.use_state(lambda: parser.parse_markdown(_SAMPLE))
file_path, set_file_path = ft.use_state(None)
dirty, set_dirty = ft.use_state(False)
session, set_session = ft.use_state(0)
```

为：

```python
tabs, set_tabs = ft.use_state(lambda: [{
    "document": parser.parse_markdown(_SAMPLE),
    "file_path": None,
    "dirty": False,
}])
active_index, set_active_index = ft.use_state(0)
session, set_session = ft.use_state(0)
confirm_close, set_confirm_close = ft.use_state(None)  # 待确认关闭的 tab index | None
```

派生当前文档：

```python
cur = tabs[active_index] if 0 <= active_index < len(tabs) else tabs[0]
document, file_path, dirty = cur["document"], cur["file_path"], cur["dirty"]
```

辅助：

```python
def _update_active(**changes):
    new_tabs = list(tabs); new_tabs[active_index] = {**new_tabs[active_index], **changes}
    set_tabs(new_tabs)

def _is_blank_untitled(tab) -> bool:
    return tab["file_path"] is None and not tab["dirty"] and not _doc_has_text(tab["document"])

def _doc_has_text(doc) -> bool:
    return any(line.raw.strip() for line in doc.lines)
```

重写操作（保持原函数名，签名不变，以最小化对 `KeyDispatcher` / `editor` 回调的改动）：

- `new_doc()`：当前标签为空白未命名时直接 `return`（已是空文档）；否则追加 `{parser.parse_markdown(""), None, False}`，`set_active_index(len-1)`，`set_session(session+1)`。
- `_open_file_by_path(path)`：先遍历 `tabs` 找 `file_path == path`，命中则切换并 `set_session+1`；否则读取文件 → `parser.parse_markdown` → 当前为空白未命名时 `_update_active(document=doc, file_path=path, dirty=False)` 否则追加新标签并激活；最后 `set_session+1`、`_push_recent_file(path)`。失败用 `SnackBar` 提示（保留原逻辑）。
- `close_tab(index)`：若 `tabs[index]["dirty"]` → `set_confirm_close(index)`；否则 `_do_close(index)`。
- `_do_close(index)`：从 `tabs` 移除；若结果为空则塞入一个空白标签；修正 `active_index`（越界则夹到末尾）；`set_session+1`。
- `close_others(index)`：保留 `index` 与活跃文档；若被保留标签 dirty 仍走确认（v1 简化：仅对当前 `index` 之外的首个脏标签依次确认——实现上先收集脏标签，逐个弹窗。为控制复杂度，v1 仅做「关闭其他」时若有任一脏标签则弹一次总确认，选「不保存」全部丢弃、「取消」中止）。**简化方案**：右键「关闭其他/全部」遇脏标签直接复用 `close_tab` 的逐个确认流程——即对每个待关标签调 `close_tab`，由其各自弹窗。这样复用既有确认逻辑，零额外弹窗代码。
- `select_tab(index)`：`index != active_index` 时 `set_active_index(index)` + `set_session+1`。
- `save_doc()`：改为 `async` 且**返回 `bool`** 表示是否真正保存成功（用户可能在另存对话框点取消）。逻辑读取当前 `document` / `file_path`，成功后 `_update_active(file_path=path, dirty=False)` 并 `document.file_path = path`、`document.dirty = False`、`_push_recent_file(path)`。保留原 `SnackBar` 失败提示。
- `on_dirty_change(d)`：`if cur["dirty"] != d: _update_active(dirty=d)`（避免每次按键触发重渲染）；`if d: _schedule_autosave()`。
- `_schedule_autosave()` / `_autosave_enabled()`：基于当前 `file_path` / `dirty`（已是派生值），逻辑不变。
- `export_doc()`：仍用当前 `document` / `file_path`，无需改动。

### 3. 顶部布局接入

`main_col` 顶部插入 `TabBar`：

```python
main_col = ft.Column(
    controls=[
        TabBar(
            tabs=[{"file_path": t["file_path"], "dirty": t["dirty"]} for t in tabs],
            active_index=active_index,
            theme_mode=theme_mode,
            on_select=select_tab,
            on_close=close_tab,
            on_new=new_doc,
            on_context_action=_on_tab_context_action,  # 关闭其他/全部/复制路径
        ),
        body,
        footer,
    ],
    spacing=0, expand=True,
)
```

- `_on_tab_context_action(action, i)`：`close`→`close_tab(i)`；`close_others`→对 `j != i` 的标签从大到小调 `close_tab(j)`（逐个确认）；`close_all`→类似；`copy_path`→`clipboard.set(file_path)`。
- `MarkdownEditor` 仍传 `key=str(session)`、`document=document`、`file_path=file_path`、原回调不变。`on_new`/`on_open`/`on_save` 仍指向重构后的同名函数。

### 4. 关闭确认弹层（复用 Stack overlay 模式）

在 `App` 末尾的 `ft.Stack` 中追加一个 `ConfirmCloseDialog`（内联小组件或直接 `ft.Container(visible=...)`），`visible = confirm_close is not None`。三按钮：

- **保存并关闭**：`page.run_task(_save_and_close)`，其中 `async def _save_and_close(): ok = await save_doc(); set_confirm_close(None); if ok: _do_close(confirm_close)`（注意闭包捕获 `confirm_close` 值）。
- **不保存**：`set_confirm_close(None); _do_close(confirm_close)`。
- **取消**：`set_confirm_close(None)`。
- 弹层样式参考 `views/settings_dialog.py` 的半透明遮罩 + 居中卡片（`bgcolor=with_opacity(0.28, BLACK)`、圆角卡片、`c.toolbar_bg`），保持视觉一致。

### 5. 快捷键接入（`views/key_bindings.py` + `services/shortcuts.py`）

- 在 `services/shortcuts.py` 的 `DEFAULT_SHORTCUTS["browse"]` 增加 `"close_tab": "ctrl+w"`、`"next_tab": "ctrl+tab"`、`"prev_tab": "ctrl+shift+tab"`；`ACTION_REGISTRY` 增加对应 `ActionDef`（`scope="browse"`、`category="文件"/"视图"`），以便设置面板可见可配置。
- `main.py` 的 `KeyDispatcher(..., app_callbacks={...})` 增加 `close_tab`、`next_tab`、`prev_tab` 三个回调（绑定到 `close_tab(active_index)` / `select_tab((active_index+1) % len)` / `select_tab((active_index-1) % len)`）。
- `key_bindings.py`：
  - 在 `handle()` 顶部（outward_sel 拦截块之后、layer 判定之前）增加 **全局标签快捷键拦截**：`ctrl+w`→`cb["close_tab"]`、`ctrl+tab`→`cb["next_tab"]`、`ctrl+shift+tab`→`cb["prev_tab"]`，return。放在顶部确保编辑态下也生效，且不被 edit 层 tab 处理吃掉。
  - 同时在 `_handle_edit_nav` 与 `editor.py` 的 `_on_key_down` 中，把现有 `if norm == "tab"` 分支加 `and not e.ctrl` 守卫，让 `Ctrl+Tab` 不再被代码块/列表缩进逻辑拦截（普通 Tab 行为不变）。
  - browse 层 `_handle_shortcuts` 增加这三个 `matches` 分支（与顶部拦截二选一即可，统一放顶部拦截更稳妥；browse 分支可省略）。
- **风险提示**：Flet 桌面端 `Ctrl+W` 可能被原生窗口管理器拦截导致关窗。实现后需实测；若冲突，回退为 `Ctrl+Shift+W` 或仅在 `page.on_keyboard_event` 中处理（Flet 该回调在桌面端先于原生快捷键收到，通常可覆盖）。计划中保留 `Ctrl+W`，实测有问题再调整默认值。

### 6. 细节与一致性

- `StatusBar`（底部状态栏）已用 `document.dirty` / `file_path`，传入的仍是当前激活文档，无需改动。
- `Sidebar` 的文件树点击 `on_open_file=_open_file_by_path` 自动复用新逻辑（切换/复用空标签）。
- 主题切换、设置面板、导出等不受影响。
- 首次启动仍加载 `_SAMPLE` 作为唯一初始标签（与现状一致）。

## 关键文件

- **新增**：`views/tab_bar.py`（`TabBar` + `_Tab` + `ConfirmCloseDialog`，或弹层单列 `views/confirm_dialog.py`）
- **修改**：`main.py`（`App` 状态重构 + 操作函数 + 布局接入 + 弹层）
- **修改**：`views/key_bindings.py`（顶部拦截 + tab 守卫）
- **修改**：`services/shortcuts.py`（`DEFAULT_SHORTCUTS` + `ACTION_REGISTRY` 新增 3 项）
- **可能微调**：`views/editor.py`（`_on_key_down` 的 `norm == "tab"` 加 `not e.ctrl` 守卫——仅当 Ctrl+Tab 在编辑态被拦截时）

## 验证

1. `python -m py_compile main.py views/tab_bar.py views/key_bindings.py services/shortcuts.py` 语法检查。
2. `python main.py` 启动桌面应用，逐项手测：
   - 点「+」或菜单「新建」→ 出现新空标签（首次空标签复用不新增）；切换标签编辑器内容正确切换、光标重置。
   - `Ctrl+O` 打开 `assets` 下任一 `.md`→ 新标签；再次打开同一文件→ 切换到已存在标签而非新开。
   - 编辑内容→ 对应标签出现 `*`；`Ctrl+S` 保存→ `*` 消失、底部状态栏同步。
   - 关闭干净标签→ 直接关闭；关闭脏标签→ 弹确认；「保存并关闭」走保存流程（取消保存则不关）、「不保存」直接关、「取消」保留。
   - 右键标签→ `ft.ContextMenu` 出现「关闭/关闭其他/关闭全部/复制路径」。
   - `Ctrl+Tab` / `Ctrl+Shift+Tab` 循环切换；`Ctrl+W` 关闭当前（脏标签走确认）。
   - 关闭最后一个标签→ 自动出现一个空标签，不出现空标签栏。
   - 亮/暗主题下标签栏视觉正确（激活态强调条、悬停、脏标记星号颜色）。
3. 回归：原快捷键（Ctrl+S/N/O/B/`/K 等）、段级编辑、跨行导航、侧边栏文件树点击均正常。
