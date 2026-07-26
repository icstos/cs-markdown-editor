# 深度对齐 Typora/VSCode 编辑体验

## 实施状态（2026-07-27 完成）

- ✅ 差距 1：同源 cursor_off 时序 bug — 全部 11 处替换完成 + 额外修复 handle_paste
- ✅ 差距 2：拖拽选区跨行精度 — LineLayoutCache.hit_test 透传完成
- ✅ 差距 3：双击选词 — on_double_tap_down + VSCode 风格词边界完成
- ✅ 验证：语法检查 / 导入 / 启动测试 / 词边界算法 10/10 单测通过

> 注：双击事件用 `on_double_tap_down`（TapEvent 带 local_position）而非 `on_double_tap`
> （ControlEventHandler 不带位置），原方案需修正。

## Context

用户反馈：输入文本后按 Enter 会从输入文本前方换行（而非光标处）。已修复 `on_submit` 的 `cursor_off` 时序 bug。

但项目 Stack 双层光标级架构中，`cursor_off` state 在 IME 期间不更新（避免重渲染打断 IME），仅 `cursor_ref.current.base` 实时跟踪。`backspace_core` / `delete_core` / `on_submit` 已对齐为读取 `cursor_ref.current.base`，但**其余所有读取 `cursor_off` 的操作函数仍存在同源 bug**：用户输入字符后立即按快捷键（Ctrl+B、Shift+方向键、Tab 等），光标位置会用输入前的旧值，导致操作位置错位。

同时调查发现：拖拽选区跨行精度依赖 `round(y / line_h)` 估算，行高不一致（标题/普通/代码块）时偏差；双击选词（Typora/VSCode 标配）未实现。

本次目标：完整修复同源时序 bug，提升拖拽选区跨行精度，增加双击选词。

## 现状评估

### 已实现（完整，无需改动）
- **光标系统**：方向键 / Home / End / PageUp / PageDown / Ctrl+Home / End 导航；点击跳转 hit_test；像素级光标对齐（HarfBuzz）
- **选区系统**：鼠标拖拽选区（on_pan_start / on_pan_update，跨行）；Shift+方向键扩展（extend_outward_*）；Shift+点击；选区高亮（跨行正确）；Ctrl+A 全选
- **基础编辑**：输入（IME 友好，格式延续）；删除（选区批量删除、行首/尾合并）；剪贴板（复制/剪切/粘贴）；撤销重做（行级 + 全文混合快照）
- **快捷键自定义**：ShortcutManager（update / reset / 冲突检测）；设置页捕获式修改；dispatcher_ref 防过期
- **长按删除**：`undo_push_pending` 门控机制（`_push_line_edit` L237-238）已实现"同行连续删除仅首次入栈"，撤销一次恢复全部——**无需额外合并逻辑**

### 待修复差距

| 差距 | 优先级 | 影响场景 |
|------|--------|----------|
| 1. 同源 cursor_off 时序 bug | 高 | IME 输入后立即按 Ctrl+B/I/U/S/`/K、Shift+方向键、Tab、方向键 |
| 2. 拖拽选区跨行精度 | 中 | 标题/普通/列表混合行间拖拽选区 |
| 3. 双击选词 | 低 | 双击英文/中文/标点选词（Typora/VSCode 标配） |

## 实施方案

### 差距 1：同源 cursor_off 时序 bug（高优先级）

**策略**：引入辅助闭包 `_cursor_base(raw_len=None)`，统一返回 IME 实时光标偏移（ref 优先，回退 state，可选钳制）。所有列出函数改为读取该值，完全对齐 `backspace_core`（[editor.py:541](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py#L541)）已有模式。

**修改文件**：`views/editor.py`

1. 在 `_set_cursor` 之后（约 L384）新增：
```python
def _cursor_base(raw_len: int | None = None) -> int:
    """IME 实时光标偏移（ref 优先，回退 state；可选钳制到 raw_len）。"""
    base = cursor_ref.current.base if cursor_ref.current else cursor_off
    if raw_len is not None:
        base = max(0, min(base, raw_len))
    return base
```

2. 逐点替换 `cursor_off` → `_cursor_base(...)`：

| 行号 | 函数 | 改动 |
|------|------|------|
| L218 | `_make_snapshot` | `cursor_off=cursor_off` → `cursor_off=_cursor_base()` |
| L243 | `_push_line_edit` | `cursor_off=cursor_off` → `cursor_off=_cursor_base()` |
| L265 | `_current_for_undo_redo`（LineEditSnapshot 分支） | 同上 |
| L677-678 | `move_left` | 入口取 `off = _cursor_base(len(_line_raw(...)))`，比较/移动用 `off` |
| L696-697 | `move_right` | 同上 |
| L726-731 | `move_home` 三态判定 | 入口取 `off = _cursor_base(raw_len)`，判定用 `off` |
| L776 | `_vertical_goto` | `preferred_col_ref.current = cursor_off` → `= _cursor_base()` |
| L849,852 | `indent_or_outdent`（普通段落 Tab 分支） | 入口取 `off = _cursor_base(len(raw))`，插入与光标移动用 `off` |
| L929 | `apply_inline_format` | `off = cursor_off` → `off = _cursor_base(len(raw))` |
| L1367,1370 | `_extend_outward_step`（起始选区分支） | `step_fn(cursor_li, cursor_off)` 与 `src_off = cursor_off` 改用 `_cursor_base()` |
| L1390 | `on_extend_outward`（从光标起始分支） | 同上 |

**已修复参照**（无需改动）：`backspace_core`（L541）、`delete_core`（L582）、`on_submit`（L620）

### 差距 2：拖拽选区跨行精度（中优先级）

**策略**：复用 `views/pixel_layout.py:LineLayoutCache.hit_test(x, y)`（已实现精确 Y 二分 + 行内 X 命中），通过新增 `on_hit_test_xy` 回调透传给 `RenderedLine`。Cache 实例在 editor.py 中惰性构建（首次 pan 事件触发），行数变化时失效。

**修改文件**：

1. **`views/editor.py`**
   - state 声明区（L194 附近）新增 `layout_cache_ref = ft.use_ref(None)`
   - `_reset_line_heights`（L1727 附近）扩展：同时清空 `layout_cache_ref.current = None`
   - 在 `_hit_test_line_x` 旁（L1626 附近）新增：
     ```python
     def _get_layout_cache():
         if layout_cache_ref.current is None:
             from views.pixel_layout import LineLayoutCache
             layout_cache_ref.current = LineLayoutCache(
                 document.lines, content_width, line_height)
         return layout_cache_ref.current

     def _hit_test_xy(li_anchor, x, y):
         cache = _get_layout_cache()
         layout = cache.get(li_anchor)
         if layout is None:
             return None
         # GestureDetector 局部坐标 → 文档坐标
         return cache.hit_test(x + layout.left_pad, y + layout.text_top)
     ```
   - `LineView(...)` 调用（L1896 附近）新增 `on_hit_test_xy=_hit_test_xy`

2. **`views/line_view.py`**
   - `LineView` 签名（L281）新增 `on_hit_test_xy: Callable[[int, float, float], tuple[int, int] | None] | None = None`
   - `RenderedLine(...)` 调用（L397）透传 `on_hit_test_xy=on_hit_test_xy`

3. **`views/rendered_line.py`**
   - `RenderedLine` 签名（L128）新增 `on_hit_test_xy` 参数
   - `_pan_target_off`（L188-202）在跨行估算前优先调用 `on_hit_test_xy`：
     ```python
     def _pan_target_off(pos):
         if pos is None:
             return (line_idx, 0)
         if on_hit_test_xy is not None:
             result = on_hit_test_xy(line_idx, pos.x, pos.y)
             if result is not None:
                 return result
         # 回退：原 round(y / line_h) 估算
         ...
     ```

### 差距 3：双击选词（低优先级）

**策略**：利用 Flet `GestureDetector.on_double_tap`，VSCode 风格词边界（同类别字符连续区间：word / space / punct；连续 CJK 视为一个词）。

**修改文件**：

1. **`views/rendered_line.py`**
   - `RenderedLine` 签名新增 `on_double_tap: Callable[[int, int], None] | None = None`
   - 新增 `_on_double_tap(e)` 处理器：命中 raw_off 后回调 `on_double_tap(line_idx, raw_off)`
   - 三处 `ft.GestureDetector` 构造新增 `on_double_tap=_on_double_tap`

2. **`views/line_view.py`**
   - `LineView` 签名新增 `on_double_tap` 参数，透传给 `RenderedLine`

3. **`views/editor.py`**
   - 在 outward_sel 函数区（L1300 附近）新增 `_select_word_at(li, raw_off)`：
     - 字符类别判定：`word`（`\w` + CJK）/ `space`（`\s`）/ `punct`（其他）
     - 从 raw_off 向左右扩展到同类边界
     - 退出光标编辑态（`set_cursor_li(None)`），设为 `outward_sel`
   - `LineView(...)` 调用新增 `on_double_tap=_select_word_at`

## 实施顺序

```
差距 1（独立，最高优先级）
  ├─ 定义 _cursor_base() 辅助函数
  ├─ 替换所有 cursor_off 读取点（11 处）
  └─ 验证 IME 场景

差距 2（独立，可与差距 1 并行）
  ├─ editor.py: layout_cache_ref + _get_layout_cache + _hit_test_xy
  ├─ line_view.py: on_hit_test_xy 透传
  └─ rendered_line.py: _pan_target_off 优先用 on_hit_test_xy

差距 3（独立，最低优先级）
  ├─ editor.py: _select_word_at 实现
  ├─ line_view.py: on_double_tap 透传
  └─ rendered_line.py: _on_double_tap 处理器
```

## 风险点

1. **`_make_snapshot` 改动影响面广**：所有 `_push_history` 调用站点（17 处）的快照 cursor_off 变为 IME 实时值。非 IME 上下文 `cursor_ref.current.base == cursor_off`（已同步），改动是 no-op，安全。
2. **`move_*` 函数读 ref**：方向键导航是 IME 结束后第一动作，cursor_ref 已就绪；IME 期间按方向键会跳到 IME 当前位置（预期行为）。
3. **LineLayoutCache 坐标系**：跨行拖拽精度依赖 `text_top` 与 `left_pad` 换算。需在标题/列表/引用/段落混合文档中测试。
4. **双击与单击双触发**：Flet 双击会先触发 `on_tap`（定位光标）再触发 `on_double_tap`（选词），视觉上有短暂光标→选区闪烁。可接受（VSCode 也有类似行为）。
5. **LineLayoutCache 构建成本**：典型文档（数百行）单次构建约 10-30ms，仅首次 pan 时构建，可接受。

## 验证

### 自动化验证
```bash
python -c "import ast; ast.parse(open('views/editor.py', encoding='utf-8').read()); print('editor.py OK')"
python -c "import ast; ast.parse(open('views/rendered_line.py', encoding='utf-8').read()); print('rendered_line.py OK')"
python -c "import ast; ast.parse(open('views/line_view.py', encoding='utf-8').read()); print('line_view.py OK')"
python main.py  # 启动应用，窗口标题"Markdown 编辑器"显示，无异常
```

### 交互验证场景

**差距 1（同源 bug）**：
- 输入 "abc" 后立即按 Ctrl+B → 应在输入后位置插入 `****`，非输入前位置
- 输入 "你好" 后按 ← → → → 应从"好"之后移动，非从输入前位置
- 输入 "abc" 后 Shift+→ 应从光标当前位置起始选区
- 输入 "abc" 后按 Tab → 应在输入后位置插入 4 空格

**差距 2（拖拽精度）**：
- 在标题行（H1, 30px）下方开始拖拽，向下拖到普通段落行（16px）→ 目标行准确落在指针所在行
- 在普通段落拖拽跨过代码块 → 选区在代码块前后普通行间正确跳跃
- 在列表项（带缩进）跨行拖拽 → X 命中准确

**差距 3（双击选词）**：
- 双击英文 "hello" → 选中 "hello"
- 双击中文 "你好世界" → 选中整个 "你好世界"（VSCode 风格连续 CJK）
- 双击标点 "," → 选中 ","
- 双击后按 Shift+方向键 → 从双击选区末端继续扩展

### 回归测试
- [ ] 工具栏每个按钮点击 + Ctrl+Z 撤销（_make_snapshot 影响）
- [ ] IME 中文输入 + 立即按方向键
- [ ] 长按 Backspace 删除 50 字符 + Ctrl+Z 一次撤销恢复全部
- [ ] 跨行拖拽选区在混合块类型文档中的精度
- [ ] 双击英文/中文/标点选词
- [ ] 撤销/重做后光标位置正确
- [ ] 任务勾选 + 撤销
- [ ] 表格内编辑 + 撤销
