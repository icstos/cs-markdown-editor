# Stack 双层叠加光标级实时渲染重构方案

## Context

当前编辑器采用"段级编辑"架构：点击某段 → 该段切换为 TextField 编辑（`active`/`active_seg`/`draft` 三状态）→ 提交后 reparse。这与 Typora 的"光标级实时渲染"体验差距明显——Typora 中光标始终在渲染文本上，输入字符即时渲染为 Markdown 格式，无段级编辑态切换。

本重构将编辑器从段级编辑彻底转为 **Flet.Stack 双层叠加的光标级 WYSIWYG**：底层渲染层完整渲染 Markdown，顶层透明 TextField 在光标像素位置接收输入，每个字符输入即时渲染到文档。

**用户已确认决策**：
1. 代码块/表格保留 flet-code-editor / flet-datatable2 独立岛屿，Stack 光标级编辑仅用于普通文本行
2. 分阶段迁移：Phase 1 核心架构，Phase 2 选区/IME/性能
3. 光标实现：透明 TextField（border=NONE + bgcolor=TRANSPARENT + cursor 可控）

**关键技术可行性（已验证）**：
- `styles.py:measure_text_width` 基于 Pillow 精确计算文本像素宽度（含 CJK 回退）
- TextField API：`border=InputBorder.NONE`、`bgcolor=TRANSPARENT`、`cursor_color/width/height`、`content_padding=Padding.zero()`、`strut_style` 均可用
- `segment_view.py:raw_to_visible_spans` 已是 Typora 式渲染脚手架（标记字符透明/灰色切换，拼接还原 line.raw）——直接复用
- `segment_view.py:active_text_field` 已验证 `StrutStyle(force_strut_height=True, height=line_height, leading=0)` 强制行高 + 6% 宽度补偿吸收 Pillow/Skia 度量差异

---

## 1. 目标架构：每激活行一个 Stack

采用"每激活行一个 Stack"而非"整文档一个 Stack"——非激活行无 TextField 开销，长文档性能可控；坐标系与行内坐标一致，无需处理整文档滚动偏移。

```
ft.Column(scroll=AUTO, controls=[
  RenderedLine(line=lines[0], cursor_off=None)     # 非激活行：纯渲染

  ft.Stack([                                         # 激活行：双层叠加
    ft.Text(spans=raw_to_visible_spans(line, base, cursor_off, heading_level),
            style=TextStyle(size=base, font_family=FONT_MAIN, height=line_height),
            strut_style=SHARED_STRUT),
    cursor_text_field(cursor_px_x, line_height_px, base, line_height,
                      on_change=handle_char_input, field_ref=cursor_field_ref,
                      nav_seq=nav_seq)
  ], width=content_width, height=line_h)

  RenderedLine(line=lines[2], cursor_off=None)
])
```

### 新状态模型（替代 active/active_seg/draft）

```python
cursor_li, set_cursor_li = ft.use_state(None)    # int | None 激活行号
cursor_off, set_cursor_off = ft.use_state(0)     # 行级 raw 偏移 0..len(line.raw)
nav_seq, set_nav_seq = ft.use_state(0)           # 触发 TextField key 重建以清空内部状态
cursor_field_ref = ft.use_ref(None)              # use_effect 显式 focus
layout_cache_ref = ft.use_ref(None)              # LineLayoutCache 像素布局
# 保留：cursor_ref(CursorState) / history_ref / code_focus_ref / table_focus_ref
# Phase 2 新增：selection (anchor_li,anchor_off,active_li,active_off) | None
```

### 数据流闭环

```
点击渲染层 → hit_test(x,y) → (li, raw_off) → set_cursor_li/off → 重渲染
  → use_effect 调 cursor_field_ref.focus() → TextField 聚焦，光标在像素位置闪烁
  → compute_cursor_px(li, off) → TextField.left/top 定位

输入字符 'a' → TextField.on_change("a") → handle_char_input
  → line.raw 插入 'a' → parser.reparse_line(line) → set_cursor_off(off+1)
  → set_nav_seq(+1) 触发 TextField key 重建清空内部状态 → 重渲染
  → 渲染层 Text 显示 'a'（渲染样式），TextField 重新定位到新光标位置
```

---

## 2. 核心模块设计

### 2.1 新建 `views/pixel_layout.py` — 像素坐标计算

```python
@dataclass
class LineLayout:
    li: int
    top: float              # 行顶 Y（含块级 padding）
    height: float           # 行总高
    text_top: float         # 文字区顶 Y
    text_height: float      # 文字行高 = base * line_height
    base_size: int
    left_pad: float         # 文字左起点 X（缩进 + padding）
    raw_offsets_x: list[float]  # 每个 raw 偏移 0..len(raw) 的 X 坐标

class LineLayoutCache:
    def __init__(self, lines, content_width, line_height=1.6): ...
    def get(self, li) -> LineLayout | None
    def cursor_px(self, li, raw_off) -> tuple[float, float, float]  # (x, y, h)
    def hit_test(self, x, y) -> tuple[int, int] | None              # (li, off)
```

**行高模型**：`text_height = block_text_size(block_type, level) * line_height`；普通行 padding 4+4，HR 8+8，围栏岛屿由原生编辑器自管理。渲染层 Text 与编辑层 TextField 用**同一 StrutStyle 实例**保证一致。

**X 坐标累加**：`_line_raw_offsets_x(line, base)` 逐段用 `measure_text_width` 累加，每段取正确 `(font_family, size)`（codespan/math 用 `FONT_MONO`+`base-1`，其余 `FONT_MAIN`+`base`）。**用 `seg.raw`（含标记字符）**——标记虽透明但占像素，保证光标 X 与渲染层对齐。

**点击命中**：`hit_test_line_x(line, x, base)` 二分查找 `raw_offsets_x`，中点吸附决定前后偏移。

### 2.2 新建 `views/cursor_layer.py` — 透明编辑层

```python
def cursor_text_field(*, cursor_px_x, cursor_px_y, line_height_px,
                      base_size, line_height, on_change, field_ref, nav_seq) -> ft.TextField:
    # value="" 永远空；border=NONE；bgcolor=TRANSPARENT；content_padding=Padding.zero()
    # text_style color=TRANSPARENT（文字不可见，仅光标可见）
    # strut_style=SHARED_STRUT（与渲染层 Text 同实例）
    # cursor_color=c.text, cursor_width=2, cursor_height=base_size
    # width=2（极窄承载光标），height=line_height_px
    # left=cursor_px_x, top=cursor_px_y（Stack 内绝对定位）
    # key=f"cursor-field-{nav_seq}"（nav_seq 变化触发重建清空内部状态）
```

**value="" 清空策略**：每次 `handle_char_input` 后 `set_nav_seq(+1)`，TextField `key` 变化 → Flet 销毁旧控件创建新控件 → 内部状态重置为 ""。fallback：`field_ref.current.value = ""` 命令式清空。

### 2.3 新建 `views/rendered_line.py` — 纯渲染行

```python
@ft.component
def RenderedLine(line, line_idx, cursor_off: int|None, base_size, line_height,
                 content_width, on_tap, on_pan_start, on_pan_update,
                 outward_range, on_extend_outward, shift_pressed_ref,
                 on_clear_outward, is_current_line) -> ft.Control:
    # 调用 raw_to_visible_spans(line, base_size, cursor_off, heading_level) 构造 TextSpan
    # 包裹 GestureDetector（on_tap/on_pan）+ _wrap_block（缩进/引用边框）
    # cursor_off=None 时所有标记透明；cursor_off=int 时光标处标记灰色
```

---

## 3. 文件改造清单

### 新建
| 文件 | 职责 |
|---|---|
| `views/pixel_layout.py` | `LineLayout`/`LineLayoutCache`/`hit_test_line_x`/`_line_raw_offsets_x` |
| `views/cursor_layer.py` | `cursor_text_field` 透明 TextField 构造 |
| `views/rendered_line.py` | `RenderedLine` 纯渲染行组件 |

### 重写
| 文件 | 改造 |
|---|---|
| `views/editor.py` | 删 `active`/`active_seg`/`draft` 三状态及 `commit_active`/`_goto`/`_toggle_seg`/`toggle_link`/`on_change_draft`/`_reconstruct_line_raw` 等段级逻辑；新增 `cursor_li`/`cursor_off` 状态 + `handle_char_input`/`backspace_core`/`delete_core`/`on_submit`/`apply_inline_format`/`move_*`/`_merge_with_prev/next_line`/`_split_line_at` 光标级实现；构造 `LineLayoutCache` 驱动定位 |
| `views/line_view.py` | 删 `active_seg`/`draft`/段级编辑分支；非激活行调 `RenderedLine`；激活行用 `ft.Stack([RenderedLine, cursor_text_field])`；围栏块保留原分支 |
| `views/key_bindings.py` | `move_left/right` 改光标级（无段内/段间区分）；删段级剪切路由；`tab`→`indent_or_outdent`；围栏块守卫保留 |
| `state/actions.py` | `EditorActions`：`active`/`active_seg`/`draft` → `cursor_li`/`cursor_off`；删段级剪切字段 |
| `services/history.py` | `EditorSnapshot`：`active`/`active_seg`/`draft`/`cursor_base`/`cursor_extent` → `cursor_li`/`cursor_off` |

### 保留（小改或无改）
| 文件 | 说明 |
|---|---|
| `models.py` | Document/Line/Segment 不变 |
| `parser.py` | `reparse_line`/`parse_inline` 不变 |
| `styles.py` | `measure_text_width`/`segment_style`/`block_text_size` 全保留 |
| `views/segment_view.py` | `raw_to_visible_spans`/`_split_seg_for_display`/`segment_to_span` 复用；删 `active_text_field`（迁至 cursor_layer） |
| `views/table_view.py` | 表格独立岛屿不变 |
| `main.py` | `nav_ref.current.active` 引用点替换为 `cursor_li`/`cursor_off` |

### 围栏岛屿与 Stack 共存
- `LineView` 检测 `line.block_type in (CODE,TABLE,MATH,HR,TOC)` → 走原 CodeEditor/DataTable 分支，不进入 Stack
- `handle_char_input`/`backspace_core` 等所有光标级操作 `_is_fence(line)` 早返回
- `code_focus_ref`/`table_focus_ref` 跟踪聚焦，KeyDispatcher 据此跳过全局键交由原生编辑器
- 方向键越界冒泡到 KeyDispatcher → 跳到相邻普通行

---

## 4. Phase 1 任务清单（核心架构 + 普通文本行）

1. **新建 `views/pixel_layout.py`**：`LineLayout`/`LineLayoutCache`/`_line_height_px`/`_line_raw_offsets_x`/`hit_test_line_x`/`hit_test_doc`。验证 (li,off) ↔ (x,y) 双向映射
2. **新建 `views/cursor_layer.py`**：`cursor_text_field`。验证透明无框 + 光标 baseline 对齐 + value="" on_change 闭环
3. **新建 `views/rendered_line.py`**：`RenderedLine` 组件，复用 `raw_to_visible_spans`
4. **重写 `views/line_view.py`**：删段级参数；非激活行 `RenderedLine`，激活行 `ft.Stack([RenderedLine, cursor_text_field])`
5. **重写 `views/editor.py`**：状态替换 + 光标级函数 + `LineLayoutCache` 驱动 + `use_effect` 聚焦
6. **重写 `views/key_bindings.py`**：光标级导航路由 + 围栏块守卫
7. **更新 `state/actions.py` + `services/history.py`**：字段替换 + snapshot 适配
8. **更新 `main.py`**：`nav_ref` 引用点替换
9. **回归测试**：加载/保存、点击定位、字符输入、Backspace/Delete/Enter/Tab、标题/列表/引用块切换、行内格式 Ctrl+B/I/U/Shift+S/`/K、代码块/表格进入退出、撤销重做、原文模式、主题切换

## 5. Phase 2 任务清单（选区/IME/性能）

1. 选区高亮 + Shift+Click/Arrow 选区 + 拖拽选区（`raw_to_visible_spans` 扩展 `selection_range` 注入 highlight_bg）
2. IME 组合态（`on_compose` + TextField 宽度撑满剩余 + 候选框不裁切）
3. 代码块/表格切换优化（越界冒泡 + 相邻行自动聚焦）
4. 性能优化（`@ft.memo` 缓存 RenderedLine + `LineLayoutCache` 增量 + `measure_text_width` LRU 缓存 + 长文档虚拟化）
5. 撤销重做适配（连续输入合并 + snapshot 光标恢复）
6. 多行文本换行（过长行自动换行 + 光标 Y 计算）

---

## 6. 风险与应对

| 风险 | 应对 |
|---|---|
| TextField 像素定位偏差（残余 padding/baseline） | `StrutStyle(force_strut_height=True, leading=0)` 强制行高；开发期 TextField 加半透明 bgcolor 肉眼校准；常量偏移校准 |
| 行高不一致（渲染层 Text vs TextField） | 两层用**同一 StrutStyle 实例**；`block_text_size` 统一取 base_size |
| 长文档性能 | 行级 key 稳定让 Flet diff 仅更新变化行；`measure_text_width` LRU 缓存；Phase 2 虚拟化 |
| TextField value="" 清空时序 | `set_nav_seq(+1)` 触发 key 重建清空内部状态；fallback 命令式 `field.value=""` |
| IME 组合态裁切 | Phase 1 接受组合态期间候选框可能裁切（不影响最终输入）；Phase 2 撑满宽度 + on_compose |
| 富文本多字号 X 累加精度 | 逐段切换 `(font_family, size)`；标记字符宽度计入；6% 放大补偿 |
| 光标跨行 Y 跳跃 | `LineLayoutCache.cursor_px(li, off)` 统一入口适配不同行高；`preferred_col_ref` 记忆列 |

---

## 7. 验证方案

1. **像素对齐验证**：开发期给 `cursor_text_field` 加 `bgcolor=ft.Colors.with_opacity(0.3, RED)`，肉眼对比光标位置与渲染层文字间隙；验证后改回 TRANSPARENT
2. **输入闭环验证**：最小 demo —— 单行文档，点击定位 → 输入 "hello" → 验证渲染层显示 "hello" 且 TextField 始终空 → Backspace 逐字删除
3. **回归验证**：Phase 1 完成后跑完整回归清单（第 4 节任务 9）
4. **性能验证**：1000 行文档下连续输入 100 字符，验证无明显卡顿；`measure_text_width` 耗时 < 0.1ms/次

---

## 关键复用点（避免重写）

- `views/segment_view.py:raw_to_visible_spans` — Typora 式标记透明/灰色切换渲染
- `views/segment_view.py:_split_seg_for_display` — 段拆分为标记/内容
- `styles.py:measure_text_width` — 像素宽度测量（含 CJK 回退）
- `styles.py:segment_style` / `block_text_size` — 段样式与字号映射
- `views/segment_view.py:active_text_field` 的 `StrutStyle` 模式 — 强制行高先例
- `parser.py:reparse_line` — 行内重解析
- `services/history.py:EditHistory` — 撤销栈（仅改 snapshot 字段）
