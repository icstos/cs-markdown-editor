# 精细化光标系统与选区系统

## Context（背景）

当前编辑器已具备段级/行级 WYSIWYG 编辑、跨段跨行 outward 选区、Shift+Click/Shift+Arrow/拖拽选区、字符级高亮等较完整的交互底座（详见 project_memory）。但仍有若干「不丝滑/不精准」的缺口，需精细化以达到自然编辑体验：

1. **PageUp/PageDown 完全缺失** —— 用户明确要求。
2. **Ctrl+Home/Ctrl+End 实现为「行首/行尾」而非「文档首/尾」**（`move_line_start`/`move_line_end` 与普通 Home/End 同效）—— 行为不符合预期。
3. **导航后光标不滚动入视** —— 只有 TOC `jump_to` 调 `scroll_to`；上/下/Home/End/点击移动光标后，光标可能离开视口而不自动跟随，体感「光标消失」。
4. **上/下行移动无「记忆列」** —— 每次用当前偏移作目标列，跨短行即被夹断丢失原列（用户确认要 VS Code 风格记忆列）。
5. **点击命中不做中点吸附** —— `_hit_test_segs` 以「字符左边界」为阈值，点击左半字符也落在其后，且最左边缘点击偏移到第 1 字符之后（off-by-one）。
6. **拖拽跨行选区不跟踪 x** —— `_pan_target_off` 跨行时硬编码 999999（行尾）/0（行首），不按鼠标 x 列定位目标行偏移，体感粗糙。

目标：补齐 PageUp/PageDown、修正 Ctrl+Home/End、导航滚动入视、记忆列、点击中点吸附、拖拽跨行 x 跟踪。**不做**拖拽到视口边缘的自动滚动（用户确认 v1 不做，记为已知限制）。

## 设计决策（已确认）

1. 上/下方向键实现「记忆列」：首次垂直移动记录 `preferred_col`（行级 raw 偏移），跨短行后回到原列；水平移动 / Home/End / Ctrl+Home/End / 点击 / 输入时清空。
2. 拖拽选区 v1 **不做**视口边缘自动滚动。
3. 其余按下方实现。

## 实现步骤

### 1. 点击命中中点吸附 — `views/line_view.py`

改写 `_hit_test_segs`（L95-124）的逐字逼近：遍历前缀宽度，当 `local_x` 落入字符 `j-1` 的宽度区间 `[prev_w, cur_w]` 时，比较 `local_x` 与中点 `(prev_w+cur_w)/2`：左半 → 边界 `j-1`（光标在该字符前），右半 → 边界 `j`（光标在该字符后）。
- 修复最左边缘 `local_x=0` → 中点 `(0+w1)/2 > 0` → 落左半 → `disp_off=0`（原为 1）。
- `local_x` 超出末字符 → `disp_off=len(display)`（不变）。
- 同法改写 `_hit_test_code`（L127-161）的列号逼近（中点吸附）。
- 新增 `_hit_test_x(line, x, base, line_height)`：y 无关变体，直接 `return _hit_test_segs(line, 0, len(line.segments), x, 0.0, base, line_height)`，供跨行拖拽复用。

### 2. 拖拽跨行 x 跟踪 — `views/line_view.py` + `views/editor.py`

- `LineView` 增加 prop `on_hit_test_x: Callable[[int, float], int] | None`（`(target_li, x) -> raw_offset`）。
- `_pan_target_off`（L727-741）跨行分支：`target_li != line_idx` 时，若 `on_hit_test_x` 可用则 `t_off = on_hit_test_x(target_li, pos.x)`（用目标行 + 同一 x 列命中），否则回退现有 999999/0。
  - 坐标一致性：各行 `Container` 横向 padding 均为 8，GestureDetector 包裹 `Text`，`pos.x` 相对 Text 起点，跨行可直接复用。
- `editor.py` 新增 `_hit_test_line_x(li, x)`：取 `document.lines[li]`，围栏/代码/数学块回退 0 或行尾；否则 `from views.line_view import _hit_test_tap` 调 `_hit_test_tap(line, x, 0.0, base, line_height)` 得 `(si, off)`，返回 `_line_raw_offset(li, si, off)`。
- 所有 `LineView(...)` 调用点（编辑器内多处）传 `on_hit_test_x=_hit_test_line_x`。

### 3. 导航滚动入视 + 视口跟踪 — `views/editor.py`

- 新增 refs：`scroll_offset_ref`、`viewport_h_ref`、`max_scroll_ref`（均 `ft.use_ref(0.0)`）。
- `list_view` Column（L2136-2142）加 `on_scroll=_on_scroll`：
  ```python
  def _on_scroll(e):
      scroll_offset_ref.current = getattr(e, "pixels", 0.0) or 0.0
      viewport_h_ref.current = getattr(e, "viewport_dimension", 0.0) or 0.0
      max_scroll_ref.current = getattr(e, "max_scroll_extent", 0.0) or 0.0
  ```
- 新增 `_ensure_visible(li)`：估算目标行像素 Y（`avg = (max_scroll+vh)/行数`，`target_y = li*avg`）；若 `so <= target_y <= so+vh-avg` 视为可见不滚动，否则 `page.run_task(_safe_scroll_to, li)`。视口尺寸未知时保守滚动。
- 新增 `_safe_scroll_to(li)`（与已修复的 `jump_to` 同款 try/except RuntimeError，防重挂载卸载报错）。
- 在 `move_up/move_down/move_home/move_end/move_doc_start/move_doc_end/page_up/page_down/activate` 末尾调 `_ensure_visible(li)`。

### 4. 记忆列 — `views/editor.py`

- 新增 `preferred_col_ref = ft.use_ref(None)`（存行级 raw 偏移或 None）。
- 抽取 `_vertical_goto(target_li)`：用 `active`+`active_seg`+`cursor_ref.current.extent` 算当前 `line_offset`；若 `preferred_col_ref.current is None` 则置为 `line_offset`；`col = preferred_col_ref.current`；`commit_active` → 围栏走 `_goto_quiet` 否则 `_locate_seg_by_raw_offset(line, col)` + `_goto`；末尾 `_ensure_visible(target_li)`。
- `move_up`/`move_down`/`page_up`/`page_down` 改用 `_vertical_goto`（保留各自越界/围栏前置判断）。
- 清空 `preferred_col_ref.current = None` 于：`activate`（点击跳转）、`move_left_cross`、`move_right_cross`、`move_home`、`move_end`、`move_doc_start`、`move_doc_end`、`on_change_draft`（输入）。段内水平箭头由原生 TextField 处理无法拦截，该情形不清空（可接受的小不一致）。

### 5. PageUp/PageDown — `views/editor.py` + `views/key_bindings.py` + `state/actions.py`

- `editor.py`：
  - `_page_rows()`：`vh = viewport_h_ref.current`；`lh = base*line_height`；`max(1, int(vh/lh))`；`vh<=0` 时回退 `page.window.height - 140`（估算 chrome）。
  - `page_up()`：编辑态 → `target=max(0, active-_page_rows())`，`_vertical_goto(target)`；浏览态 → `_scroll_by_page(-1)`。
  - `page_down()`：编辑态 → `target=min(len-1, active+_page_rows())`，`_vertical_goto(target)`；浏览态 → `_scroll_by_page(1)`。
  - `_scroll_by_page(direction)`：`page.run_task` 调 `lv.scroll_to(delta=direction*viewport_h)`（try/except RuntimeError）。
- `key_bindings.py` `handle()`：在标签快捷键拦截块之后、`layer` 判定之前加：
  ```python
  if norm == "pageup" and actions is not None:
      actions.page_up(); return
  if norm == "pagedown" and actions is not None:
      actions.page_down(); return
  ```
  （`page up`/`page down` → norm `pageup`/`pagedown`，两层均生效。）
- `state/actions.py`：`EditorActions` 增 `page_up`/`page_down` 字段。

### 6. Ctrl+Home/Ctrl+End → 文档首/尾 — `views/editor.py` + `views/key_bindings.py` + `state/actions.py`

- `editor.py`：`move_line_start`→重命名 `move_doc_start`，体跳文档首行（`_goto(0, seg_idx=0, cursor_at=0)`，围栏走 `_goto_quiet`）；`move_line_end`→`move_doc_end`，体跳文档末行末段段尾。均清空 `preferred_col` + `_ensure_visible`。
- `key_bindings.py`：`norm=="home": actions.move_doc_start() if e.ctrl else actions.move_home()`；`norm=="end": actions.move_doc_end() if e.ctrl else actions.move_end()`。
- `state/actions.py`：字段 `move_line_start`/`move_line_end` → `move_doc_start`/`move_doc_end`。
- `editor.py` EditorActions 构造（L1831-1834）：同步重命名 + 增 `page_up=page_up, page_down=page_down`。

## 关键文件

- **`views/line_view.py`**：`_hit_test_segs`/`_hit_test_code` 中点吸附；新增 `_hit_test_x`；`LineView` 增 `on_hit_test_x` prop；`_pan_target_off` 跨行用 x 跟踪。
- **`views/editor.py`**：refs（scroll/preferred_col）；`_on_scroll`/`_ensure_visible`/`_safe_scroll_to`/`_vertical_goto`/`_page_rows`/`_scroll_by_page`/`_hit_test_line_x`；`page_up`/`page_down`；`move_doc_start`/`move_doc_end`（重命名+改体）；`move_up`/`move_down` 用记忆列；多处清空 `preferred_col` + `_ensure_visible`；`list_view` 加 `on_scroll`；`LineView` 调用传 `on_hit_test_x`；EditorActions 构造同步。
- **`views/key_bindings.py`**：`handle()` 顶部 `pageup`/`pagedown` 拦截；`home`/`end` 的 Ctrl 分支改 `move_doc_start`/`move_doc_end`。
- **`state/actions.py`**：`EditorActions` 增 `page_up`/`page_down`，`move_line_start`/`move_line_end` → `move_doc_start`/`move_doc_end`。

## 已知限制（v1 不做）

- 拖拽到视口顶/底边缘不自动滚动（跨视口大范围拖拽选区暂只覆盖可见区）。
- 代码块点击命中仍为几何估算（`_hit_test_code`），仅做中点吸附改进，不重写布局测量；用户可用方向键微调。
- 段内水平箭头（原生 TextField）不清空记忆列（小不一致）。

## 验证

1. `python -m py_compile views/editor.py views/line_view.py views/key_bindings.py state/actions.py`。
2. `python main.py` 启动，逐项手测：
   - **光标**：上/下方向键跨短行后回到原列（记忆列）；左/右/Home/End/点击/输入后记忆列清空；Home 行首、End 行尾；Ctrl+Home 文档首、Ctrl+End 文档尾；PageUp/PageDown 翻页且光标跟随、浏览态纯滚动；上/下/翻页后光标自动滚入视口（不消失）。
   - **点击**：点击字符左半/右半分别落在前/后；最左边缘点击落在第 1 字符前；行尾空白点击落在行尾。
   - **选区**：Shift+左/右逐字扩展、Shift+上/下跨行；Shift+Click 定位选区末端；鼠标拖拽选区跨行时末端跟随鼠标 x 列（非整行）；选区高亮字符级正确；Esc/普通点击/非 Shift 方向键清除选区；Ctrl+C/Ctrl+X/Ctrl+Backspace 对 outward 选区生效。
   - **回归**：Tab 缩进、BackSpace/Delete 合并、代码块编辑、表格编辑、标签切换、Ctrl+S 保存均正常。
