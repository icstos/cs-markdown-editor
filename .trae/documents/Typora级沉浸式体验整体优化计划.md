# Typora 级沉浸式体验整体优化计划

## Summary

围绕「媲美 Typora 的沉浸式实时渲染、所见即所得编辑体验」总目标，分四个阶段落地六项改动，按「内功 → 视觉 → 信息 → 动效」递进，每阶段独立可验证、可单独回滚：

- **阶段 1（方向 B）撤销粒度与性能**：新增行级快照 `LineEditSnapshot`，editor 高频编辑路径切到行级快照，大文档撤销/重做从 O(全文序列化) 降到 O(单行)。
- **阶段 2（方向 A）行内 WYSIWYG 打磨**：链接/图片段 URL 按光标位置折叠——光标不在 URL 子段时 URL 零宽度折叠，仅显示文本；光标进入 URL 子段时才灰色可见。对齐 Typora 最小语法噪声。
- **阶段 3（方向 D）信息层与外围打磨**：状态栏补段落数 + 阅读时长；工具栏 tooltip 动态读取 `ShortcutManager` 自定义键位。
- **阶段 4（方向 C）交互动效**：侧边栏开合宽度动画；大纲/搜索跳转后目标行临时高亮（脉冲淡出）。

## Current State Analysis

### 阶段 1 现状（撤销/重做）
- [core/history.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/history.py) 仅 `EditorSnapshot`（frozen dataclass：markdown 全文 + cursor_li/off + raw_mode/raw_draft）。`push`/`pop_undo`/`pop_redo` 栈容量 50。
- editor.py `_make_snapshot`（L209）每次 `parser.serialize(document)` 序列化全文；`_push_history`/`_maybe_push_history` 在 backspace/delete/on_submit/indent/set_block/handle_paste/_delete_raw_range 等高频路径调用。
- **差距**：每次按键级编辑都入栈一份全文快照。1000 行文档撤销栈 50 条 = 50×全文序列化，内存与 CPU 双重压力，与 Typora 行级撤销差距明显。

### 阶段 2 现状（行内 WYSIWYG）
- [utils/segment_helpers.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/segment_helpers.py) `split_seg_for_display`（L117-131）对 LINK 固定返回 `[("[",marker),(text,content),("](",marker),(url,marker),(")",marker)]`，URL 永远标 `is_marker=True`。
- [views/segment_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/segment_view.py) `raw_to_visible_spans`（L330-346）：光标在段内时所有 marker（含 URL）一律灰色可见。
- **差距**：光标在链接文本上时，URL `](url)` 也灰色显示，视觉噪声大。Typora 仅在光标进入 URL 子段时显示 URL。

### 阶段 3 现状（信息层）
- [views/status_bar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/status_bar.py) L77-96：行/列 + 词数 + 字符数。缺段落数、阅读时长。
- [views/toolbar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/toolbar.py) L63-78：tooltip 硬编码 `"加粗  Ctrl+B"` 等，不读 ShortcutManager。
- **差距**：用户改键位后 tooltip 仍显示旧键位；状态栏信息密度低于 Typora。

### 阶段 4 现状（动效）
- [main.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/main.py) L663-677：`sidebar_open` 三元 `Sidebar(...) if sidebar_open else ft.Container(width=0)`，无动画，开合瞬间跳变。
- [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) `jump_to`（L1588）：设光标 + 滚动贴顶，无目标行高亮反馈。
- **差距**：侧边栏硬切；跳转后无视觉确认，长文档跳转后需扫视才能定位目标行。

## Proposed Changes

### 阶段 1：行级撤销快照（方向 B）

#### B1 — 新增 `LineEditSnapshot` 类型（core/history.py）

**What**：在 `EditorSnapshot` 旁新增 frozen dataclass `LineEditSnapshot`，记录单行编辑前后状态。

**Why**：高频按键级编辑（字符输入、backspace、delete）只改一行，全文快照浪费。行级快照仅存改动行的 before/after raw + 光标位置，内存 O(1)/操作，撤销时只 reparse 单行。

**How**：
```python
@dataclass(frozen=True)
class LineEditSnapshot:
    """行级编辑快照：单行 before/after raw + 光标恢复点。

    适用：字符输入 / backspace / delete / 行内格式包裹等单行编辑。
    不适用：行增删（on_submit/_delete_raw_range/handle_paste 多行），
    这些仍用 EditorSnapshot 全文快照。
    """
    line_idx: int
    before_raw: str
    after_raw: str
    cursor_li: int | None
    cursor_off: int
    raw_mode: bool
    raw_draft: str
```

`EditHistory` 栈类型扩展为 `list[EditorSnapshot | LineEditSnapshot]`，`push`/`pop_undo`/`pop_redo` 签名不变（鸭子类型，比较末项 `== snap` 仍生效，因为两类快照字段不同永远不会相等，去重逻辑安全）。`pop_undo`/`pop_redo` 返回类型标注改为联合类型。

**文件**：[core/history.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/history.py)

#### B2 — editor 高频路径切到行级快照（views/editor.py）

**What**：在 `_maybe_push_history` 前增加 `_push_line_edit(before_raw, after_raw)`；`_restore_snapshot` 分支处理 `LineEditSnapshot`。

**Why**：字符输入（`handle_char_input`）目前不 push 历史（用 `undo_push_pending` 标记 + 下次操作前 `_maybe_push_history` 入全文快照）。改为：每次 IME 会话结束/行内编辑边界入行级快照，撤销时单行 reparse。

**How**：
- 新增 `_push_line_edit(li: int, before_raw: str)`：在 `handle_char_input` 会话启动（L375 `_maybe_push_history()` 处）改为入 `LineEditSnapshot(line_idx=li, before_raw=原始raw, after_raw=当前raw, ...)`。
- `backspace_core`/`delete_core` 单字删除（L470/L510 `_maybe_push_history()`）改用 `_push_line_edit`。
- 行合并（L481 `_push_history()`、L520 `_push_history()`）、`on_submit`、`handle_paste` 多行、`_delete_raw_range` 保持 `EditorSnapshot` 全文快照（行结构变化）。
- `_restore_snapshot` 增加 `isinstance(snap, LineEditSnapshot)` 分支：
  ```python
  if isinstance(snap, LineEditSnapshot):
      line = document.lines[snap.line_idx]
      _reparse_atomic(line, snap.before_raw)
      mark_dirty()
      set_cursor_li(snap.cursor_li)
      set_cursor_off(snap.cursor_off)
      set_cursor_line(snap.cursor_li or 0)
      set_nav_seq(nav_seq + 1)
  ```
- `undo`/`redo` 取栈时，`pop_undo(current)` 的 `current` 仍用 `_make_snapshot()`（全文），行级快照入重做栈时存 `after_raw` 作为撤销恢复点。

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)

### 阶段 2：链接/图片 URL 折叠（方向 A）

#### A1 — URL 按光标位置折叠（utils/segment_helpers.py + views/segment_view.py）

**What**：`split_seg_for_display` 对 LINK/IMAGE 增加 `cursor_local: int | None` 参数，光标不在 URL 子段时 URL 标 `is_marker=True` 但 `text=""`（零宽度折叠）；光标在 URL 子段时 URL 标 `is_marker=True` 且 `text=url`（灰色可见）。

**Why**：Typora 链接渲染态只显示文本 `[文本]`，光标进入 `[文本](url)` 的 url 区域时才显示 url。当前实现光标在链接任意位置都显示 url，噪声大。

**How**：
- `split_seg_for_display(seg, cursor_local=None)` 新增可选参数（默认 None=浏览态，URL 折叠）。
- LINK 分支（L117-123）改造：
  ```python
  if t == SegType.LINK:
      if raw.startswith("[") and raw.endswith(")") and "](" in raw:
          idx = raw.index("](")
          text_part = raw[1:idx]
          url_part = raw[idx + 2:-1]
          url_start_local = idx + 2  # URL 在 raw 中的起始偏移
          url_end_local = len(raw) - 1
          # 光标是否在 URL 子段 [url_start_local, url_end_local)
          url_visible = (
              cursor_local is not None
              and url_start_local <= cursor_local < url_end_local
          )
          url_text = url_part if url_visible else ""
          return [
              ("[", True), (text_part, False), ("](", True),
              (url_text, True),  # 光标不在 URL 时零宽度
              (")", True),
          ]
      return [(raw, False)]
  ```
- IMAGE 同理（L125-131）。
- `segment_view.raw_to_visible_spans`（L332）调用 `split_seg_for_display(seg)` 改为传入段内光标偏移：
  ```python
  if cursor_in_seg:
      cursor_local = cursor_raw_offset - seg_start
      pieces = split_seg_for_display(seg, cursor_local=cursor_local)
  ```
- 注意：`segment_to_spans_partial`（向外选区高亮）调用 `split_seg_for_display(seg)` 不传 cursor（保持浏览态 URL 折叠），保证选区高亮时 URL 也不显示。

**文件**：[utils/segment_helpers.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/segment_helpers.py)、[views/segment_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/segment_view.py)

### 阶段 3：信息层与外围打磨（方向 D）

#### D1 — 状态栏补段落数与阅读时长（views/status_bar.py）

**What**：状态栏在「行/列」与「词数」之间插入「段落数 N」；在「字符数」后追加「阅读 N min」。

**Why**：Typora 状态栏显示字符数、词数、段落数、阅读时长，信息密度高，利于写作节奏感知。

**How**：
- `StatusBar` 内增加派生：
  ```python
  para_count = sum(1 for ln in document.lines if ln.block_type == BlockType.PARAGRAPH and (ln.raw or "").strip())
  reading_min = max(1, round(word_count / 300))  # 中文 300 字/分钟
  ```
- Row 控件序列在「行 列」后插入：
  ```python
  ft.Container(width=Spacing.XXL),
  ft.Text(value=f"{para_count} 段", size=12, color=c.muted, font_family=FONT_MAIN),
  ```
  末尾追加：
  ```python
  ft.Container(width=Spacing.XL),
  ft.Text(value=f"阅读 {reading_min} min", size=12, color=c.muted, font_family=FONT_MAIN),
  ```
- 需 `from models import BlockType`（status_bar.py 当前未导入 BlockType）。

**文件**：[views/status_bar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/status_bar.py)

#### D2 — 工具栏 tooltip 动态读取快捷键（views/toolbar.py + main.py）

**What**：`Toolbar` 新增 `shortcut_mgr` 参数，tooltip 从 `shortcut_mgr.get("edit", "format_bold")` 等读取实际键位并格式化。

**Why**：用户自定义快捷键后，工具栏 tooltip 仍显示硬编码 `Ctrl+B`，不一致。

**How**：
- `Toolbar` 签名首位新增 `shortcut_mgr`：
  ```python
  @ft.component
  def Toolbar(shortcut_mgr, on_h1, on_h2, ...):
  ```
- 内部辅助：
  ```python
  def _combo(action_id: str) -> str:
      combo = shortcut_mgr.get("edit", action_id) if shortcut_mgr else ""
      return _format_combo(combo)  # "ctrl+shift+s" → "Ctrl+Shift+S"

  def _format_combo(combo: str) -> str:
      if not combo:
          return ""
      parts = combo.split("+")
      label = "+".join(p.upper() if p in ("ctrl","alt","shift") else p for p in parts)
      return label
  ```
- tooltip 改造（保留动作名 + 键位）：
  ```python
  _btn(ft.Icons.FORMAT_BOLD, f"加粗  {_combo('format_bold')}" if _combo('format_bold') else "加粗", on_bold),
  ```
  块级按钮（h1/h2/h3/段落/列表/引用/代码块/分隔线）无快捷键绑定的保持原 tooltip；行内按钮（bold/italic/highlight/code/link/strike）读 `format_*`。
- `_btn` 工厂不变（tooltip 字符串外部组装）。
- main.py 调用处（editor.py `_tool_area` L1837）`Toolbar(...)` 首位传 `shortcut_mgr`。editor.py 已有 `shortcut_mgr`？——需确认：editor.py 当前未接收 shortcut_mgr，需在 `MarkdownEditor` 签名新增 `shortcut_mgr` 参数，main.py L678 传入。

**文件**：[views/toolbar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/toolbar.py)、[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)（MarkdownEditor 签名 + _tool_area 传参）、[main.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/main.py)（MarkdownEditor 调用处传 shortcut_mgr）

### 阶段 4：交互动效（方向 C）

#### C1 — 侧边栏开合宽度动画（main.py）

**What**：`sidebar_open` 不再用三元硬切，改为 `ft.AnimatedSwitcher` 或对 `ft.Container(width=...)` 启用 `animate=ft.Animation(...)`，宽度从 0↔sidebar_width 平滑过渡。

**Why**：硬切突兀，动画过渡更符合桌面端交互直觉（VSCode/Typora 均有侧边栏动画）。

**How**：
- 方案：用 `ft.Container` 包裹 Sidebar，`width=sidebar_width if sidebar_open else 0`，`animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT)`，`clip_behavior=ft.ClipBehavior.HARD_EDGE`（宽度收拢时裁剪内容）。
- 替换 main.py L663-677：
  ```python
  sidebar_container = ft.Container(
      width=width_px if sidebar_open else 0,
      animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
      clip_behavior=ft.ClipBehavior.HARD_EDGE,
      content=Sidebar(...) if sidebar_open else ft.Container(),
  )
  body = ft.Row(
      controls=[sidebar_container, MarkdownEditor(...)],
      spacing=0,
      expand=True,
  )
  ```
- 注意：`sidebar_open=False` 时 Sidebar 仍需渲染（content 不可为空容器否则动画期无内容），改为始终渲染 Sidebar 但外层 Container width=0 裁剪。或用 `ft.AnimatedSwitcher` 切换。选择前者更平滑（避免 switcher 的 fade 切换与 width 动画叠加）。
- 实际实现：`sidebar_open=False` 时 content=空 `ft.Container()`，width 从 sidebar_width 动画到 0；`sidebar_open=True` 时 content=Sidebar，width 从 0 动画到 sidebar_width。需在动画完成前 content 已就位，故 open 时先 setContent 再 setWidth，close 时先 setWidth 再 setContent（动画后）。简化：始终持有 Sidebar，width 控制可见性 + clip。

**文件**：[main.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/main.py)

#### C2 — 跳转目标行高亮（views/editor.py + views/line_view.py）

**What**：`jump_to(li)` 后触发目标行 1.2s 脉冲高亮（淡入→保持→淡出）。

**Why**：长文档跳转后无视觉确认，用户需扫视定位。Typora/VSCode 跳转后目标行短暂高亮。

**How**：
- editor.py 新增 state：`flash_li, set_flash_li = ft.use_state(-1)`（-1=无高亮）。
- `jump_to` 末尾：
  ```python
  set_flash_li(li)
  page = ft.context.page
  if page is not None:
      async def _clear_flash():
          await asyncio.sleep(1.2)
          set_flash_li(-1)
      page.run_task(_clear_flash)
  ```
- 主渲染循环 LineView 调用处（L1773）新增 prop：
  ```python
  is_flash=flash_li == i,
  ```
- LineView 签名新增 `is_flash: bool = False`，传给 `_wrap_block`。
- `_wrap_block`（line_view.py L120）增加 `is_flash` 分支：
  ```python
  if is_flash:
      content = ft.Container(
          content=content,
          bgcolor=ft.Colors.with_opacity(0.18, c.link),
          border_radius=Radius.LG,
          animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
      )
  ```
- 高亮随 `flash_li` 从 `li` 变 `-1` 自动消失（重渲染 + animate 淡出 bgcolor）。为保证淡出，flash 容器始终存在但 `bgcolor` opacity 从 0.18→0，需 `animate` 在 Container 上。简化：`is_flash=True` 时 bgcolor=0.18，`is_flash=False` 时 bgcolor=0.0，animate 200ms 过渡。

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)、[views/line_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/line_view.py)

## Assumptions & Decisions

1. **行级快照适用边界**：仅单行内容编辑（字符输入、单字删除、行内格式包裹）用 `LineEditSnapshot`；行增删（回车、行合并、多行粘贴、跨行选区删除、缩进改变行结构）仍用 `EditorSnapshot` 全文快照。理由：行结构变化涉及 `document.lines` 列表增删，单行快照无法表达。
2. **URL 折叠粒度**：光标在 `[文本](url)` 的 `文本` 子段时 URL 折叠；光标在 `url` 子段时 URL 可见。光标在 `[` `](` `)` 等 marker 上时 URL 折叠（marker 本身灰色可见）。判定区间：`[url_start_local, url_end_local)` 左闭右开。
3. **阅读时长算法**：中文 300 字/分钟（word_count 已含中文字符），最低 1 分钟。与国际阅读时长估算一致。
4. **侧边栏动画方案**：用 Container width + animate，非 AnimatedSwitcher。理由：switcher 切换有 fade，与 width 动画叠加显凌乱；Container width 动画更顺滑。close 时 content 置空避免动画期残留；open 时先 setContent。
5. **跳转高亮不与当前行高亮冲突**：`is_current_line`（蓝色左边框 + 淡蓝底）与 `is_flash`（淡蓝底脉冲）可叠加。视觉上 flash 更强烈且 1.2s 消失，current 持续。若叠加过亮，flash 容器包在 current 容器外层（先 flash 后 current 包裹），底色叠加可接受。
6. **tooltip 格式化**：`ctrl+shift+s` → `Ctrl+Shift+S`（修饰键大写，字符键原样）。无绑定时 tooltip 仅显示动作名。
7. **不改动既有快捷键系统**：阶段 3 D2 仅读取 `shortcut_mgr.get()`，不改 `ShortcutManager` 接口（上一轮已完成快捷键自定义改造）。

## Verification Steps

### 阶段 1 验证
1. 启动应用，打开 200+ 行文档，连续输入 50 字符 → 撤销应逐字恢复（行级快照），不卡顿。
2. 大文档下连续 backspace 删 30 字 → 撤销逐字恢复。
3. 回车新增行后撤销 → 行消失（全文快照恢复），验证行级/全文快照混合栈正确。
4. 多行粘贴后撤销 → 恢复粘贴前状态（全文快照）。
5. 重做链路：撤销 3 次后重做 → 逐级恢复。
6. 内存对比：50 次按键后 undo 栈内存应远小于全文快照方案。

### 阶段 2 验证
1. 渲染态链接 `![alt](url.png)` / `[文本](http://x)` → URL 不可见，仅显示 alt/文本。
2. 点击链接文本 → 光标在文本子段，URL 仍不可见（折叠）。
3. 用方向键把光标移到 `](` 之后（URL 子段）→ URL 灰色可见。
4. 光标移出链接段 → URL 再次折叠。
5. 向外选区拖拽经过链接 → 选区高亮正常，URL 不显示（浏览态）。

### 阶段 3 验证
1. 状态栏显示「行 X 列 Y」「N 段」「N 词」「N 字符」「阅读 N min」五项。
2. 段落数仅计非空段落行（不含标题/列表/引用/空行）。
3. 阅读时长随词数变化，最低 1 min。
4. 设置页改 `format_bold` 为 `Ctrl+Shift+B` → 工具栏加粗按钮 tooltip 显示「加粗  Ctrl+Shift+B」。
5. 清空 `format_italic` 绑定 → tooltip 仅显示「斜体」。

### 阶段 4 验证
1. 点击侧边栏切换按钮 → 侧边栏 200ms 平滑收拢/展开，无硬切。
2. 大纲点击某标题 → 文档滚动贴顶 + 目标行 1.2s 蓝色脉冲高亮后淡出。
3. 搜索结果点击 → 同上高亮。
4. 跳转高亮与当前行高亮叠加时视觉可接受（不刺眼）。
5. 连续点击不同大纲项 → 高亮跟随最新目标行，旧高亮立即消失。

### 整体冒烟
- `python main.py` 启动无异常。
- 亮/暗主题切换后所有新增高亮/动画颜色正确。
- IME 输入中文期间光标行为不受阶段 1 行级快照影响（IME 会话仍走 `handle_char_input`，行级快照在会话启动时入栈一次，不每字入栈）。

## 关键文件清单

| 阶段 | 文件 | 改动 |
|------|------|------|
| B1 | [core/history.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/history.py) | 新增 `LineEditSnapshot` dataclass |
| B2 | [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) | `_push_line_edit` + `_restore_snapshot` 分支 + 高频路径切换 |
| A1 | [utils/segment_helpers.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/utils/segment_helpers.py) | `split_seg_for_display` 增 `cursor_local` 参数 |
| A1 | [views/segment_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/segment_view.py) | `raw_to_visible_spans` 传段内光标偏移 |
| D1 | [views/status_bar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/status_bar.py) | 补段落数 + 阅读时长 |
| D2 | [views/toolbar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/toolbar.py) | `shortcut_mgr` 参数 + tooltip 动态格式化 |
| D2 | [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) | `MarkdownEditor` 签名增 `shortcut_mgr` + `_tool_area` 传参 |
| D2 | [main.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/main.py) | `MarkdownEditor` 调用处传 `shortcut_mgr` |
| C1 | [main.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/main.py) | 侧边栏 Container width + animate |
| C2 | [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) | `flash_li` state + `jump_to` 触发 + LineView 传 `is_flash` |
| C2 | [views/line_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/line_view.py) | `_wrap_block` 增 `is_flash` 高亮分支 |

## 实施顺序

按阶段递进，每阶段完成后冒烟验证再进入下一阶段：

1. **阶段 1（B1→B2）**：先 `core/history.py` 加类型，再 editor.py 切换。独立可验证（撤销/重做链路）。
2. **阶段 2（A1）**：改 segment_helpers + segment_view。独立可验证（链接 URL 折叠）。
3. **阶段 3（D1→D2）**：先状态栏（独立），再工具栏 tooltip（涉及 main→editor→toolbar 三层传参）。
4. **阶段 4（C1→C2）**：先侧边栏动画（main.py 单文件），再跳转高亮（editor + line_view）。
