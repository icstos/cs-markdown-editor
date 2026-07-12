"""Markdown 编辑器入口。

- 注册本地字体 AlibabaPuHuiTi-3-55-Regular
- 声明式渲染：page.render(App)
- 文档状态上抛到 App 层，便于 New / Open / Save
- 段级编辑、Typora 式实时渲染由 views/editor 负责
"""

import asyncio
import os

import flet as ft

import parser
from models import Document
from styles import C_BORDER, C_MUTED, C_TEXT, FONT_MAIN, only_border
from views.editor import MarkdownEditor

_SAMPLE = r"""# Markdown 编辑器

基于 Flet 0.85.3 声明式组件与 mistune 实时渲染，参考 Typora 的段级编辑体验。

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


@ft.component
def App():
    document, set_document = ft.use_state(lambda: parser.parse_markdown(_SAMPLE))
    file_path, set_file_path = ft.use_state(None)
    dirty, set_dirty = ft.use_state(False)
    session, set_session = ft.use_state(0)  # 切换文档时自增，强制编辑器重置内部状态
    # 导航接口：editor 把光标状态与导航函数写入此 ref，App 的 on_key 据此分发
    nav_ref = ft.use_ref(None)

    # FilePicker：挂到 page.overlay 才能弹出系统对话框
    picker_holder = ft.use_ref()
    # Clipboard：Ctrl+C 时先让 SelectionArea 原生复制纯文本，再异步读取→匹配→替换为 Markdown
    clipboard_holder = ft.use_ref()
    # page 引用：事件回调中 ft.context.page 可能不可用，提前缓存
    page_ref = ft.use_ref()

    def _mount_picker():
        page = ft.context.page
        page_ref.current = page
        picker = ft.FilePicker()
        picker_holder.current = picker
        page.overlay.append(picker)
        clip = ft.Clipboard()
        clipboard_holder.current = clip
        page.overlay.append(clip)
        return lambda: (
            page.overlay.remove(picker),
            page.overlay.remove(clip),
        )

    ft.use_effect(_mount_picker, [])

    def on_dirty_change(d: bool):
        set_dirty(d)

    def new_doc():
        doc = parser.parse_markdown("")
        doc.file_path = None
        set_document(doc)
        set_file_path(None)
        set_dirty(False)
        set_session(session + 1)

    def open_doc():
        picker = picker_holder.current
        if picker is None:
            return
        files = picker.pick_files(
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
            ft.context.page.open(ft.SnackBar(ft.Text(f"打开失败：{e}")))
            return
        doc = parser.parse_markdown(text)
        doc.file_path = path
        set_document(doc)
        set_file_path(path)
        set_dirty(False)
        set_session(session + 1)

    def save_doc():
        text = parser.serialize(document)
        path = file_path
        if not path:
            picker = picker_holder.current
            if picker is None:
                return
            path = picker.save_file(
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
            ft.context.page.open(ft.SnackBar(ft.Text(f"保存失败：{e}")))
            return
        document.file_path = path
        document.dirty = False
        set_file_path(path)
        set_dirty(False)

    # ---- 快捷键 + 光标导航 ----
    # KeyDownEvent 只有 key 属性，无修饰键信息，需手动跟踪 Ctrl 按下/释放
    ctrl_held = ft.use_ref(False)

    async def _do_copy():
        """Ctrl+C 后异步执行：等待原生复制完成→读取纯文本→匹配文档→替换为 Markdown。"""
        await asyncio.sleep(0.2)
        clip = clipboard_holder.current
        if clip is None:
            return
        plain = await clip.get()
        if not plain:
            return
        nav = nav_ref.current
        if nav is None or not nav.get("compute_markdown_from_text"):
            return
        md = nav["compute_markdown_from_text"](plain)
        if md and md != plain:
            await clip.set(md)

    def on_key(e):
        key = e.key or ""
        norm = key.replace(" ", "").lower()

        # 跟踪 Ctrl/Meta 修饰键状态
        if norm.startswith("control") or norm.startswith("meta"):
            ctrl_held.current = True
            return

        # 导航键：仅在编辑态有激活段时由 editor 暴露的接口处理
        nav = nav_ref.current
        if nav and nav.get("active") is not None:
            if norm == "home":
                nav["move_home"]()
                return
            if norm == "end":
                nav["move_end"]()
                return
            if norm == "arrowup":
                nav["move_up"]()
                return
            if norm == "arrowdown":
                nav["move_down"]()
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

        if not ctrl_held.current:
            return
        k = key.upper()
        if k == "S":
            save_doc()
        elif k == "N":
            new_doc()
        elif k == "O":
            open_doc()
        elif k == "C":
            # 非编辑态：让 SelectionArea 原生复制后异步替换为 Markdown 源码
            nav = nav_ref.current
            if nav and nav.get("active") is None:
                page = page_ref.current
                if page is not None:
                    page.run_task(_do_copy)

    def on_key_up(e):
        key = e.key or ""
        norm = key.replace(" ", "").lower()
        if norm.startswith("control") or norm.startswith("meta"):
            ctrl_held.current = False

    def _app_bar():
        return ft.Container(
            bgcolor=ft.Colors.WHITE,
            border=only_border(bottom=ft.BorderSide(1, C_BORDER)),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            content=ft.Row(
                controls=[
                    ft.Row(
                        controls=[
                            ft.IconButton(
                                icon=ft.Icons.NOTE_ADD,
                                tooltip="新建  Ctrl+N",
                                on_click=lambda e: new_doc(),
                                icon_size=20,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.FILE_OPEN,
                                tooltip="打开  Ctrl+O",
                                on_click=lambda e: open_doc(),
                                icon_size=20,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.SAVE,
                                tooltip="保存  Ctrl+S",
                                on_click=lambda e: save_doc(),
                                icon_size=20,
                            ),
                        ],
                        spacing=2,
                    ),
                    ft.Container(width=8),
                    ft.Text(
                        value=("● " if dirty else "") + _file_name(file_path),
                        size=14,
                        color=C_TEXT,
                        weight=ft.FontWeight.W_500,
                        font_family=FONT_MAIN,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
            ),
        )

    return ft.KeyboardListener(
        content=ft.Column(
            controls=[
                _app_bar(),
                ft.Container(
                    content=MarkdownEditor(
                        key=str(session),
                        document=document,
                        on_dirty_change=on_dirty_change,
                        nav_ref=nav_ref,
                    ),
                    expand=True,
                    bgcolor=ft.Colors.WHITE,
                ),
            ],
            expand=True,
        ),
        on_key_down=on_key,
        on_key_up=on_key_up,
        expand=True,
    )


async def main(page: ft.Page):
    page.title = "Markdown 编辑器"
    page.fonts = {"Alibaba": "assets/fonts/AlibabaPuHuiTi-3-55-Regular.otf"}
    page.theme = ft.Theme(
        font_family="Alibaba",
        color_scheme_seed=ft.Colors.BLUE,
        color_scheme=ft.ColorScheme(
            surface=ft.Colors.WHITE,
        ),
    )
    page.scroll = ft.ScrollMode.AUTO
    page.bgcolor = ft.Colors.WHITE
    page.window.width = 960
    page.window.height = 720
    page.window.min_width = 640
    page.window.min_height = 480
    await page.window.center()
    page.render(App)


if __name__ == "__main__":
    ft.run(main)
