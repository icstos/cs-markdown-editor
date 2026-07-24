# 产品需求文档（PRD）：cs-markdown-editor

| 项目 | 内容 |
|------|------|
| 项目名称 | cs-markdown-editor |
| 版本 | v0.1.0 |
| 文档日期 | 2026-07-24 |
| 状态 | 持续迭代 |
| 技术定位 | 基于 Flet 0.86.2 声明式组件 + mistune 实时渲染的 Typora 式段级所见即所得 Markdown 桌面编辑器 |

---

## 1. 项目概述

### 1.1 背景

传统 Markdown 编辑器多为「源码 / 预览」双栏模式，编辑时直面 Markdown 语法，阅读时切换到渲染视图，心智负担大、视觉割裂。Typora 首创「段级所见即所得」——渲染与编辑合一，点击某段才显露其原生语法，其余保持渲染样式，更贴近人类文档编辑直觉。

本项目在 Python 桌面端复刻并增强这一体验，面向需要在本地高效撰写技术文档、笔记、README 的开发者与知识工作者。

### 1.2 目标

- **核心目标**：实现 Typora 级别的段级 WYSIWYG 编辑体验，点击即编辑、实时渲染、所见即所得。
- **增强目标**：在段级编辑基础上，补齐精细化光标/选区系统、多文档标签页、代码块/表格等专业块级编辑能力，达到可与 Typora/VSCode 对标的桌面写作工具水准。
- **工程目标**：以 Flet 声明式范式（`@ft.component` + `use_state`/`use_effect` + `@ft.observable`）构建，保证 `UI = f(state)` 的可维护架构。

### 1.3 目标用户

- 开发者：撰写 README、技术文档、API 文档
- 知识工作者：整理笔记、知识库、长文
- 对编辑体验有较高要求、偏好本地离线工具的用户

### 1.4 典型场景

1. 打开/新建多个 `.md` 文档，标签页切换并行编辑
2. 撰写时点击加粗文本即显示 `**加粗**` 语法编辑，失焦自动渲染
3. 在代码块中直接编辑代码，享受语法高亮与行号
4. 在表格中点击单元格编辑，增删行列、设置对齐
5. 跨段 Shift+Click 选区后剪切/删除
6. 一键导出 HTML 分享

---

## 2. 功能性需求

### 2.1 编辑体验（核心）

| 编号 | 需求 | 优先级 | 状态 |
|------|------|--------|------|
| ED-01 | 段级编辑：点击行内任意段（加粗/斜体/代码/链接等）切换到该段原生 Markdown 编辑，其余段保持渲染 | P0 | 已实现 |
| ED-02 | 标题整行编辑：点击标题进入编辑态以整行原文（含 `#`）编辑，可增删 `#` 调整级别 | P0 | 已实现 |
| ED-03 | 跨段/行光标导航：方向键段间/行间无缝移动，Home/End 跳行首尾，Ctrl+Home/End 跳文档首末，PageUp/PageDown 翻页 | P0 | 已实现 |
| ED-04 | 记忆列：上下方向键跨短行时保持原列偏移（VSCode 风格） | P1 | 已实现 |
| ED-05 | 行首/行尾合并：Backspace 行首与前一行合并，Delete 行尾与下一行合并，光标落合并点 | P0 | 已实现 |
| ED-06 | 向外选区：Shift+Click / Shift+方向键 起始跨段/跨行选区，高亮覆盖；支持剪切/删除/取消 | P0 | 已实现 |
| ED-07 | 列表缩进：Tab/Shift+Tab 调整列表项缩进级别 | P0 | 已实现 |
| ED-08 | 撤销/重做：Ctrl+Z / Ctrl+Y（Ctrl+Shift+Z），基于快照栈（容量 50） | P0 | 已实现 |
| ED-09 | 智能复制粘贴：跨行复制自动还原 Markdown 源码；多行粘贴自动拆分为新行 | P0 | 已实现 |
| ED-10 | 智能剪切：渲染态/编辑态段内/向外选区三态剪切行为分别处理 | P1 | 已实现 |
| ED-11 | 原文模式：一键切换纯 Markdown 源码编辑 | P1 | 已实现 |
| ED-12 | 续行：Enter 提交并换行，列表自动续行（任务/有序递增），标题在光标处拆分 | P0 | 已实现 |

### 2.2 块级支持

| 编号 | 需求 | 优先级 | 状态 |
|------|------|--------|------|
| BK-01 | 标题 H1–H6：六级字号字重递进，阅读态隐藏 `#`，色阶区分（红橙绿青蓝紫） | P0 | 已实现 |
| BK-02 | 无序/有序列表：嵌套缩进，无序圆点按层级着色（与标题共用色阶），有序自动编号 | P0 | 已实现 |
| BK-03 | 任务列表：`- [ ]` / `- [x]`，可点击复选框切换 | P1 | 已实现 |
| BK-04 | 引用：多层嵌套，左侧竖线标识 | P1 | 已实现 |
| BK-05 | 代码块：基于 flet-code-editor，语法高亮、行号（位数自适应）、语言选择、可编辑、亮暗主题联动 | P0 | 已实现 |
| BK-06 | 行间公式：`$$...$$` | P2 | 已实现 |
| BK-07 | 分隔线：`---`/`***`/`___` | P2 | 已实现 |
| BK-08 | 目录：`[toc]` 块，点击条目跳转标题 | P2 | 已实现 |
| BK-09 | 表格：基于 flet-datatable2，单击单元格编辑，行列增删、对齐设置、Tab/Enter 导航、右键菜单 | P0 | 已实现 |

### 2.3 行内格式

加粗 `**`、斜体 `*`、行内代码 `` ` ``、删除线 `~~`、高亮 `==`、上标 `^`、下标 `~`、链接 `[]()`、图片 `![]()`、行内公式 `$...$`，支持组合语法（如 `***加粗斜体***`）。

### 2.4 多文档与文件

| 编号 | 需求 | 优先级 | 状态 |
|------|------|--------|------|
| FM-01 | 顶部多标签页：显示文件名，未保存修改标星号 `*` | P0 | 已实现 |
| FM-02 | 新建/打开/关闭/切换标签，关闭未保存时确认 | P0 | 已实现 |
| FM-03 | 新建/打开/保存（`.md`/`.markdown`/`.txt`） | P0 | 已实现 |
| FM-04 | 导出 HTML（mistune 渲染，含表格/脚注/任务列表扩展） | P1 | 已实现 |
| FM-05 | 标签快捷键：Ctrl+Tab/Ctrl+Shift+Tab 切换，Ctrl+W 关闭 | P1 | 已实现 |

### 2.5 辅助功能

| 编号 | 需求 | 优先级 | 状态 |
|------|------|--------|------|
| AU-01 | 侧边栏：文件树浏览 + 大纲导航 + 文档搜索 | P1 | 已实现 |
| AU-02 | 状态栏：光标位置、文件信息、字数统计 | P1 | 已实现 |
| AU-03 | 格式工具栏：H1–H3、加粗、斜体、链接、删除线、代码块、引用、列表、分隔线等 | P1 | 已实现 |
| AU-04 | 设置面板：编辑/外观/行为/快捷键/高级五分区配置 | P1 | 已实现 |
| AU-05 | 亮/暗主题切换，代码块高亮主题联动 | P1 | 已实现 |
| AU-06 | 专注模式（隐藏工具栏/侧边栏） | P2 | 已实现 |
| AU-07 | 快捷键可配置（settings.json） | P1 | 已实现 |

---

## 3. 非功能性需求

| 编号 | 维度 | 要求 |
|------|------|------|
| NF-01 | 平台 | 面向 Windows 桌面优先，macOS/Linux 待验证 |
| NF-02 | 性能 | 声明式重渲染需控制开销，大文档（千行级）编辑不卡顿 |
| NF-03 | 一致性 | UI = f(state)，模型层 observable 变更可靠触发重渲染 |
| NF-04 | 可维护性 | 三级状态模型分层清晰，动作契约（EditorActions）强类型 |
| NF-05 | 视觉 | 科学、有序、清爽、科技感、专业；亮暗主题对比度达标 |
| NF-06 | 交互 | 光标/选区行为贴合人类编辑直觉，点击精准定位 |
| NF-07 | Python | ≥ 3.12（使用 StrEnum） |

---

## 4. 功能模块

### 4.1 模块全景

```
┌─────────────────────────────────────────────────────────┐
│  App（main.py）：多标签模型 / 文件IO / 主题 / 组装       │
├─────────┬───────────────────────────────────────────────┤
│ TabBar  │  顶部多文档标签页                              │
├─────────┼───────────────────────────────────────────────┤
│ SideBar │  文件树 / 大纲 / 搜索                          │
├─────────┼───────────────────────────────────────────────┤
│         │  MarkdownEditor（编辑器根组件）                │
│  Editor ├───────────────────────────────────────────────┤
│  核心   │  ├─ Toolbar      格式工具栏                    │
│         │  ├─ LineView      行视图（渲染态+编辑态）       │
│         │  │   └─ SegmentView  段级渲染/编辑              │
│         │  ├─ CodeBlockView 代码块独立岛屿               │
│         │  ├─ TableView     表格独立岛屿                 │
│         │  ├─ KeyDispatcher 键盘事件分发                 │
│         │  └─ StatusBar    状态栏                        │
├─────────┼───────────────────────────────────────────────┤
│ Models  │  Document / Line / Segment（@ft.observable）   │
│ Parser  │  mistune 行内AST + 行级块识别 + HTML导出        │
│ Styles  │  主题配色 / 段样式 / 文本测量                   │
│ State   │  EditorActions / CursorState                   │
│ Services│  EditHistory / ShortcutManager                 │
└─────────┴───────────────────────────────────────────────┘
```

### 4.2 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 应用入口 | [main.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/main.py) | App 组件、多文档标签模型、文件操作、主题、侧边栏/状态栏组装、键盘事件接入 |
| 数据模型 | [models.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/models.py) | Segment/Line/Document 三级 observable 模型，SegType/BlockType 枚举 |
| 解析器 | [parser.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/parser.py) | 行级块识别、行内 mistune AST 解析、reparse_line、选区↔源码转换、HTML 导出 |
| 样式系统 | [styles.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/styles.py) | 亮暗配色、标题色阶/字重、列表色阶、段→TextStyle、Pillow 文本测量 |
| 编辑器根 | [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) | 状态编排、光标导航、向外选区、撤销重做、表格/代码块回调、行合并 |
| 行视图 | [views/line_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/line_view.py) | 单行渲染态 TextSpan + 编辑态段级布局 + Shift+Click 检测 + 拖拽选区 |
| 段视图 | [views/segment_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/segment_view.py) | 段级渲染 TextSpan / 编辑 TextField + 选区高亮 |
| 代码块 | [views/code_block_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) | flet-code-editor 多行编辑，语法高亮、行号、语言选择（独立岛屿） |
| 表格 | [views/table_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/table_view.py) | flet-datatable2 单元格编辑、行列增删、对齐、Tab/Enter 导航（独立岛屿） |
| 键盘分发 | [views/key_bindings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py) | KeyDispatcher：浏览/编辑两层 + 向外选区拦截 + 代码块/表格聚焦守卫 |
| 工具栏 | [views/toolbar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/toolbar.py) | 块级/行内格式按钮 |
| 标签栏 | [views/tab_bar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/tab_bar.py) | TabBar + 关闭确认对话框 |
| 侧边栏 | [views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) | 文件树 / 大纲 / 搜索三面板 |
| 设置 | [views/settings_dialog.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/settings_dialog.py) | 五分区配置面板 |
| 状态栏 | [views/status_bar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/status_bar.py) | 光标位置 / 文件信息 |
| 动作契约 | [state/actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/state/actions.py) | EditorActions dataclass：editor → main/key_bindings 强类型动作集合 |
| 光标状态 | [state/cursor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/state/cursor.py) | CursorState：base/extent/draft_len |
| 历史栈 | [services/history.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/history.py) | EditorSnapshot 快照撤销/重做（容量 50） |
| 快捷键 | [services/shortcuts.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/shortcuts.py) | ShortcutManager / matches / normalize |

### 4.3 独立岛屿架构

代码块与表格采用「独立岛屿」设计，区别于普通段的 active/draft 系统：

- **代码块**：始终可编辑的 CodeEditor，通过 `on_change_code` 原地更新行模型，仅在行数变化时触发重渲染更新高度。`code_focus_ref` 跟踪聚焦状态，KeyDispatcher 据此跳过全局导航键。
- **表格**：自管理编辑状态（edit_cell/edit_draft）的 DataTable2，通过 `on_change_cell` 原地更新、`on_table_op` 处理结构操作。`table_focus_ref` 跟踪聚焦，Tab/Enter/方向键交由内部 TextField 处理。

---

## 5. 技术方案

### 5.1 技术栈

| 依赖 | 版本 | 用途 |
|------|------|------|
| Flet | ≥ 0.86.2 | 声明式 GUI（`@ft.component` + `use_state`/`use_effect` + `@ft.observable`） |
| mistune | ≥ 3.3.4 | 行内 AST 解析 + HTML 导出（strikethrough/mark/上下标/table/task_lists/footnotes 插件） |
| Pillow | ≥ 12.3.0 | 文本像素宽度测量（编辑块自适应）+ 图片尺寸读取缩放 |
| flet-code-editor | latest | 代码块语法高亮编辑（基于 flutter_code_editor） |
| flet-datatable2 | latest | 表格渲染与编辑（DataTable 扩展） |
| Python | ≥ 3.12 | StrEnum 等特性 |

### 5.2 三级状态模型

```
Document ─── Line ─── Segment
  │           │          │
  │           │          └─ 最小可编辑单元（纯文本 / **加粗** / `代码` / 链接 …）
  │           └─ 块级行（标题 / 列表 / 引用 / 代码块 / 表格 …）
  └─ 整个文档（行列表 + 文件元信息）
```

三者均 `@ft.observable`，字段变更自动触发依赖组件重绘，符合 `UI = f(state)`。

### 5.3 段级编辑流程

```
点击段 → activate（提交上一段）→ on_change 更新 draft
  → on_blur/on_submit 提交（reparse 该行）→ 重新渲染
```

- **普通块**编辑态：`[前段 Text] + [激活段 TextField] + [后段 Text]`
- **标题块**：单个 TextField 承载整行 `line.raw`（含 `#`）
- **围栏块**（代码/公式/表格）：独立岛屿多行编辑，不参与行合并

### 5.4 键盘事件分发

`KeyDispatcher`（[key_bindings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py)）持有 `actions_ref`，每次渲染读最新 `EditorActions`：

```
page.on_keyboard_event → KeyDispatcher.handle(e)
  ├─ 代码块/表格聚焦守卫（跳过全局导航键，交由原生 TextField）
  ├─ 向外选区拦截块（outward_sel is not None 优先路由）
  ├─ layer 判定（active is not None → edit；否则 browse）
  ├─ edit 层：导航键 + Shift+Arrow 起始 outward
  ├─ browse 层 BackSpace：SelectionArea 选区删除
  └─ _handle_shortcuts（save/new/open/copy/cut/paste/undo/redo …）
```

### 5.5 向外选区模型

```
outward_sel = (anchor_li, anchor_off, active_li, active_off) | None
```

- `*_off` 为行级 raw 偏移（段 raw 拼接后的逻辑偏移）
- Shift+Click / Shift+Arrow 起始，起始时退出编辑态进入纯浏览选区
- 删除/剪切按 raw 偏移操作文本，跨行合并边界行后 `reparse_line`

### 5.6 关键设计决策

| 决策 | 说明 |
|------|------|
| SelectionArea 包裹 Column | 用 `ft.SelectionArea + Column(scroll=AUTO)` 而非 ListView，解决垂直拖拽手势冲突 |
| 光标用 ref 而非 state | `on_selection_change` 通过 `cursor_ref` 更新，避免输入时重渲染致光标跳动 |
| draft_ref 同步镜像 | 闭包 draft 在 set_draft 后到下次渲染前 stale，持续删除需立即读最新草稿 |
| nav_seq 触发重建 | 跨段导航递增 nav_seq 作 TextField key，强制重建重新 autofocus |
| 延迟 blur（pending_blur） | 重渲染旧 TextField 卸载触发 on_blur，延迟 0.05s deactivate 避免覆盖 set_active |
| 块级前缀也是段 | `#`/`-`/`>` 统一抽象为 Segment，前缀与行内 span 点击编辑行为一致 |
| 主题同步渲染 | App 渲染期同步写 `page.theme_mode`，保证子组件取色一致 |
| EditorActions 替代 nav_ref 字典 | 强类型 dataclass，必填字段构造校验，避免 `nav.get("xxx")` 静默失败 |
| 结构操作重建新 Line 对象 | 表格 add_col 等结构操作创建新 Line 替换原对象，确保元素引用变化触发 observable 重渲染 |

### 5.7 配置项（settings.json）

内容宽度、边距、字号、行高、字体、自动保存、专注模式、工具栏显隐、代码主题（亮 GITHUB / 暗 ATOM_ONE_DARK）、导出格式、侧边栏（开关/面板/宽度）、最近文件、快捷键映射（browse/edit 两层）。

---

## 6. 目录结构

```
cs-markdown-editor/
├── main.py                  # 入口：App 组件、多标签模型、文件IO、主题、组装
├── models.py                # 数据模型：Segment / Line / Document（@ft.observable）
├── parser.py                # Markdown 解析：行级/段级/选区↔源码/HTML 导出
├── styles.py                # 主题配色、段→TextStyle、标题字重、列表色阶、文本测量
├── settings.json            # 用户设置
├── pyproject.toml           # 项目元数据与依赖
├── README.md                # 项目说明
├── assets/
│   └── fonts/
│       └── AlibabaPuHuiTi-3-55-Regular.otf   # 主字体
├── services/
│   ├── history.py           # 撤销/重做栈：EditorSnapshot 快照（容量 50）
│   └── shortcuts.py         # 快捷键管理：ShortcutManager / matches / normalize
├── state/
│   ├── actions.py           # EditorActions dataclass：editor → main/key_bindings 动作契约
│   └── cursor.py            # CursorState：base/extent/draft_len
├── views/
│   ├── editor.py            # 编辑器根组件：状态编排、光标导航、向外选区、撤销重做
│   ├── line_view.py         # 行视图：渲染态 TextSpan + 编辑态段级布局 + 选区检测
│   ├── segment_view.py      # 段级渲染：TextSpan/TextField + 选区高亮
│   ├── key_bindings.py      # 键盘事件分发器：KeyDispatcher
│   ├── table_view.py        # 表格视图：DataTable2 单元格编辑、行列增删、对齐
│   ├── toolbar.py           # 格式工具栏
│   ├── sidebar.py           # 侧边栏：文件树/大纲/搜索
│   ├── tab_bar.py           # 顶部多标签栏
│   ├── settings_dialog.py   # 设置对话框：五分区配置
│   └── status_bar.py        # 状态栏
└── .trae/
    └── documents/           # 设计文档（本 PRD 及各重构计划）
```

---

## 7. 数据模型

### 7.1 SegType（段类型）

`TEXT` / `STRONG` / `EMPHASIS` / `CODESPAN` / `LINK` / `IMAGE` / `STRIKE` / `HIGHLIGHT` / `SUPERSCRIPT` / `SUBSCRIPT` / `INLINE_MATH` / `HEADING_PREFIX` / `LIST_PREFIX` / `QUOTE_PREFIX` / `CODE` / `MATH`

### 7.2 BlockType（块类型）

`PARAGRAPH` / `HEADING` / `LIST_UO` / `LIST_O` / `QUOTE` / `CODE` / `TABLE` / `HR` / `MATH` / `TOC` / `BLANK`

### 7.3 核心结构

- **Segment**：`seg_type` / `raw`（原生源码）/ `text`（显示文本）/ `url` / `level` / `marks`（组合格式元组）
- **Line**：`block_type` / `raw` / `segments` / `level` / `lang` / `ordered` / `task` / `checked`
- **Document**：`lines` / `file_path` / `dirty`

### 7.4 不变量

- `"".join(seg.raw for seg in line.segments) == line.raw`（段 raw 拼接还原行源码）
- 表格行 `block_type == TABLE`，分隔行单元格非空且匹配 `:?-{3,}:?`

---

## 8. 风险点与应对

| 编号 | 风险 | 影响 | 等级 | 应对 |
|------|------|------|------|------|
| R-01 | **observable 重渲染触发不可靠**：浅拷贝列表元素引用不变时，`document.lines = lines` 可能判定未变化不触发重渲染（已致 add_col 不生效） | 结构操作不即时生效 | 高 | 结构操作一律创建新 Line 对象替换，确保元素引用变化；已修复并纳入约定 |
| R-02 | **Flet 桌面端成熟度**：桌面模式需 GPU，沙箱环境易报 `hit restricted`；部分事件能力缺失（TapEvent 无修饰键、KeyDownEvent 无 ctrl/shift） | 部署/交互受限 | 中 | 沙箱内禁用 GPU 访问需关闭沙箱启动；修饰键用 KeyboardListener 跟踪 shift_pressed_ref |
| R-03 | **声明式重渲染性能**：大文档（千行级）每次 observable 变更重渲染整树可能卡顿 | 大文档编辑体验 | 中 | 段级编辑仅 reparse 当前行；独立岛屿（代码/表格）原地更新避免全局重渲染；后续可考虑虚拟化 |
| R-04 | **第三方扩展维护风险**：flet-code-editor / flet-datatable2 非核心 Flet 包，版本兼容与维护依赖上游 | 代码块/表格功能可能因升级失效 | 中 | 锁定可用版本；try/except 降级（DataTable2 → ft.DataTable） |
| R-05 | **mistune AST 与段级编辑一致性**：行内解析→Segment 转换需保证 raw 可还原，组合格式嵌套（`***加粗斜体***`）处理复杂 | 编辑后模型失真 | 中 | `_node_raw_text` 递归重建包裹语法；`_collect_marks` 收集组合标记；reparse_line 兜底 |
| R-06 | **光标/选区边界 case**：跨段导航、拖拽选区、Shift+Click、记忆列等存在大量边界，易回归 | 交互偶发异常 | 中 | ref 同步访问避免 stale；拖拽起始清旧选区；applied_cursor 拦截 stale 事件；持续回归测试 |
| R-07 | **撤销栈容量限制**：快照存全文 Markdown，容量 50，大文档内存占用高 | 内存压力 | 低 | 容量可配置；当前 50 折中；超大文档可考虑差分快照 |
| R-08 | **跨平台未验证**：面向 Windows 优先，macOS/Linux 字体路径、窗口行为未测 | 跨平台可用性 | 低 | 字体打包进 assets；后续在 macOS/Linux 验证 |
| R-09 | **字体依赖**：硬依赖 AlibabaPuHuiTi 字体，缺失时回退 | 视觉不一致 | 低 | assets/fonts 内置；缺失时系统字体回退 |
| R-10 | **editor.py 体量过大**（~10万字节） | 可维护性下降 | 中 | 后续按职责拆分（光标/选区/表格回调/行合并等子模块） |
| R-11 | **TextField 焦点/卸载竞态**：重渲染卸载旧 TextField 触发 on_blur，与 set_active/on_submit 竞态 | 编辑态异常退出/光标跳 | 中 | pending_blur 延迟 + nav_guard 守卫（表格/代码块）+ 取消机制 |
| R-12 | **Python 3.12+ 限制**：使用 StrEnum，低版本 Python 无法运行 | 部署门槛 | 低 | pyproject.toml 声明 requires-python ≥ 3.12 |

---

## 9. 后续演进方向（规划）

- 大文档虚拟化渲染（ListView 替代 Column）以突破性能瓶颈
- 图片粘贴/拖拽插入与本地资源管理
- 公式块基于 KaTeX/MathJax 实时预览
- 文档 diff 与版本管理
- 全局搜索替换
- 插件/自定义主题机制
- macOS/Linux 适配验证

---

## 10. 附录

### 10.1 快捷键速查

| 快捷键 | 功能 |
|--------|------|
| Ctrl+N / O / S | 新建 / 打开 / 保存 |
| Ctrl+Z / Y | 撤销 / 重做 |
| Ctrl+/ | 原文模式 |
| Ctrl+B | 侧边栏 |
| Ctrl+Shift+L | 主题 |
| Ctrl+, | 设置 |
| Ctrl+K | 专注模式 |
| Ctrl+Tab / Ctrl+Shift+Tab | 切换标签 |
| Ctrl+W | 关闭标签 |
| Shift+Click / Shift+方向键 | 向外选区 |
| Home/End, Ctrl+Home/End | 行首尾 / 文档首末 |
| Tab / Shift+Tab | 列表缩进 |
| Enter | 提交换行（列表续行） |
| Escape | 取消选区 |

### 10.2 相关设计文档

- [.trae/documents/top-tab-bar-multi-doc.md](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/.trae/documents/top-tab-bar-multi-doc.md) — 多文档标签页设计
- [.trae/documents/cursor-selection-refinement.md](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/.trae/documents/cursor-selection-refinement.md) — 光标系统精细化
- [.trae/documents/cursor-selection-system-refinement.md](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/.trae/documents/cursor-selection-system-refinement.md) — 选区系统精细化
- [.trae/documents/table-refactoring-plan.md](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/.trae/documents/table-refactoring-plan.md) — 表格重构计划
