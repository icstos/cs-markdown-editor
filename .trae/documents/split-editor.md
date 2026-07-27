# 向右拆分编辑器（VSCode 风格 Ctrl+\）

## Context

用户需要参考 VSCode 的 split editor 功能，按 `Ctrl+\` 将当前文档在右侧拆分出第二个编辑器视口，实现同文档多视口查看。两个编辑器共享同一个 `document` 对象（`@ft.observable`，修改自动同步），各自独立的光标和滚动位置。

## 架构分析

- 键盘事件在 **page 级别**处理：`main.py` 中 `page.on_keyboard_event = _handler` → 单一 `KeyDispatcher.handle(e)`
- `KeyDispatcher` 接收 `app_callbacks` dict（`save`/`new`/`open`/`toggle_word_wrap` 等），全局分发
- `nav_ref.current` 在 `MarkdownEditor` 渲染时写入 `EditorActions` 实例（光标位置、导航函数）
- `MarkdownEditor` 是 `@ft.component`，通过 `key` prop 控制重建（切换标签时 `key=str(session)` 强制重置）
- 当前布局：`body = ft.Row([sidebar, Container(MarkdownEditor, expand=True)])`

## 方案

### 1. 快捷键注册 — `services/shortcuts.py`

- `DEFAULT_SHORTCUTS["browse"]` 添加 `"toggle_split_editor": "ctrl+\\"`
- `ACTION_REGISTRY` 添加 `ActionDef("toggle_split_editor", "拆分编辑器", "both", "视图", "向右拆分编辑器，多视口查看同一文档。", {"browse": "ctrl+\\"})`

### 2. 快捷键分发 — `views/key_bindings.py`

在全局区（layer 判定之前，与 `toggle_word_wrap` 同级）添加：
```python
if matches(combo, browse_sc.get("toggle_split_editor", "ctrl+\\")):
    cb["toggle_split_editor"]()
    return
```

### 3. MarkdownEditor 新增 prop — `views/editor.py`

添加 `show_toolbar: bool | None = None` prop。当 `None` 时用 `settings.get("show_toolbar", True)`，当 `False` 时隐藏工具栏（用于右侧拆分编辑器）。

```python
# 函数签名添加
show_toolbar: bool | None = None,

# 函数体内
_show_toolbar = show_toolbar if show_toolbar is not None else settings.get("show_toolbar", True)
```

将 `show_toolbar` 变量（约第 159 行）替换为 `_show_toolbar`。

### 4. main.py 核心改动

#### 4a. 新增状态

```python
split_editor, set_split_editor = ft.use_state(False)    # 是否拆分
active_pane, set_active_pane = ft.use_state(0)           # 0=左, 1=右（焦点跟踪）
nav_ref_split = ft.use_ref(None)                          # 右侧编辑器的 nav_ref
```

#### 4b. toggle_split_editor 回调

```python
def toggle_split_editor():
    set_split_editor(not split_editor)
    set_active_pane(0)  # 关闭拆分时焦点回左侧
```

#### 4c. app_callbacks 添加

```python
"toggle_split_editor": toggle_split_editor,
```

#### 4d. KeyDispatcher 的 actions_ref

```python
# 根据 active_pane 选择对应的 nav_ref
active_nav_ref = nav_ref_split if (split_editor and active_pane == 1) else nav_ref

dispatcher = KeyDispatcher(
    ...
    actions_ref=active_nav_ref,
    ...
)
```

#### 4e. body 布局

```python
if split_editor:
    editor_area = ft.Row(
        controls=[
            ft.Container(
                content=MarkdownEditor(
                    key=f"{session}-0",
                    document=document,
                    ...
                    nav_ref=nav_ref,
                    on_editor_focus=lambda: set_active_pane(0),
                ),
                expand=True,
                on_click=lambda e: set_active_pane(0),
            ),
            ft.VerticalDivider(width=1, color=c.border),
            ft.Container(
                content=MarkdownEditor(
                    key=f"{session}-1",
                    document=document,
                    ...
                    nav_ref=nav_ref_split,
                    show_toolbar=False,        # 右侧无工具栏
                    on_editor_focus=lambda: set_active_pane(1),
                ),
                expand=True,
                on_click=lambda e: set_active_pane(1),
            ),
        ],
        spacing=0,
        expand=True,
    )
else:
    editor_area = ft.Container(
        content=MarkdownEditor(
            key=str(session),
            document=document,
            ...
            nav_ref=nav_ref,
        ),
        expand=True,
    )

body = ft.Row(controls=[sidebar_container, editor_area], spacing=0, expand=True)
```

#### 4f. 状态栏 cursor 跟踪

```python
active_nav = nav_ref_split if (split_editor and active_pane == 1) else nav_ref
_actions = active_nav.current
cursor_row_col = _actions.get_cursor_row_col() if _actions else (1, 1)
```

### 5. 焦点跟踪 — `views/editor.py`

MarkdownEditor 添加 `on_editor_focus: Callable[[], None] | None = None` prop。在光标 TextField 的 `on_focus` 回调中调用（已有 `on_focus` 逻辑，追加调用 `on_editor_focus`）。

## 不改动

- `Document` 模型：两个 MarkdownEditor 共享同一 `document` 对象，`@ft.observable` 自动同步
- 工具栏按钮：暂不在工具栏添加拆分按钮（快捷键已足够，后续可加）
- 右侧编辑器工具栏：隐藏（`show_toolbar=False`），保持简洁

## 验证

1. `Ctrl+\` → 右侧出现第二个编辑器，显示同一文档
2. 左侧编辑文本 → 右侧实时同步（`@ft.observable`）
3. 点击右侧编辑器 → 状态栏光标位置切换为右侧
4. 右侧独立滚动、独立光标定位
5. `Ctrl+\` 再次 → 关闭拆分
6. 切换标签 → 拆分状态保持/重置（设计选择：重置更简单）
7. `python -c "import main"` → 导入正常
8. `python -m tests.test_soft_wrap` → 37 项测试通过
