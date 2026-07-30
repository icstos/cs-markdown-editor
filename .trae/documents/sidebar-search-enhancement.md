# 侧边栏搜索功能增强

## Context

当前侧边栏搜索面板(`views/sidebar.py`)仅支持当前文档的行级子串匹配(大小写不敏感),点击结果只跳到行首(`off=0`)。参考 VSCode/Typora,需增强为:

1. **关键词高亮**:搜索结果预览中匹配段背景色高亮
2. **精确跳转**:点击结果光标跳转到关键词的精确 offset(非行首)
3. **4 个搜索选项**(均默认关闭,持久化到 settings.json):
   - 搜索整个文件夹(开启时跨文件搜索,范围沿用文件树根目录:`workspace_folder` > 当前文件所在目录)
   - 区分大小写
   - 查找整个单词
   - 正则表达式
4. **跨文件结果按文件分组展示**(VSCode 风格:文件名组标题 + 组内匹配行)

## 核心架构发现

- `ctx.set_cursor(li, off)` 已存在([_cursor.py:72](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor/_cursor.py#L72)),自动钳制 off 到 `[0, len(raw)]`,**光标层无需改造**
- 跳转链路 `jump_to_line(li)` 当前只接受 li,签名需扩展为 `jump_to_line(li, off=None)`(向后兼容:off=None 退化为 off=0)
- `open_file_by_path(path)` 不支持"打开后跳转",需新增 `jump_to` 参数 + pending_jump 机制解决 EditorActions 重建时序
- `Colors` dataclass ([styles.py:33](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/styles.py#L33)) `frozen=True`,可通过默认值字段扩展
- `update_setting(key, value)` 已存在,可复用持久化搜索选项

## 修改文件清单

| 文件 | 改动要点 |
|---|---|
| [config/settings.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/config/settings.py) | `DEFAULT_SETTINGS` 新增 4 键:`search_case_sensitive/search_whole_word/search_regex/search_folder`(全 False) |
| [styles.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/styles.py) | `Colors` 新增 `search_match_bg`/`search_match_fg`(带默认值);`_LIGHT`/`_DARK` 各赋值(亮黄/暗琥珀) |
| [views/editor/_scroll.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor/_scroll.py#L377) | `jump_to(li)` → `jump_to(li, off=None)`,非围栏行用 `ctx.set_cursor(li, off if off is not None else 0)` |
| [core/actions.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/core/actions.py#L86) | `jump_to_line` 类型注解 → `Callable[[int, int \| None], None]` |
| [views/editor/_context.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/editor/_context.py) | `jump_to` 类型注解放宽(默认值已是变参 lambda,运行时无需改) |
| [app/_focus_router.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/_focus_router.py#L48) | `jump_to_line(li)` → `jump_to_line(li, off=None)`,透传 |
| [app/_file_io_ops.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/_file_io_ops.py#L58) | `open_file_by_path(path, jump_to=None)`,jump_to 非空时写入 pending_jump_ref + 递增 sig |
| [app/_context.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/_context.py) | 新增 `pending_jump_ref`/`open_file_and_jump`/`set_pending_jump_sig` 装配槽 |
| [app/__init__.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/__init__.py) | 新增 `pending_jump_ref` + `pending_jump_sig` state + `use_effect([session, pending_jump_sig])` 消费跳转;装配 `open_file_and_jump` |
| [app/_render.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/_render.py) | Sidebar 实例化新增 prop `on_open_file_and_jump`/`on_update_setting` |
| [views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) | 搜索逻辑全面重写(算法 + UI),新增 `on_open_file_and_jump`/`on_update_setting` props |

## 关键实现要点

### 1. 跳转链路扩展(向后兼容)

`_scroll.py` L377 `jump_to` 改造(围栏块保持浏览态 fallback):

```python
def jump_to(li: int, off: int | None = None):
    if not (0 <= li < len(ctx.document.lines)):
        return
    line = ctx.document.lines[li]
    if _is_fence(line):
        ctx.set_cursor_line(li)
        ctx.set_cursor_li(None)
    else:
        ctx.set_cursor(li, off if off is not None else 0)
    # ... flash_li + safe_scroll_to 不变
```

所有现有调用方(大纲、[toc] 块、测试 mock)只传 li,`off=None` 退化为原 `off=0` 行为,零破坏。

### 2. 跨文件"打开后跳转"机制

**时序问题**:`open_file_by_path` 切换 tab 时 `set_session(session+1)`,通过 `key=f"{session}-0"` 重建 MarkdownEditor,EditorActions 在子组件渲染后才写入 `nav_ref.current`。Flet effect 执行顺序是深度优先渲染→自底向上 effect,App 的 effect 运行时 nav_ref.current 已就位。

**双触发器**:引入 `pending_jump_sig` 计数器,解决"文件已是当前 tab 时 session 不变"的边角情况。effect 依赖 `[session, pending_jump_sig]`。

```python
# app/__init__.py
pending_jump_ref = ft.use_ref(None)       # (li, off) | None
pending_jump_sig, set_pending_jump_sig = ft.use_state(0)

def _fire_pending_jump():
    job = pending_jump_ref.current
    if job is None:
        return
    pending_jump_ref.current = None  # 消费即清
    li, off = job
    actions = ctx.get_active_nav().current
    if actions is not None:
        actions.jump_to_line(li, off)

ft.use_effect(_fire_pending_jump, [session, pending_jump_sig])
```

```python
# app/_file_io_ops.py
def open_file_by_path(path: str, jump_to: tuple[int, int | None] | None = None):
    if jump_to is not None:
        ctx.pending_jump_ref.current = jump_to
        ctx.set_pending_jump_sig(ctx.pending_jump_sig + 1)
    # ... 原打开逻辑不变(去重切换/复用空标签/追加新 tab)
```

### 3. 搜索算法(re 模块统一处理 4 选项)

```python
import re

def _build_query_regex(query, case_sensitive, whole_word, regex) -> re.Pattern | None:
    """4 选项组合编译正则。无效正则返回 None(调用方提示)。"""
    q = query.strip()
    if not q:
        return None
    pattern_str = q if regex else re.escape(q)
    if whole_word:
        pattern_str = rf"\b{pattern_str}\b"
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern_str, flags)
    except re.error:
        return None
```

返回结构携带 offset+长度(供高亮与跳转):
- 当前文档:`[(line_idx, [(start, end), ...]), ...]`
- 跨文件:`[(path, file_name, [(line_idx, [(s,e),...]), ...]), ...]`

### 4. 跨文件搜索(异步 + 防竞态)

复用现有 `_scan_markdown_files` 提取所有 .md 路径,每文件单独 `asyncio.to_thread` 读取+匹配,文件间检查 token 提前终止。性能保护:
- `_MAX_CROSS_FILES=500` / `_MAX_PER_FILE=50` / `_MAX_CROSS_TOTAL=1000`
- `_MAX_FILE_SIZE=1MB`(getsize 先判断,不读盘)
- `_MAX_LINE_LEN=2000`(超长行只取首匹配)

### 5. UI 设计

```
┌─────────────────────────────────┐
│ 🔍 [搜索框 TextField         ]  │
├─────────────────────────────────┤
│ [📁] [Aa] [ab] [.*]  |  N 个结果 │  ← 选项工具栏 + 计数
├─────────────────────────────────┤
│  结果 ListView                   │
│  当前文档:行 N + 高亮预览        │
│  跨文件:文件名组标题             │
│     └ 行 N + 高亮预览            │
└─────────────────────────────────┘
```

- **选项工具栏**:4 个紧凑切换按钮,active 时主题色半透明背景(复用 `c.link` + `with_opacity(0.15)`,与现有 `_panel_tab` 风格一致)。文件夹用 IconButton;3 个文本选项用 Text-based toggle(Aa/ab/.*)
- **高亮预览**:`ft.Text(spans=[TextSpan...])` 单控件,匹配段 `bgcolor=c.search_match_bg`。窗口以首匹配为中心前后 30 字符,截断加 `…`
- **选项持久化**:`set_opts(key, value)` → `on_update_setting(f"search_{key}", value)` → App 层 `update_setting`

### 6. hook 缓存策略

- `pattern` = `use_memo([search_query, 4 选项])` — 编译正则较重,当前文档+跨文件共享
- `search_results` = `use_memo([pattern, _lines_sig])` — pattern 已聚合选项,避免重复依赖
- `cross_results` = `use_state + use_effect([pattern, _search_folder, root_dir, fs_version])` — 异步 IO 不能用 memo,token 防竞态

## 验证步骤

### 单元测试(新增 `tests/test_sidebar_search.py`)

1. `_build_query_regex` 4 选项组合(普通子串/大小写/整词/正则/无效正则)
2. `_match_lines` 返回 offset+长度,多匹配行返回多区间
3. `_search_in_file` 边界(超大文件跳过/读取失败/超长行)
4. `_collect_md_paths` 嵌套树扁平化
5. `_build_preview_spans` 匹配段有 bgcolor
6. `jump_to(li, off)` 非围栏行调 `set_cursor(li, off)`,围栏行 fallback,`off=None` 退化为 0

### 集成手动测试

1. 当前文档搜索:输入关键词→预览高亮→点击→光标跳到精确 offset(非行首)
2. 区分大小写:切换 Aa→大小写差异结果变化
3. 整词:切换 ab→`cat` 不匹配 `category`
4. 正则:切换 .*→`\d+` 匹配数字;`[`→显示"正则表达式无效"
5. 搜索整个文件夹:切换 📁→结果按文件分组→点击组内项→文件打开+光标跳转
6. 跨文件时序:点击未打开文件→新 tab 打开→光标定位到匹配 offset
7. 同文件已打开:点击当前 tab 文件的跨文件结果→不新开 tab→光标跳转
8. 选项持久化:切换后重启→状态保留
9. 性能:500 文件工作区+1MB 文件→不卡 UI
10. 大纲回归:点击大纲项→仍跳到行首(向后兼容未破坏)

## 实施顺序

1. **底层先行**:`config/settings.py` + `styles.py`(纯数据,零风险)
2. **跳转链路**:`_scroll.py` → `_focus_router.py` → 类型注解(可独立验证大纲仍工作)
3. **跨文件打开跳转**:`_file_io_ops.py` + `app/__init__.py` effect + `_context.py` + `_render.py`
4. **搜索算法**:`views/sidebar.py` 纯函数(`_build_query_regex`/`_match_lines`/`_search_in_file`/`_build_preview_spans`)
5. **搜索 UI**:`_render_search_panel` 重写 + 选项工具栏 + Sidebar props
6. **测试**:`tests/test_sidebar_search.py`
