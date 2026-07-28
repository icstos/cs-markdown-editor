# Markdown 编辑器

基于 [Flet](https://flet.dev) 0.86.2 声明式组件与 [mistune](https://mistune.lepture.com/) 实时解析，对标 [Typora](https://typora.io/) 的**光标级所见即所得（WYSIWYG）**编辑体验。

采用 **Stack 双层架构**：底层渲染层始终以渲染样式呈现 Markdown，顶层透明 TextField 承载光标与输入。每个字符输入即时渲染到文档，光标与渲染层文字像素级对齐——彻底消除传统「源码 / 预览」双栏割裂与「段级编辑」的语法噪声跳变。

## 特性

### 沉浸式编辑体验

- **光标级实时渲染**：点击任意位置即显示光标，输入字符立即融入渲染样式；语法标记（`#`、`**`、`` ` ``、`-`、`>` 等）在非激活段透明，仅光标所在段的最小语法标记变灰可见，对齐 Typora 最小语法噪声
- **像素级光标对齐**：基于 HarfBuzz（与 Skia/Flutter 同引擎）整形测量文本 advance，光标 X 坐标与渲染层 TextSpan 像素级贴合；Flet 默认 letter_spacing 已补偿
- **IME 友好**：透明 TextField 不设 `value` 属性，由 `use_effect` 异步清空内部值；同行输入 `key` 不变（基于 `li + nav_seq`），保持 IME 组合态不被重渲染打断
- **多文档标签页**：顶部标签栏支持并行编辑多个文档，未保存修改标星号 `*`；新建 / 关闭 / 切换标签，关闭未保存文档时弹出确认对话框；右键菜单支持「关闭 / 关闭其他 / 关闭全部 / 复制路径」
- **跨段光标导航**：方向键在段间 / 行间无缝移动，`Home` / `End` 跳转行首 / 行尾，`Ctrl+Home` / `Ctrl+End` 跳文档首末，`PageUp` / `PageDown` 翻页；上下方向键跨短行时记忆列偏移（VSCode 风格），点击命中按中点吸附到最近段边界
- **行首 / 行尾合并**：`Backspace` 在行首与前一行合并，`Delete` 在行尾与下一行合并——所有行内块类型（标题 / 列表 / 引用 / 段落）行为一致，光标落在合并点
- **向外选区**：`Shift+Click` 或 `Shift+方向键` 从编辑光标起始跨段 / 跨行选区，高亮覆盖范围；支持 `Ctrl+X` 剪切、`Backspace` / `Delete` 删除、`Escape` 取消、`Ctrl+C` 复制选区 raw 文本
- **行内格式快捷键**：`Ctrl+B` 加粗、`Ctrl+I` 斜体、`Ctrl+U` 高亮、`Ctrl+Shift+S` 删除线、`` Ctrl+` `` 行内代码、`Ctrl+K` 链接；选中文本自动包裹对应语法并**保持选区**，再次按下同一快捷键取消语法标记（Toggle 行为），无选中插入空语法标记（光标落标记中间）；浏览态无需先进入编辑态即可生效，渲染态同段选区同样支持包裹
- **URL 智能折叠**：链接 `[text](url)` 与图片 `![alt](url)` 的 URL 子段根据光标位置动态折叠——光标在文本/alt 段时 URL 折叠为零宽度（最小语法噪声），光标进入 URL 段时完整可见；链接编辑视为常规文本编辑，光标移出链接区间即自动折叠回渲染态
- **自适应宽度与软换行**：长行超出视口时按可用宽度自动断行（CJK 逐字、西文按词），`Alt+Z` 一键切换自动换行开关（VSCode 风格，设置持久化）；窗口尺寸变化时段落宽度实时自适应重排
- **向右拆分编辑器**：`Ctrl+\` 将当前文档在右侧拆分出第二视口（VSCode 风格），两视口共享同一文档、独立光标与滚动，便于多处对照查看与编辑
- **列表 / 引用缩进**：`Tab` / `Shift+Tab` 在列表项内调整缩进级别；在引用行内调整引用嵌套层级（增减 `>` 前缀），左侧彩色边框随之重排
- **行级撤销快照**：单行编辑（字符输入 / backspace / delete / 行内格式包裹）使用 `LineEditSnapshot` 行级快照（内存 O(1)/操作），行结构变化（回车 / 行合并 / 多行粘贴）才使用全文快照；撤销栈固定容量 50，大文档不膨胀
- **智能复制粘贴**：跨行复制自动还原为 Markdown 源码；多行粘贴自动拆分为新行
- **智能剪切**：渲染态 `Ctrl+X` 复制 Markdown 源码并删除选中；编辑态段内选区同步提交剪切后草稿（避免与原生 TextField 剪切竞态导致双份剪切）；向外选区复制 raw 文本并删除选区
- **原文模式**：一键切换到纯 Markdown 源码编辑
- **设置面板**：编辑 / 外观 / 行为 / 快捷键 / 高级五个分区，可配置内容宽度、边距、字号、行高、字体、自动保存、专注模式、工具栏显隐、代码主题、导出格式等

### 块级支持

| 类型 | 说明 |
|------|------|
| 标题 H1–H6 | 六级字号与字重递进；阅读态隐藏 `#`，用颜色区分级别；`Ctrl+1`–`6` 切换级别、`Ctrl+0` 恢复段落 |
| 无序 / 有序列表 | 嵌套缩进；无序列表圆点 `•` 按层级着色（与标题共用色阶），有序保留数字 |
| 任务列表 | `- [ ]` / `- [x]`，可点击复选框切换状态 |
| 引用 | 支持多层嵌套，左侧逐层包裹彩色边框（复用标题色阶红橙绿青蓝紫，半透明柔和色调），`Tab` / `Shift+Tab` 行内调整引用层级 |
| 代码块 | 基于 flet-code-editor，语法高亮（亮色 GitHub / 暗色 One Dark）、行号（位数自适应）、语言选择下拉、折叠、复制按钮、可直接编辑；始终可编辑的独立岛屿 |
| 行间公式 | `$$...$$`；浏览态 ft.Markdown 渲染 LaTeX，点击进入编辑态同时显示源码编辑器与实时渲染预览（垂直堆叠） |
| 分隔线 | `---` / `***` / `___` |
| 目录 | `[toc]` 卡片式目录：头部图标 + 标题 + 计数，彩色细竖线区分标题级别，同级别左对齐，H1/H2 加粗；点击条目跳转对应标题（带高亮脉冲反馈） |
| 表格 | 基于 flet-datatable2，单击单元格编辑，行列增删、对齐设置、`Tab` / `Enter` 单元格导航、右键菜单；自管理独立岛屿 |

### 行内格式

加粗、斜体、行内代码、删除线、==高亮==、上标 `^x^`、下标 `~x~`、链接、图片、行内公式 `$...$`，支持组合语法（如 `***加粗斜体***`）。

### 视觉与主题

- **标题色阶**：红 → 橙 → 绿 → 青 → 蓝 → 紫（H1–H6），亮 / 暗主题各自适配对比度
- **标题字重**：H1 `W_800` 至 H6 `W_500`，逐级递减
- **列表圆点**：嵌套层级复用标题色阶（每 2 空格一级）
- **亮 / 暗主题**：工具栏一键切换，代码块高亮主题随主题联动
- **向外选区高亮**：选区段注入半透明背景色（`link` 色 22% 不透明度），与激活行色调一致
- **侧边栏开合动画**：宽度 200ms `EASE_OUT` 平滑过渡，`HARD_EDGE` 裁剪防溢出
- **跳转高亮脉冲**：大纲 / 搜索 / TOC 点击跳转后目标行 300ms 淡蓝底脉冲反馈
- **当前行高亮**：激活行左侧 3px `link` 色边条 + 半透明背景，编辑焦点清晰可辨
- **行内代码 / 公式选区着色**：左键拖选时背景色随选区高亮变化
- **标签栏紧凑化**：固定宽度 200px、文件名超出省略、鼠标悬停显示全名 tooltip、整体高度收紧，保持简洁专业
- **软换行视觉行布局**：长行按可用宽度切分为多个视觉行，渲染层与光标测量共用同一断行算法，光标 Y 按视觉行精确定位；上下方向键 / PageUp / PageDown 按视觉行导航（非逻辑行），跨视觉行一致列定位

### 文件与导出

- 新建 / 打开 / 保存（`.md` / `.markdown` / `.txt`）
- 导出 HTML（mistune 渲染，含表格、脚注、任务列表等扩展）
- 自动保存（可配置间隔，异步回写避免阻塞 UI）
- 最近文件列表（侧边栏无 `file_path` 时显示）

## 技术栈

| 依赖 | 用途 |
|------|------|
| [Flet](https://flet.dev) ≥ 0.86.2 | 声明式 GUI（`@ft.component` + `use_state` / `use_effect` + `@ft.observable` / `@ft.memo`） |
| [mistune](https://mistune.lepture.com/) ≥ 3.3.4 | 行内 AST 解析；HTML 导出（含 strikethrough / mark / 上下标 / 表格等插件） |
| [uharfbuzz](https://github.com/harfbuzz/uharfbuzz) ≥ 0.40.0 | 文本整形测量（与 Skia/Flutter 同引擎，光标像素级对齐渲染层文字） |
| [Pillow](https://pillow.readthedocs.io/) ≥ 12.3.0 | 图片尺寸读取与缩放 |
| [flet-code-editor](https://pub.dev/packages/flet_code_editor) ≥ 0.86.2 | 代码块语法高亮编辑（基于 flutter_code_editor，行号 / 高亮 / 语言切换 / 折叠） |
| [flet-datatable2](https://pub.dev/packages/flet_datatable2) ≥ 0.86.2 | 表格渲染与编辑（DataTable 扩展，固定表头 / 单元格编辑） |

> **Python** ≥ 3.12（`pyproject.toml`）；模型层使用 `StrEnum`（3.11+ 特性）

## 安装与运行

**推荐**（基于 `pyproject.toml`）：

```bash
pip install -e .
python main.py
```

或安装依赖后运行：

```bash
pip install flet mistune pillow uharfbuzz flet-code-editor flet-datatable2
python main.py
```

安装后也可通过入口命令启动：

```bash
cs-markdown-editor
```

## 快捷键

所有快捷键均可在 **设置 → 快捷键** 中自定义：点击「修改」→ 按下新组合键 → 立即生效；`Esc` 取消、`Backspace` 清空。支持冲突检测、导入 / 导出方案、恢复默认。

### 浏览态（无激活行）

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+N` | 新建文档 |
| `Ctrl+O` | 打开文件 |
| `Ctrl+S` | 保存文件 |
| `Ctrl+W` | 关闭当前标签（脏标签走确认） |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | 切换到右 / 左标签（循环） |
| `Ctrl+C` | 复制（自动还原为 Markdown 源码） |
| `Ctrl+X` | 剪切（复制 Markdown 并删除选中内容） |
| `Ctrl+V` | 粘贴（多行自动拆分） |
| `Ctrl+A` | 全选文档 |
| `Ctrl+Z` | 撤销 |
| `Ctrl+Y` / `Ctrl+Shift+Z` | 重做 |
| `Ctrl+/` | 切换原文模式 |
| `Ctrl+Shift+B` | 切换侧边栏 |
| `Ctrl+Shift+L` | 切换亮 / 暗主题 |
| `Ctrl+,` | 打开设置 |
| `Ctrl+Shift+K` | 切换聚焦模式 |
| `Alt+Z` | 切换自动换行（VSCode 风格） |
| `Ctrl+\` | 向右拆分编辑器 / 关闭拆分（VSCode 风格，多视口查看同一文档） |

### 编辑态（光标在文档中）

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+0` | 当前行恢复为普通段落 |
| `Ctrl+1` – `Ctrl+6` | 当前行为 H1–H6 标题 |
| `Ctrl+B` | 加粗（选中包裹 `**` 并保持选区，再次按下取消标记；无选中插入空标记 `**\|**`） |
| `Ctrl+I` | 斜体（选中包裹 `*` 并保持选区，再次按下取消；无选中插入空标记） |
| `Ctrl+U` | 高亮（选中包裹 `==` 并保持选区，再次按下取消；无选中插入空标记） |
| `Ctrl+Shift+S` | 删除线（选中包裹 `~~` 并保持选区，再次按下取消；无选中插入空标记） |
| `` Ctrl+` `` | 行内代码（选中包裹 `` ` `` 并保持选区，再次按下取消；无选中插入空标记） |
| `Ctrl+K` | 链接（选中包裹 `[选区](url)`，无选中插入 `[](url)`；链接编辑视为常规文本编辑） |
| `Ctrl+Enter` | 切换原文模式 |
| `Escape` | 切换侧边栏（无向外选区时） |
| `Ctrl+S` / `Ctrl+Z` / `Ctrl+Y` / `Ctrl+Shift+Z` | 保存 / 撤销 / 重做（与浏览态一致） |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` / `Ctrl+A` | 剪贴板 / 全选（与浏览态一致） |

### 光标导航（固定，不可自定义）

| 快捷键 | 功能 |
|--------|------|
| `←` / `→` | 段间跨行移动（到边界时跳到相邻段；标题整行编辑时在行首 / 行尾跨行）；软换行时跨视觉行移动 |
| `↑` / `↓` | 按视觉行上下移动（软换行开启时按视觉行而非逻辑行），跨行记忆列偏移 |
| `Home` / `End` | 跳到行首 / 行尾 |
| `Ctrl+Home` / `Ctrl+End` | 跳到文档首 / 末 |
| `PageUp` / `PageDown` | 按视觉行向上 / 下翻页 |
| `Backspace` | 行首与前一行合并（删除换行符，光标落在合并点）；向外选区激活时删除选区 |
| `Delete` | 行尾与下一行合并（删除换行符，光标落在合并点）；向外选区激活时删除选区 |
| `Tab` / `Shift+Tab` | 列表项缩进 / 取消缩进；引用行调整嵌套层级；表格内单元格导航 |
| `Enter` | 提交当前段并换行（列表自动续行；标题在光标处拆分为两行）；表格内移动到下行同列 |
| `Shift+Click` | 从编辑光标起始向外选区（跨段 / 跨行）；渲染态同段选中可被行内格式快捷键包裹 |
| `Shift+←` / `Shift+→` | 向左 / 右扩展向外选区（段边界时起始选区） |
| `Shift+↑` / `Shift+↓` | 向上 / 下扩展向外选区 |

工具栏按钮提供 H1–H3、正文、列表、引用、代码块、分隔线，以及加粗、斜体、高亮、行内代码、链接、删除线等行内格式操作，tooltip 动态显示当前绑定的快捷键。

## 架构设计

### 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│  main.py            入口：App 组件、多文档标签、文件 IO、主题、│
│                     侧边栏、状态栏、设置弹层、拆分编辑器组装   │
├──────────────────────────────────────────────────────────────┤
│  views/             声明式 Flet 视图组件                      │
│    editor.py        编辑器根组件（Stack 双层状态编排）         │
│    line_view.py     行视图（围栏岛屿 / RenderedLine + Stack）  │
│    rendered_line.py 渲染层：TextSpan + GestureDetector 命中   │
│    cursor_layer.py  透明光标 TextField（IME 友好）            │
│    pixel_layout.py  像素布局缓存 + 命中测试                   │
│    segment_view.py  段级 TextSpan 渲染                        │
│    key_bindings.py  KeyDispatcher 键盘事件分发                │
│    toolbar.py / sidebar.py / tab_bar.py / status_bar.py       │
│    settings_dialog.py / table_view.py                         │
├──────────────────────────────────────────────────────────────┤
│  core/              编辑器核心状态                            │
│    actions.py       EditorActions：editor → App 动作契约      │
│    cursor.py        CursorState：光标位置镜像（ref 非 state）  │
│    history.py       EditHistory：撤销/重做栈（混合快照）      │
├──────────────────────────────────────────────────────────────┤
│  services/          业务逻辑层                                │
│    shortcuts.py     ShortcutManager：读取/更新/冲突检测       │
│    file_io.py       read_text / write_text                    │
├──────────────────────────────────────────────────────────────┤
│  utils/             通用工具层（无项目内依赖）                │
│    segment_helpers  段类型常量 / display_text / 段拆分        │
│    text_layout      HarfBuzz 文本测量 / 图片尺寸              │
│    table_helpers    表格行解析与拼接                          │
│    file_helpers     文件名派生                                │
├──────────────────────────────────────────────────────────────┤
│  config/            配置层                                    │
│    settings.py      DEFAULT_SETTINGS / load / save（深合并）  │
│    sample.py        示例文档                                  │
├──────────────────────────────────────────────────────────────┤
│  models/            数据模型（@ft.observable）                │
│    document.py      Segment / Line / Document 三级状态        │
├──────────────────────────────────────────────────────────────┤
│  parser.py          Markdown 解析：行级 / 段级 / 选区↔源码    │
│  styles.py          主题配色 / 段→TextStyle / 排版常量        │
└──────────────────────────────────────────────────────────────┘
```

### 三级状态模型

```
Document ─── Line ─── Segment
  │           │          │
  │           │          └─ 最小可编辑单元（纯文本 / **加粗** / `代码` / 链接 …）
  │           └─ 块级行（标题 / 列表 / 引用 / 代码块 / 分隔线 …）
  └─ 整个文档（行列表 + 文件元信息）
```

三者均用 `@ft.observable` 装饰，字段变更自动触发依赖组件重绘，符合 `UI = f(state)` 声明式范式。`models/` 是包，`__init__.py` 重新导出所有公共符号，保持 `from models import Document` 等引用兼容。

### Stack 双层光标级架构

```
┌─────────────────────────────────────────────┐
│  ft.Stack                                   │
│  ┌───────────────────────────────────────┐  │
│  │  底层：RenderedLine                    │  │
│  │  - TextSpan 列表（raw_to_visible_spans）│  │
│  │  - cursor_off=None：所有标记透明       │  │
│  │  - cursor_off=int：光标段标记变灰可见  │  │
│  │  - GestureDetector：点击/拖拽命中测试  │  │
│  ├───────────────────────────────────────┤  │
│  │  顶层：cursor_text_field（透明）       │  │
│  │  - 全透明背景/边框/文字                │  │
│  │  - StrutStyle 与底层共用（行高对齐）   │  │
│  │  - Stack 内绝对定位 (cursor_px_x, 0)   │  │
│  │  - 不设 value（IME 友好）              │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**编辑流（光标级，无段级编辑态，IME 友好）**：

```
点击渲染层 → hit_test(x, y) → (li, raw_off) → set_cursor(li, off) → 重渲染
  → use_effect 调 cursor_field.focus() → 透明 TextField 聚焦，光标像素位置闪烁
  → 输入字符 → TextField.on_change(value) → handle_char_input（ignore/replace/append）
  → line.raw 插入 value → parser.reparse_line_atomic（原子化，1 次 observable 通知）
  → cursor_ref.reset(off + len(value))  # 不递增 nav_seq，保持 IME 组合态
  → use_effect([clear_value_seq]) 异步清空 TextField 内部 value → 准备下次输入
  → 渲染层 Text 显示新内容，TextField 重新定位到新光标位置
```

**围栏岛屿**（CODE / TABLE / MATH / HR / TOC）：自管理独立可编辑控件，不进入 Stack。代码块用 `CodeEditor` 始终可编辑；表格用 `DataTable2` 单元格编辑；公式 / 分隔线 / TOC 视图态渲染。

### 像素布局与命中测试（`views/pixel_layout.py`）

```
LineLayoutCache（每次渲染重建）
  ├─ 逐行累加 Y：top / height / text_top / text_height
  ├─ 逐段累加 X：raw_offsets_x[0..len(raw)]（相对文字左起点）
  │   ├─ cursor_raw_offset=None（浏览态）：所有段标记折叠，仅 display_text 占宽度
  │   └─ cursor_raw_offset=int（激活行）：光标段 measure_text_offsets 整形 raw
  │      （含标记宽度，捕获 kerning），其余段标记折叠
  ├─ cursor_px(li, off) → (x, y_in_stack, height)：光标像素位置
  └─ hit_test(x, y) → (li, raw_offset)：先 y 二分定位行，再 x 中点吸附 + 折叠标记扫描
```

**光标在段内**：该段标记变灰可见占宽度（逐字符测量 raw）；其余段标记折叠。保证光标 X 与渲染层 TextSpan 像素级对齐（HarfBuzz 与 Skia 同引擎）。

### 自适应宽度与软换行（2D 视觉行布局）

开启自动换行（`Alt+Z`，默认开）时，长行按可用宽度切分为多个视觉行，光标从 1D（行内偏移）升级为 2D（视觉行 + 列）定位：

```
_line_visual_layout(line, base, wrap_width, cursor_raw_offset, line_height)
  ├─ 按可用宽度 wrap_width 切分逻辑行 → N 个 VisualLine
  │   ├─ CJK 逐字断行、西文按词断行（保留断行点偏移）
  │   ├─ 每个 VisualLine 记录 start_raw / offsets_x[]（逐字符 X 像素）
  │   └─ vline_idx（视觉行序号，用于光标 Y 定位）
  ├─ 渲染层 _maybe_stack_multi：按视觉行切片 flat spans → 多个单行 Text
  └─ 光标层 _cursor_overlay：_find_vline_for_raw 定位视觉行
      └─ cursor_px_y = vline.vline_idx * text_h（2D Y 定位）

raw-flat 映射（_build_raw_to_flat_map）
  └─ 原始 Markdown 偏移 ↔ 渲染文本位置，支持跨视觉行 span 切片
```

渲染层与光标测量**共用同一 `_line_visual_layout` 断行算法**，换行点天然一致；上下方向键 / PageUp / PageDown 按视觉行导航，跨视觉行保持记忆列（`preferred_col_ref`）。窗口尺寸变化时 `viewport_w` state 触发 `content_width` 重算，段落实时重排。

### 向右拆分编辑器（多视口）

`Ctrl+\`（VSCode 风格）将当前文档在右侧拆分出第二个 `MarkdownEditor` 视口：

```
main.py
  ├─ split_editor state（是否拆分）+ active_pane state（0=左, 1=右 焦点跟踪）
  ├─ nav_ref / nav_ref_split：两视口各自的 EditorActions 引用
  ├─ 两视口共享同一 document（@ft.observable，修改自动同步）
  ├─ KeyDispatcher.actions_ref = 焦点视口的 nav_ref（键盘事件作用于焦点视口）
  └─ 左视口 key=f"{session}-0"（拆分 / 非拆分同 key → 切换拆分不重置左视口光标）
```

焦点跟踪：光标 TextField 聚焦时经 `on_cursor_focus → on_editor_focus → _set_active_pane` 切换 `active_pane`（同值不重渲染）；状态栏光标位置、TOC 跳转、键盘事件均跟随焦点视口。右视口隐藏工具栏（`show_toolbar=False`）且不抢占 autofocus（`keyboard_autofocus=False`），保持简洁。

### EditorActions 数据契约

`core/actions.py` 的 `EditorActions` dataclass 是 editor.py 每次渲染上抛给 App 层（main.py / key_bindings.py）的动作集合，替代旧的 `nav_ref` 字典。所有字段在构造时必填（缺失即报错），包含：

- **当前状态**：`cursor_li` / `cursor_off` / `active_line` / `raw_mode` / `cursor_ref` / `selection_text_ref` / `nav_seq`
- **光标导航**：`move_left` / `move_right` / `move_home` / `move_end` / `move_doc_start` / `move_doc_end` / `move_up` / `move_down` / `page_up` / `page_down`
- **删除 / 缩进**：`backspace_core` / `delete_core` / `indent_or_outdent`
- **剪贴板 / 选区**：`handle_paste` / `handle_cut` / `handle_delete_selection` / `apply_inline_format_to_selection` / `compute_markdown_from_text`
- **向外选区**：`outward_sel` / `shift_pressed_ref` / `ctrl_pressed_ref` / `extend_outward_{left,right,up,down}` / `handle_outward_{cut,delete,copy}` / `clear_outward_sel` / `select_all`
- **全局动作**：`undo` / `redo` / `jump_to_line` / `toggle_raw` / `toggle_focus_mode` / `set_block`（Ctrl+0~6 标题级别）/ `apply_inline_format`（Ctrl+B/I/U/… 行内格式）
- **代码块 / 表格**：`code_focus_ref` / `table_focus_ref`（聚焦守卫）
- **状态栏**：`get_cursor_row_col`

### 撤销 / 重做（混合快照）

```python
Snapshot = EditorSnapshot | LineEditSnapshot

@dataclass(frozen=True)
class EditorSnapshot:    # 全文快照：行结构变化（回车 / 行合并 / 多行粘贴）
    markdown: str
    cursor_li: int | None
    cursor_off: int
    raw_mode: bool
    raw_draft: str

@dataclass(frozen=True)
class LineEditSnapshot:  # 行级快照：单行编辑（字符输入 / backspace / 行内格式）
    line_idx: int
    raw: str
    cursor_li: int | None
    cursor_off: int
    raw_mode: bool
    raw_draft: str
```

`EditHistory` 固定容量 50，混合存储两种快照：单行编辑走 `LineEditSnapshot`（内存 O(1)/操作，大文档撤销栈不膨胀）；行结构变化走 `EditorSnapshot` 全文序列化。相邻相同快照去重。

### 键盘事件分发

`KeyDispatcher`（`views/key_bindings.py`）替代 main.py 的 on_key 闭包，持有 `actions_ref` 引用，每次渲染读取最新 `EditorActions`：

```
page.on_keyboard_event → KeyDispatcher.handle(e)
  ├─ 快捷键捕获模式（capturing != (None, None) 时优先拦截，写入新组合键）
  ├─ 原生编辑控件聚焦守卫（code_focus_ref / table_focus_ref → 放行导航 + 剪贴板键）
  ├─ 向外选区拦截块（outward_sel is not None 时优先路由）
  │   ├─ BackSpace / Delete → handle_outward_delete
  │   ├─ Ctrl+X → handle_outward_cut
  │   ├─ Ctrl+C → handle_outward_copy
  │   ├─ Escape → clear_outward_sel
  │   ├─ Shift+Arrow → extend_outward_*
  │   └─ 非 Shift 方向键 → clear_outward_sel
  ├─ layer 判定（cursor_li is not None → edit；否则 browse）
  ├─ edit 层：_handle_edit_nav（导航键 + Shift+Arrow 起始 outward）
  ├─ browse 层 Backspace：handle_delete_selection（SelectionArea 选区）
  └─ _handle_shortcuts（save / new / open / copy / cut / paste / undo / redo …）
```

**关键路由点**：向外选区激活时 `cursor_li is None` → `layer=browse`，若不拦截则 `_handle_edit_nav` 不被调用、Backspace 误路由到 SelectionArea 删除分支。因此在 `handle` 顶部加拦截块，在 layer 判定前优先路由 outward_sel 相关键。

### 快捷键自定义（捕获式）

```
设置 → 快捷键 tab → 点击「修改」
  → set_capturing((layer, action_id))
  → KeyDispatcher.handle 顶部拦截下一个组合键
  → _on_capture(layer, action_id, combo)
  → shortcut_mgr.update(layer, action_id, combo)
  → set_capturing((None, None)) 退出捕获模式
  → 改键立即生效（dispatcher_ref.current 每次渲染读取最新实例）
```

`dispatcher_ref` 解决空依赖 effect 闭包捕获首次 dispatcher 的 bug：每次渲染 `dispatcher_ref.current = dispatcher`，`_handler` 通过 ref 读取最新实例，改键后即时生效。`Esc` 取消捕获、`Backspace` 清空绑定。

### 文件结构

```
cs-markdown-editor/
├── main.py                  # 入口：App 组件、多文档标签模型、文件操作、主题、侧边栏、状态栏组装
├── parser.py                # Markdown 解析：行级 / 段级 / 选区↔源码 / HTML 导出
├── styles.py                # 主题配色、段→TextStyle、标题字重、列表色阶、Border 工具
├── settings.json            # 用户设置（内容宽度、边距、字号、行高、主题、代码高亮、快捷键等）
├── pyproject.toml           # 项目元数据与依赖
├── assets/
│   ├── fonts/
│   │   └── AlibabaPuHuiTi-3-55-Regular.otf
│   └── images/              # 示例图片等资源
├── config/
│   ├── settings.py          # DEFAULT_SETTINGS / load_settings / save_settings（深合并 shortcuts）
│   └── sample.py            # 示例文档 SAMPLE_MD
├── core/
│   ├── actions.py           # EditorActions dataclass：editor → main/key_bindings 动作契约
│   ├── cursor.py            # CursorState：base / extent / draft_len 光标状态
│   └── history.py           # EditHistory：撤销/重做栈（EditorSnapshot | LineEditSnapshot）
├── models/
│   └── document.py          # Segment / Line / Document（@ft.observable）
├── services/
│   ├── shortcuts.py         # ShortcutManager：读取/更新/重置/冲突检测 + ACTION_REGISTRY
│   └── file_io.py           # read_text / write_text
├── utils/
│   ├── segment_helpers.py   # PREFIX_SEGTYPES / MONO_SEGTYPES / WRAP_SYNTAX / display_text / split_seg_for_display
│   ├── text_layout.py       # HarfBuzz measure_text_offsets / measure_text_width / image_fit_size
│   ├── table_helpers.py     # 表格行解析与拼接、对齐正则
│   └── file_helpers.py      # 文件名派生等文件工具
└── views/
    ├── editor.py            # 编辑器根组件：Stack 双层状态编排、光标导航、向外选区、撤销/重做、行内格式
    ├── line_view.py         # 行视图：围栏岛屿分支 + RenderedLine + Stack + 跳转高亮
    ├── rendered_line.py     # 渲染层：raw_to_visible_spans + GestureDetector 命中测试
    ├── cursor_layer.py      # 透明光标 TextField（IME 友好，StrutStyle 行高对齐）
    ├── pixel_layout.py      # LineLayoutCache：像素布局缓存 + cursor_px + hit_test
    ├── segment_view.py      # 段级 TextSpan 渲染（含向外选区字符级高亮）
    ├── key_bindings.py      # KeyDispatcher：浏览/编辑两层 + outward 拦截 + 快捷键捕获 + 原生控件守卫
    ├── table_view.py        # 表格视图：DataTable2 单元格编辑、行列增删、对齐、Tab/Enter 导航
    ├── toolbar.py           # 格式工具栏：块级/行内按钮，tooltip 动态显示自定义键位
    ├── tab_bar.py           # 顶部多文档标签栏 + ConfirmCloseDialog + 右键菜单
    ├── sidebar.py           # 侧边栏：文件树 / 大纲（点击跳转带高亮脉冲）/ 搜索
    ├── settings_dialog.py   # 设置对话框：五分区配置面板 + 快捷键捕获式自定义
    └── status_bar.py        # 状态栏：光标行列 / 段落数 / 词数 / 字符数 / 阅读时长 / 换行 / 拆分指示
```

```
tests/                      # 单元测试（python -m tests.test_<name>）
├── test_soft_wrap.py       # 软换行 2D 视觉行布局 / raw-flat 映射 / span 切片（37 项）
├── test_table_smoke.py     # 表格行解析与拼接冒烟测试
└── test_task_smoke.py      # 任务列表解析冒烟测试
```

### 样式系统（`styles.py`）

| 能力 | 说明 |
|------|------|
| `Colors` dataclass | 亮 / 暗两套配色（bg / surface / text / muted / link / code_bg / heading_colors …） |
| `get_colors(mode)` | 按 `ft.ThemeMode` 返回对应 `Colors` |
| `_current_colors()` | 渲染期同步取色（与 `page.theme_mode` 一致） |
| `heading_colors` | H1–H6 六级标题色（红橙绿青蓝紫） |
| `block_text_size` | 标题字号阶梯 30 → 24 → 20 → 18 → 16 → 16 |
| `block_weight` | 标题字重阶梯 W_800 → W_500 |
| `list_color_level` | 列表缩进 → 1..6 色阶（`indent // 2 + 1`） |
| `segment_style` | 行内段类型 → `TextStyle`，支持 `marks` 组合格式 |
| `only_border` | 单边 Border 工具（避免 `ft.border.all` 兼容性问题） |
| `card_shadow` / `Elevation` / `Radius` / `Spacing` | 阴影 / 圆角 / 间距常量 |

### 关键设计决策

- **Stack 双层架构**：底层 `RenderedLine` 渲染 + 顶层透明 `cursor_text_field` 输入，光标与渲染层文字像素级对齐；替代段级编辑态的 `[前段 Text] + [激活段 TextField] + [后段 Text]` 拼接布局
- **光标用 `ref` 而非 `state`**：`cursor_ref` 在 `on_selection_change` 中直接修改 `base` / `extent`，避免输入时触发重渲染导致光标跳动；`cursor_off` state 仅在 `_end_input_session` 同步
- **`nav_seq` 触发重建**：仅撤销/重做等强制重建场景递增，作为 `cursor_text_field` 的 `key` 一部分；同行输入 `nav_seq` 不变 → `key` 不变 → 不重建 → IME 组合态保持
- **不设 `value` 属性**：透明 TextField 不设 `value`，避免 Flet 重渲染同步 value 打断 IME 组合态；由 `use_effect([clear_value_seq])` 在重渲染后异步清空 Flutter 端内部 value
- **`StrutStyle` 强制行高**：渲染层 Text 与 cursor TextField 共用同一 `StrutStyle` 实例（`force_strut_height=True`），保证光标 baseline 与渲染层文字 baseline 像素级对齐
- **HarfBuzz 整形测量**：替代 Pillow `getlength`（截断字形 advance，数字每字偏差 0.2px 累积导致光标重叠）；HarfBuzz 与 Skia 同引擎，光标像素级对齐渲染层 TextSpan
- **letter_spacing 补偿**：Flet 0.86 `Text` 默认 `letter_spacing=0.25`（非 Flutter 标准 0.0），测量端按 0.25/字形补偿，否则光标偏移随字符数线性累积
- **CJK 字体回退**：主字体不含 CJK 字形时按 CJK 边界切分，CJK 片段改用主中文字体测量，贴合 Skia 渲染回退行为
- **行级撤销快照**：`LineEditSnapshot` 仅存单行 raw + 光标位置（内存 O(1)/操作），替代全文档序列化；行结构变化仍走 `EditorSnapshot` 全文快照
- **URL 智能折叠**：`split_seg_for_display(seg, cursor_local)` 根据 `cursor_local` 控制 LINK/IMAGE 的 URL 子段可见性；`cursor_local=None` 返回非空供 `pixel_layout` 偏移测量，渲染层对 marker 零宽度处理
- **原子化重解析**：高频编辑路径用 `reparse_line_atomic`（仅触发 1 次 observable 通知，替代 `reparse_line` 的 2-7 次）
- **`ft.memo` 行级缓存**：`LineView` 用 `@ft.memo` 装饰，非激活行的 prop 集合稳定（`line` / `line_idx` / `content_width` / `line_height` + 版本号 prop + 回调），cursor 移动时仅旧激活行 + 新激活行 prop 变化，其余 N-2 行直接复用缓存
- **块级前缀也是段**：`#`、`-`、`>` 统一抽象为 `Segment`；标题在阅读态隐藏前缀、编辑态整行原文
- **独立岛屿架构**：代码块（flet-code-editor）与表格（flet-datatable2）作为自管理独立岛屿，不走 active/draft 系统；内部自管编辑状态，通过 `on_change_*` 原地更新行模型避免频繁重渲染致光标跳动，仅在行数变化时触发重渲染更新高度
- **代码块 / 表格聚焦守卫**：`code_focus_ref` / `table_focus_ref` 跟踪聚焦状态，`KeyDispatcher` 据此跳过全局导航 / 剪贴板键，交由原生 TextField 处理 Tab / Enter / Backspace / 方向键 / 复制
- **结构操作重建新 Line 对象**：表格 `add_col` / `delete_col` / `set_align` 等原地修改 `lines[i].raw` 时，必须创建新 `Line` 对象替换，否则 `document.lines = lines` 浅拷贝元素引用不变，observable 判定未变化不触发重渲染
- **渲染态选区包裹行内格式**：渲染态选中文字产生 `outward_sel` 而非 `active`，`toggle_inline` / `toggle_link` 在 `cursor_li is None` 时检查 `outward_sel_ref`，同段选区在 raw 两侧插入包裹标记并 `reparse_line`；跨段选区静默跳过
- **段内剪切同步执行**：`handle_segment_cut_sync` 同步捕获选区 + 剪切 + 提交（不通过 `page.run_task`），在原生 TextField 剪切前完成；原生剪切产生的 `on_change` 因值相等被去重跳过，避免双份剪切竞态
- **`EditorActions` 替代 `nav_ref` 字典**：旧 `nav_ref.current = {20+ 字符串 key}` 字典改为 `EditorActions` dataclass，必填字段构造时校验，避免 `nav.get("xxx")` 静默失败
- **`dispatcher_ref` 防过期**：`ft.use_effect(_bind_keyboard, [])` 空依赖会捕获首次渲染的 dispatcher；用 `dispatcher_ref.current = dispatcher` 每次渲染同步，`_handler` 通过 ref 读取最新实例，改快捷键后即时生效
- **主题同步渲染**：`App` 在渲染期间同步写入 `page.theme_mode`，保证子组件 `_current_colors()` 取色与切换一致
- **侧边栏开合动画**：`Container` 包裹 `Sidebar`，宽度 200ms `EASE_OUT` 动画 + `HARD_EDGE` 裁剪防溢出
- **跳转高亮脉冲**：`flash_li` state + `is_flash` prop 传入 `LineView._wrap_block`，淡蓝底 18% 不透明度 + 300ms `animate` 淡入淡出，1.2s 后自动清回 -1
- **两步精准滚动**：大纲点击跳转先估算偏移触发目标行构建，再用 `on_size_change` 实测高度修正，首次点击即滚动到视口顶部
- **软换行共用断行算法**：渲染层（`_maybe_stack_multi`）与光标层（`_cursor_overlay`）共用 `_line_visual_layout`，换行点天然一致；光标 Y 按视觉行序号 × 行高定位，避免渲染与光标错位
- **raw-flat 映射**：`_build_raw_to_flat_map` 建立原始 Markdown 偏移 ↔ 渲染文本位置映射，支持跨视觉行 span 切片；前缀段末偏移特殊处理（映射到 `flat_pos + display_len` 而非 `flat_pos`）
- **视口宽度去重**：`viewport_w_ref` 缓存上次宽度，`on_size_change` 仅在 >1px 变化时 `set_viewport_w`，避免微小抖动触发频繁重排
- **拆分编辑器共享文档**：两视口传入同一 `document` 对象，`@ft.observable` 自动同步；左视口 `key=f"{session}-0"` 在拆分 / 非拆分下保持一致，切换拆分不重置左视口光标状态
- **拆分焦点跟踪**：`active_pane` state + `active_pane_ref` 镜像（防同值重渲染），`KeyDispatcher.actions_ref` 与状态栏光标位置均按焦点视口选择 `nav_ref` / `nav_ref_split`；右视口 `keyboard_autofocus=False` 避免挂载时抢走左视口焦点
- **行内格式 Toggle**：`apply_inline_format` 检测选区是否已被对应语法包裹，已包裹则取消（移除两侧标记）、未包裹则包裹，包裹后通过 `outward_sel` 保持选区；浏览态（`cursor_li is None`）优先处理 `outward_sel`，无需先进入编辑态
- **链接常规文本编辑**：链接编辑不再走专用字段跳转状态机，视为常规文本编辑，依赖渲染层 `split_seg_for_display` 按光标位置自动显示 / 折叠 URL；避免 `set_nav_seq+1` 重建 TextField 导致快速输入丢失

## 许可证

MIT
