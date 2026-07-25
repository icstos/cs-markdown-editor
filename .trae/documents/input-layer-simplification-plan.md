# IME 输入层精简 + 文档对齐方案

## Context

Stack 双层架构（渲染层 + 透明光标 TextField）已在之前会话中完整实现，光标级 WYSIWYG 编辑可用。但当前 `handle_char_input` 为兼容中文 IME 演化成了 70 行 5 分支的复杂状态机，引入了 `virtual_raw` 冗余跟踪、重复检测防御、`cursor_off` state 在 IME 期间过时等维护负担，且多处文档与代码实际行为不一致。

用户已确认：**保留 IME 会话式输入**（value 增长式，不破坏中文输入组合态），**精简实现并对齐文档**。本次重构不改 IME 策略本身，只消除冗余、修复潜在 bug、统一文档。

**预期收益**：
- `handle_char_input` 70 行 → ~35 行，5 分支 → 3 分支
- 移除 `virtual_raw` 冗余字段
- 修复 `backspace_core`/`delete_core` 在 IME 会话期间用过时 `cursor_off` 的 bug（中文输入后按 Backspace 删错字符）
- 5 个文件的文档与代码行为对齐

## 关键发现

1. **`virtual_raw` 冗余**：`parser.reparse_line(line, new_raw)` 在 [parser.py:485-486](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/parser.py#L485-L486) 直接 `line.raw = new_raw`，无归一化。每次 `reparse_line` 后 `line.raw == virtual_raw`，二者永远同步，`virtual_raw` 是纯冗余跟踪。

2. **`backspace_core` 真实 bug**：IME 会话期间 `handle_char_input` 不调用 `set_cursor_off`（避免重渲染打断 IME），导致 `cursor_off` state 停在会话起始位置。`backspace_core` 读 `cursor_off` 会用过时值。例：raw="abc" off=3 → 输入 "你" 后 raw="abc你" 但 cursor_off 仍为 3 → Backspace 删除 raw[2]="c" 而非 raw[3]="你"。修复：改读 `cursor_ref.current.base`（`handle_char_input` 实时更新）。

3. **重复检测根因已移除**：双发问题原根因是 `cursor_text_field` 的 `autofocus=True`，已在 [cursor_layer.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/cursor_layer.py) 移除。残留场景仅"切行时 use_effect 异步清空窗口期 IME 重发"，保留 5 行安全网即可，无需 13 行完整检查。

## 实施步骤

### 步骤 1：修复 `backspace_core` / `delete_core`（独立 bug 修复，风险最低）

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)（约 L454-525）

两个函数开头读取光标位置处，将 `cursor_off` 替换为 `cursor_ref.current.base`：

```python
def backspace_core():
    """光标级 Backspace：删光标前字符；行首则与前一行合并。"""
    if outward_sel_ref.current is not None:
        handle_outward_delete()
        return
    if cursor_li is None:
        return
    li = cursor_li
    if not (0 <= li < len(document.lines)):
        return
    line = document.lines[li]
    if _is_fence(line):
        return
    # 修复：用 cursor_ref.current.base（IME 期间实时更新），不用 cursor_off state
    off = cursor_ref.current.base if cursor_ref.current else cursor_off
    if off > 0:
        _maybe_push_history()
        raw = _line_raw(line)
        new_raw = raw[:off - 1] + raw[off:]
        parser.reparse_line(line, new_raw)
        mark_dirty()
        _set_cursor(li, off - 1)
    elif li > 0:
        # 行首合并逻辑不变
        prev = document.lines[li - 1]
        if _is_fence(prev):
            return
        _push_history()
        undo_push_pending.current = True
        prev_raw = _line_raw(prev)
        cur_raw = _line_raw(line)
        junction = len(prev_raw)
        merged = prev_raw + cur_raw
        parser.reparse_line(prev, merged)
        document.lines = document.lines[:li] + document.lines[li + 1:]
        mark_dirty()
        suppress_blur.current = True
        _set_cursor(li - 1, junction)
```

`delete_core` 同样改读 `cursor_ref.current.base`。

### 步骤 2：移除 `virtual_raw` 字段

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)（L146 声明 + L357/L364/L373/L410 访问点）

- L146 声明改为：`input_session_ref = ft.use_ref({"li": -1, "start_off": -1, "last_value": ""})`（删除 `"virtual_raw": None`）
- `_end_input_session` 末尾重置同样删除 `virtual_raw` 键
- `handle_char_input` 重写时一并移除所有 `state["virtual_raw"]` 访问

### 步骤 3：重写 `handle_char_input` 为 3 分支（依赖步骤 2）

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)（L323-417）

5 分支 → 3 分支模型：

```python
def handle_char_input(value: str):
    """字符输入：增量式编辑（IME 友好，3 分支模型）。

    分支：
    - ignore: value == last_value（重发）或 last_value 包含 value（删除由 backspace 处理）
    - replace: IME 组合完成（value 含非 ASCII 且 last_value 全 ASCII），替换 [start_off, end_off]
    - append: 在 end_off 处插入增量（value 以 last_value 为前缀；或上次已提交非 ASCII 后起新组合）

    不调用 set_cursor_off（避免重渲染打断 IME），cursor_off state 在 _end_input_session
    中统一同步；cursor_ref 实时跟踪最新位置供 backspace_core/delete_core 读取。
    """
    if cursor_li is None or not value:
        return
    li = cursor_li
    if not (0 <= li < len(document.lines)):
        return
    line = document.lines[li]
    if _is_fence(line):
        return

    state = input_session_ref.current

    # 新会话启动（首次输入或会话已结束）
    if state["li"] != li or state["start_off"] < 0:
        raw = _line_raw(line)
        off = cursor_off
        # 安全网：value 已在文档中（切行时 use_effect 异步清空窗口期 IME 重发）
        if off + len(value) <= len(raw) and raw[off:off + len(value)] == value:
            state["li"], state["start_off"], state["last_value"] = li, off, value
            cursor_ref.current.reset(off + len(value), len(raw))
            return
        _maybe_push_history()
        state["li"] = li
        state["start_off"] = cursor_off
        state["last_value"] = ""

    start_off = state["start_off"]
    last_value = state["last_value"]

    # 分支 1: ignore
    if value == last_value:
        return
    if last_value and last_value.startswith(value):
        return

    raw = _line_raw(line)  # reparse_line 后 line.raw 已同步，无需 virtual_raw
    end_off = start_off + len(last_value)

    # 分支 2: replace（IME 组合完成：value 含非 ASCII，last_value 全 ASCII）
    is_ime_compose = (
        last_value
        and any(ord(c) > 127 for c in value)
        and all(ord(c) < 128 for c in last_value)
    )
    if is_ime_compose:
        new_raw = raw[:start_off] + value + raw[end_off:]
    # 分支 3: append（在 end_off 处插入增量）
    else:
        if value.startswith(last_value):
            new_part = value[len(last_value):]
        else:
            # 上次为已提交非 ASCII，本次为新组合：提交 last_value，起新会话
            new_part = value
            state["start_off"] = end_off
            start_off = end_off
        new_raw = raw[:end_off] + new_part + raw[end_off:]

    state["last_value"] = value
    new_off = start_off + len(value)
    cursor_ref.current.reset(new_off, len(new_raw))
    parser.reparse_line(line, new_raw)
    mark_dirty()
```

### 步骤 4：精简 `_end_input_session`

**文件**：[views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)（L249-271）

```python
def _end_input_session():
    """结束 IME 输入会话：同步 cursor_off + 重置状态 + 触发清空 value。

    仅从 _set_cursor 调用（li 变化/off 不连续/None）。此时 IME 组合已结束，
    清空 value 安全。use_effect([clear_value_seq]) 在重渲染后异步清空 TextField。
    """
    state = input_session_ref.current
    if state["li"] >= 0 and state["start_off"] >= 0:
        set_cursor_off(state["start_off"] + len(state["last_value"]))
        set_cursor_line(state["li"])
    input_session_ref.current = {"li": -1, "start_off": -1, "last_value": ""}
    set_clear_value_seq(clear_value_seq + 1)
```

### 步骤 5：文档对齐

#### [views/cursor_layer.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/cursor_layer.py)

- **L10-11**：删除「value="" 始终为空（构造值）」，改为「不设置 value 属性（避免 Flet 重渲染同步 value 打断 IME）；由 editor 端 use_effect([clear_value_seq]) 异步清空」
- **L72**：`on_change(value)` 注释「应仅 1 字符或空」改为「value 为 TextField 当前完整值（IME 期间可能多字符）」
- 保留 L86-95 + L100-102（已正确）

#### [views/line_view.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/line_view.py)

- **L5**：「光标 TextField 始终 value=""」改为「光标 TextField 不设 value 属性（IME 友好），由 editor 端 use_effect 异步清空」
- **L10**：补充「cursor_ref」（line_view 实际已用 `cursor_ref.current.base`）

#### [core/actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/actions.py)

- **L21**：「nav_seq 每次输入/导航递增，触发 cursor_text_field 的 key 重建以清空内部 value」改为「nav_seq 仅撤销/重做递增，强制 cursor_text_field key 重建以刷新内部状态；同行输入不递增以保持 IME 组合态」
- **L22**：「透明 cursor_text_field 始终 value=""」改为「透明 cursor_text_field 不设 value 属性（IME 友好），value 清空由 editor 端 use_effect 异步执行」
- **L48**：`nav_seq: int  # 触发 cursor_text_field 的 key 重建以清空内部 value` 改为 `nav_seq: int  # 撤销/重做时递增以强制 cursor_text_field key 重建`

#### [core/cursor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/cursor.py)

- **L6**：「cursor_base / cursor_extent」改为「base / extent」（字段名已改）
- **L8-11**：删除 `on_selection_change` / `draft_ref` 相关过时描述，改为「base/extent 在 _set_cursor 与 handle_char_input 中通过 reset() 同步更新；line_view 与 backspace_core/delete_core 通过 cursor_ref.current.base 读取 IME 期间最新光标位置（cursor_off state 在 IME 期间不更新）」
- **L19**：`draft_len` 字段注释改为「行 raw 总长度，用于光标越界钳制」

#### [views/editor.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py)

- **L14**：「use_effect([cursor_off]) 异步清空」改为「use_effect([clear_value_seq]) 异步清空」
- **L144-150**：移除 `virtual_raw` 相关注释
- **L249-262**：精简 `_end_input_session` 注释（步骤 4）
- **L323-334**：`handle_char_input` 文档改为 3 分支模型描述（步骤 3）

## 复用的现有函数

- [parser.py:reparse_line](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/parser.py#L480) — 行级重解析（已验证 `line.raw = new_raw` 直接赋值）
- [views/editor.py:_line_raw](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor.py) — 整行 raw 读取（line.raw 或 segments 拼接）
- [core/cursor.py:CursorState.reset](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/cursor.py) — 光标位置同步
- 现有 `clear_value_seq` + `use_effect` 机制保留（Flet 异步限制决定）

## 风险评估

| 改动 | 风险 | 回滚策略 |
|------|------|---------|
| 移除 `virtual_raw` | 极低（已验证 line.raw == virtual_raw） | 恢复字段与 4 处访问点 |
| `handle_char_input` 5→3 分支 | 中（IME 边缘行为） | 恢复独立分支3判定 |
| 移除完整重复检测 | 低（根因 autofocus 已移除） | 恢复 13 行检查 |
| `backspace_core`/`delete_core` 改读 cursor_ref | 低（实时同步） | 恢复读 cursor_off state |
| 文档对齐 | 零（仅改注释） | 不需要 |

## 验证

### 手动测试清单

**英文输入**：
- [ ] 点击行内定位 → 输入 "hello" → 文档逐字符显示，光标实时跟随
- [ ] Backspace 5 次 → 删除 "hello"，光标准确回退

**中文 IME 输入**（核心验证）：
- [ ] 点击 off=3 → 输入 "nihao" → 文档显示 "你好"（组合过程不破坏）
- [ ] 组合过程中光标保持在组合文本末尾
- [ ] **Backspace → 删除 "好"（不是 "ni" 或其他）** ← 此项验证步骤 1 的 bug 修复

**中英混合**：
- [ ] 输入 "hello" → 输入 "你好" → 输入 "world" → 文档显示 "hello你好world" 无丢失
- [ ] Backspace 5 次 → 删除 "world"，光标在 "好" 后

**IME 组合中断**：
- [ ] 输入 "ni"（未提交）→ 点击其他行 → 原行无残留字符
- [ ] 回到原行 → 可正常输入，无重发

**切行清空**：
- [ ] A 行输入 "w" → 点击 B 行 → A 行 TextField value 被清空（无残值）
- [ ] 立即回 A 行原位 → 若 IME 重发 "w" → 安全网拦截，无重复插入

**撤销/重做**：
- [ ] 输入若干字符 → Ctrl+Z → 文档回退，光标位置正确
- [ ] IME 组合后撤销 → 整个组合作为一个撤销单元

### 性能验证

- [ ] 输入 100 英文字符 → 无明显卡顿
- [ ] 输入 50 中文字符 → IME 组合流畅，无打断
