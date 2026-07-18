"""Markdown 编辑器入口。

- 注册本地字体 AlibabaPuHuiTi-3-55-Regular
- 声明式渲染：page.render(App)
- 文档状态上抛到 App 层，便于 New / Open / Save
- 段级编辑、Typora 式实时渲染由 views/editor 负责
"""

import asyncio
import json
import os
import re

import flet as ft

import parser
from models import BlockType, Document
from styles import FONT_MAIN, get_colors, only_border
from views.editor import MarkdownEditor
from views.sidebar import Sidebar

_SAMPLE = r"""# Markdown 编辑器

基于 Flet 0.86.1 声明式组件与 mistune 实时渲染，参考 Typora 的段级编辑体验。

## 特性
- 所见即所得

# 测试
### 行内元素
- **加粗**、*斜体*、`行内代码`、~~删除线~~、[链接](https://flet.dev)、$a=b+c$
测试，**加粗**，*斜==体==*，***加粗且斜体***,~~删除文本~~ ==高亮==
测试，**加粗**，*斜体*，***加粗且斜体***
行内代码: `import os`

- ==高亮==
- 上标：x^2^
- 下标：x~3~

### 标题
# 一级标题
## 二级*标题*
### 三级标题
#### 四级标题
##### 五级标题
###### 六级标题

- 段级编辑：点击任意段即显示其最小语法，其余保持渲染样式
- 三级状态：文档 / 行 / 文本段
- 支持 `代码块`、列表、引用、分隔线
- 水平分割线

链接：[百度](http://www.baidu.com)

$$
x = \dfrac{-b \pm \sqrt{b^2 - 4ac}}{2a} 
$$


#### 列表

- 无序**列表1**
- 无序*列表2*
  - 无序列表3
  - 无序列表4
    - 无序列表**5**


1. 第一步
2. 第二步
   1. 子步骤1
   2. 子步骤2
3. 第三步

嵌套列表
> 这是一段引用文字，左侧有边框、文字柔和。
> 引用，块注释
>
> > 双层引用（嵌套引用）



> 引用 **加粗**
> > 双层引用，**加粗**



### 复选框
- [x] 已完成事项
- [ ] 待办事项 1
- [ ] 待办事项 2
- [ ] 复选框1
- [ ] 复选框2
    - [x] 复选框*2-1*

#### 图片

- 本地图
![大图](assets/images/big.png)

![百度](https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png)

### 代码块

```python
import os

def greet(name: str) -> str:
    return f"hello, {name}"
```

### 无语言标记的代码块
```
这是没有语言标记的代码块
可以包含任意内容
```

#### 表格

| 标题             |       标题       |             标题 |
| :--------------- | :--------------: | ---------------: |
| 居左测试文本     |   居中测试文本   |     居右测试文本 |
| 居左测试文本 1   |  居中测试文本 2  |   居右测试文本 3 |
| 居左测试文本 11  | 居中测试文本 22  |  居右测试文本 33 |
| 居左测试文本 111 | 居中测试文本 222 | 居右测试文本 333 |

### 目录
[toc]

### 水平分割线

---

点击任意位置开始编辑。

## 英文
This is a **bold** text and this is *italic*. Here's some `inline code`.

"""


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _file_name(path: str | None) -> str:
    return os.path.basename(path) if path else "未命名.md"


_SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")

_DEFAULT_SETTINGS = {
    "content_max_width": 920,
    "content_padding": 36,
    "content_padding_top": 24,
    "show_footer": True,
    "body_font_size": 16,
    "line_height": 1.6,
    "font_family": "Alibaba",
    "auto_save": False,
    "remember_focus_mode": False,
    "show_toolbar": True,
    "show_line_numbers": False,
    "code_theme_dark": "ATOM_ONE_DARK",
    "code_theme_light": "GITHUB",
    "export_format": "html",
    "sidebar_open": False,
    "sidebar_panel": "files",
    "sidebar_width": 256,
    "recent_files": [],
    "shortcuts": {
        "browse": {
            "save": "ctrl+s",
            "open": "ctrl+o",
            "new": "ctrl+n",
            "undo": "ctrl+z",
            "redo": "ctrl+y",
            "redo_alt": "ctrl+shift+z",
            "toggle_sidebar": "ctrl+b",
            "toggle_theme": "ctrl+shift+l",
            "toggle_raw": "ctrl+/",
            "open_settings": "ctrl+comma",
            "focus_mode": "ctrl+k",
        },
        "edit": {
            "save": "ctrl+s",
            "undo": "ctrl+z",
            "redo": "ctrl+y",
            "redo_alt": "ctrl+shift+z",
            "toggle_raw": "ctrl+enter",
            "toggle_sidebar": "escape",
            "focus_mode": "ctrl+k",
        },
    },
}

_ACTION_REGISTRY = [
    {
        "id": "save",
        "label": "保存",
        "scope": "both",
        "category": "文件",
        "description": "保存当前文档到磁盘。",
        "default": {"browse": "ctrl+s", "edit": "ctrl+s"},
    },
    {
        "id": "open",
        "label": "打开",
        "scope": "browse",
        "category": "文件",
        "description": "打开 Markdown 文件。",
        "default": {"browse": "ctrl+o"},
    },
    {
        "id": "new",
        "label": "新建",
        "scope": "browse",
        "category": "文件",
        "description": "创建空白文档。",
        "default": {"browse": "ctrl+n"},
    },
    {
        "id": "undo",
        "label": "撤销",
        "scope": "both",
        "category": "编辑",
        "description": "回退上一笔编辑。",
        "default": {"browse": "ctrl+z", "edit": "ctrl+z"},
    },
    {
        "id": "redo",
        "label": "重做",
        "scope": "both",
        "category": "编辑",
        "description": "恢复最近撤销的编辑。",
        "default": {"browse": "ctrl+y", "edit": "ctrl+y"},
    },
    {
        "id": "redo_alt",
        "label": "重做（备用）",
        "scope": "both",
        "category": "编辑",
        "description": "兼容 VS Code 风格的重做键位。",
        "default": {"browse": "ctrl+shift+z", "edit": "ctrl+shift+z"},
    },
    {
        "id": "toggle_sidebar",
        "label": "切换侧边栏",
        "scope": "both",
        "category": "视图",
        "description": "显示或隐藏侧边栏。",
        "default": {"browse": "ctrl+b", "edit": "escape"},
    },
    {
        "id": "toggle_theme",
        "label": "切换主题",
        "scope": "browse",
        "category": "视图",
        "description": "在亮色与暗色主题间切换。",
        "default": {"browse": "ctrl+shift+l"},
    },
    {
        "id": "toggle_raw",
        "label": "原文模式",
        "scope": "both",
        "category": "写作",
        "description": "在可视化编辑与原始 Markdown 间切换。",
        "default": {"browse": "ctrl+/", "edit": "ctrl+enter"},
    },
    {
        "id": "open_settings",
        "label": "打开设置",
        "scope": "browse",
        "category": "设置",
        "description": "进入设置中心。",
        "default": {"browse": "ctrl+comma"},
    },
    {
        "id": "focus_mode",
        "label": "聚焦模式",
        "scope": "both",
        "category": "视图",
        "description": "切换窗口全屏聚焦写作。",
        "default": {"browse": "ctrl+k", "edit": "ctrl+k"},
    },
    {
        "id": "format_h1",
        "label": "一级标题",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为一级标题。",
        "default": {},
    },
    {
        "id": "format_h2",
        "label": "二级标题",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为二级标题。",
        "default": {},
    },
    {
        "id": "format_h3",
        "label": "三级标题",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为三级标题。",
        "default": {},
    },
    {
        "id": "format_paragraph",
        "label": "正文段落",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为普通段落。",
        "default": {},
    },
    {
        "id": "format_list",
        "label": "无序列表",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为无序列表。",
        "default": {},
    },
    {
        "id": "format_quote",
        "label": "引用",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为引用块。",
        "default": {},
    },
    {
        "id": "format_code_block",
        "label": "代码块",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为代码块。",
        "default": {},
    },
    {
        "id": "format_hr",
        "label": "分隔线",
        "scope": "edit",
        "category": "格式",
        "description": "将当前行切换为分隔线。",
        "default": {},
    },
    {
        "id": "format_bold",
        "label": "加粗",
        "scope": "edit",
        "category": "行内格式",
        "description": "切换当前段落的加粗。",
        "default": {},
    },
    {
        "id": "format_italic",
        "label": "斜体",
        "scope": "edit",
        "category": "行内格式",
        "description": "切换当前段落的斜体。",
        "default": {},
    },
    {
        "id": "format_code",
        "label": "行内代码",
        "scope": "edit",
        "category": "行内格式",
        "description": "切换当前段落的行内代码。",
        "default": {},
    },
    {
        "id": "format_link",
        "label": "链接",
        "scope": "edit",
        "category": "行内格式",
        "description": "为当前段落插入或移除链接。",
        "default": {},
    },
    {
        "id": "format_strike",
        "label": "删除线",
        "scope": "edit",
        "category": "行内格式",
        "description": "切换当前段落的删除线。",
        "default": {},
    },
]


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged = dict(_DEFAULT_SETTINGS)
            merged.update(data)
            return merged
    except Exception:
        pass
    return dict(_DEFAULT_SETTINGS)


def _save_settings(settings: dict) -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@ft.component
def App():
    document, set_document = ft.use_state(lambda: parser.parse_markdown(_SAMPLE))
    file_path, set_file_path = ft.use_state(None)
    dirty, set_dirty = ft.use_state(False)
    session, set_session = ft.use_state(0)  # 切换文档时自增，强制编辑器重置内部状态
    # 亮/暗主题模式
    theme_mode, set_theme_mode = ft.use_state(ft.ThemeMode.LIGHT)
    settings, set_settings = ft.use_state(_load_settings)
    settings_open, set_settings_open = ft.use_state(False)
    settings_tab, set_settings_tab = ft.use_state("edit")
    shortcut_focus, set_shortcut_focus = ft.use_state((None, None))
    # 导航接口：editor 把光标状态与导航函数写入此 ref，App 的 on_key 据此分发
    nav_ref = ft.use_ref(None)

    # FilePicker / Clipboard：service 实例，通过 ref 在事件回调中访问
    picker_holder = ft.use_ref()
    clipboard_holder = ft.use_ref()
    # page 引用：事件回调中 ft.context.page 可能不可用，提前缓存
    page_ref = ft.use_ref()

    # 同步设置 page.theme_mode：use_effect 在渲染之后执行，本次渲染期间
    # 子组件（MarkdownEditor→LineView 等）调用 _current_colors() 读到的
    # 还是旧 page.theme_mode，导致切换主题后内容颜色不实时刷新。
    # 在渲染期间同步写入，保证子组件取色正确。
    _page_now = ft.context.page
    if _page_now is not None:
        _page_now.theme_mode = theme_mode
        _page_now.bgcolor = get_colors(theme_mode).bg

    def _mount_picker():
        page = ft.context.page
        page_ref.current = page
        # FilePicker / Clipboard 是 service，不需要添加到 page.overlay
        picker_holder.current = ft.FilePicker()
        clipboard_holder.current = ft.Clipboard()

    ft.use_effect(_mount_picker, [])

    def _apply_theme():
        # 推送 page 级属性（theme_mode / bgcolor / 原生 chrome）到 UI
        page = ft.context.page
        page.theme_mode = theme_mode
        page.bgcolor = get_colors(theme_mode).bg
        page.update()
        return

    ft.use_effect(_apply_theme, [theme_mode])

    def toggle_theme():
        set_theme_mode(
            ft.ThemeMode.DARK
            if theme_mode == ft.ThemeMode.LIGHT
            else ft.ThemeMode.LIGHT
        )

    def open_settings():
        set_settings_open(True)

    def close_settings():
        set_settings_open(False)

    def select_settings_tab(tab: str):
        set_settings_tab(tab)

    def get_shortcuts(layer: str) -> dict:
        shortcuts = settings.get("shortcuts", _DEFAULT_SETTINGS["shortcuts"])
        return dict(shortcuts.get(layer, {}))

    def update_shortcut(layer: str, action: str, combo: str):
        shortcuts = dict(settings.get("shortcuts", _DEFAULT_SETTINGS["shortcuts"]))
        layer_map = dict(shortcuts.get(layer, {}))
        layer_map[action] = combo
        shortcuts[layer] = layer_map
        update_setting("shortcuts", shortcuts)

    def _normalize_shortcut(combo: str) -> str:
        combo = (combo or "").strip().lower().replace(" ", "")
        if combo == "ctrl+comma":
            return "ctrl+,"
        return combo

    def _action_layers() -> list[str]:
        return ["browse", "edit"]

    def _action_for(layer: str, action_id: str) -> dict | None:
        for item in _ACTION_REGISTRY:
            if item["id"] == action_id:
                return item
        return None

    def _action_shortcut(layer: str, action_id: str) -> str:
        return get_shortcuts(layer).get(action_id, "")

    def _shortcut_conflicts(layer: str) -> list[tuple[str, str, str]]:
        items = list(get_shortcuts(layer).items())
        seen: dict[str, str] = {}
        conflicts: list[tuple[str, str, str]] = []
        for action, combo in items:
            norm = _normalize_shortcut(combo)
            if not norm:
                continue
            if norm in seen:
                conflicts.append((norm, seen[norm], action))
            else:
                seen[norm] = action
        return conflicts

    def _conflict_map(layer: str) -> dict[str, list[str]]:
        cmap: dict[str, list[str]] = {}
        for combo, a, b in _shortcut_conflicts(layer):
            cmap.setdefault(combo, []).extend([a, b])
        return cmap

    def _conflict_summary(layer: str) -> str | None:
        conflicts = _shortcut_conflicts(layer)
        if not conflicts:
            return None
        parts = [f"{combo}({a}/{b})" for combo, a, b in conflicts[:3]]
        extra = f" 等{len(conflicts) - 3}项" if len(conflicts) > 3 else ""
        return f"检测到冲突：{'、'.join(parts)}{extra}"

    def _focus_first_conflict():
        layer, action = _first_conflict_target()
        if layer and action:
            set_shortcut_focus((layer, action))
            select_settings_tab("shortcuts")
            open_settings()

    def _first_conflict_target() -> tuple[str | None, str | None]:
        for layer in _action_layers():
            cmap = _conflict_map(layer)
            if cmap:
                combo = next(iter(cmap))
                return layer, cmap[combo][0]
        return None, None

    def _shortcut_is_conflict(layer: str, action: str) -> bool:
        conflict_layer, conflict_action = shortcut_focus
        return conflict_layer == layer and conflict_action == action

    def _set_scope_shortcut(layer: str, action: str, value: str):
        update_shortcut(layer, action, value)

    def _reset_action(layer: str, action_id: str):
        next_shortcuts = dict(settings.get("shortcuts", _DEFAULT_SETTINGS["shortcuts"]))
        layer_map = dict(next_shortcuts.get(layer, {}))
        default = _action_for(layer, action_id)
        if default is None:
            return
        layer_map[action_id] = default.get("default", {}).get(layer, "")
        next_shortcuts[layer] = layer_map
        update_setting("shortcuts", next_shortcuts)

    def _action_rows():
        rows = []
        for layer in _action_layers():
            layer_actions = [
                a for a in _ACTION_REGISTRY if a["scope"] in ("both", layer)
            ]
            cmap = _conflict_map(layer)
            rows.append(
                ft.Container(
                    padding=ft.Padding.only(top=4, bottom=4),
                    content=ft.Text(
                        "浏览态" if layer == "browse" else "编辑态",
                        size=13,
                        weight=ft.FontWeight.W_700,
                    ),
                )
            )
            for action in layer_actions:
                current = _action_shortcut(layer, action["id"])
                default = action.get("default", {}).get(layer, "")
                is_conflict = bool(current and current in cmap)
                rows.append(
                    ft.Container(
                        bgcolor=ft.Colors.with_opacity(0.10, ft.Colors.RED)
                        if is_conflict
                        else None,
                        border_radius=10,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        content=ft.Column(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Column(
                                            controls=[
                                                ft.Text(
                                                    action["label"],
                                                    size=13,
                                                    weight=ft.FontWeight.W_600,
                                                ),
                                                ft.Text(
                                                    f"{action['category']} · {action['description']}",
                                                    size=11,
                                                    color=get_colors(theme_mode).muted,
                                                ),
                                            ],
                                            spacing=2,
                                            expand=True,
                                        ),
                                        ft.TextField(
                                            value=current,
                                            hint_text=default or "未绑定",
                                            dense=True,
                                            border=ft.InputBorder.UNDERLINE,
                                            text_size=12,
                                            width=160,
                                            border_color="#E66A00"
                                            if is_conflict
                                            else None,
                                            focused_border_color="#E66A00"
                                            if is_conflict
                                            else get_colors(theme_mode).link,
                                            on_submit=lambda e, l=layer, a=action["id"]: (
                                                _set_scope_shortcut(
                                                    l,
                                                    a,
                                                    (e.control.value or "").lower(),
                                                )
                                            ),
                                        ),
                                        ft.TextButton(
                                            "恢复默认",
                                            on_click=lambda e, l=layer, a=action["id"]: (
                                                _reset_action(l, a)
                                            ),
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ],
                            spacing=6,
                        ),
                    )
                )
        return rows

    def update_setting(key: str, value):
        next_settings = dict(settings)
        next_settings[key] = value
        set_settings(next_settings)
        _save_settings(next_settings)
        _apply_content_layout()
        if key == "shortcuts":
            layer, action = _first_conflict_target()
            set_shortcut_focus((layer, action))

    def _autosave_enabled() -> bool:
        return bool(settings.get("auto_save", False)) and bool(file_path)

    def _schedule_autosave():
        if not dirty or not _autosave_enabled():
            return
        page = page_ref.current
        if page is None:
            return

        async def _debounced_save():
            await asyncio.sleep(2.0)
            if dirty and _autosave_enabled():
                await save_doc()

        page.run_task(_debounced_save)

    def reset_settings():
        next_settings = dict(_DEFAULT_SETTINGS)
        set_settings(next_settings)
        _save_settings(next_settings)

    def reset_shortcuts():
        next_settings = dict(settings)
        next_settings["shortcuts"] = dict(_DEFAULT_SETTINGS["shortcuts"])
        set_settings(next_settings)
        _save_settings(next_settings)
        set_shortcut_focus((None, None))
        select_settings_tab("advanced")
        open_settings()

    async def export_shortcuts():
        picker = picker_holder.current
        if picker is None:
            return
        path = await picker.save_file(
            dialog_title="导出快捷键方案",
            file_name="shortcuts.json",
            allowed_extensions=["json"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            payload = json.dumps(
                settings.get("shortcuts", _DEFAULT_SETTINGS["shortcuts"]),
                ensure_ascii=False,
                indent=2,
            )
            _write_file(path, payload)
        except Exception as e:
            if page_ref.current is not None:
                page_ref.current.open(ft.SnackBar(ft.Text(f"导出失败：{e}")))
            return
        if page_ref.current is not None:
            page_ref.current.open(ft.SnackBar(ft.Text("快捷键方案已导出")))

    async def import_shortcuts():
        picker = picker_holder.current
        if picker is None:
            return
        files = await picker.pick_files(
            dialog_title="导入快捷键方案",
            allowed_extensions=["json"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not files:
            return
        try:
            payload = _read_file(files[0].path)
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("JSON 格式不正确")
            next_settings = dict(settings)
            next_settings["shortcuts"] = data
            set_settings(next_settings)
            _save_settings(next_settings)
            set_shortcut_focus((None, None))
        except Exception as e:
            if page_ref.current is not None:
                page_ref.current.open(ft.SnackBar(ft.Text(f"导入失败：{e}")))
            return
        if page_ref.current is not None:
            page_ref.current.open(ft.SnackBar(ft.Text("快捷键方案已导入")))

    def _push_recent_file(path: str):
        """把 path 加入最近文件列表头部（去重、截断 10 条）并持久化。"""
        if not path:
            return
        recent = list(settings.get("recent_files", []))
        if path in recent:
            recent.remove(path)
        recent.insert(0, path)
        recent = recent[:10]
        update_setting("recent_files", recent)

    def _open_file_by_path(path: str):
        """从绝对路径打开文件（供侧边栏文件树点击与 open_doc 复用）。"""
        try:
            text = _read_file(path)
        except Exception as e:
            if page_ref.current is not None:
                page_ref.current.open(ft.SnackBar(ft.Text(f"打开失败：{e}")))
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        set_document(doc)
        set_file_path(path)
        set_dirty(False)
        set_session(session + 1)
        _push_recent_file(path)

    def toggle_sidebar():
        update_setting("sidebar_open", not settings.get("sidebar_open", False))

    def _apply_content_layout():
        page = page_ref.current
        if page is None:
            return
        page.update()

    def change_sidebar_panel(panel: str):
        update_setting("sidebar_panel", panel)

    def change_sidebar_width(width: int):
        update_setting("sidebar_width", width)

    def jump_to_line(li: int):
        nav = nav_ref.current
        if nav and nav.get("jump_to_line"):
            nav["jump_to_line"](li)

    def on_dirty_change(d: bool):
        set_dirty(d)
        if d:
            _schedule_autosave()

    def new_doc():
        doc = parser.parse_markdown("")
        doc.file_path = None
        set_document(doc)
        set_file_path(None)
        set_dirty(False)
        set_session(session + 1)

    async def open_doc():
        picker = picker_holder.current
        if picker is None:
            return
        files = await picker.pick_files(
            dialog_title="打开 Markdown",
            allowed_extensions=["md", "markdown", "txt"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not files:
            return
        _open_file_by_path(files[0].path)

    async def save_doc():
        text = parser.serialize(document)
        path = file_path
        if not path:
            picker = picker_holder.current
            if picker is None:
                return
            path = await picker.save_file(
                dialog_title="保存 Markdown",
                file_name="未命名.md",
                allowed_extensions=["md"],
                file_type=ft.FilePickerFileType.CUSTOM,
            )
            if not path:
                return
            if not path.lower().endswith(".md"):
                path += ".md"
        try:
            _write_file(path, text)
        except Exception as e:
            page_ref.current.open(ft.SnackBar(ft.Text(f"保存失败：{e}")))
            return
        document.file_path = path
        document.dirty = False
        set_file_path(path)
        set_dirty(False)
        _push_recent_file(path)

    async def export_doc():
        """导出为 HTML 文件。"""
        md_text = parser.serialize(document)
        html = parser.to_html(md_text)
        picker = picker_holder.current
        if picker is None:
            return
        path = await picker.save_file(
            dialog_title="导出 HTML",
            file_name=_file_name(file_path).replace(".md", ".html"),
            allowed_extensions=["html"],
            file_type=ft.FilePickerFileType.CUSTOM,
        )
        if not path:
            return
        if not path.lower().endswith(".html"):
            path += ".html"
        try:
            _write_file(path, html)
        except Exception as e:
            page_ref.current.open(ft.SnackBar(ft.Text(f"导出失败：{e}")))
            return
        page_ref.current.open(ft.SnackBar(ft.Text("导出成功")))

    # ---- 快捷键 + 光标导航 ----
    # page.on_keyboard_event 的 KeyboardEvent 直接提供 ctrl/meta 修饰键状态
    # 粘贴前的 draft 快照（供 handle_paste 做 diff 定位粘贴位置）
    paste_old_draft = ft.use_ref("")
    # on_key 闭包引用随渲染变化的状态，用 ref 保持最新版本供事件回调调用
    on_key_ref = ft.use_ref(None)

    async def _do_copy():
        """Ctrl+C：用 SelectionArea 选区文本计算 Markdown 覆盖剪贴板。

        用 selection_text_ref（on_change 上报的选区纯文本）而非 clipboard.get()
        读取：原生 SelectionArea 复制到剪贴板的时序不可靠（sleep 0.2s 仍可能读到
        空或旧值），且 selection_text_ref 与 BackSpace 删除选区共用同一数据源，
        行为一致更可靠。
        """
        await asyncio.sleep(0.05)
        nav = nav_ref.current
        if nav is None:
            return
        sel_ref = nav.get("selection_text_ref")
        plain = (sel_ref.current if sel_ref is not None else "") or ""
        if not plain or not nav.get("compute_markdown_from_text"):
            return
        try:
            md = nav["compute_markdown_from_text"](plain)
            if md and md != plain:
                clipboard = clipboard_holder.current
                if clipboard is not None:
                    await clipboard.set(md)
        except Exception:
            return

    async def _do_cut():
        """Ctrl+X：用 SelectionArea 选区文本计算 Markdown 覆盖剪贴板，并删除选中内容。"""
        await asyncio.sleep(0.05)
        nav = nav_ref.current
        if nav is None:
            return
        sel_ref = nav.get("selection_text_ref")
        plain = (sel_ref.current if sel_ref is not None else "") or ""
        if not plain or not nav.get("handle_cut"):
            return
        try:
            await nav["handle_cut"](plain)
        except Exception:
            return

    async def _do_paste_check():
        """Ctrl+V 后异步检查剪贴板是否含多行内容，若是则拆分为多行插入。"""
        await asyncio.sleep(0.05)
        clipboard = clipboard_holder.current
        if clipboard is None:
            return
        try:
            text = await clipboard.get()
        except Exception:
            return
        if not text or "\n" not in text:
            return
        nav = nav_ref.current
        if nav is None or not nav.get("handle_paste"):
            return
        try:
            nav["handle_paste"](text, paste_old_draft.current)
        except Exception:
            return

    def _combo(e) -> str:
        parts = []
        if getattr(e, "ctrl", False) or getattr(e, "meta", False):
            parts.append("ctrl")
        if getattr(e, "shift", False):
            parts.append("shift")
        if getattr(e, "alt", False):
            parts.append("alt")
        key = (e.key or "").replace(" ", "").lower()
        if key in ("control", "meta", "shift", "alt"):
            return ""
        mapping = {
            "arrowleft": "left",
            "arrowright": "right",
            "arrowup": "up",
            "arrowdown": "down",
            " ": "space",
            "comma": ",",
            "escape": "esc",
            "enter": "enter",
        }
        key = mapping.get(key, key)
        return "+".join(parts + [key])

    def _matches(combo: str, target: str) -> bool:
        return combo == target or (
            target == "ctrl+," and combo in {"ctrl+comma", "ctrl+,"}
        )

    def on_key(e):
        combo = _combo(e)
        key = e.key or ""
        norm = key.replace(" ", "").lower()
        nav = nav_ref.current
        layer = "edit" if nav and nav.get("active") is not None else "browse"
        shortcuts = get_shortcuts(layer)

        if layer == "edit":
            if norm == "home":
                nav["move_line_start"]() if e.ctrl else nav["move_home"]()
                return
            if norm == "end":
                nav["move_line_end"]() if e.ctrl else nav["move_end"]()
                return
            if norm == "arrowup":
                nav["move_up"]()
                return
            if norm == "arrowdown":
                nav["move_down"]()
                return
            if norm == "backspace":
                nav["backspace_core"]()
                return
            if norm == "delete":
                nav["delete_core"]()
                return
            if norm == "tab":
                if (
                    nav.get("active_line") is not None
                    and getattr(nav["active_line"], "block_type", None)
                    == BlockType.CODE
                ):
                    return
                if e.shift:
                    if nav.get("indent_or_outdent"):
                        nav["indent_or_outdent"](-1)
                    else:
                        nav["move_left"]()
                else:
                    if nav.get("indent_or_outdent"):
                        nav["indent_or_outdent"](1)
                    else:
                        nav["move_right"]()
                return
            if norm == "arrowleft" and nav["extent"] == 0 and nav["base"] == 0:
                nav["move_left"]()
                return
            if (
                norm == "arrowright"
                and nav["extent"] == nav["draft_len"]
                and nav["base"] == nav["draft_len"]
            ):
                nav["move_right"]()
                return

        if (
            norm == "backspace"
            and nav
            and nav.get("active") is None
            and not nav.get("raw_mode")
        ):
            sel_ref = nav.get("selection_text_ref")
            plain = (sel_ref.current if sel_ref is not None else "") or ""
            if plain and nav.get("handle_delete_selection"):
                nav["handle_delete_selection"](plain)
                return

        page = page_ref.current
        if page is None:
            return
        if layer == "browse":
            if _matches(combo, shortcuts.get("save", "ctrl+s")):
                page.run_task(save_doc)
            elif _matches(combo, shortcuts.get("new", "ctrl+n")):
                new_doc()
            elif _matches(combo, shortcuts.get("open", "ctrl+o")):
                page.run_task(open_doc)
            elif _matches(combo, shortcuts.get("toggle_sidebar", "ctrl+b")):
                toggle_sidebar()
            elif _matches(combo, shortcuts.get("toggle_theme", "ctrl+shift+l")):
                toggle_theme()
            elif _matches(combo, shortcuts.get("toggle_raw", "ctrl+/")):
                nav = nav_ref.current
                if nav and nav.get("toggle_raw"):
                    nav["toggle_raw"]()
            elif _matches(combo, shortcuts.get("open_settings", "ctrl+comma")):
                open_settings()
            elif _matches(combo, shortcuts.get("focus_mode", "ctrl+k")):
                nav = nav_ref.current
                if nav and nav.get("toggle_focus_mode"):
                    nav["toggle_focus_mode"]()
            elif _matches(combo, shortcuts.get("redo", "ctrl+y")) or _matches(
                combo, shortcuts.get("redo_alt", "ctrl+shift+z")
            ):
                nav = nav_ref.current
                if nav and nav.get("redo"):
                    nav["redo"]()
            elif _matches(combo, shortcuts.get("undo", "ctrl+z")):
                nav = nav_ref.current
                if nav and nav.get("undo"):
                    nav["undo"]()
            elif combo == "ctrl+c":
                nav = nav_ref.current
                if nav and nav.get("active") is None:
                    page.run_task(_do_copy)
            elif combo == "ctrl+x":
                nav = nav_ref.current
                if nav and nav.get("active") is None:
                    page.run_task(_do_cut)
            elif combo == "ctrl+v":
                nav = nav_ref.current
                if nav and nav.get("active") is not None:
                    paste_old_draft.current = nav.get("draft", "")
                    page.run_task(_do_paste_check)
            return
        if _matches(combo, shortcuts.get("save", "ctrl+s")):
            page.run_task(save_doc)
        elif _matches(combo, shortcuts.get("undo", "ctrl+z")):
            nav = nav_ref.current
            if nav and nav.get("undo"):
                nav["undo"]()
        elif _matches(combo, shortcuts.get("redo", "ctrl+y")) or _matches(
            combo, shortcuts.get("redo_alt", "ctrl+shift+z")
        ):
            nav = nav_ref.current
            if nav and nav.get("redo"):
                nav["redo"]()
        elif _matches(combo, shortcuts.get("toggle_raw", "ctrl+enter")):
            nav = nav_ref.current
            if nav and nav.get("toggle_raw"):
                nav["toggle_raw"]()
        elif _matches(combo, shortcuts.get("toggle_sidebar", "escape")):
            toggle_sidebar()
        elif combo == "ctrl+c":
            nav = nav_ref.current
            if nav and nav.get("active") is None:
                page.run_task(_do_copy)
        elif combo == "ctrl+x":
            nav = nav_ref.current
            if nav and nav.get("active") is None:
                page.run_task(_do_cut)
        elif combo == "ctrl+v":
            nav = nav_ref.current
            if nav and nav.get("active") is not None:
                paste_old_draft.current = nav.get("draft", "")
                page.run_task(_do_paste_check)

    # 每次渲染更新 on_key_ref，使 page.on_keyboard_event 总能调用最新闭包
    on_key_ref.current = on_key

    def _bind_keyboard():
        page = ft.context.page
        page_ref.current = page
        if page is None:
            return lambda: None

        def _handler(e):
            if on_key_ref.current is not None:
                try:
                    on_key_ref.current(e)
                except Exception:
                    return

        page.on_keyboard_event = _handler

        def _cleanup():
            if page_ref.current is not None:
                try:
                    page_ref.current.on_keyboard_event = None
                except Exception:
                    pass

        return _cleanup

    ft.use_effect(_bind_keyboard, [])

    sections = {
        "edit": ("编辑", "调整编辑区布局与写作行为。"),
        "appearance": ("外观", "控制主题、字体与视觉密度。"),
        "behavior": ("行为", "控制保存、专注与工具栏行为。"),
        "shortcuts": ("快捷键", "查看常用快捷键说明。"),
        "advanced": ("高级", "预留代码主题、导出等高级选项。"),
    }
    current_title, current_desc = sections.get(settings_tab, sections["edit"])
    settings_view = ft.Container(
        visible=settings_open,
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.28, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=1020,
            height=720,
            bgcolor=get_colors(theme_mode).toolbar_bg,
            border_radius=18,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=24,
                color=ft.Colors.with_opacity(0.18, ft.Colors.BLACK),
                offset=ft.Offset(0, 8),
            ),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(
                spacing=0,
                controls=[
                    ft.Container(
                        width=250,
                        bgcolor=ft.Colors.with_opacity(
                            0.18, get_colors(theme_mode).border
                        ),
                        padding=20,
                        content=ft.Column(
                            expand=True,
                            controls=[
                                ft.Text("设置", size=22, weight=ft.FontWeight.W_700),
                                ft.Text(
                                    "Typora 风格的可配置中心",
                                    size=12,
                                    color=get_colors(theme_mode).muted,
                                ),
                                ft.Container(height=18),
                                *[
                                    ft.Container(
                                        border_radius=10,
                                        bgcolor=ft.Colors.with_opacity(
                                            0.12, get_colors(theme_mode).link
                                        )
                                        if settings_tab == tab
                                        else None,
                                        padding=ft.Padding.symmetric(
                                            horizontal=12, vertical=10
                                        ),
                                        content=ft.Row(
                                            controls=[
                                                ft.Icon(
                                                    icon=icon,
                                                    size=16,
                                                    color=get_colors(theme_mode).link
                                                    if settings_tab == tab
                                                    else get_colors(theme_mode).muted,
                                                ),
                                                ft.TextButton(
                                                    label,
                                                    on_click=lambda e, t=tab: (
                                                        select_settings_tab(t)
                                                    ),
                                                ),
                                            ],
                                            spacing=8,
                                        ),
                                    )
                                    for tab, label, icon in [
                                        ("edit", "编辑", ft.Icons.EDIT),
                                        ("appearance", "外观", ft.Icons.PALETTE),
                                        ("behavior", "行为", ft.Icons.TUNE),
                                        ("shortcuts", "快捷键", ft.Icons.KEYBOARD),
                                        ("advanced", "高级", ft.Icons.SETTINGS),
                                    ]
                                ],
                                ft.Container(expand=True),
                                ft.TextButton(
                                    "恢复默认", on_click=lambda e: reset_settings()
                                ),
                            ],
                            spacing=8,
                        ),
                    ),
                    ft.Container(width=1, bgcolor=get_colors(theme_mode).border),
                    ft.Container(
                        expand=True,
                        padding=24,
                        content=ft.Column(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Column(
                                            controls=[
                                                ft.Text(
                                                    current_title,
                                                    size=20,
                                                    weight=ft.FontWeight.W_700,
                                                ),
                                                ft.Text(
                                                    current_desc,
                                                    size=12,
                                                    color=get_colors(theme_mode).muted,
                                                ),
                                            ],
                                            spacing=2,
                                        ),
                                        ft.Container(expand=True),
                                        ft.IconButton(
                                            icon=ft.Icons.CLOSE,
                                            on_click=lambda e: close_settings(),
                                        ),
                                    ]
                                ),
                                ft.Container(height=8),
                                ft.Container(
                                    visible=settings_tab == "edit",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text(
                                                "布局",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("内容宽度", width=96),
                                                    ft.Slider(
                                                        min=680,
                                                        max=1200,
                                                        divisions=13,
                                                        value=settings[
                                                            "content_max_width"
                                                        ],
                                                        expand=True,
                                                        on_change=lambda e: (
                                                            update_setting(
                                                                "content_max_width",
                                                                int(e.control.value),
                                                            )
                                                        ),
                                                    ),
                                                    ft.Text(
                                                        str(
                                                            settings[
                                                                "content_max_width"
                                                            ]
                                                        )
                                                    ),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("左右边距", width=96),
                                                    ft.Slider(
                                                        min=12,
                                                        max=64,
                                                        divisions=13,
                                                        value=settings[
                                                            "content_padding"
                                                        ],
                                                        expand=True,
                                                        on_change=lambda e: (
                                                            update_setting(
                                                                "content_padding",
                                                                int(e.control.value),
                                                            )
                                                        ),
                                                    ),
                                                    ft.Text(
                                                        str(settings["content_padding"])
                                                    ),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("顶部边距", width=96),
                                                    ft.Slider(
                                                        min=8,
                                                        max=48,
                                                        divisions=10,
                                                        value=settings[
                                                            "content_padding_top"
                                                        ],
                                                        expand=True,
                                                        on_change=lambda e: (
                                                            update_setting(
                                                                "content_padding_top",
                                                                int(e.control.value),
                                                            )
                                                        ),
                                                    ),
                                                    ft.Text(
                                                        str(
                                                            settings[
                                                                "content_padding_top"
                                                            ]
                                                        )
                                                    ),
                                                ]
                                            ),
                                            ft.Switch(
                                                label="显示底部状态栏",
                                                value=settings["show_footer"],
                                                on_change=lambda e: update_setting(
                                                    "show_footer", e.control.value
                                                ),
                                            ),
                                        ],
                                        spacing=12,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "appearance",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text(
                                                "字体与排版",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("正文大小", width=96),
                                                    ft.Slider(
                                                        min=14,
                                                        max=20,
                                                        divisions=6,
                                                        value=settings[
                                                            "body_font_size"
                                                        ],
                                                        expand=True,
                                                        on_change=lambda e: (
                                                            update_setting(
                                                                "body_font_size",
                                                                int(e.control.value),
                                                            )
                                                        ),
                                                    ),
                                                    ft.Text(
                                                        str(settings["body_font_size"])
                                                    ),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("行高", width=96),
                                                    ft.Slider(
                                                        min=1.2,
                                                        max=2.0,
                                                        divisions=8,
                                                        value=settings["line_height"],
                                                        expand=True,
                                                        on_change=lambda e: (
                                                            update_setting(
                                                                "line_height",
                                                                round(
                                                                    float(
                                                                        e.control.value
                                                                    ),
                                                                    1,
                                                                ),
                                                            )
                                                        ),
                                                    ),
                                                    ft.Text(
                                                        str(settings["line_height"])
                                                    ),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("字体", width=96),
                                                    ft.Dropdown(
                                                        options=[
                                                            ft.dropdown.Option(
                                                                "Alibaba"
                                                            ),
                                                            ft.dropdown.Option("Sans"),
                                                            ft.dropdown.Option("Serif"),
                                                            ft.dropdown.Option(
                                                                "Monospace"
                                                            ),
                                                        ],
                                                        value=settings["font_family"],
                                                        expand=True,
                                                        on_select=lambda e: (
                                                            update_setting(
                                                                "font_family",
                                                                e.control.value,
                                                            )
                                                        ),
                                                    ),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("代码主题(暗)", width=96),
                                                    ft.Dropdown(
                                                        options=[
                                                            ft.dropdown.Option(
                                                                "ATOM_ONE_DARK"
                                                            ),
                                                            ft.dropdown.Option(
                                                                "GITHUB"
                                                            ),
                                                            ft.dropdown.Option(
                                                                "VS2015"
                                                            ),
                                                        ],
                                                        value=settings[
                                                            "code_theme_dark"
                                                        ],
                                                        expand=True,
                                                        on_select=lambda e: (
                                                            update_setting(
                                                                "code_theme_dark",
                                                                e.control.value,
                                                            )
                                                        ),
                                                    ),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("代码主题(亮)", width=96),
                                                    ft.Dropdown(
                                                        options=[
                                                            ft.dropdown.Option(
                                                                "GITHUB"
                                                            ),
                                                            ft.dropdown.Option(
                                                                "ATOM_ONE_LIGHT"
                                                            ),
                                                            ft.dropdown.Option(
                                                                "VS2015"
                                                            ),
                                                        ],
                                                        value=settings[
                                                            "code_theme_light"
                                                        ],
                                                        expand=True,
                                                        on_select=lambda e: (
                                                            update_setting(
                                                                "code_theme_light",
                                                                e.control.value,
                                                            )
                                                        ),
                                                    ),
                                                ]
                                            ),
                                        ],
                                        spacing=12,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "behavior",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text(
                                                "行为",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                            ),
                                            ft.Switch(
                                                label="自动保存",
                                                value=settings["auto_save"],
                                                on_change=lambda e: update_setting(
                                                    "auto_save", e.control.value
                                                ),
                                            ),
                                            ft.Switch(
                                                label="记住聚焦模式",
                                                value=settings["remember_focus_mode"],
                                                on_change=lambda e: update_setting(
                                                    "remember_focus_mode",
                                                    e.control.value,
                                                ),
                                            ),
                                            ft.Switch(
                                                label="显示工具栏",
                                                value=settings["show_toolbar"],
                                                on_change=lambda e: update_setting(
                                                    "show_toolbar", e.control.value
                                                ),
                                            ),
                                            ft.Switch(
                                                label="显示行号",
                                                value=settings["show_line_numbers"],
                                                on_change=lambda e: update_setting(
                                                    "show_line_numbers", e.control.value
                                                ),
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text(
                                                        "自动保存间隔(秒)", width=140
                                                    ),
                                                    ft.Slider(
                                                        min=3,
                                                        max=60,
                                                        divisions=19,
                                                        value=10,
                                                        expand=True,
                                                        on_change=lambda e: None,
                                                    ),
                                                    ft.Text("10"),
                                                ]
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Text("导出默认格式", width=140),
                                                    ft.Dropdown(
                                                        options=[
                                                            ft.dropdown.Option("html"),
                                                            ft.dropdown.Option("pdf"),
                                                            ft.dropdown.Option("md"),
                                                        ],
                                                        value=settings["export_format"],
                                                        expand=True,
                                                        on_select=lambda e: (
                                                            update_setting(
                                                                "export_format",
                                                                e.control.value,
                                                            )
                                                        ),
                                                    ),
                                                ]
                                            ),
                                        ],
                                        spacing=12,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "shortcuts",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text(
                                                "常用快捷键",
                                                size=14,
                                                weight=ft.FontWeight.W_600,
                                            ),
                                            ft.Text("Ctrl+S 保存", size=12),
                                            ft.Text("Ctrl+O 打开", size=12),
                                            ft.Text("Ctrl+N 新建", size=12),
                                            ft.Text("Ctrl+Z 撤销", size=12),
                                            ft.Text(
                                                "Ctrl+Y / Ctrl+Shift+Z 重做", size=12
                                            ),
                                            ft.Text(
                                                "编辑态：Ctrl+Enter 原文模式 / Esc 侧边栏",
                                                size=12,
                                            ),
                                            ft.Text(
                                                "浏览态：Ctrl+/ 原文模式 / Ctrl+, 设置",
                                                size=12,
                                            ),
                                            ft.Text("Ctrl+B 切换侧边栏", size=12),
                                            ft.Text("Ctrl+Shift+L 切换主题", size=12),
                                            ft.Text("Ctrl+K 聚焦模式", size=12),
                                            ft.Text(
                                                "Tab / Shift+Tab 列表缩进", size=12
                                            ),
                                            ft.Text("Home / End 段首段尾", size=12),
                                            ft.Text("Ctrl+1/2/3 标题", size=12),
                                        ],
                                        spacing=8,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "advanced",
                                    content=ft.Column(
                                        controls=[
                                            ft.Row(
                                                [
                                                    ft.Column(
                                                        controls=[
                                                            ft.Text(
                                                                "动作管理",
                                                                size=14,
                                                                weight=ft.FontWeight.W_600,
                                                            ),
                                                            ft.Text(
                                                                "统一查看并管理浏览态 / 编辑态动作、默认键位与冲突状态。",
                                                                size=12,
                                                                color=get_colors(
                                                                    theme_mode
                                                                ).muted,
                                                            ),
                                                        ],
                                                        spacing=2,
                                                        expand=True,
                                                    ),
                                                    ft.Container(expand=True),
                                                    ft.TextButton(
                                                        "导入方案",
                                                        on_click=lambda e: (
                                                            page_ref.current.run_task(
                                                                import_shortcuts
                                                            )
                                                        ),
                                                    ),
                                                    ft.TextButton(
                                                        "导出方案",
                                                        on_click=lambda e: (
                                                            page_ref.current.run_task(
                                                                export_shortcuts
                                                            )
                                                        ),
                                                    ),
                                                    ft.TextButton(
                                                        "恢复默认快捷键",
                                                        on_click=lambda e: (
                                                            reset_shortcuts()
                                                        ),
                                                    ),
                                                ]
                                            ),
                                            ft.Container(height=6),
                                            ft.Row(
                                                controls=[
                                                    ft.Container(
                                                        expand=True,
                                                        content=ft.TextField(
                                                            hint_text="搜索动作、说明、快捷键…",
                                                            dense=True,
                                                            border=ft.InputBorder.OUTLINE,
                                                            prefix_icon=ft.Icons.SEARCH,
                                                            on_change=lambda e: None,
                                                        ),
                                                    ),
                                                    ft.TextButton(
                                                        "定位第一个冲突",
                                                        on_click=lambda e: (
                                                            _focus_first_conflict()
                                                        ),
                                                    ),
                                                ],
                                                spacing=10,
                                            ),
                                            ft.Row(
                                                [
                                                    ft.Container(
                                                        expand=True,
                                                        padding=ft.Padding.symmetric(
                                                            horizontal=10, vertical=8
                                                        ),
                                                        border_radius=10,
                                                        bgcolor=ft.Colors.with_opacity(
                                                            0.08,
                                                            get_colors(theme_mode).link,
                                                        ),
                                                        content=ft.Column(
                                                            controls=[
                                                                ft.Text(
                                                                    "浏览态",
                                                                    size=12,
                                                                    weight=ft.FontWeight.W_700,
                                                                ),
                                                                ft.Text(
                                                                    _conflict_summary(
                                                                        "browse"
                                                                    )
                                                                    or "无冲突",
                                                                    size=11,
                                                                    color="#E66A00"
                                                                    if _conflict_summary(
                                                                        "browse"
                                                                    )
                                                                    else get_colors(
                                                                        theme_mode
                                                                    ).muted,
                                                                ),
                                                            ],
                                                            spacing=2,
                                                        ),
                                                    ),
                                                    ft.Container(
                                                        expand=True,
                                                        padding=ft.Padding.symmetric(
                                                            horizontal=10, vertical=8
                                                        ),
                                                        border_radius=10,
                                                        bgcolor=ft.Colors.with_opacity(
                                                            0.08,
                                                            get_colors(theme_mode).link,
                                                        ),
                                                        content=ft.Column(
                                                            controls=[
                                                                ft.Text(
                                                                    "编辑态",
                                                                    size=12,
                                                                    weight=ft.FontWeight.W_700,
                                                                ),
                                                                ft.Text(
                                                                    _conflict_summary(
                                                                        "edit"
                                                                    )
                                                                    or "无冲突",
                                                                    size=11,
                                                                    color="#E66A00"
                                                                    if _conflict_summary(
                                                                        "edit"
                                                                    )
                                                                    else get_colors(
                                                                        theme_mode
                                                                    ).muted,
                                                                ),
                                                            ],
                                                            spacing=2,
                                                        ),
                                                    ),
                                                ],
                                                spacing=10,
                                            ),
                                            ft.Container(height=4),
                                            ft.Container(
                                                expand=True,
                                                border_radius=12,
                                                bgcolor=ft.Colors.with_opacity(
                                                    0.04, get_colors(theme_mode).text
                                                ),
                                                padding=ft.Padding.all(12),
                                                content=ft.Column(
                                                    controls=_action_rows(),
                                                    spacing=8,
                                                    scroll=ft.ScrollMode.AUTO,
                                                ),
                                            ),
                                        ],
                                        spacing=10,
                                    ),
                                ),
                            ],
                            scroll=ft.ScrollMode.AUTO,
                        ),
                    ),
                ],
            ),
        ),
    )

    sidebar_open = settings.get("sidebar_open", False)
    body = ft.Row(
        controls=[
            Sidebar(
                document=document,
                file_path=file_path,
                theme_mode=theme_mode,
                settings=settings,
                active_panel=settings.get("sidebar_panel", "files"),
                on_change_panel=change_sidebar_panel,
                on_open_file=_open_file_by_path,
                on_jump_to_line=jump_to_line,
                on_width_change=change_sidebar_width,
            )
            if sidebar_open
            else ft.Container(width=0),
            MarkdownEditor(
                key=str(session),
                document=document,
                file_path=file_path,
                on_new=new_doc,
                on_open=lambda: page_ref.current.run_task(open_doc),
                on_save=lambda: page_ref.current.run_task(save_doc),
                on_export=lambda: page_ref.current.run_task(export_doc),
                on_dirty_change=on_dirty_change,
                nav_ref=nav_ref,
                clipboard_ref=clipboard_holder,
                theme_mode=theme_mode,
                on_toggle_theme=toggle_theme,
                settings=settings,
                on_open_settings=open_settings,
                sidebar_open=sidebar_open,
                on_toggle_sidebar=toggle_sidebar,
            ),
        ],
        spacing=0,
        expand=True,
    )

    # 底部状态栏：贯穿侧边栏 + 编辑区全宽，放在 body 之下
    def _build_footer():
        if not settings.get("show_footer", True):
            return ft.Container(height=0)
        c = get_colors(theme_mode)
        nav = nav_ref.current
        if nav and nav.get("get_cursor_row_col"):
            row, col = nav["get_cursor_row_col"]()
        else:
            row, col = 1, 1
        char_count = len(parser.serialize(document))
        word_count = len(
            re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", parser.serialize(document))
        )
        fname = os.path.basename(file_path) if file_path else "未命名.md"
        return ft.Container(
            bgcolor=ft.Colors.with_opacity(0.03, c.text),
            border=only_border(top=ft.BorderSide(1, c.border)),
            padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            content=ft.Row(
                controls=[
                    ft.IconButton(
                        icon=ft.Icons.VIEW_SIDEBAR
                        if not sidebar_open
                        else ft.Icons.MENU_OPEN,
                        tooltip="切换侧边栏",
                        on_click=lambda e: toggle_sidebar(),
                        icon_size=16,
                        style=ft.ButtonStyle(
                            color=c.link if sidebar_open else c.muted,
                            padding=4,
                        ),
                    ),
                    ft.Icon(
                        icon=ft.Icons.CIRCLE,
                        size=8,
                        color="#FF9F0A" if document.dirty else "#35C759",
                    ),
                    ft.Text(
                        value=fname,
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Container(expand=True),
                    ft.Text(
                        value=f"行 {row}  列 {col}",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(width=16),
                    ft.Text(
                        value=f"{word_count} 词",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                    ft.Container(width=12),
                    ft.Text(
                        value=f"{char_count} 字符",
                        size=12,
                        color=c.muted,
                        font_family=FONT_MAIN,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    main_col = ft.Column(
        controls=[
            body,
            _build_footer(),
        ],
        spacing=0,
        expand=True,
    )

    return ft.Stack(
        controls=[
            main_col,
            settings_view,
        ],
        expand=True,
    )


async def main(page: ft.Page):
    page.title = "Markdown 编辑器"
    page.fonts = {"Alibaba": "assets/fonts/AlibabaPuHuiTi-3-55-Regular.otf"}
    # 亮/暗两套主题，由 App 的 theme_mode state 切换
    # 背景色由 App._apply_theme 通过 page.bgcolor 单独设置，不放在 ColorScheme
    page.theme = ft.Theme(
        font_family="Alibaba",
        color_scheme=ft.ColorScheme(
            surface="#FFFFFF",
            on_surface="#1F2329",
            primary="#1677FF",
        ),
    )
    page.dark_theme = ft.Theme(
        font_family="Alibaba",
        color_scheme=ft.ColorScheme(
            surface="#161B22",
            on_surface="#E6EDF3",
            primary="#58A6FF",
        ),
    )
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window.width = 1200
    page.window.height = 720
    page.window.min_width = 720
    page.window.min_height = 480
    await page.window.center()
    page.render(App)


def main_sync():
    """同步入口，供 console_scripts 调用。"""
    ft.run(main)


if __name__ == "__main__":
    ft.run(main)
