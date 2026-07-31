# 优化分隔线（HR）支持 — 参考 Typora

## Context（背景）

当前 HR（分隔线 `---`/`***`/`___`）作为"围栏岛屿"渲染：`ft.Divider` 全宽横线、`quote_bar` 色、不承载光标、点击无法编辑。与 Typora 差距明显：Typora 的分隔线是淡雅横线，点击可显示 `---` 源码编辑。

根因：HR 在 `FENCE_BLOCK_TYPES` 中（[segment_helpers.py:25-38](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/segment_helpers.py#L25-L38)），导致 `_is_fence(line)` 拦截 HR，点击/Enter/Backspace/Delete/导航/选区全部跳过 HR，无法进入光标编辑态。

**目标**：让 HR 走普通文本路径（Typora 式 WYSIWYG）——浏览态渲染淡雅横线，激活态显示 `---` 源码 + 光标可编辑，操作自然丝滑，视觉科学清爽。

## 方案概述

从 `FENCE_BLOCK_TYPES` 移除 HR，让 HR 可导航/可选区/可承载光标。HR 浏览态（非激活）渲染优化横线，激活态 fall through 到普通文本渲染路径（复用 `RenderedLine + _cursor_overlay`，无需新增 state/回调）。对 HR 的 Enter/Backspace/Delete 做特殊处理（符合 Typora 直觉）。

**关键优势**：_render.py 已对所有非 TABLE 行统一传入普通文本回调（[_render.py:116-170](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor/_render.py#L116-L170)），HR 激活后回调自动生效，**_render.py 无需改动**。

## 分步实现（按文件）

### 1. `utils/segment_helpers.py` — 核心改动
从 `FENCE_BLOCK_TYPES` 移除 `BlockType.HR`（L27-33）。同步更新注释。
- 影响：`is_fence(line)` 对 HR 返回 False，18 处调用点自动让 HR 参与导航/选区/合并/剪切/行内格式
- `pixel_layout.py` L181 不再短路 HR，HR 走普通文本布局（`---` 仅 3 字符不会换行）

### 2. `parser/reparse.py` — 编辑后块类型自动转换
HR 分支（L67-69 / L119-122）增加 `_RE_HR.match(raw)` 检测：匹配则保持 HR，不匹配则 fall through 到普通块重建（变段落）。
- 顶部 import 追加 `_RE_HR`（已 import `_RE_CODE_FENCE`/`_RE_MATH_BLOCK`）
- 效果：`---`→`***` 保持 HR；`---`→`--` 变段落（Typora 式）

### 3. `parser/block.py` — segments 与 raw 一致性
L150-152 改为 `Segment(SegType.TEXT, raw, raw)`（保留原 raw `***`/`___`，而非强制 `"---"`），确保 `line.raw == "".join(s.raw)`。

### 4. `views/line_view.py` — 浏览态横线 + 激活态 fall through
- HR 分支（L574）加 `and not is_active` 条件：浏览态渲染优化横线，激活态 fall through 到 L686 普通文本路径
- 横线视觉优化：`ft.Container(height=1, bgcolor=with_opacity(0.25, c.muted), border_radius=0.5)`，外层 `padding=Spacing.LG`，`ink=True` + `on_click` 触发 `on_tap(line_idx, len(raw))` 进入编辑态
- `_wrap_block`（L199-210）对 HR 行 padding 用 `Spacing.LG`（8px），与 `_block_padding` HR 分支 (8,8,0) 一致，保证光标 Y 偏移与渲染层对齐

### 5. `views/editor/_blocks.py` — 创建后进入编辑态
L123-125 改为：创建 HR 后 `set_cursor(li, len(new_raw))`（=3，光标在 `---` 末尾），而非 `set_cursor_li(None)`。

### 6. `views/editor/_cursor.py` — HR 特殊交互
- **on_submit**（L413 push_history 之后、L425 _RE_FENCE_TRIGGER 之前）：HR 行 Enter 在下方插入新空行（`parse_markdown("").lines[0]`），光标移到新行首，不分割 `---`
- **backspace_core**（L307 off 计算后、L308 `if off > 0` 之前）：HR 行首 Backspace（off==0）转为空段落（`parse_markdown("").lines[0]` 替换），不合并 `---` 到前一行；HR 行中间 Backspace 走默认分支，reparse 后 raw 不匹配 `_RE_HR` 自动变段落
- **delete_core**（L345 fence 检查后）：HR 行尾 Delete（off==len(raw)）转为空段落，不合并下一行

### 7. `views/editor/_scroll.py` — 行高估算
L163 围栏块列表移除 HR，单独处理：`if line.block_type == BlockType.HR: return base * line_height + 16`（8+8 padding）。

### 8. `styles.py` — 无需改动
HR segments 是 TEXT 段，走 `segment_style(TEXT)` 返回默认 16/NORMAL + `c.text`。激活态 `---` 显示正常文本色（与 Typora 一致）。

## 视觉设计

| 属性 | 当前 | 优化后 |
|------|------|--------|
| 颜色 | `c.quote_bar` | `with_opacity(0.25, c.muted)` — 淡雅科学 |
| 厚度 | 1px | 1px — 保持细腻 |
| 圆角 | 无 | 0.5px — 两端柔和 |
| 上下间距 | Spacing.LG | Spacing.LG（8px）由 _wrap_block padding 提供 |
| 宽度 | 撑满 | 撑满（受 padding 约束，与 Typora 一致） |
| 点击 | ink=True | ink=True + on_click 进编辑态 |

激活态 `---` 源码：`c.text` 正常文本色、16px、NORMAL、FONT_MAIN，光标 2px 全局样式。

## 风险点与验证

1. **光标 Y 偏移**：HR 行 _wrap_block padding 8+8 在 Stack 外层，光标 Y 相对 Stack 顶（padding 后），不受影响。_block_padding (8,8,0) 与 _wrap_block 8+8 一致，跨行命中 Y 正确。
2. **撤销/重做**：`---`→`--` 变段落后 Ctrl+Z，raw 恢复 `---`，reparse 重新识别 HR。
3. **选区跨 HR**：_step_left/_step_right 不再跳过 HR，选区可含 HR，Ctrl+C 复制含 `---`。
4. **序列化往返**：`---`→`***` 编辑后序列化应为 `***`（segments 保留原 raw）。

## 验证方法

1. 跑现有测试：`python -m pytest tests/test_parser_roundtrip.py tests/test_reparse.py tests/test_soft_wrap.py tests/test_cursor.py -q`
2. 新增测试（test_reparse.py / test_cursor.py 风格）：
   - `---`→`--` reparse 后 block_type==PARAGRAPH；`---`→`***` 保持 HR
   - `is_fence(HR_line)` 返回 False
   - HR 行 Enter 插入空行；行首 Backspace 转空段落；行尾 Delete 转空段落
   - set_block(HR) 后光标 off==3
3. 手动验证：创建 HR→点击横线显示 `---` 源码→编辑成 `***`→失焦恢复横线；上下键导航经过 HR；拖拽选区跨越 HR
