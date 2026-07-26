# 增量渲染优化：禁止全文档重绘

## Context

当前 `cs-markdown-editor` 在文档行数较多（500+ 行）时出现明显卡顿，连续输入体验下降。根因是渲染架构未做虚拟化与行级 memo：

1. **`ft.Column` 全量渲染**：所有 N 行都被实例化为 Flutter widget 参与 layout/paint，N=1000 时 Flutter 端开销 O(N)
2. **`line_controls` 每次重建**：`MarkdownEditor` 重渲染时 `line_controls = []` 重新构造 N 个 `LineView` Python 对象，Python 端开销 O(N)
3. **状态广播**：`cursor_li` / `cursor_off` 作为 prop 传给每行，光标移动时所有 N 行 prop 都变，即使加 `ft.memo` 也无法跳过

目标：仅重渲染需要的区域（旧激活行 + 新激活行 = 2 行），保证连续输入无卡顿。

## 前置验证（Stage 0）

**目的**：验证 Flet 0.86 的 `ListView.scroll_to(scroll_key=...)` 在 1000 行虚拟化列表上能跳转到指定 item。

**方法**：写一个临时 `_probe_listview.py`，构造 1000 行 `ft.ListView`（每行 `ft.Text` with `key=f"item-{i}"`），加一个按钮点击后调 `await list_view_ref.current.scroll_to(scroll_key="item-500", duration=500)`，观察是否能跳到第 500 行。

**判定**：跳转成功 → 进入 Stage 1。失败 → 切回退方案：短文档（< 200 行）用 `ft.Column` + `scroll_to(offset=)`，长文档用 `ft.ListView` + `scroll_key`。

清理：验证脚本用后删除。

## Stage 1：Column → ListView 虚拟化 + scroll_to 改造

### 改动 1.1：渲染容器替换

**文件**：`views/editor.py`
**位置**：`return ft.KeyboardListener(...)` 内层 `ft.Column(ref=list_view_ref, ...)` 段（约 1736-1743 行）

将：
```python
ft.Column(
    ref=list_view_ref,
    controls=line_controls,
    expand=True,
    spacing=0,
    scroll=ft.ScrollMode.AUTO,
    on_scroll=_on_scroll,
)
```

改为：
```python
ft.ListView(
    ref=list_view_ref,
    controls=line_controls,
    expand=True,
    spacing=0,
    auto_scroll=False,               # 必须 False，否则 scroll_to 失效
    build_controls_on_demand=True,   # 显式声明虚拟化（默认即 True）
    cache_extent=800,                # 上下缓冲像素，约 30 行
    # item_extent 留空：行高因 block_type/level 而异
    on_scroll=_on_scroll,
)
```

### 改动 1.2：`_safe_scroll_to` 改 `scroll_key` 模式

**文件**：`views/editor.py`
**位置**：`_safe_scroll_to`（约 1351-1369 行）

将原 `li * 行高` 像素估算 + `scroll_to(offset=...)` 模式改为 `scroll_key=f"line-{li}"` 模式。Flutter 的 `Scrollable.ensureVisible` 自动判断目标可见性，可见时不滚动，无需手动估算 Y 像素。

```python
async def _safe_scroll_to(li: int):
    if list_view_ref.current is None or not (0 <= li < len(document.lines)):
        return
    line = document.lines[li]
    # 表格被聚合：定位到表格起始行的 key
    if line.block_type == BlockType.TABLE:
        ts = li
        while ts > 0 and document.lines[ts - 1].block_type == BlockType.TABLE:
            ts -= 1
        key = f"table-{ts}"
    else:
        key = f"line-{li}"
    try:
        await list_view_ref.current.scroll_to(scroll_key=key, duration=100)
    except Exception:
        pass
```

### 改动 1.3：`_scroll_by_page` 改估算目标行号 + 复用 `_safe_scroll_to`

**文件**：`views/editor.py`
**位置**：`_scroll_by_page`（约 1405-1414 行）

`delta=` 模式在 ListView 上也失效，改为估算目标行号后调 `_safe_scroll_to`：

```python
async def _scroll_by_page(direction: int):
    if list_view_ref.current is None:
        return
    viewport = viewport_h_ref.current or 600
    avg_row_h = body_font_size * line_height + 4
    rows_per_page = max(1, int(viewport / avg_row_h))
    cur_first_li = max(0, int(scroll_offset_ref.current / avg_row_h))
    target_li = max(0, min(len(document.lines) - 1, cur_first_li + direction * rows_per_page))
    await _safe_scroll_to(target_li)
```

### 风险与验证

- **`auto_scroll` 必须显式 False**：Flet 文档明确 `auto_scroll=True` 时 `scroll_to()` 失效
- **`item_extent` 必须留空**：标题行高 30*1.6=48，正文 16*1.6=25.6，固定 `item_extent` 会压扁标题
- **`SelectionArea` 跨屏外行选区**：当前项目主要用 `outward_sel` 机制做跨行选区，`SelectionArea` 主要用于行内文本复制。Stage 1 验证后若发现跨行选区异常，可改用 `outward_sel` 全权管理
- **`cursor_field_ref` 在屏外未实例化**：激活行必然可见，`cursor_field_ref` 仅激活行使用，无影响

## Stage 2：状态局部化 + 行级 memo

### 改动 2.1：移除非激活行的 cursor_li/cursor_off prop

**文件 A**：`views/editor.py`
**位置**：`line_controls = []` 构建循环（约 1554-1618 行）

普通行分支改为：
```python
line_controls.append(
    LineView(
        key=f"line-{i}",
        line=line,
        line_idx=i,
        # 移除 cursor_li=cursor_li
        cursor_off=cursor_off if is_act else None,   # None = 非激活行
        cursor_ref=cursor_ref if is_act else None,
        nav_seq=nav_seq if is_act else 0,
        field_ref=cursor_field_ref if is_act else None,
        content_width=content_width,
        line_height=line_height,
        is_current_line=is_act,
        # 新增版本号触发 prop（解决 reparse_line 就地修改导致 memo 误判）
        line_raw_version=len(line.raw) if line.raw else 0,
        line_seg_count=len(line.segments),
        # 其余回调不变
        ...
    )
)
```

**文件 B**：`views/line_view.py`
**位置**：`LineView` 函数签名（约 207-248 行）+ 函数体（约 258-269 行）

签名修改：
```python
@ft.memo                          # 新增装饰器
@ft.component
def LineView(
    line: Line,
    line_idx: int,
    *,
    # 移除 cursor_li 参数
    cursor_off: int | None = None,   # None = 非激活行
    cursor_ref: ft.Ref | None = None,
    nav_seq: int = 0,
    field_ref: ft.Ref | None = None,
    content_width: float | None = None,
    line_height: float = 1.6,
    is_current_line: bool = False,   # 唯一的激活标志
    # 新增版本号触发 prop（LineView 内部不使用，仅触发 memo 检测）
    line_raw_version: int = 0,
    line_seg_count: int = 0,
    # ... 其余参数不变
) -> ft.Control:
```

函数体修改（约 258-269 行）：
```python
c = _current_colors()
base = block_text_size(line.block_type, line.level)
is_active = is_current_line   # 直接用 prop，不再比较 cursor_li

# 激活行：优先 cursor_ref.current.base（IME 期间最新位置）
effective_cursor_off = cursor_off
if is_active and cursor_ref is not None and cursor_ref.current is not None:
    ref_off = getattr(cursor_ref.current, "base", None)
    if ref_off is not None and ref_off >= 0:
        effective_cursor_off = ref_off
```

### 改动 2.2：TableView 加 memo + 版本号 prop

**文件**：`views/table_view.py`
**位置**：`TableView` 装饰器（约 123 行）+ 签名

```python
@ft.memo
@ft.component
def TableView(
    lines: list[Line],
    line_idx: int,
    # ... 原参数
    *,
    # 新增版本号触发 prop
    lines_version: int = 0,                  # = len(document.lines)
    first_line_raw_version: int = 0,         # = len(document.lines[line_idx].raw)
    # ...
):
```

**editor.py 中 TableView 调用处**（约 1559-1581 行）增加这两个 prop：
```python
TableView(
    key=f"table-{table_start}",
    lines=document.lines,
    line_idx=table_start,
    lines_version=len(document.lines),
    first_line_raw_version=len(document.lines[table_start].raw) if 0 <= table_start < len(document.lines) else 0,
    # ... 其余参数
)
```

### memo 触发原理

`parser.reparse_line(line, new_raw)` 是**就地修改** line 对象（`line.raw = new_raw`、`line.segments = rebuilt.segments`），不替换 line 引用。`ft.memo` 浅比较 `line` prop 引用未变会误判未刷新。

通过加 `line_raw_version` / `line_seg_count` 两个看似无用的 prop（LineView 内部不读取），`reparse_line` 后这两个值变化 → memo 检测到 prop 变化 → 重新执行函数体 → 文档刷新。

### 风险与验证

- **IME 期间 cursor_off 滞后**：当前 `handle_char_input` 不调 `set_cursor_off`（line 326 注释），cursor_off state 在 IME 期间滞后，真实位置由 `cursor_ref.current.base` 跟踪。改造后此机制完全保留（`line_view.py` 第 266-269 行逻辑不动）
- **`outward_range` prop 多行变化**：跨行选区时多行 `outward_range` 变化 → memo 失效重渲染（符合预期）
- **撤销/重做 `nav_seq` 变化**：仅激活行 `nav_seq` 变化 → 仅激活行重渲染（符合预期）
- **TableView `lines` 引用稳定**：`lines=document.lines` 每次都可能是同一引用 → 加 `lines_version` + `first_line_raw_version` 触发刷新

## 验证方案

### 功能验证清单

| 场景 | 验证点 |
|------|--------|
| 1000 行文档连续输入 | 输入无卡顿，光标实时跟随，IME 组合正常 |
| 1000 行文档滚动 | 滚动流畅，无明显掉帧 |
| 光标跨行移动（↑↓） | 仅激活行变化，其他行不闪烁 |
| PageUp / PageDown | 跳转准确 |
| Ctrl+Home / Ctrl+End | 跳到文档首/末行 |
| 大纲侧边栏点击跳转 | 跳到对应标题行 |
| 撤销/重做 | nav_seq 变化，激活行重建 |
| 表格内 Tab 导航 | 单元格间导航正常 |
| 代码块输入 | CodeEditor 高度自适应 |
| 向外选区拖拽 | 跨行选区高亮正确 |
| 原文模式切换 | 全文档重渲染不卡顿 |
| 短文档（< 50 行） | 与改造前行为完全一致 |

### 性能验证方法

**测试文档**：构造 1000 行文档（10 个 H1 + 10 个代码块 + 50 个表格 + 930 行段落）。

**测试方法**：在 `MarkdownEditor` 函数体顶部加临时计时：
```python
import time
_t0 = time.perf_counter()
# ... 函数体
_t1 = time.perf_counter()
if _t1 - _t0 > 0.03:
    print(f"[perf] MarkdownEditor render: {(_t1-_t0)*1000:.1f}ms")
```

连续输入 100 个字符，记录每次渲染耗时。

**性能目标**：
- 1000 行文档单次渲染 < 30ms（改造前预估 200-500ms）
- 连续输入时帧率 > 50fps
- 滚动帧率 > 55fps

### 回归测试

每个 Stage 完成后运行：
1. 打开 `config/sample.py` 中的示例文档，验证渲染正确
2. 验证所有快捷键（`services/key_bindings.py`）
3. 验证文件保存/加载
4. 验证主题切换、原文模式切换、聚焦模式切换

## 关键文件改动汇总

| 文件 | 改动点 | 阶段 |
|------|--------|------|
| `views/editor.py` | Column → ListView（约 1736-1743 行） | Stage 1 |
| `views/editor.py` | `_safe_scroll_to` 改 scroll_key（约 1351-1369 行） | Stage 1 |
| `views/editor.py` | `_scroll_by_page` 改 scroll_key（约 1405-1414 行） | Stage 1 |
| `views/editor.py` | line_controls 循环：移除非激活行 cursor_li，加版本号 prop（约 1554-1618 行） | Stage 2 |
| `views/line_view.py` | LineView 签名：移除 cursor_li，加版本号 prop（约 207-248 行） | Stage 2 |
| `views/line_view.py` | LineView 函数体：is_active 用 is_current_line（约 258-269 行） | Stage 2 |
| `views/line_view.py` | LineView 装饰器加 `@ft.memo`（约 207 行） | Stage 2 |
| `views/table_view.py` | TableView 装饰器加 `@ft.memo` + 版本号 prop（约 123 行） | Stage 2 |

## 实施顺序

```
Stage 0 (前置验证) ── 验证 scroll_key 在 ListView 上可用
   ↓
Stage 1 (P1 + P4) ── Column→ListView + scroll_to 改造
   ↓ 验证功能不退化 + 性能提升
Stage 2 (P2 + P3) ── 状态局部化 + ft.memo
   ↓ 验证 memo 命中率 + 文档刷新正确
完成
```

**不合并 Stage 1 与 Stage 2**：Stage 1 改容器层，Stage 2 改 prop 层，分开便于问题定位。

## 不做的事项（明确排除）

- **P5 回调闭包稳定化**：30+ 回调包 wrapper，改造量大。Stage 1+2 完成后若仍有卡顿再考虑
- **P6 TOC 计算 use_memo 化**：收益小（约 0.5ms），非必要
- **改 `parser.reparse_line` 返回新对象**：涉及 editor.py 多处调用方改动，风险高。改用版本号 prop 兜底
- **`LineLayoutCache` 增量更新**：当前调用频率不高，优先级低
