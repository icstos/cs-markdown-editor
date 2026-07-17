"""Markdown 编辑器入口。

- 注册本地字体 AlibabaPuHuiTi-3-55-Regular
- 声明式渲染：page.render(App)
- 文档状态上抛到 App 层，便于 New / Open / Save
- 段级编辑、Typora 式实时渲染由 views/editor 负责
"""

import asyncio
import json
import os

import flet as ft

import parser
from models import BlockType, Document
from styles import get_colors
from views.editor import MarkdownEditor

_SAMPLE = r"""# Markdown 编辑器

基于 Flet 0.86.0 声明式组件与 mistune 实时渲染，参考 Typora 的段级编辑体验。

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
    with open(path, "r", encoding="utf-8") as f:
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
}


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
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
    # 导航接口：editor 把光标状态与导航函数写入此 ref，App 的 on_key 据此分发
    nav_ref = ft.use_ref(None)

    # FilePicker：挂到 page.overlay 才能弹出系统对话框
    picker_holder = ft.use_ref()
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
        # FilePicker 是 service，不需要添加到 page.overlay
        picker_holder.current = ft.FilePicker()

    ft.use_effect(_mount_picker, [])

    def _apply_theme():
        # 推送 page 级属性（theme_mode / bgcolor / 原生 chrome）到 UI
        page = ft.context.page
        page.theme_mode = theme_mode
        page.bgcolor = get_colors(theme_mode).bg
        page.update()

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

    def update_setting(key: str, value):
        next_settings = dict(settings)
        next_settings[key] = value
        set_settings(next_settings)
        _save_settings(next_settings)

    def reset_settings():
        next_settings = dict(_DEFAULT_SETTINGS)
        set_settings(next_settings)
        _save_settings(next_settings)

    def on_dirty_change(d: bool):
        set_dirty(d)

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
        path = files[0].path
        try:
            text = _read_file(path)
        except Exception as e:
            page_ref.current.open(ft.SnackBar(ft.Text(f"打开失败：{e}")))
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        set_document(doc)
        set_file_path(path)
        set_dirty(False)
        set_session(session + 1)

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
        """Ctrl+C 后异步执行：等待原生复制完成→读取纯文本→匹配文档→替换为 Markdown。"""
        await asyncio.sleep(0.2)
        page = page_ref.current
        if page is None:
            return
        try:
            plain = await page.clipboard.get()
        except Exception:
            return
        if not plain:
            return
        nav = nav_ref.current
        if nav is None or not nav.get("compute_markdown_from_text"):
            return
        try:
            md = nav["compute_markdown_from_text"](plain)
            if md and md != plain:
                await page.clipboard.set(md)
        except Exception:
            return

    async def _do_cut():
        """Ctrl+X 后异步执行：等待原生复制完成→读取纯文本→匹配文档→替换为 Markdown→删除选中内容。"""
        await asyncio.sleep(0.2)
        page = page_ref.current
        if page is None:
            return
        try:
            plain = await page.clipboard.get()
        except Exception:
            return
        if not plain:
            return
        nav = nav_ref.current
        if nav is None or not nav.get("handle_cut"):
            return
        try:
            await nav["handle_cut"](plain)
        except Exception:
            return

    async def _do_paste_check():
        """Ctrl+V 后异步检查剪贴板是否含多行内容，若是则拆分为多行插入。"""
        await asyncio.sleep(0.05)
        page = page_ref.current
        if page is None:
            return
        try:
            text = await page.clipboard.get()
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

    def on_key(e):
        key = e.key or ""
        norm = key.replace(" ", "").lower()

        # 导航键：仅在编辑态有激活段时由 editor 暴露的接口处理
        nav = nav_ref.current
        if nav and nav.get("active") is not None:
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
                if nav.get("active_line") is not None and getattr(nav["active_line"], "block_type", None) == BlockType.CODE:
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
            if norm == "arrowleft":
                if nav["extent"] == 0 and nav["base"] == 0:
                    nav["move_left"]()
                    return
            if norm == "arrowright":
                if (
                    nav["extent"] == nav["draft_len"]
                    and nav["base"] == nav["draft_len"]
                ):
                    nav["move_right"]()
                    return

        if not (e.ctrl or e.meta):
            return
        k = key.upper()
        page = page_ref.current
        if page is None:
            return
        if k == "S":
            page.run_task(save_doc)
        elif k == "N":
            new_doc()
        elif k == "O":
            page.run_task(open_doc)
        elif k == "C":
            # 非编辑态：让 SelectionArea 原生复制后异步替换为 Markdown 源码
            nav = nav_ref.current
            if nav and nav.get("active") is None:
                page.run_task(_do_copy)
        elif k == "X":
            # 非编辑态：让 SelectionArea 原生复制后异步剪切（复制 Markdown + 删除选中内容）
            nav = nav_ref.current
            if nav and nav.get("active") is None:
                page.run_task(_do_cut)
        elif k == "V":
            # 编辑态：保存粘贴前 draft，再异步检查剪贴板是否含多行
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
        bgcolor=ft.Colors.with_opacity(0.35, ft.Colors.BLACK),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=1020,
            height=720,
            bgcolor=get_colors(theme_mode).toolbar_bg,
            border_radius=18,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Row(
                spacing=0,
                controls=[
                    ft.Container(
                        width=250,
                        bgcolor=ft.Colors.with_opacity(0.18, get_colors(theme_mode).border),
                        padding=20,
                        content=ft.Column(
                            expand=True,
                            controls=[
                                ft.Text("设置", size=22, weight=ft.FontWeight.W_700),
                                ft.Text("Typora 风格的可配置中心", size=12, color=get_colors(theme_mode).muted),
                                ft.Container(height=18),
                                *[
                                    ft.Container(
                                        border_radius=10,
                                        bgcolor=ft.Colors.with_opacity(0.12, get_colors(theme_mode).link) if settings_tab == tab else None,
                                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                                        content=ft.Row(
                                            controls=[
                                                ft.Icon(icon=icon, size=16, color=get_colors(theme_mode).link if settings_tab == tab else get_colors(theme_mode).muted),
                                                ft.TextButton(label, on_click=lambda e, t=tab: select_settings_tab(t)),
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
                                ft.TextButton("恢复默认", on_click=lambda e: reset_settings()),
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
                                                ft.Text(current_title, size=20, weight=ft.FontWeight.W_700),
                                                ft.Text(current_desc, size=12, color=get_colors(theme_mode).muted),
                                            ],
                                            spacing=2,
                                        ),
                                        ft.Container(expand=True),
                                        ft.IconButton(icon=ft.Icons.CLOSE, on_click=lambda e: close_settings()),
                                    ]
                                ),
                                ft.Container(height=8),
                                ft.Container(
                                    visible=settings_tab == "edit",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("布局", size=14, weight=ft.FontWeight.W_600),
                                            ft.Row([ft.Text("内容宽度", width=96), ft.Slider(min=680, max=1200, divisions=13, value=settings["content_max_width"], expand=True, on_change=lambda e: update_setting("content_max_width", int(e.control.value))), ft.Text(str(settings["content_max_width"]))]),
                                            ft.Row([ft.Text("左右边距", width=96), ft.Slider(min=12, max=64, divisions=13, value=settings["content_padding"], expand=True, on_change=lambda e: update_setting("content_padding", int(e.control.value))), ft.Text(str(settings["content_padding"]))]),
                                            ft.Row([ft.Text("顶部边距", width=96), ft.Slider(min=8, max=48, divisions=10, value=settings["content_padding_top"], expand=True, on_change=lambda e: update_setting("content_padding_top", int(e.control.value))), ft.Text(str(settings["content_padding_top"]))]),
                                            ft.Switch(label="显示底部状态栏", value=settings["show_footer"], on_change=lambda e: update_setting("show_footer", e.control.value)),
                                        ],
                                        spacing=12,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "appearance",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("字体与排版", size=14, weight=ft.FontWeight.W_600),
                                            ft.Row([ft.Text("正文大小", width=96), ft.Slider(min=14, max=20, divisions=6, value=settings["body_font_size"], expand=True, on_change=lambda e: update_setting("body_font_size", int(e.control.value))), ft.Text(str(settings["body_font_size"]))]),
                                            ft.Row([ft.Text("行高", width=96), ft.Slider(min=1.2, max=2.0, divisions=8, value=settings["line_height"], expand=True, on_change=lambda e: update_setting("line_height", round(float(e.control.value), 1))), ft.Text(str(settings["line_height"]))]),
                                            ft.Row([ft.Text("字体", width=96), ft.Dropdown(options=[ft.dropdown.Option("Alibaba"), ft.dropdown.Option("Sans"), ft.dropdown.Option("Serif"), ft.dropdown.Option("Monospace")], value=settings["font_family"], expand=True, on_select=lambda e: update_setting("font_family", e.control.value))]),
                                            ft.Row([ft.Text("代码主题(暗)", width=96), ft.Dropdown(options=[ft.dropdown.Option("ATOM_ONE_DARK"), ft.dropdown.Option("GITHUB"), ft.dropdown.Option("VS2015")], value=settings["code_theme_dark"], expand=True, on_select=lambda e: update_setting("code_theme_dark", e.control.value))]),
                                            ft.Row([ft.Text("代码主题(亮)", width=96), ft.Dropdown(options=[ft.dropdown.Option("GITHUB"), ft.dropdown.Option("ATOM_ONE_LIGHT"), ft.dropdown.Option("VS2015")], value=settings["code_theme_light"], expand=True, on_select=lambda e: update_setting("code_theme_light", e.control.value))]),
                                        ],
                                        spacing=12,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "behavior",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("行为", size=14, weight=ft.FontWeight.W_600),
                                            ft.Switch(label="自动保存", value=settings["auto_save"], on_change=lambda e: update_setting("auto_save", e.control.value)),
                                            ft.Switch(label="记住聚焦模式", value=settings["remember_focus_mode"], on_change=lambda e: update_setting("remember_focus_mode", e.control.value)),
                                            ft.Switch(label="显示工具栏", value=settings["show_toolbar"], on_change=lambda e: update_setting("show_toolbar", e.control.value)),
                                            ft.Switch(label="显示行号", value=settings["show_line_numbers"], on_change=lambda e: update_setting("show_line_numbers", e.control.value)),
                                            ft.Row([ft.Text("自动保存间隔(秒)", width=140), ft.Slider(min=3, max=60, divisions=19, value=10, expand=True, on_change=lambda e: None), ft.Text("10")]),
                                            ft.Row([ft.Text("导出默认格式", width=140), ft.Dropdown(options=[ft.dropdown.Option("html"), ft.dropdown.Option("pdf"), ft.dropdown.Option("md")], value=settings["export_format"], expand=True, on_select=lambda e: update_setting("export_format", e.control.value))]),
                                        ],
                                        spacing=12,
                                    ),
                                ),
                                ft.Container(
                                    visible=settings_tab == "shortcuts",
                                    content=ft.Column(
                                        controls=[
                                            ft.Text("常用快捷键", size=14, weight=ft.FontWeight.W_600),
                                            ft.Text("Ctrl+S 保存", size=12),
                                            ft.Text("Ctrl+O 打开", size=12),
                                            ft.Text("Ctrl+N 新建", size=12),
                                            ft.Text("Ctrl+/ 原文模式", size=12),
                                            ft.Text("Ctrl+K 插入链接", size=12),
                                            ft.Text("Tab / Shift+Tab 列表缩进", size=12),
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
                                            ft.Text("高级", size=14, weight=ft.FontWeight.W_600),
                                            ft.Text("代码主题、导出格式、最近文件、自动备份、外部存储等可继续在此扩展。", size=12, color=get_colors(theme_mode).muted),
                                        ],
                                        spacing=8,
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

    return ft.Stack(
        controls=[
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
                theme_mode=theme_mode,
                on_toggle_theme=toggle_theme,
                settings=settings,
                on_open_settings=open_settings,
            ),
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
    page.window.width = 960
    page.window.height = 720
    page.window.min_width = 640
    page.window.min_height = 480
    await page.window.center()
    page.render(App)


def main_sync():
    """同步入口，供 console_scripts 调用。"""
    ft.run(main)


if __name__ == "__main__":
    ft.run(main)
