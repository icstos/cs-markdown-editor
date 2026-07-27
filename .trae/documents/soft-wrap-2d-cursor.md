# 自适应宽度与软换行（2D 光标）实现计划

## Context（背景）

当前编辑器行内文本用 `ft.Text(width=float("inf"))` 渲染（[rendered_line.py:471](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/rendered_line.py#L471)），宽度无限故**不换行**，超出视口的文本被 `ft.Column` 裁切，用户无法看到也无法编辑。

光标系统是 **1D 单行像素偏移**：`_line_raw_offsets_x` 返回逐字符 X 偏移（[pixel_layout.py:175](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/pixel_layout.py#L175)），`LineLayoutCache` 固定单行高度，`cursor_text_field` 单行定位在 `(x, 0)`。换行需要 **2D 定位**（视觉行 + 行内 X），且**渲染换行点必须与光标测量换行点完全一致**，否则光标 Y 落到错误的视觉行。

用户对光标精度要求极高（IME 组合、HarfBuzz cluster 级 kerning、标记折叠），因此采用**手动换行**：渲染与光标测量调用同一个换行函数，对齐由构造保证，不依赖复刻 Flutter 的 UAX#14 换行算法。

## 方案：手动换行（Option A）

新增换行测量函数，把一行的 1D 偏移按 `wrap_width` 切成 N 个视觉行；渲染层把 flat span 列表按视觉行切片，每个视觉行渲染为单独的单行 `ft.Text`（`no_wrap=True`，绝对定位 `top=i*text_h`）；光标用同一测量定位到 `(x, vline_idx*text_h)`。渲染与测量共用 `_line_visual_layout`，换行点天然一致。

围栏岛屿（CODE/MATH/HR/TOC/TABLE）走独立控件，**不参与换行**。

## 文件改动

### 1. `views/pixel_layout.py` — 换行测量 + 2D 布局
- 新增 `@dataclass VisualLine`：`vline_idx / start_raw / end_raw / offsets_x(行内) / width`
- 新增 `_line_visual_layout(line, base, wrap_width, cursor_raw_offset, line_height) -> list[VisualLine]`：复用 `_line_raw_offsets_x`（1D，已含标记折叠/kerning/逐段字体），再调用 `_wrap_offsets_into_visual_lines` 做换行切分
- 新增 `_wrap_offsets_into_visual_lines(offsets_x, raw_text, wrap_width)`：纯换行算法。断行规则：ASCII 空格/tab 后可断；CJK 字符（复用 `utils/text_layout.py` 的 `_CJK_RE`）前可断；超长不可断词（URL）强制断；前缀段（#/•/>）不断、留在 vline 0。trailing space 留在当前行
- 新增 `_find_vline_for_raw(visual_lines, raw_off)`：二分查找 raw_off 所在视觉行
- `LineLayout`：`raw_offsets_x` → `visual_lines: list[VisualLine]` + `num_vlines`；`height = num_vlines*text_h + pad_top + pad_bottom`（变量高度）
- `LineLayoutCache._build`：`wrap_width = max(50, content_width - left_pad)`；围栏块短路（占位单 vline）；`self._content_width` 现在实际用于换行
- `cursor_px(li, off)`：返回 `(x, vline_idx*text_h, text_h)`（y 非零）
- `hit_test(x, y)`：Y 二分定逻辑行 → `vline_idx = int(local_y // text_h)` 定视觉行 → `hit_test_line_x_raw(vline.offsets_x, local_x)`（复用）→ `raw_off = vline.start_raw + local_off`
- 保留 `raw_offsets_x` 兼容属性供过渡调用方

### 2. `views/rendered_line.py` — 渲染 N 个视觉行
- 新增 `_build_raw_to_flat_map(line, cursor_off)`：raw 偏移 → flat 文本位置（用 `split_seg_for_display` 的标记折叠逻辑，单一真源）
- 新增 `_slice_spans_for_visual_line(flat_spans, raw_to_flat, vline)`：按视觉行 raw 范围切 flat spans（跨边界 span 拆分，保留 style/on_click/tooltip/url）
- 重构 `_maybe_stack` → `_maybe_stack_multi`：Stack 内 N 个 `ft.Text(spans=vline_spans, width=wrap_width, height=text_h, no_wrap=True, top=i*text_h)` + 可选 cursor_overlay；Stack `height = num_vlines*text_h`，`clip_behavior=NONE`（IME 候选框）
- `RenderedLine` 主体：`flat_spans = _spans_with_highlight(...)` → `visual_lines = _line_visual_layout(line, base, wrap_width=content_width-left_pad, cursor_raw_offset=cursor_off)` → 逐行切片 → `_maybe_stack_multi`
- 行内公式浏览态（`ft.Markdown` 路径）：把 Container 宽度约束到 `wrap_width`，让 Markdown 原生换行
- 空行/任务行/图片行：任务行 Stack 高度变 N*text_h，Checkbox 对齐 vline 0；其余维持

### 3. `views/line_view.py` — 光标 overlay 2D 定位
- `_cursor_overlay`（[line_view.py:191](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/line_view.py#L191)）：改用 `_line_visual_layout`，`cursor_px_x = vline.offsets_x[local_off]`，`cursor_px_y = vline.vline_idx*(base*line_height)`；任务行前缀宽度扣除仅在 vline 0

### 4. `views/cursor_layer.py` — 光标 TextField
- `cursor_text_field` 已有 `cursor_px_y` 参数（[cursor_layer.py:52](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/cursor_layer.py#L52)），无需改签名；调用方传非零 y
- TextField 保持 `multiline=False`（IME 关键）；`key = li+nav_seq` 不含 vline_idx → 同行跨视觉行移动不重建控件，IME 组合态保持
- 宽度：`width = wrap_width - cursor_px_x`（用 wrap_width，保底 200px 给 IME 空间）

### 5. `views/editor.py` — 视觉行导航
- 新增 `_cursor_vline_info(li, off)`：返回当前光标的 `(visual_lines, vline, current_x)`
- `_vertical_goto` → 视觉行感知：同逻辑行内移到上/下视觉行；越界则跨逻辑行（目标行用 browse 态 `cursor_raw_offset=None` 算视觉行，取末行/首行）；`hit_test_line_x_raw(vline.offsets_x, target_x)` 落点
- `preferred_col_ref` 语义从 raw 偏移改为 **X 像素**（跨视觉行更准）
- `page_up/page_down`：按视觉行计数（`viewport // text_h`）
- `_step_up/_step_down`（向外选区）：同步改视觉行步进
- `_estimate_line_height`（[editor.py:1874](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py#L1874)）：未构建行用 `measure_text_width(raw)` 估算视觉行数 `max(1, int(est_w//wrap_width)+1)`
- `content_width` 已传入 `LineLayoutCache`（[editor.py:1983](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py#L1983)），无需新管线

## 实施顺序（每阶段保持可运行）

1. **测量基础设施**：加 `VisualLine`/`_line_visual_layout`/`_wrap_offsets_into_visual_lines`/`_find_vline_for_raw`，单元测试，暂不接入渲染（行为不变）
2. **LineLayout 2D**：`LineLayout` 改 `visual_lines`，`_build`/`cursor_px`/`hit_test` 2D；单视觉行行为不变
3. **渲染 N 视觉行**：`_slice_spans_for_visual_line`/`_maybe_stack_multi`，`RenderedLine` 接入 → 长行视觉换行（光标仍 1D）
4. **光标 2D 定位**：`_cursor_overlay` 传 `cursor_px_y` → 光标在换行行上正确定位
5. **视觉行导航**：`_vertical_goto`/`page_up/down`/`_step_up/down`/`_estimate_line_height` → 上下键按视觉行移动
6. **边界与抛光**：任务行/行内公式/选区高亮跨视觉行/IME 跨视觉行/滚动稳定性

## 验证

**单元测试（无需 Flet 运行）**：
- `_wrap_offsets_into_visual_lines`：空文本/单空格/长拉丁文（空格断）/长 CJK（逐字断）/混合/超长 URL（强制断）/trailing space
- `_find_vline_for_raw` 二分正确性
- `_slice_spans_for_visual_line`：跨边界 span、边界对齐 span、空 span、纯标记 span；断言「所有视觉行 span 文本拼接 == 原 flat 文本」
- `hit_test_line_x_raw` 在单视觉行 offsets 上仍通过

**集成手测（Flet）**：
- 长段落输入 → 视觉换行，光标对齐
- 点击换行第 2 视觉行 → 落到正确 raw 偏移
- 上箭头从 vline 1 → vline 0（X 保持）；从 vline 0 → 上一逻辑行末视觉行
- PageUp/Down 按视觉行翻页
- IME：vline 0 末尾输入中文，组合完成跨到 vline 1 不打断
- 拖拽选区跨视觉行 → 高亮跨行正确
- 代码块/公式块/表格 → 不受影响
- 标题/列表/引用长内容 → 换行，前缀仅 vline 0，续行对齐内容左
- 回归：kerning 行（"AVAIL"/"1.23456789"）、IME 组合、标记折叠、链接编辑

## 主要风险与缓解

1. **Span 切片 bug**（跨边界拆分 off-by-one 致渲染乱码）：用 `split_seg_for_display` 做 raw→flat 映射（单一真源）；断言拼接一致性 + 边界用例测试
2. **IME 跨视觉行打断**：TextField `key` 不含 vline_idx，仅 `top/left/width` 变化不重建；手测中文拼音在 vline 0 末尾完成跨行
3. **激活行 vs 浏览行换行点不一致**（标记可见致 reflow）：Typora 式固有行为，光标仍与渲染对齐（共用同一测量），可接受
4. **性能**（每行每渲染重算换行）：`_wrap_offsets_into_visual_lines` 是 O(N) 浮点扫描，廉价；超长行可按 `(line.raw, cursor_off, wrap_width)` LRU 缓存；先Profile再优化
5. **left_pad 与 `_wrap_block` 容器 padding 不一致**：已核对——引用 `level*12`（`_QUOTE_INDENT`=`Spacing.XL`=12）与 `_wrap_block` 每层 `Spacing.XL`=12 一致；列表 `level*20` 两边一致

## 关键复用点

- `_line_raw_offsets_x`（[pixel_layout.py:175](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/pixel_layout.py#L175)）：1D 偏移，含标记折叠/kerning/逐段字体
- `measure_text_offsets`（[text_layout.py:258](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/text_layout.py#L258)）：HarfBuzz cluster 级，LRU 缓存
- `_CJK_RE`（[text_layout.py:51](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/text_layout.py#L51)）：CJK 检测单一真源
- `split_seg_for_display`（`utils/segment_helpers.py`）：标记折叠拆分
- `hit_test_line_x_raw`（[pixel_layout.py:264](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/pixel_layout.py#L264)）：单视觉行 X 命中，复用
- `_block_padding`（[pixel_layout.py:151](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/pixel_layout.py#L151)）：left_pad 计算
- `on_line_size_change` + `line_heights_ref`：变量高度已支持
