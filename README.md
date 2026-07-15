# Markdown 编辑器

基于 Flet 0.86.0 声明式组件与 mistune 实时渲染，参考 [Typora](https://typora.io/) 的段级所见即所得（WYSIWYG）编辑体验。

点击任意段即显示其最小 Markdown 语法，其余保持渲染样式——这是与传统"源码 / 预览"双栏编辑器最大的不同。

## 特性

- **段级编辑**：点击行内任意段（加粗、斜体、行内代码、链接等）即切换到该段的原生 Markdown 编辑，其余段保持渲染样式
- **块级支持**：标题（H1-H6）、有序/无序列表、任务列表、引用、代码块（语法高亮）、行间公式、分隔线、目录 `[toc]`
- **行内格式**：加粗、斜体、行内代码、删除线、链接、图片、行内公式 `$...$`
- **跨段光标导航**：方向键在段间/行间无缝移动，Home/End 跳转行首/行尾
- **智能复制粘贴**：跨行复制自动还原为 Markdown 源码；多行粘贴自动拆分为新行
- **原文模式**：一键切换到纯 Markdown 源码编辑
- **文件操作**：新建 / 打开 / 保存（`.md` 文件）

## 技术栈

| 依赖 | 用途 |
|------|------|
| [Flet](https://flet.dev) 0.86.0 | 声明式 GUI 框架（`@ft.component` + `use_state` / `use_effect`） |
| [mistune](https://mistune.lepture.com/) | Markdown 行内 AST 解析（含 strikethrough 插件） |
| [Pillow](https://pillow.readthedocs.io/) | 文本像素宽度测量（编辑块自适应）+ 图片尺寸读取 |

> Python 3.11+（使用 `StrEnum`）

## 安装与运行

```bash
pip install flet mistune pillow
```

```bash
python main.py
```

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+N` | 新建文档 |
| `Ctrl+O` | 打开文件 |
| `Ctrl+S` | 保存文件 |
| `Ctrl+C` | 复制（自动还原为 Markdown 源码） |
| `Ctrl+V` | 粘贴（多行自动拆分） |
| `←` / `→` | 段间跨行移动（到边界时跳到相邻段） |
| `↑` / `↓` | 上下行移动（按行内逻辑偏移定位） |
| `Home` / `End` | 跳到行首段 / 行尾段 |
| `Enter` | 提交当前段并换行（列表自动续行） |

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
  → on_blur/on_submit 提交（reparse 该行）→ 重新渲染
```

编辑态布局：`[前段 Text] + [激活段 TextField] + [后段 Text]`

仅激活段显示原生 Markdown，其余段保持渲染样式。

### 文件结构

```
cs-markdown-editor/
├── main.py              # 入口：App 组件、文件操作、键盘快捷键
├── models.py            # 数据模型：Segment / Line / Document（@ft.observable）
├── parser.py            # Markdown 解析：行级/段级/选区↔源码转换
├── styles.py            # 样式常量、段→TextStyle 映射、文本宽度测量
├── assets/
│   └── fonts/
│       └── AlibabaPuHuiTi-3-55-Regular.otf
└── views/
    ├── editor.py        # 编辑器根组件：状态编排、光标导航、工具栏联动
    ├── line_view.py     # 行视图：渲染态 TextSpan + 编辑态段级布局
    ├── segment_view.py  # 段级渲染：TextSpan（渲染）/ TextField（编辑）
    └── toolbar.py       # 格式工具栏：块级/行内/视图切换按钮
```

### 关键设计决策

- **`SelectionArea` 包裹 `Column`**：用 `ft.SelectionArea` + `Column(scroll=AUTO)` 而非 `ListView`，解决垂直拖拽选择手势冲突
- **光标用 `ref` 而非 `state`**：`on_selection_change` 通过 `cursor_ref` 更新光标位置，避免输入时触发重渲染导致光标跳动
- **`use_effect` 显式聚焦**：`SelectionArea` 内 `autofocus` 因手势竞争不可靠，用 `async use_effect` + `await field.focus()` 确保编辑态光标可见
- **`nav_seq` 触发重建**：每次跨段导航递增 `nav_seq`，作为 TextField 的 `key` 强制重建以重新 `autofocus`
- **块级前缀也是段**：`# `、`- `、`> ` 统一抽象为 `Segment`，使"点击即编辑"对前缀与行内 span 行为一致
