"""MarkText 风格全局菜单按钮（嵌入标签栏最左侧）。

标签栏最左侧仅显示一个菜单图标按钮（≡），点击后弹出包含
文件/编辑/段落/格式/视图/帮助六组的分级菜单，悬停某组展开二级菜单。
替代原有顶部工具栏区域（ToolArea）。

实现说明：
- Flet 的 PopupMenuButton.items 只接受 PopupMenuItem，而 PopupMenuItem 无子菜单
  属性（点击即关闭），无法实现六组嵌套子菜单。故采用 MenuBar + 单个
  SubmenuButton（菜单图标 content）实现等效的「一个按钮收纳六组」交互，
  视觉与 PopupMenuButton 一致，且支持完整嵌套子菜单。
- 所有功能 100% 复用原有回调（ctx 装配槽 + EditorActions via get_active_nav）
- 菜单项显示快捷键标签（右对齐灰色文本），点击后自动关闭（close_on_click=True）
- 打开最近文件子菜单：无记录时显示「暂无最近文件」并置灰
- 导出子菜单：HTML / Word / PDF 三格式（复用 Pandoc 导出能力）
- 帮助菜单：调用系统浏览器打开对应网页
"""

import os

import flet as ft

from models import BlockType
from styles import FONT_MAIN, Radius, Spacing, get_colors

# 菜单字号（紧凑，与标签栏一致）
_MENU_FONT = 13
_MENU_SHORTCUT_FONT = 11
_MENU_ITEM_HEIGHT = 36
_MENU_ICON_SIZE = 18  # 顶层菜单图标字号（与标签栏图标一致）


def _menu_item(
    label: str,
    shortcut: str = "",
    on_click=None,
    disabled: bool = False,
    c=None,
) -> ft.MenuItemButton:
    """构造单个菜单项：左侧标签 + 右侧快捷键。"""
    controls = [
        ft.Text(
            value=label,
            size=_MENU_FONT,
            font_family=FONT_MAIN,
            color=c.text if not disabled else c.muted,
        ),
    ]
    if shortcut:
        controls.append(
            ft.Container(
                content=ft.Text(
                    value=shortcut,
                    size=_MENU_SHORTCUT_FONT,
                    font_family=FONT_MAIN,
                    color=c.muted,
                ),
                margin=ft.Margin.only(left=24),
            )
        )
    return ft.MenuItemButton(
        content=ft.Row(
            controls=controls,
            spacing=0,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        on_click=on_click if not disabled else None,
        disabled=disabled,
        close_on_click=True,
        height=_MENU_ITEM_HEIGHT,
    )


def _separator(c) -> ft.MenuItemButton:
    """菜单分隔线。"""
    return ft.MenuItemButton(
        content=ft.Divider(height=1, thickness=1, color=c.border),
        disabled=True,
        close_on_click=False,
        height=1,
    )


def _submenu(label: str, controls: list, c) -> ft.SubmenuButton:
    """构造一级子菜单按钮。"""
    return ft.SubmenuButton(
        content=ft.Text(
            value=label,
            size=_MENU_FONT,
            font_family=FONT_MAIN,
            color=c.text,
        ),
        controls=controls,
    )


def build_global_menu(ctx, theme_mode: ft.ThemeMode) -> ft.MenuBar:
    """构造 MarkText 风格全局菜单栏。

    从 ctx 装配槽读取所有业务回调，EditorActions 通过 ctx.get_active_nav() 路由
    到当前焦点视口。所有原有功能 100% 复用，无核心逻辑重写。

    Args:
        ctx: AppContext 实例（持有所有跨控制器回调）
        theme_mode: 当前主题模式
    Returns:
        ft.MenuBar 控件，嵌入标签栏最左侧
    """
    c = get_colors(theme_mode)
    settings = ctx.settings or {}
    page_ref = ctx.page_ref

    # ---- 辅助：调用当前焦点视口的 EditorActions ----
    def _nav():
        """获取当前焦点视口的 EditorActions。"""
        nav = ctx.get_active_nav()
        if nav is not None and nav.current is not None:
            return nav.current
        return None

    def _do_editor(method_name: str, *args):
        """调用 EditorActions 上的同步方法（undo/redo/select_all/set_block 等）。"""
        n = _nav()
        if n is not None:
            fn = getattr(n, method_name, None)
            if fn is not None:
                fn(*args)

    def _run_async(coro_fn, *args):
        """异步执行协程（文件 IO 等）。"""
        if page_ref.current is not None:
            page_ref.current.run_task(coro_fn, *args)

    def _do_clipboard(method_name: str):
        """通过 KeyDispatcher 执行剪贴板操作（cut/copy/paste/paste_plain）。

        这些操作需要异步读取剪贴板 + 访问 selection_text_ref / paste_old_draft，
        直接调用 EditorActions.handle_paste 等方法缺少剪贴板文本。KeyDispatcher
        的 _do_cut / _do_copy / _do_paste_check / _do_paste_plain_check 封装了
        完整的剪贴板读取 + 选区判断 + 异步插入流程，100% 复用原有逻辑。
        """
        d = ctx.dispatcher_ref.current if ctx.dispatcher_ref is not None else None
        if d is not None and page_ref.current is not None:
            fn = getattr(d, method_name, None)
            if fn is not None:
                page_ref.current.run_task(fn)

    # ============ 文件菜单 ============
    recent_files = settings.get("recent_files", [])
    # 过滤已删除文件
    recent_existing = [p for p in recent_files if os.path.exists(p)] if recent_files else []

    recent_controls = []
    if not recent_existing:
        recent_controls.append(
            _menu_item("暂无最近文件", disabled=True, c=c)
        )
    else:
        for p in recent_existing[:10]:
            name = os.path.basename(p)
            recent_controls.append(
                _menu_item(
                    name,
                    on_click=lambda e, path=p: ctx.open_file_by_path(path),
                    c=c,
                )
            )

    export_controls = [
        _menu_item("HTML", on_click=lambda e: _run_async(ctx.export_doc, "html"), c=c),
        _menu_item("Word", on_click=lambda e: _run_async(ctx.export_doc, "docx"), c=c),
        _menu_item("PDF", on_click=lambda e: _run_async(ctx.export_doc, "pdf"), c=c),
    ]

    file_controls = [
        _menu_item("新建标签页", "Ctrl+N", on_click=lambda e: ctx.new_doc(), c=c),
        _separator(c),
        _menu_item("打开文件", "Ctrl+O", on_click=lambda e: _run_async(ctx.open_doc), c=c),
        _menu_item("打开文件夹", "Ctrl+Shift+O", on_click=lambda e: _run_async(ctx.open_folder), c=c),
        _submenu("打开最近", recent_controls, c),
        _separator(c),
        _menu_item("保存", "Ctrl+S", on_click=lambda e: _run_async(ctx.save_doc), c=c),
        _menu_item("另存为...", "Ctrl+Shift+S", on_click=lambda e: _run_async(ctx.save_as_doc), c=c),
        _submenu("导出", export_controls, c),
        _menu_item("设置", "Ctrl+,", on_click=lambda e: ctx.open_settings(), c=c),
        _menu_item("关闭", "Ctrl+W", on_click=lambda e: ctx.close_tab(ctx.active_index), c=c),
    ]

    # ============ 编辑菜单 ============
    # 剪贴板操作通过 KeyDispatcher 的异步方法路由：_do_cut / _do_copy /
    # _do_paste_check / _do_paste_plain_check 封装了剪贴板读取 + 选区判断，
    # 与快捷键 Ctrl+X/C/V/Shift+V 完全复用同一流程。
    edit_controls = [
        _menu_item("撤销", "Ctrl+Z", on_click=lambda e: _do_editor("undo"), c=c),
        _menu_item("重做", "Ctrl+Shift+Z", on_click=lambda e: _do_editor("redo"), c=c),
        _separator(c),
        _menu_item("剪切", "Ctrl+X", on_click=lambda e: _do_clipboard("_do_cut"), c=c),
        _menu_item("复制", "Ctrl+C", on_click=lambda e: _do_clipboard("_do_copy"), c=c),
        _menu_item("复制为富文本", "Ctrl+Shift+C", disabled=True, c=c),
        _menu_item("粘贴", "Ctrl+V", on_click=lambda e: _do_clipboard("_do_paste_check"), c=c),
        _menu_item("粘贴为纯文本", "Ctrl+Shift+V", on_click=lambda e: _do_clipboard("_do_paste_plain_check"), c=c),
        _menu_item("删除行", "Ctrl+Shift+D", disabled=True, c=c),
        _separator(c),
        _menu_item("全选", "Ctrl+A", on_click=lambda e: _do_editor("select_all"), c=c),
        _menu_item("查找", "Ctrl+F", on_click=lambda e: ctx.focus_search(), c=c),
        _menu_item("替换", "Ctrl+H", on_click=lambda e: ctx.toggle_replace_bar(), c=c),
        _menu_item("全局查找", "Ctrl+Shift+F", on_click=lambda e: ctx.focus_search(), c=c),
    ]

    # ============ 段落菜单 ============
    paragraph_controls = [
        _menu_item("标题 1", "Ctrl+1", on_click=lambda e: _do_editor("set_block", BlockType.HEADING, 1), c=c),
        _menu_item("标题 2", "Ctrl+2", on_click=lambda e: _do_editor("set_block", BlockType.HEADING, 2), c=c),
        _menu_item("标题 3", "Ctrl+3", on_click=lambda e: _do_editor("set_block", BlockType.HEADING, 3), c=c),
        _menu_item("标题 4", "Ctrl+4", on_click=lambda e: _do_editor("set_block", BlockType.HEADING, 4), c=c),
        _menu_item("标题 5", "Ctrl+5", on_click=lambda e: _do_editor("set_block", BlockType.HEADING, 5), c=c),
        _menu_item("标题 6", "Ctrl+6", on_click=lambda e: _do_editor("set_block", BlockType.HEADING, 6), c=c),
        _separator(c),
        _menu_item("表格", "Ctrl+T", on_click=lambda e: _do_editor("format_table"), c=c),
        _menu_item("代码块", on_click=lambda e: _do_editor("set_block", BlockType.CODE), c=c),
        _menu_item("引用块", on_click=lambda e: _do_editor("set_block", BlockType.QUOTE), c=c),
        _menu_item("公式块", on_click=lambda e: _do_editor("set_block", BlockType.MATH), c=c),
        _separator(c),
        _menu_item("有序列表", on_click=lambda e: _do_editor("set_block", BlockType.LIST_O), c=c),
        _menu_item("无序列表", "Ctrl+L", on_click=lambda e: _do_editor("set_block", BlockType.LIST_UO), c=c),
        _menu_item("任务列表", "Ctrl+Shift+L", on_click=lambda e: _do_editor("format_task"), c=c),
        _separator(c),
        _menu_item("常规段落", "Ctrl+0", on_click=lambda e: _do_editor("set_block", BlockType.PARAGRAPH), c=c),
        _menu_item("水平分割线", "Ctrl+Shift+U", on_click=lambda e: _do_editor("set_block", BlockType.HR), c=c),
        _menu_item("前置元数据", "Alt+Ctrl+Y", disabled=True, c=c),
    ]

    # ============ 格式菜单 ============
    format_controls = [
        _menu_item("粗体", "Ctrl+B", on_click=lambda e: _do_editor("apply_inline_format", "bold"), c=c),
        _menu_item("斜体", "Ctrl+I", on_click=lambda e: _do_editor("apply_inline_format", "italic"), c=c),
        _menu_item("下划线", "Ctrl+U", disabled=True, c=c),
        _menu_item("高亮", "Ctrl+Shift+H", on_click=lambda e: _do_editor("apply_inline_format", "highlight"), c=c),
        _separator(c),
        _menu_item("行内代码", "Ctrl+`", on_click=lambda e: _do_editor("apply_inline_format", "code"), c=c),
        _menu_item("行内公式", "Ctrl+Shift+M", on_click=lambda e: _do_editor("apply_inline_format", "inline_math"), c=c),
        _menu_item("删除线", "Ctrl+D", on_click=lambda e: _do_editor("apply_inline_format", "strike"), c=c),
        _separator(c),
        _menu_item("超链接", "Ctrl+K", on_click=lambda e: _do_editor("apply_inline_format", "link"), c=c),
        _menu_item("图片", "Ctrl+Shift+I", disabled=True, c=c),
        _separator(c),
        _menu_item("清除格式", "Ctrl+R", disabled=True, c=c),
    ]

    # ============ 视图菜单 ============
    view_controls = [
        _menu_item("源码模式", "Ctrl+/", on_click=lambda e: _do_editor("toggle_raw"), c=c),
        _menu_item("切换主题", "Alt+T", on_click=lambda e: ctx.toggle_theme(), c=c),
        _menu_item("切换侧边栏", "Ctrl+Shift+B", on_click=lambda e: ctx.toggle_sidebar(), c=c),
        _menu_item("切换自动换行", "Ctrl+Shift+R", on_click=lambda e: ctx.toggle_word_wrap(), c=c),
        _separator(c),
        _menu_item("放大", "Ctrl+Shift+=", on_click=lambda e: ctx.zoom_in(), c=c),
        _menu_item("缩小", "Ctrl+Shift+-", on_click=lambda e: ctx.zoom_out(), c=c),
        _menu_item("实际大小", "Ctrl+Shift+0", on_click=lambda e: ctx.zoom_reset(), c=c),
    ]

    # ============ 帮助菜单 ============
    def _open_url(e, url: str):
        """调用系统默认浏览器打开 URL。"""
        if page_ref.current is not None:
            page_ref.current.launch_url(url, web_popup_window_name=ft.UrlTarget.BLANK)

    # TODO: 替换为实际网址
    _URL_OFFICIAL = "https://example.com"  # TODO: 官方网站
    _URL_CHANGELOG = "https://example.com/changelog"  # TODO: 更新日志
    _URL_PRIVACY = "https://example.com/privacy"  # TODO: 隐私条款
    _URL_FEEDBACK = "https://example.com/feedback"  # TODO: 反馈
    _URL_CREDITS = "https://example.com/credits"  # TODO: 鸣谢
    _URL_ABOUT = "https://example.com/about"  # TODO: 关于

    help_controls = [
        _menu_item("官方网站", on_click=lambda e: _open_url(e, _URL_OFFICIAL), c=c),
        _menu_item("更新日志", on_click=lambda e: _open_url(e, _URL_CHANGELOG), c=c),
        _menu_item("隐私条款", on_click=lambda e: _open_url(e, _URL_PRIVACY), c=c),
        _separator(c),
        _menu_item("反馈", on_click=lambda e: _open_url(e, _URL_FEEDBACK), c=c),
        _menu_item("鸣谢", on_click=lambda e: _open_url(e, _URL_CREDITS), c=c),
        _menu_item("关于", on_click=lambda e: _open_url(e, _URL_ABOUT), c=c),
    ]

    # ============ 组装 MenuBar：单个菜单图标按钮收纳六组 ============
    # 标签栏最左侧仅显示一个 ≡ 菜单图标，点击弹出文件/编辑/段落/格式/视图/帮助
    # 六组分级菜单（悬停展开二级）。视觉等效 PopupMenuButton，且支持完整嵌套。
    return ft.MenuBar(
        controls=[
            ft.SubmenuButton(
                content=ft.Icon(
                    ft.Icons.MENU,
                    size=_MENU_ICON_SIZE,
                    color=c.text,
                ),
                controls=[
                    _submenu("文件", file_controls, c),
                    _submenu("编辑", edit_controls, c),
                    _submenu("段落", paragraph_controls, c),
                    _submenu("格式", format_controls, c),
                    _submenu("视图", view_controls, c),
                    _submenu("帮助", help_controls, c),
                ],
                # 紧凑图标按钮：减小内边距，圆角与标签栏一致
                style=ft.ButtonStyle(
                    padding=Spacing.XS,
                    shape=ft.RoundedRectangleBorder(radius=Radius.SM),
                    bgcolor={
                        ft.ControlState.HOVERED: c.hover,
                        ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                    },
                ),
            ),
        ],
        style=ft.MenuStyle(
            bgcolor=c.toolbar_bg,
            shadow_color=ft.Colors.TRANSPARENT,
            elevation=0,
        ),
    )
