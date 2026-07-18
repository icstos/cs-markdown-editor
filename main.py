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
from services.shortcuts import DEFAULT_SHORTCUTS, ShortcutManager
from styles import FONT_MAIN, get_colors, only_border
from views.editor import MarkdownEditor
from views.key_bindings import KeyDispatcher
from views.settings_dialog import SettingsDialog
from views.sidebar import Sidebar
from views.status_bar import StatusBar

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
    "shortcuts": {k: dict(v) for k, v in DEFAULT_SHORTCUTS.items()},
}



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

    # ShortcutManager：无状态读取器，每次渲染重建。update_setting 通过 lambda
    # 前向引用（update_setting 在下方定义，调用时才解析），打破循环依赖。
    shortcut_mgr = ShortcutManager(settings, lambda key, value: update_setting(key, value))

    def update_setting(key: str, value):
        next_settings = dict(settings)
        next_settings[key] = value
        set_settings(next_settings)
        _save_settings(next_settings)
        _apply_content_layout()
        if key == "shortcuts":
            layer, action = shortcut_mgr.first_conflict_target()
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
        next_settings["shortcuts"] = {k: dict(v) for k, v in DEFAULT_SHORTCUTS.items()}
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
                settings.get("shortcuts", DEFAULT_SHORTCUTS),
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
        actions = nav_ref.current
        if actions is not None:
            actions.jump_to_line(li)

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

    # KeyDispatcher：替代 on_key 闭包。持有 shortcut_mgr + nav_ref 引用，
    # editor.py 每次渲染写入最新 EditorActions 后 dispatcher 读到的就是最新值，
    # 无需 on_key_ref 中转层。
    dispatcher = KeyDispatcher(
        shortcut_mgr=shortcut_mgr,
        actions_ref=nav_ref,
        clipboard_ref=clipboard_holder,
        page_ref=page_ref,
        paste_old_draft=paste_old_draft,
        app_callbacks={
            "save": save_doc,
            "new": new_doc,
            "open": open_doc,
            "toggle_sidebar": toggle_sidebar,
            "toggle_theme": toggle_theme,
            "open_settings": open_settings,
        },
    )

    def _bind_keyboard():
        page = ft.context.page
        page_ref.current = page
        if page is None:
            return lambda: None

        def _handler(e):
            try:
                dispatcher.handle(e)
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

    settings_view = SettingsDialog(
        open_state=settings_open,
        tab=settings_tab,
        settings=settings,
        theme_mode=theme_mode,
        shortcut_focus=shortcut_focus,
        shortcut_mgr=shortcut_mgr,
        on_close=close_settings,
        on_select_tab=select_settings_tab,
        on_update=update_setting,
        on_reset_all=reset_settings,
        on_reset_shortcuts=reset_shortcuts,
        on_import=lambda: page_ref.current.run_task(import_shortcuts),
        on_export=lambda: page_ref.current.run_task(export_shortcuts),
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
    _actions = nav_ref.current
    cursor_row_col = _actions.get_cursor_row_col() if _actions else (1, 1)
    footer = (
        StatusBar(
            document=document,
            file_path=file_path,
            dirty=document.dirty,
            sidebar_open=settings.get("sidebar_open", False),
            cursor_row_col=cursor_row_col,
            theme_mode=theme_mode,
            on_toggle_sidebar=toggle_sidebar,
        )
        if settings.get("show_footer", True)
        else ft.Container(height=0)
    )

    main_col = ft.Column(
        controls=[
            body,
            footer,
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
