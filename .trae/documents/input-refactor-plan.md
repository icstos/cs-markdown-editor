# 输入功能彻底重构方案：Typora 式空 TextField + Delta 计算

## Context

当前编辑器的输入功能依赖 `cursor_field_value` state 镜像 `input_session.last_value`（非空 TextField value），导致了一连串 IME workaround：4 分支模型（ignore/composing-cancel/replace/append）、`_fix_ime_doubling`（翻倍修正）、`_detect_ime_compose`（上屏检测）、`_compute_composing_trim`（回车裁剪）。这些 workaround 的根因是：`_reparse_atomic` 触发 `line.notify()` → 重渲染 → Flet 同步非空 value 到 Flutter → IME 状态被打断 → 字符吞没/翻倍/composing 残留。

用户要求彻底重构为 Typora 式空 TextField：编辑框内容始终为空，每个字符输入自动渲染到文档，光标位置基于字体度量计算，简化状态管理。

## 核心假设（需先验证）

**Flet 0.86 声明式 diff：若 TextField `value=""` 在重渲染前后保持 `""`（相等），Flet 不同步 value 到 Flutter，Flutter 端内部 value（由 IME 管理）被保留。**

历史 bug 区分：`value=None`（未设置）→ Flet 同步 null → 清空内部值 → IME 重发（吞没根因）。`value=""`（显式空串）→ diff 检测 `""→""` 无变化 → 跳过同步 → 内部值保留。

**用户决策**：先验证再实施（Phase 0 为阻断性前置），_fix_ime_doubling 全部移除（不留安全网）。

## 实施步骤

### Phase 0：核心假设验证（阻断性前置，用户手动执行）

创建 `verify_value_diff.py`（项目根，验证后删除不入仓），在 Windows + 中文输入法下运行：

```python
import flet as ft

@ft.component
def VerifyApp():
    counter, set_counter = ft.use_state(0)
    last_value_ref = ft.use_ref("")
    history_ref = ft.use_ref([])

    def on_change(e):
        v = e.control.value
        last_value_ref.current = v
        history_ref.current.append(v)
        set_counter(counter + 1)  # 触发重渲染，value 仍为 ""

    return ft.Column([
        ft.Text(f"重渲染次数: {counter}"),
        ft.Text(f"上次值: {last_value_ref.current!r}"),
        ft.Text(f"历史: {history_ref.current[-5:]}"),
        ft.TextField(value="", on_change=on_change, autofocus=True),
    ])

ft.run(VerifyApp, view=ft.AppView.WEB_BROWSER)
```

**验证用例与判定**：

| # | 操作 | 期望（通过） | 失败特征 |
|---|------|-------------|---------|
| A | 依次输入 `abcd` | history: `['a','ab','abc','abcd']` | 中途 value 被清空，字符不累积 |
| B | 拼音 `ni` → 选 `你` | history: `['n','ni','你']` | composing 中断，无法完整输入 |
| C | 五笔 `wq` → `你` | history: `['w','wq','你']` | 翻倍 `['w','wq','wqwq']` 或 composing 重置 |
| D | composing `wq` 按 Esc | history: `['w','wq','w']` 或 `['w','wq','']` | composing 中途被打断 |
| E | 连续 10 个 `a` | history 累积到 `'aaaaaaaaaa'` | 中途清空，最终少于 10 个 |

**全部通过** → 进入 Phase 1-6 全量实施，_fix_ime_doubling 全部移除。
**任一失败** → 启用 Fallback A（保留 `cursor_field_value` + delta 模型，保留 `_fix_ime_doubling`）。

### Phase 1：State 简化

**文件**：`views/editor/__init__.py`、`views/editor/_context.py`

- 删除 `cursor_field_value` / `set_cursor_field_value` state（`__init__.py:120`）
- 删除 `clear_value_seq` / `set_clear_value_seq` state（`__init__.py:117`）
- 删除 `input_session_ref` ref（`__init__.py:115`）
- 新增 `last_value_ref = ft.use_ref("")`（跟踪上次 on_change 值）
- `EditorContext` 同步增删字段

### Phase 2：cursor_layer + line_view 简化

**文件**：`views/cursor_layer.py`、`views/line_view.py`

- `cursor_text_field`：`value` 始终为 `""`，更新注释说明 diff 策略
- `LineView`：删除 `input_session_ref` / `cursor_value` 参数
- `_cursor_overlay`：删除 `cursor_value` / `pos_value` 参数，删除 `start_local` 调整逻辑，`cursor_px_x = vline.offsets_x[local_off]` 直接使用

### Phase 3：handle_char_input 重写（核心）

**文件**：`views/editor/_cursor.py`、`views/editor/_render.py`

`_render.py`：删除 `cursor_field_value` / `input_session_ref` 传参。

`_cursor.py` 重写 `handle_char_input` 为 delta 模型：

```python
def handle_char_input(value: str):
    if ctx.cursor_li is None or ctx.outward_sel_ref.current is not None:
        return
    li = ctx.cursor_li
    if not (0 <= li < len(ctx.document.lines)):
        return
    line = ctx.document.lines[li]
    if _is_fence(line):
        return

    old_value = ctx.last_value_ref.current
    new_value = value

    # 计算公共前缀
    cp = 0
    while cp < len(old_value) and cp < len(new_value) and old_value[cp] == new_value[cp]:
        cp += 1
    removed = old_value[cp:]    # 被删除部分
    inserted = new_value[cp:]   # 被插入部分

    if not removed and not inserted:
        return  # 无变化，忽略

    raw = _line_raw(line)
    cursor_base = ctx.cursor_ref.current.base if ctx.cursor_ref.current else ctx.cursor_off
    start_off = cursor_base - len(old_value)
    doc_start = max(0, min(start_off + cp, len(raw)))
    doc_end = max(0, min(start_off + len(old_value), len(raw)))

    new_raw = raw[:doc_start] + inserted + raw[doc_end:]
    ctx.push_line_edit(li, raw)
    _reparse_atomic(line, new_raw)
    ctx.mark_dirty()

    new_cursor = doc_start + len(inserted)
    ctx.cursor_ref.current.reset(new_cursor, len(new_raw))
    ctx.last_value_ref.current = new_value
```

**自动覆盖场景**：
- ASCII 追加：`old="" new="a"` → insert `"a"`
- composing 增长：`old="w" new="wq"` → cp=1, insert `"q"`
- IME 上屏：`old="wq" new="你"` → cp=0, removed=`"wq"` insert=`"你"`
- 连续上屏第二字：`old="你vb" new="你好"` → cp=1, removed=`"vb"` insert=`"好"`
- composing 取消：`old="你vb" new="你"` → cp=1, removed=`"vb"` insert=`""`

`_end_input_session` 简化：重置 `last_value_ref.current = ""` + 递增 `nav_seq` 重建 TextField。

`on_submit` 保留 composing 安全网（内联 5 行判断，非独立函数）：若 `last_value` 是 `value` 的真前缀，裁剪文档区域。

### Phase 4：移除 IME workaround 函数

**文件**：`views/_editor_helpers.py`

- 删除 `_fix_ime_doubling`（L67-106）
- 删除 `_detect_ime_compose`（L109-142）
- 删除 `_compute_composing_trim`（L145-174）

### Phase 5：测试更新

**文件**：`tests/test_cursor_ime_cancel.py` → `tests/test_cursor_delta.py`、`tests/test_cursor_fence_submit.py`、`tests/test_editor_helpers.py`

- 更新 mock ctx：`input_session_ref` → `last_value_ref`，删除 `set_cursor_field_value` / `set_clear_value_seq`
- 删除 IME workaround 测试（`test_ime_doubling_*`、`test_ime_compose_*`、`test_composing_trim_*`）
- 新增 delta 模型测试：ASCII 追加 / composing 增长 / IME 上屏替换 / composing 取消 / 全部放弃 / 无变化忽略

### Phase 6：文档清理

更新所有改动文件的 docstring，移除对已删状态的引用。

## Fallback 方案（Phase 0 失败时）

**Fallback A**：保留 `cursor_field_value` state（value 仍非空），但 `handle_char_input` 改用 delta 模型。因 value 仍同步到 Flutter，IME 翻倍根因可能再现，保留 `_fix_ime_doubling` 作为安全网。删除 `_detect_ime_compose` / `_compute_composing_trim`（delta 自动覆盖）。净收益：4 分支 → 1 分支，移除 2/3 workaround，保留 1 个翻倍修正。

## 验证

1. **单元测试**：`python -m pytest tests/ -q` 全绿
2. **手动测试**（Windows + 中文输入法）：
   - ASCII 连续输入无吞字/翻倍
   - 中文拼音/五笔上屏无 composing 残留
   - composing 取消（Esc/Enter）无残留
   - 跨行切换无字符泄漏
   - 撤销重做正常
   - 光标像素对齐无漂移
   - 长串相同字符（10 个 `a`）正常累积

## 关键文件

- `views/editor/_cursor.py` — handle_char_input 重写（核心）
- `views/editor/__init__.py` — state 增删
- `views/editor/_context.py` — EditorContext 字段
- `views/line_view.py` — _cursor_overlay 简化
- `views/cursor_layer.py` — value="" 策略
- `views/_editor_helpers.py` — 移除 IME workaround
- `views/editor/_render.py` — 移除传参
