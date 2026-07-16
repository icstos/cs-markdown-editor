# Markdown 编辑器

基于 [Flet](https://flet.dev) 0.86.0 声明式组件与 [mistune](https://mistune.lepture.com/) 实时解析，参考 [Typora](https://typora.io/) 的段级所见即所得（WYSIWYG）编辑体验。

点击任意段即显示其最小 Markdown 语法，其余保持渲染样式——这是与传统「源码 / 预览」双栏编辑器最大的不同。

## 特性

### 编辑体验

- **段级编辑**：点击行内任意段（加粗、斜体、行内代码、链接等）即切换到该段的原生 Markdown 编辑，其余段保持渲染样式
- **标题整行编辑**：点击标题进入编辑态时，以整行原文（如 `### 我的标题`）在单个输入框中编辑，可直接增删 `#` 调整级别
- **跨段光标导航**：方向键在段间 / 行间无缝移动，`Home` / `End` 跳转行首 / 行尾
- **智能复制粘贴**：跨行复制自动还原为 Markdown 源码；多行粘贴自动拆分为新行
- **原文模式**：一键切换到纯 Markdown 源码编辑

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

### 行内格式

加粗、斜体、行内代码、删除线、==高亮==、上标 `^x^`、下标 `~x~`、链接、图片、行内公式 `$...$`，支持组合语法（如 `***加粗斜体***`）。

### 视觉与主题

- **标题色阶**：红 → 橙 → 绿 → 青 → 蓝 → 紫（H1–H6），亮 / 暗主题各自适配对比度
- **标题字重**：H1 `W_800` 至 H6 `W_500`，逐级递减
- **列表圆点**：嵌套层级复用标题色阶（每 2 空格一级）
- **亮 / 暗主题**：工具栏一键切换，代码块高亮主题随主题联动

### 文件与导出

- 新建 / 打开 / 保存（`.md` / `.markdown` / `.txt`）
- 导出 HTML（mistune 渲染，含表格、脚注、任务列表等扩展）

## 技术栈

| 依赖 | 用途 |
|------|------|
| [Flet](https://flet.dev) ≥ 0.86.0 | 声明式 GUI（`@ft.component` + `use_state` / `use_effect` + `@ft.observable`） |
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
| `Ctrl+X` | 剪切（非编辑态：复制 Markdown 并删除选中内容） |
| `Ctrl+V` | 粘贴（编辑态：多行自动拆分） |
| `←` / `→` | 段间跨行移动（到边界时跳到相邻段；标题整行编辑时在行首 / 行尾跨行） |
| `↑` / `↓` | 上下行移动（按行内逻辑偏移定位） |
| `Home` / `End` | 跳到行首 / 行尾 |
| `Enter` | 提交当前段并换行（列表自动续行；标题在光标处拆分为两行） |

工具栏还提供：`Ctrl+1` / `Ctrl+2` / `Ctrl+3`（H1–H3）、`Ctrl+B`（加粗）、`Ctrl+I`（斜体）、`Ctrl+K`（链接）等提示（具体绑定见工具栏 tooltip）。

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

### 文件结构

```
cs-markdown-editor/
├── main.py              # 入口：App 组件、文件操作、主题、键盘快捷键
├── models.py            # 数据模型：Segment / Line / Document（@ft.observable）
├── parser.py            # Markdown 解析：行级 / 段级 / 选区↔源码 / HTML 导出
├── styles.py            # 主题配色、段→TextStyle、标题字重、列表色阶、文本测量
├── pyproject.toml       # 项目元数据与依赖
├── assets/
│   ├── fonts/
│   │   └── AlibabaPuHuiTi-3-55-Regular.otf
│   └── images/          # 示例图片等资源
└── views/
    ├── editor.py        # 编辑器根组件：状态编排、光标导航、工具栏联动
    ├── line_view.py     # 行视图：渲染态 TextSpan + 编辑态段级 / 标题整行布局
    ├── segment_view.py  # 段级渲染：TextSpan（渲染）/ TextField（编辑）
    └── toolbar.py       # 格式工具栏：块级 / 行内按钮
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
- **`use_effect` 显式聚焦**：`SelectionArea` 内 `autofocus` 因手势竞争不可靠，用 `async use_effect` + `await field.focus()` 确保编辑态光标可见
- **`nav_seq` 触发重建**：每次跨段导航递增 `nav_seq`，作为 TextField 的 `key` 强制重建以重新 `autofocus`
- **块级前缀也是段**：`# `、`- `、`> ` 统一抽象为 `Segment`；标题在阅读态隐藏前缀、编辑态整行原文
- **主题同步渲染**：`App` 在渲染期间同步写入 `page.theme_mode`，保证子组件 `_current_colors()` 取色与切换一致

## 许可证

MIT
