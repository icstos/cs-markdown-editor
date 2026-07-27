# 链接语法 Typora 式交互优化

## Context

当前编辑器的链接语法（`[text](url)`）已具备基础渲染与 Ctrl+K 包裹能力，但与 Typora 的交互体验存在明显差距：

- **Ctrl+K 选中态**：选中文本按 Ctrl+K 后，选区停留在链接文本上，而用户真正想输入的是 URL，需手动移光标——不符合"选中→输入即替换"的桌面端直觉。
- **outward 选区缺"打字替换"**：浏览态选中任意文本后直接按键无响应（浏览态无聚焦 TextField），这是缺失的基础编辑行为。
- **无 Tab 字段跳转**：编辑链接时无法用 Tab 在 text 与 url 间切换，需逐字符穿越 `](` 标记。
- **无悬停提示**：浏览态链接不显示 URL 预览，用户无法预判跳转目标。

本次优化对齐 Typora 行为，复用既有的 `outward_sel` 选区设施，让"选中→Ctrl+K→输入 URL"形成自然丝滑的流式操作。

## 目标行为（对齐 Typora）

| 场景 | 期望行为 |
|------|---------|
| 选中文本 → Ctrl+K | 包裹 `[selected](url)`，**URL 占位符 "url" 被高亮选中** |
| 上一步后直接输入 ASCII 字符 | "url" 被替换为输入字符，进入编辑态，光标在字符后 |
| 上一步后按 Tab | 选区跳到链接文本 "selected" |
| 选中链接文本 → Shift+Tab | 选区跳回 URL |
| 无选区 → Ctrl+K | 插入 `[](url)`，光标在 `[]` 内（保持现状） |
| 编辑态光标在链接 text 内 → Tab | 光标跳到 url 前，URL 子段变灰可见 |
| 编辑态光标在 url 内 → Shift+Tab | 光标跳回 text 前 |
| 鼠标悬停链接 | 显示完整 URL tooltip |
| Ctrl+Click 链接 | 打开 URL（已一致，不变） |

## 实现方案

### 第 1 步：新增公共 helper（无副作用，可独立验证）

**文件**：[utils/segment_helpers.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/segment_helpers.py)

新增 `link_field_ranges(seg, seg_start) -> tuple[int, int, int, int] | None`：
- 返回 `(text_start, text_end, url_start, url_end)` 行级 raw 偏移（end exclusive）
- 非 LINK 段或格式异常返回 None
- 复用 `split_seg_for_display` 中已有的 `idx = raw.index("](")` 定位逻辑
- 供 P0.3 / P1.6 / 渲染层共享，消除重复计算

### 第 2 步：EditorActions 增 3 个可选字段（向后兼容）

**文件**：[core/actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/actions.py) (L96-110 可选字段区)

```python
handle_outward_type_char: Callable[[str], None] | None = None
jump_link_field: Callable[[int], bool] | None = None   # outward_sel 态 Tab 跳转，返回是否消费
jump_link_cursor: Callable[[int], bool] | None = None  # edit 态 Tab 跳转，返回是否消费
```

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) EditorActions 构造处 (~L2128-2138) 追加 3 个 kwargs。

### 第 3 步：editor.py 新增 3 个动作函数

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)

#### 3a. `handle_outward_type_char(char: str)` — 打字替换 outward 选区（P0.2 通用基础行为）
- 取 `outward_sel_ref.current`，规整为 a≤b，单行校验
- `_push_history()` + `undo_push_pending.current = True`
- 一次 reparse 完成删除+插入：`new_raw = raw[:a_off] + char + raw[b_off:]`
- `_reparse_atomic(line, new_raw)` + `document.lines = list(document.lines)` + `mark_dirty()`
- `_set_outward_sel(None)` 清高亮 → `_set_cursor(a_li, a_off + len(char))` 切换到编辑态
- 现有 `use_effect(_focus_cursor_field, [cursor_li])` 自动聚焦 cursor_text_field，下一字符走正常 IME 输入流
- 跨行 outward_sel v1 不处理（return，回退到无响应）

#### 3b. `jump_link_field(direction: int) -> bool` — outward_sel 态 Tab 跳转（P0.3）
- 取 `outward_sel_ref.current`，规整为 a≤b，单行校验，返回 False 若不满足
- 遍历 `lines[a_li].segments` 累加 `acc`，定位包含 `b_off`（active 端）的 LINK 段
- 调 `link_field_ranges(seg, acc)` 得 `(ts, te, us, ue)`
- `direction=1`（Tab）：当前在 text range → `_set_outward_sel((a_li, us, a_li, ue))`；返回 True
- `direction=-1`（Shift+Tab）：当前在 url range → `_set_outward_sel((a_li, ts, a_li, te))`；返回 True
- 空 range（`te==ts` 或 `ue==us`）：跳过去后立即转 edit 模式（`_set_cursor(a_li, range_start)` + `_set_outward_sel(None)`），因零宽选区无意义
- 未找到 LINK 段返回 False（KeyDispatcher 据此 fall-through）

#### 3c. `jump_link_cursor(direction: int) -> bool` — edit 态 Tab 跳转（P1.6）
- 取 `cursor_li` + `_cursor_base()` 光标位置
- 遍历 `lines[cursor_li].segments` 定位包含光标的 LINK 段
- `link_field_ranges` 得 `(ts, te, us, ue)`
- `direction=1`：光标在 `[ts, te]` → `_set_cursor(cursor_li, us)`；返回 True
- `direction=-1`：光标在 `[us, ue]` → `_set_cursor(cursor_li, ts)`；返回 True
- 否则返回 False（fall-through 到 `indent_or_outdent`）
- `_set_cursor` 触发重渲染，`raw_to_visible_spans` 检测光标在 URL range → URL 子段变灰可见（既有逻辑）

### 第 4 步：key_bindings.py 新增分发分支

**文件**：[views/key_bindings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py)

#### 4a. 模块级 `_extract_printable_char(e) -> str | None`
- 排除 Ctrl/Meta/Alt 组合、功能键（F1-F12）、导航键（Tab/Enter/Esc/Backspace/Delete/Home/End/PageUp/Down/Arrows）、修饰键本身
- 单字符可打印 → 返回（字母按 shift 决定大小写）
- `space` → 返回 `" "`
- 其余返回 None

#### 4b. outward_sel 拦截块尾部追加（L227 后，L229 全局标签快捷键前）
```python
# Tab：链接字段跳转（仅在链接段上消费，否则 fall-through）
if norm == "tab" and not e.ctrl:
    if actions.jump_link_field is not None and actions.jump_link_field(-1 if e.shift else 1):
        return
# 可打印字符：打字替换 outward 选区（通用基础编辑行为）
char = _extract_printable_char(e)
if char is not None and actions.handle_outward_type_char is not None:
    actions.handle_outward_type_char(char)
    return
```
注意：Tab 分支用 `if ... and ...: return`（仅消费成功的跳转），字符分支无条件消费（outward_sel 激活时任何可打印字符都应替换选区）。

#### 4c. `_handle_edit_nav` Tab 分支改造（L330-349）
在现有 CODE/TABLE 检查后、`indent_or_outdent` 调用前插入：
```python
if actions.jump_link_cursor is not None and actions.jump_link_cursor(-1 if e.shift else 1):
    return True
```
仅当光标在链接段字段内时消费 Tab，否则 fall-through 到原有缩进/插空格逻辑。

### 第 5 步：_apply_outward_wrap link 分支调整（P0.1 一行改动）

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) `_apply_outward_wrap` (L1053-1064)

当前 link 分支末尾：
```python
_set_outward_sel((a_li, a_off + 1, a_li, a_off + 1 + len(selected)))
```
改为把选区移到 URL 占位符 "url" 上。为稳健（selected 可能含 `]`），**用 `new_raw.index("](", a_off)` 定位** `](` 而非算术推导：
```python
# 定位插入后的 URL 占位符范围（用 index 稳健处理 selected 含 ] 的情况）
bracket_idx = new_raw.index("](", a_off)  # ]( 的位置
url_start = bracket_idx + 2
url_end = len(new_raw) - 1  # 最后一个 ) 前
# 钳制到本行范围内
_set_outward_sel((a_li, url_start, a_li, url_end))
```

### 第 6 步：链接悬停 tooltip（P1.5，独立可上线）

**文件**：[views/segment_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/segment_view.py)

在 LINK 段渲染时给 TextSpan 注入 `tooltip=seg.url`：
- `segment_to_span` (L117-121)：LINK 段加 `kwargs["tooltip"] = seg.url`
- `raw_to_visible_spans` (L370-372)：浏览态 display span 加 tooltip（仅当 `seg.seg_type == SegType.LINK and seg.url`）
- 编辑态光标在段内时，内容 piece span 加 tooltip，marker piece 不加

**风险**：Flet `TextSpan.tooltip` 底层 Flutter `TextSpan` 不原生支持，可能静默无效。先按 `tooltip=` 属性实现，实测若无效则降级为 P2（on_enter/on_exit + 全局浮层），不影响 P0 上线。

## 文件改动清单

| 文件 | 改动 |
|------|------|
| [utils/segment_helpers.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/segment_helpers.py) | 新增 `link_field_ranges` |
| [core/actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/actions.py) | EditorActions 加 3 个可选字段 |
| [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) | 新增 3 个动作函数 + 接入构造 + `_apply_outward_wrap` link 分支改 1 处 |
| [views/key_bindings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/key_bindings.py) | 模块级 `_extract_printable_char` + 2 处分发分支 |
| [views/segment_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/segment_view.py) | LINK 段注入 tooltip |

## 关键决策

1. **复用 outward_sel 而非程序化选中文本**：cursor_text_field 受 IME 约束不能设 value，无法程序化选中文本。outward_sel 是浏览态选区的成熟设施（高亮+删除+复制），用它高亮 URL 占位符是最自然的方案。
2. **新增"打字替换 outward 选区"为通用行为**：不仅服务于链接，也补齐了"浏览态选中任意文本→打字替换"的桌面端基础编辑直觉。
3. **Tab 跳转返回 bool 控制 fall-through**：未在链接段上时 Tab 正常落回缩进/插空格，不破坏既有列表/段落 Tab 行为。
4. **IME 首字符限制**：中文输入法组合态首字符不触发 KeyDownEvent，故选中 URL 占位符后首字符需为 ASCII（如 `h` 打 `https://`）。URL 几乎均为 ASCII，限制可接受；用户也可先 Tab 进入 edit 模式再 IME 输入。
5. **link_field_ranges 用 index("](") 而非算术**：稳健处理 selected 内含 `]` 的边界情况。

## 验证方法

### 启动应用手工验证
```
python main.py
```

| 场景 | 步骤 | 期望 |
|------|------|------|
| Ctrl+K 选中态 | 选中 "foo" → Ctrl+K | `[foo](url)` 渲染，"url" 高亮选中 |
| 打字替换 URL | 上一步后输入 'h' | "url" 替换为 "h"，光标在 'h' 后，进入编辑态 |
| Tab 跳 text | Ctrl+K 后按 Tab | 选区跳到 "foo" |
| Shift+Tab 跳 url | 选区在 text 时按 Shift+Tab | 选区跳回 "url" |
| 通用打字替换 | 选中任意单词 → 按 'a' | 选区替换为 'a'，进入编辑态 |
| 无选区 Ctrl+K | 无选区 Ctrl+K | 插入 `[](url)`，光标在 `[]` 间 |
| 编辑态 Tab | 光标在链接 text 内 → Tab | 光标跳到 url 前，URL 变灰可见 |
| 编辑态 Shift+Tab | 光标在 url 内 → Shift+Tab | 光标跳回 text 前 |
| 悬停 tooltip | 鼠标悬停链接 | 显示 URL（若 Flet 支持） |
| Ctrl+Click | Ctrl+Click 链接 | 浏览器打开 URL（回归） |

### 回归测试
- Ctrl+B/I/U 包裹选区 toggle 行为不变
- Shift+Arrow 扩展选区、Ctrl+C/X/V 剪贴板、Backspace/Delete 删除选区 — 全部回归
- 列表/引用块 Tab 缩进 — 在 LINK 段外按 Tab 仍走 `indent_or_outdent`
- IME 中文输入 — 正常文本输入流不受影响

### 单元测试（可选）
为 `link_field_ranges` 补充用例：
- `[text](url)` → `(1, 5, 7, 10)`
- `[](url)` → `(1, 1, 3, 6)`
- `[text]()` → `(1, 5, 7, 7)`
- 非 LINK 段 → `None`
- `[**b**](u)` → `(1, 6, 8, 9)`
