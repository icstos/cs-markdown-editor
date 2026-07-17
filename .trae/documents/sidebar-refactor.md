# 界面重构：工具区简化 + 左侧侧边栏 + 状态栏开关

## Context

当前编辑器界面：顶部工具区是两行结构（菜单+文件名+动作按钮 / Toolbar），无侧边栏，底部状态栏仅显示文件名与统计。用户希望向 Typora/VSCode 风格靠拢：工具区压成一行、新增左侧侧边栏（文件/大纲/搜索三面板）、底部状态栏加侧边栏开关。目标是保持简洁专业的视觉，同时补齐文件浏览、大纲跳转、文档内查找能力。

已确认决策：文件树根=当前文件所在目录（无文件时显示最近文件列表）；搜索=当前文档内行级子串匹配；侧边栏在左侧、默认收起。

## 架构与状态归属

| 状态 | 归属 | 持久化 |
|---|---|---|
| sidebar_open / sidebar_panel / sidebar_width / recent_files | App（放进 `settings` 字典，复用 `update_setting` 通道） | settings.json |
| file_filter / search_query | Sidebar 内部 use_state | 不持久化 |
| 跳转能力 | editor 暴露 `nav_ref.current["jump_to_line"]`（复用现有 `jump_to`） | — |

通信：App 把 `document`/`file_path`/`settings`/`active_panel` 与回调 `on_change_panel`/`on_open_file`/`on_jump_to_line` 直传 Sidebar；把 `sidebar_open`/`on_toggle_sidebar` 传给 MarkdownEditor 用于 footer 开关按钮。大纲/搜索由 Sidebar 从 `document.lines` 自行派生（`document` 是 `@ft.observable`，实时刷新）。

## 布局

```
ft.Column[
  _tool_area()                          # 单行：菜单 | 分隔 | Toolbar | 弹性 | 原文/导出/聚焦/主题
  ft.Row[                               # 中间区
    Sidebar(...) if sidebar_open else (不渲染),
    MarkdownEditor 编辑区(SelectionArea / raw_editor),
  ]
  _footer()                             # 单行：侧边栏开关 | 状态点 | 文件名 | 弹性 | 行列 | 词数 | 字符数
]
```
外层 `App` 仍为 `ft.Stack([body, settings_view])`。侧边栏收起时用条件渲染（不放入 Row.controls），避免占位。

## 实现步骤

### 1. main.py：扩展 settings 与回调
- `_DEFAULT_SETTINGS`（main.py:156-171）追加：`sidebar_open=False`、`sidebar_panel="files"`、`sidebar_width=256`、`recent_files=[]`。
- 新增 `_push_recent_file(path)`：去重后插入头部、截断 10 条、`update_setting("recent_files", ...)`。在 `open_doc`（main.py:280-302）读取成功后、`save_doc`（main.py:304-329）首次保存新文件后调用。
- 新增 `_open_file_by_path(path)`：抽出 `open_doc` 核心逻辑（读文件→parse→set_document/set_file_path/set_dirty/set_session+`_push_recent_file`），供侧边栏文件树点击调用。
- 新增 `toggle_sidebar()`=`update_setting("sidebar_open", not settings.get("sidebar_open",False))`；`change_sidebar_panel(p)`=`update_setting("sidebar_panel",p)`；`jump_to_line(li)`=转发到 `nav_ref.current["jump_to_line"]`。
- 顶层布局（main.py:1046-1067）：把 `MarkdownEditor` 包入 `ft.Row`，前置条件渲染 `Sidebar`（从 `views.sidebar` 导入）。给 `MarkdownEditor` 新传 `sidebar_open`、`on_toggle_sidebar`。
- 窗口尺寸（main.py:1092-1095）：`width` 960→1200，`min_width` 640→720（给侧边栏留空间）。

### 2. 新增 views/sidebar.py
组件 `Sidebar(document, file_path, theme_mode, settings, active_panel, on_change_panel, on_open_file, on_jump_to_line)`，宽度取 `settings["sidebar_width"]`，背景 `c.surface`，右边框 `c.border`。
- 顶部 Tab Row：三个 IconButton（文件/大纲/搜索），激活态背景 `link` 透明色 + 图标 `link` 色。
- 辅助函数：
  - `_compute_toc(document)` → `[(line_idx, level, text)]`，复用 editor.py:1286-1299 的派生逻辑。
  - `_scan_markdown_files(root, max_depth=3)` → 递归 `os.scandir`，跳过 `.`/`__pycache__`/`node_modules`，仅收 `.md`/`.markdown`，返回 `[("dir",name,children)|("file",name,abspath)]`。
  - `_filter_tree(tree, q)` → 子串过滤，保留含匹配的父目录。
  - `_render_tree(tree, on_open_file, c, depth=0)` → `ft.Column` + 缩进 14px/级，文件项 `INSERT_DRIVE_FILE_OUTLINED`+`on_click=on_open_file(abspath)`，目录项 `FOLDER_OUTLINED`（首版不折叠）。
  - `_match_lines(document, q, limit=200)` → 行级大小写不敏感子串匹配，返回 `[(line_idx, preview)]`，preview 取匹配位前后 30 字符 + `…`。
  - `_search_box(value, on_change, placeholder)` → `ft.TextField(prefix_icon=SEARCH, dense=True, border=UNDERLINE)`。
  - `_empty_hint(text)` → 居中提示。
- 文件面板：无 file_path → 列出 `settings["recent_files"]`（过滤不存在）；有 file_path → 搜索框 + 文件树。
- 大纲面板：列 `_compute_toc`，按 level 缩进，`on_click=on_jump_to_line(li)`。
- 搜索面板：搜索框 + 结果列表（`行 N` + preview，`on_click=on_jump_to_line(li)`）。

### 3. views/editor.py：props + 工具区 + 状态栏
- `MarkdownEditor` 签名（editor.py:117-132）新增 `sidebar_open: bool=False`、`on_toggle_sidebar: Callable[[],None]|None=None`。
- `nav_ref.current`（editor.py:1255-1283）追加 `"jump_to_line": jump_to`。
- 重写 `_tool_area`（editor.py:1335-1449）：删除文件名/状态/快捷键提示 Column（信息移到 footer），改为单行 Row：`PopupMenuButton(菜单)` → `_tb_divider()` → `Toolbar(...)` → `Container(expand=True)` → 原文/导出/聚焦/主题 4 个 `_btn`。padding 收紧到 `vertical=6`。
- 重写 `_footer`（editor.py:1452-1503）：最左加 `IconButton(VIEW_SIDEBAR / DOCK_TO_LEFT, on_click=on_toggle_sidebar)`，激活态 `color=c.link`；状态点改为独立 `ft.Icon(CIRCLE, size=8)`；文件名独立 `ft.Text`（ellipsis）；右侧保留行列/词数/字符数。
- 返回结构（editor.py:1505-1534）不变。

### 4. views/toolbar.py
无需修改，复用 `_btn`/`_divider`。

## 关键复用点
- `jump_to(li)`（editor.py:1244-1251）：已封装 `list_view_ref.scroll_to(scroll_key=f"line-{li}")` + `_goto(li,0)`，直接通过 nav_ref 暴露。
- `toc_entries` 派生逻辑（editor.py:1286-1299）：Sidebar 的 `_compute_toc` 复制同款，保证与 `[toc]` 块一致。
- `update_setting`（main.py:258-262）：复用为侧边栏状态持久化通道。
- `_read_file`/`parser.parse_markdown`（main.py:140-142）：`_open_file_by_path` 复用。

## 验证
1. `python -m py_compile main.py views\editor.py views\sidebar.py` 通过。
2. `python main.py` 启动：
   - 工具区单行；footer 左侧有侧边栏开关按钮 + 状态点 + 文件名。
   - 点开关按钮→侧边栏左侧展开/收起；重启后保持上次状态（查 settings.json）。
   - 三面板图标切换；切换后重启保持。
   - 文件面板：打开 .md 文件后显示同目录 .md 树（≤3 层）；搜索框过滤；点击文件项打开该文件；新建文档后显示最近文件列表。
   - 大纲面板：列出标题按级别缩进；点击跳转滚动+激活段；改标题后实时更新。
   - 搜索面板：输入关键词列出匹配行预览；点击跳转；空输入清空；无匹配显示提示。
3. 回归：Ctrl+S/Z/Y/N/O、段级编辑、跨段导航、剪切/复制/粘贴、原文/聚焦/主题切换、设置弹窗均正常。
4. 亮/暗主题下侧边栏配色协调。
