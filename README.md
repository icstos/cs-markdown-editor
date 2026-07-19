# Markdown 编辑器

基于 [Flet](https://flet.dev) 0.86.1 声明式组件与 [mistune](https://mistune.lepture.com/) 实时解析，参考 [Typora](https://typora.io/) 的段级所见即所得（WYSIWYG）编辑体验。

点击任意段即显示其最小 Markdown 语法，其余保持渲染样式——这是与传统「源码 / 预览」双栏编辑器最大的不同。

## 特性

### 编辑体验

- **段级编辑**：点击行内任意段（加粗、斜体、行内代码、链接等）即切换到该段的原生 Markdown 编辑，其余段保持渲染样式
- **标题整行编辑**：点击标题进入编辑态时，以整行原文（如 `### 我的标题`）在单个输入框中编辑，可直接增删 `#` 调整级别
- **跨段光标导航**：方向键在段间 / 行间无缝移动，`Home` / `End` 跳转行首 / 行尾
- **行首 / 行尾合并**：`Backspace` 在行首与前一行合并，`Delete` 在行尾与下一行合并——所有行内块类型（标题 / 列表 / 引用 / 段落）行为一致，光标落在合并点
- **向外选区**：`Shift+Click` 或 `Shift+方向键` 从编辑光标起始跨段 / 跨行选区，高亮覆盖范围；支持 `Ctrl+X` 剪切、`Backspace` / `Delete` 删除、`Escape` 取消
- **列表缩进**：`Tab` / `Shift+Tab` 在列表项内调整缩进级别
- **撤销 / 重做**：`Ctrl+Z` / `Ctrl+Y`（或 `Ctrl+Shift+Z`），基于快照栈（固定容量 50）
- **智能复制粘贴**：跨行复制自动还原为 Markdown 源码；多行粘贴自动拆分为新行
- **智能剪切**：
  - 渲染态：`Ctrl+X` 复制 Markdown 源码并删除选中内容
  - 编辑态段内选区：`Ctrl+X` 立即提交剪切后的草稿到文档并重定位光标（同步执行，避免与原生 TextField 剪切竞态导致双份剪切）
  - 向外选区：`Ctrl+X` 复制选区 raw 文本到剪贴板并删除选区内容
- **原文模式**：一键切换到纯 Markdown 源码编辑
- **设置面板**：编辑 / 外观 / 行为 / 快捷键 / 高级五个分区，可配置内容宽度、边距、字号、行高、字体、自动保存、专注模式、工具栏显隐、代码主题、导出格式等

### 块级支持

| 类型 | 说明 |
|------|------|
| 标题 H1–H6 | 六级字号与字重递进；阅读态隐藏 `#`，用颜色区分级别 |
| 无序 / 有序列表 | 嵌套缩进；无序列表圆点按层级着色（与标题共用色阶） |
| 任务列表 | `- [ ]` / `- [x]`，可点击复选框切换状态 |
| 引用 | 支持多层嵌套，左侧竖线标识 |
| 代码块 | 语法高亮（亮色 GitHub / 暗色 One Dark），可编辑语言标识 |
| 行间公式 | `$$...$$` |
| 分隔线 | `---` / `***` / `___` |
| 目录 | `[toc]` 块，点击条目跳转对应标题 |
| 表格 | `| a | b |` 语法，单元格可编辑 |

### 行内格式

加粗、斜体、行内代码、删除线、==高亮==、上标 `^x^`、下标 `~x~`、链接、图片、行内公式 `$...$`，支持组合语法（如 `***加粗斜体***`）。

### 视觉与主题

- **标题色阶**：红 → 橙 → 绿 → 青 → 蓝 → 紫（H1–H6），亮 / 暗主题各自适配对比度
- **标题字重**：H1 `W_800` 至 H6 `W_500`，逐级递减
- **列表圆点**：嵌套层级复用标题色阶（每 2 空格一级）
- **亮 / 暗主题**：工具栏一键切换，代码块高亮主题随主题联动
- **向外选区高亮**：选区段注入半透明背景色（`link` 色 22% 不透明度），与激活行色调一致

### 文件与导出

- 新建 / 打开 / 保存（`.md` / `.markdown` / `.txt`）
- 导出 HTML（mistune 渲染，含表格、脚注、任务列表等扩展）

## 技术栈

| 依赖 | 用途 |
|------|------|
| [Flet](https://flet.dev) ≥ 0.86.1 | 声明式 GUI（`@ft.component` + `use_state` / `use_effect` + `@ft.observable`） |
| [mistune](https://mistune.lepture.com/) ≥ 3.0 | 行内 AST 解析；HTML 导出（含 strikethrough / mark / 上下标 / 表格等插件） |
| [Pillow](https://pillow.readthedocs.io/) ≥ 10.0 | 文本像素宽度测量（编辑块自适应）+ 图片尺寸读取与缩放 |

> **Python** ≥ 3.12（`pyproject.toml`）；模型层使用 `StrEnum`（3.11+ 特性）

## 安装与运行

**推荐**（基于 `pyproject.toml`）：

```bash
pip install -e .
python main.py
```

或安装依赖后运行：

```bash
pip install flet mistune pillow
python main.py
```

安装后也可通过入口命令启动：

```bash
cs-markdown-editor
```

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+N` | 新建文档 |
| `Ctrl+O` | 打开文件 |
| `Ctrl+S` | 保存文件 |
| `Ctrl+C` | 复制（非编辑态：自动还原为 Markdown 源码） |
| `Ctrl+X` | 剪切（非编辑态：复制 Markdown 并删除选中内容；编辑态段内选区：立即提交并重定位光标；向外选区：复制 raw 文本并删除选区） |
| `Ctrl+V` | 粘贴（编辑态：多行自动拆分） |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` / `Ctrl+Shift+Z` | 重做 |
| `Ctrl+/` | 切换原文模式 |
| `Ctrl+B` | 切换侧边栏 |
| `Ctrl+Shift+L` | 切换亮 / 暗主题 |
| `Ctrl+,` | 打开设置 |
| `Ctrl+K` | 切换聚焦模式 |
| `Shift+Click` | 从编辑光标起始向外选区（跨段 / 跨行） |
| `Shift+←` / `Shift+→` | 向左 / 右扩展向外选区（段边界时起始选区） |
| `Shift+↑` / `Shift+↓` | 向上 / 下扩展向外选区 |
| `←` / `→` | 段间跨行移动（到边界时跳到相邻段；标题整行编辑时在行首 / 行尾跨行） |
| `↑` / `↓` | 上下行移动（按行内逻辑偏移定位） |
| `Home` / `End` | 跳到行首 / 行尾（`Ctrl+Home` / `Ctrl+End` 跳到文档首 / 末） |
| `Backspace` | 行首与前一行合并（删除换行符，光标落在合并点）；向外选区激活时删除选区 |
| `Delete` | 行尾与下一行合并（删除换行符，光标落在合并点）；段尾非末段直接删除下一段首字符；向外选区激活时删除选区 |
| `Tab` / `Shift+Tab` | 列表项缩进 / 取消缩进 |
| `Enter` | 提交当前段并换行（列表自动续行；标题在光标处拆分为两行） |
| `Escape` | 取消向外选区（选区激活时）；编辑态切换侧边栏（无选区时） |

工具栏按钮提供 H1–H3、加粗、斜体、链接、删除线、代码块、引用、列表、分隔线等格式操作（tooltip 中标注了对应的快捷键提示）。

## 架构设计

### 三级状态模型

```
Document ─── Line ─── Segment
  │           │          │
  │           │          └─ 最小可编辑单元（纯文本 / **加粗** / `代码` / 链接 …）
  │           └─ 块级行（标题 / 列表 / 引用 / 代码块 / 分隔线 …）
  └─ 整个文档（行列表 + 文件元信息）
```

三者均用 `@ft.observable` 装饰，字段变更自动触发依赖组件重绘，符合 `UI = f(state)` 声明式范式。

### 段级编辑流程

```
点击段 → activate（提交上一段）→ on_change 更新 draft
  → on_blur / on_submit 提交（reparse 该行）→ 重新渲染
```

**普通块**编辑态布局：`[前段 Text] + [激活段 TextField] + [后段 Text]`，仅激活段显示原生 Markdown。

**标题块**例外：编辑态使用单个 `TextField` 承载整行 `line.raw`（含 `#` 前缀），提交时整行 `reparse_line`，阅读态仍按段渲染样式。

**围栏块**（代码 / 公式）：使用 `CodeBlockEditor` 多行编辑，方向键在块内处理，`Backspace` / `Delete` 不触发行合并。

### 向外选区状态模型

```
outward_sel = (anchor_li, anchor_off, active_li, active_off) | None
```

- `*_off` 为行级 raw 偏移（段 raw 拼接后的逻辑偏移）
- `Shift+Click` / `Shift+Arrow` 起始：从当前编辑光标（`cursor_ref.extent` + 段偏移）作为 anchor，扩展到目标点
- 起始时 `set_active(None)` 退出编辑态，进入纯浏览态选区
- 删除 / 剪切：`_delete_raw_range` 按 raw 偏移直接操作文本，跨行时合并边界行，`parser.reparse_line` 重解析
- 光标落点：删除起点（`_locate_seg_by_raw_offset` 转 seg_idx + seg_offset）

### 键盘事件分发

`KeyDispatcher`（`views/key_bindings.py`）替代 main.py 的 on_key 闭包，持有 `actions_ref` 引用，每次渲染读取最新 `EditorActions`：

```
page.on_keyboard_event → KeyDispatcher.handle(e)
  ├─ 向外选区拦截块（outward_sel is not None 时优先路由）
  │   ├─ BackSpace / Delete → handle_outward_delete
  │   ├─ Ctrl+X → handle_outward_cut
  │   ├─ Escape → clear_outward_sel
  │   ├─ Shift+Arrow → extend_outward_*
  │   └─ 非 Shift 方向键 → clear_outward_sel
  ├─ layer 判定（active is not None → edit；否则 browse）
  ├─ edit 层：_handle_edit_nav（导航键 + Shift+Arrow 起始 outward）
  ├─ browse 层 BackSpace：handle_delete_selection（SelectionArea 选区）
  └─ _handle_shortcuts（save / new / open / copy / cut / paste / undo / redo …）
```

**关键路由点**：向外选区激活时 `active is None` → `layer=browse`，若不拦截则 `_handle_edit_nav` 不被调用、BackSpace 误路由到 SelectionArea 删除分支。因此在 `handle` 顶部加拦截块，在 layer 判定前优先路由 outward_sel 相关键。

### EditorActions 数据契约

`state/actions.py` 的 `EditorActions` dataclass 是 editor.py 每次渲染上抛给 App 层（main.py / key_bindings.py）的动作集合，替代旧的 `nav_ref` 字典。所有字段在构造时必填（缺失即报错），包含：

- 当前状态：`active` / `active_seg` / `draft` / `active_line` / `raw_mode` / `cursor_ref` / `selection_text_ref`
- 光标导航：`move_left` / `move_right` / `move_home` / `move_end` / `move_line_start` / `move_line_end` / `move_up` / `move_down`
- 删除 / 缩进：`backspace_core` / `delete_core` / `indent_or_outdent`
- 剪贴板 / 选区：`handle_paste` / `handle_cut` / `handle_delete_selection` / `compute_markdown_from_text`
- 向外选区：`outward_sel` / `shift_pressed_ref` / `extend_outward_{left,right,up,down}` / `handle_outward_cut` / `handle_outward_delete` / `handle_segment_cut_sync` / `handle_segment_cut_clipboard` / `clear_outward_sel`
- 全局动作：`undo` / `redo` / `jump_to_line` / `toggle_raw` / `toggle_focus_mode` / `exit_code_block`
- 代码块内部：`handle_tab_in_code` / `handle_backspace_in_code` / `handle_delete_in_code` / `handle_enter_in_code`
- 状态栏：`get_cursor_row_col`

### 文件结构

```
cs-markdown-editor/
├── main.py              # 入口：App 组件、文件操作、主题、侧边栏、状态栏组装
├── models.py            # 数据模型：Segment / Line / Document（@ft.observable）
├── parser.py            # Markdown 解析：行级 / 段级 / 选区↔源码 / HTML 导出
├── styles.py            # 主题配色、段→TextStyle、标题字重、列表色阶、文本测量
├── settings.json        # 用户设置（内容宽度、边距、字号、行高、主题、代码高亮等）
├── pyproject.toml       # 项目元数据与依赖
├── _verify_step1_2.py   # 自动化验证：拼接一致性 / staging reparse / 段定位往返
├── assets/
│   ├── fonts/
│   │   └── AlibabaPuHuiTi-3-55-Regular.otf
│   └── images/          # 示例图片等资源
├── services/
│   ├── history.py       # 撤销 / 重做栈：EditorSnapshot 快照（固定容量 50）
│   └── shortcuts.py     # 快捷键管理：ShortcutManager / matches / normalize
├── state/
│   ├── actions.py       # EditorActions dataclass：editor → main/key_bindings 动作契约
│   └── cursor.py        # CursorState：base / extent / draft_len 光标状态
└── views/
    ├── editor.py        # 编辑器根组件：状态编排、光标导航、向外选区、撤销 / 重做
    ├── line_view.py     # 行视图：渲染态 TextSpan + 编辑态段级布局 + Shift+Click 检测
    ├── segment_view.py  # 段级渲染：TextSpan（渲染）/ TextField（编辑）+ 选区高亮
    ├── key_bindings.py  # 键盘事件分发器：KeyDispatcher（浏览 / 编辑两层 + outward 拦截）
    ├── code_block_view.py # 代码块编辑器：CodeBlockEditor 多行编辑
    ├── table_view.py    # 表格视图：单元格编辑
    ├── toolbar.py       # 格式工具栏：块级 / 行内按钮
    ├── sidebar.py       # 侧边栏：文件树 / 大纲
    ├── settings_dialog.py # 设置对话框：五分区配置面板
    └── status_bar.py    # 状态栏：光标位置 / 文件信息
```

### 样式系统（`styles.py`）

| 能力 | 说明 |
|------|------|
| `get_colors(mode)` | 亮 / 暗两套 `Colors` 配色 |
| `heading_colors` | H1–H6 六级标题色（红橙绿青蓝紫） |
| `block_text_size` | 标题字号阶梯 30 → 24 → 20 → 18 → 16 → 16 |
| `block_weight` | 标题字重阶梯 W_800 → W_500 |
| `list_color_level` | 列表缩进 → 1..6 色阶（`indent // 2 + 1`） |
| `segment_style` | 行内段类型 → `TextStyle`，支持 `marks` 组合格式 |
| `measure_text_width` | Pillow 字体测量，驱动编辑块宽度自适应 |

### 关键设计决策

- **`SelectionArea` 包裹 `Column`**：用 `ft.SelectionArea` + `Column(scroll=AUTO)` 而非 `ListView`，解决垂直拖拽选择手势冲突
- **光标用 `ref` 而非 `state`**：`on_selection_change` 通过 `cursor_ref` 更新光标位置，避免输入时触发重渲染导致光标跳动
- **`draft_ref` 同步镜像**：闭包 `draft` 在 `set_draft` 后到下次渲染前是 stale 的，持续 `Delete` / `Backspace` 时 `delete_core` / `backspace_core` 需立即读到最新草稿；`_set_draft` 同步更新 `draft_ref` + 排队 `set_draft`
- **`applied_cursor` 拦截 stale 事件**：Flutter 聚焦时先触发 `on_focus`（设置正确光标），再触发 `on_selection_change`（段尾）；用 `applied_cursor` ref 识别并丢弃 stale 段尾事件，避免覆盖 `cursor_ref`
- **`use_effect` 显式聚焦**：`SelectionArea` 内 `autofocus` 因手势竞争不可靠，用 `async use_effect` + `await field.focus()` 确保编辑态光标可见
- **`nav_seq` 触发重建**：每次跨段导航递增 `nav_seq`，作为 TextField 的 `key` 强制重建以重新 `autofocus`
- **延迟 blur（`pending_blur`）**：重渲染会导致旧 TextField 卸载触发 `on_blur`，覆盖 `set_active`；`on_blur` 用 `pending_blur` ref + `asyncio.sleep(0.05)` 延迟 deactivate，`_goto` / `on_submit` / `handle_paste` / `set_block` / `toggle_raw` 取消 `pending_blur` 保留编辑状态
- **行首 / 行尾合并统一**：`Backspace` 在行首、`Delete` 在行尾对所有行内块类型（标题 / 列表 / 引用 / 段落）统一执行行合并，删除换行符并将光标定位到合并点；围栏块（代码 / 公式 / 分隔线 / 目录）不参与合并
- **撤销快照**：`EditorSnapshot` 记录 Markdown 全文 + 激活段 + 草稿 + 光标位置 + 原文模式状态；`_push_history` 在结构性操作前入栈，`undo` / `redo` 弹出快照恢复
- **块级前缀也是段**：`#`、`-`、`>` 统一抽象为 `Segment`；标题在阅读态隐藏前缀、编辑态整行原文
- **主题同步渲染**：`App` 在渲染期间同步写入 `page.theme_mode`，保证子组件 `_current_colors()` 取色与切换一致
- **向外选区路由拦截**：`outward_sel` 激活时 `active is None` → `layer=browse` → `_handle_edit_nav` 不被调用；在 `KeyDispatcher.handle` 顶部加拦截块，在 layer 判定前优先路由 BackSpace / Delete / Ctrl+X / Escape / Shift+Arrow 到 outward handlers
- **Shift 键状态跟踪**：Flet 0.86.1 的 `TapEvent` 无修饰键字段、`KeyDownEvent` 无 ctrl / shift 字段；用 `KeyboardListener.on_key_down`（key=="shift" → True）+ `on_key_up`（key=="shift" → False）跟踪 Shift 状态，`shift_pressed_ref` 传给 LineView 检测 Shift+Click
- **段内剪切同步执行**：`handle_segment_cut_sync` 同步捕获选区 + 剪切 + 提交（不通过 `page.run_task`），在原生 TextField 剪切前完成；原生剪切产生的 `on_change` 因值相等被 `on_change_draft` 去重跳过，避免「已剪切 draft + 旧光标选区」竞态导致双份剪切；剪贴板写入由 `handle_segment_cut_clipboard` 异步执行
- **`EditorActions` 替代 `nav_ref` 字典**：旧 `nav_ref.current = {20+ 字符串 key}` 字典改为 `EditorActions` dataclass，必填字段构造时校验，避免 `nav.get("xxx")` 静默失败

## 许可证

MIT
