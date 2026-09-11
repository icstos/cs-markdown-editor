"""通用 UI 部件工厂（自包含，可被任意面板复用）。

从 views/sidebar.py 迁出：右键菜单包裹 / 拖拽反馈 / 放置目标 / 行容器 /
搜索框 / 空态提示等控件工厂与文件树或搜索业务无关，属可复用展示部件。
侧边栏按原私有名导入使用，调用点无需改写。
"""

import os
from collections.abc import Callable

import flet as ft

from styles import FONT_MAIN, Radius, Spacing
from views.native_scope import native_focus_hooks



# ---- 通用控件工厂 ----


def _wrap_context_menu(
    content: ft.Control,
    path: str,
    is_dir: bool,
    on_action: Callable[[str, str], None],
    compare_source: str | None = None,
    key: str | None = None,
    menu_state: dict | None = None,
) -> ft.ContextMenu:
    """将列表项包裹在右键菜单中。

    文件菜单：打开 / 选择以进行比较 / 与已选项目进行比较 /
            新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 创建副本 / 删除
    文件夹菜单：打开 / 新建文件 / 新建文件夹 / 复制路径 / 打开文件位置 / 重命名 / 删除
    （文件夹无"比较"和"创建副本"）
    「打开」语义：文件 → 编辑器（.md）/ 系统默认程序（非 .md）；文件夹 → 资源管理器打开。
    compare_source 非空时，文件项显示「与已选项目进行比较」。
    key 透传至 ft.ContextMenu，供 ListView 按路径复用项实例（虚拟化 reconciliation）。

    menu_state 非空时（工作区模式，外层有空白菜单 GestureDetector），用
    GestureDetector 监听 on_secondary_tap_down（按下阶段）置 inner_active 标志位，
    供外层在 on_secondary_tap_up（抬起阶段）判断右键是否命中文件项——
    避免右键文件项时内外两层菜单同时弹出。
    """
    items: list[ft.PopupMenuItem] = []

    # 打开：文件用 OPEN_IN_NEW，文件夹用 FOLDER_OPEN（区分语义）
    items.append(
        ft.PopupMenuItem(
            content="打开",
            icon=ft.Icons.FOLDER_OPEN if is_dir else ft.Icons.OPEN_IN_NEW,
            on_click=lambda e, p=path: on_action("open", p),
        )
    )

    if not is_dir:
        # 文件比较：选择以进行比较 / 与已选项目进行比较（VSCode 风格）
        items.append(
            ft.PopupMenuItem(
                content="选择以进行比较", icon=ft.Icons.DIFFERENCE,
                on_click=lambda e, p=path: on_action("select_for_compare", p),
            )
        )
        if compare_source and os.path.abspath(compare_source) != os.path.abspath(path):
            items.append(
                ft.PopupMenuItem(
                    content="与已选项目进行比较", icon=ft.Icons.COMPARE_ARROWS,
                    on_click=lambda e, p=path: on_action("compare_with_selected", p),
                )
            )
    items.append(ft.PopupMenuItem())  # 分隔

    # 新建文件/文件夹
    items.append(
        ft.PopupMenuItem(
            content="新建文件", icon=ft.Icons.NOTE_ADD,
            on_click=lambda e, p=path: on_action("new_file", p),
        )
    )
    items.append(
        ft.PopupMenuItem(
            content="新建文件夹", icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=lambda e, p=path: on_action("new_folder", p),
        )
    )
    items.append(ft.PopupMenuItem())  # 分隔

    # 路径操作
    items.append(
        ft.PopupMenuItem(
            content="复制路径", icon=ft.Icons.CONTENT_COPY,
            on_click=lambda e, p=path: on_action("copy_path", p),
        )
    )
    items.append(
        ft.PopupMenuItem(
            content="打开文件位置", icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e, p=path: on_action("reveal", p),
        )
    )
    items.append(ft.PopupMenuItem())  # 分隔

    # 文件操作
    items.append(
        ft.PopupMenuItem(
            content="重命名", icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
            on_click=lambda e, p=path: on_action("rename", p),
        )
    )
    if not is_dir:
        items.append(
            ft.PopupMenuItem(
                content="创建副本", icon=ft.Icons.FILE_COPY_OUTLINED,
                on_click=lambda e, p=path: on_action("duplicate", p),
            )
        )
    items.append(
        ft.PopupMenuItem(
            content="删除", icon=ft.Icons.DELETE_OUTLINE,
            on_click=lambda e, p=path: on_action("delete", p),
        )
    )

    if menu_state is None:
        # 最近文件列表模式：无外层空白菜单，保持默认自动触发即可
        return ft.ContextMenu(
            content=content,
            secondary_items=items,
            key=key,
        )

    # 工作区模式：行菜单由 ContextMenu 原生 down 触发（Listener 实现）。
    # 此标志 GD 仅在非拖拽模式生效（拖拽模式下被行 DragTarget 吞掉，天然
    # 失效但无害——行/空白分流由手势竞技场与行占位 GD 完成，见 _wrap_row_gesture）。
    # 回调仅置标志位，不消费事件，不影响左键点击与列表滚动。
    def _on_inner_secondary(_e):
        menu_state["inner_active"] = True

    def _on_inner_close(_e):
        menu_state["inner_active"] = False

    detector = ft.GestureDetector(
        content=content,
        on_secondary_tap_down=_on_inner_secondary,
    )
    return ft.ContextMenu(
        content=detector,
        secondary_items=items,
        key=key,
        on_dismiss=_on_inner_close,
        on_select=_on_inner_close,
    )


def _wrap_blank_context_menu(
    content: ft.Control,
    root_dir: str,
    on_action: Callable[[str, str], None],
    c,
    menu_state: dict,
) -> tuple[ft.GestureDetector, ft.ContextMenu]:
    """文件面板空白区域右键菜单（VSCode 资源管理器风格）。

    仅包含不依赖具体文件项的操作：新建文件 / 新建文件夹 / 复制路径 / 打开文件位置。
    path=root_dir（is_dir=True），复用 on_sidebar_context_action 的新建逻辑
    （dir_path = path if is_dir else dirname(path) → 在根目录下创建）。

    返回 (blank_body, holder)：
    - blank_body：GestureDetector 包裹列表主体（含其外层根 DragTarget），
      on_secondary_tap_down 手动 open 菜单，必须在所有 DragTarget 外层
      （实测 DragTarget 会吞掉其子孙 GD 的右键 tap 识别器）；
    - holder：零尺寸 ContextMenu 载体，不参与命中测试（自动触发物理上不可能），
      仅在 open() 时显示 items——不依赖客户端对 secondary_trigger=None 的支持。

    行/空白分流机制（拖拽模式）：行级菜单由行 ContextMenu 原生 down 触发
    （Listener 实现，不受 DragTarget 影响）；行的最外层「竞技场占位 GD」
    （见 _wrap_row_gesture）以更深的识别器在右键行时赢得手势竞技场，本 GD
    被竞技场拒绝——只有右键空白区域时本 GD 才是唯一识别器而获胜并弹菜单。

    inner_active 标志为非拖拽模式的兜底（无 DragTarget 时行内标志 GD 存活，
    置位后此处跳过；行菜单 on_dismiss/on_select 复位，无残留）。
    """
    items: list[ft.PopupMenuItem] = [
        ft.PopupMenuItem(
            content="新建文件", icon=ft.Icons.NOTE_ADD,
            on_click=lambda e, p=root_dir: on_action("new_file", p),
        ),
        ft.PopupMenuItem(
            content="新建文件夹", icon=ft.Icons.CREATE_NEW_FOLDER,
            on_click=lambda e, p=root_dir: on_action("new_folder", p),
        ),
        ft.PopupMenuItem(),  # 分隔
        ft.PopupMenuItem(
            content="复制路径", icon=ft.Icons.CONTENT_COPY,
            on_click=lambda e, p=root_dir: on_action("copy_path", p),
        ),
        ft.PopupMenuItem(
            content="打开文件位置", icon=ft.Icons.FOLDER_OPEN,
            on_click=lambda e, p=root_dir: on_action("reveal", p),
        ),
    ]

    async def _on_blank_secondary_down(e):
        # child-first：右键文件项时内层已置位标志（同 down 相位、必先执行），
        # 此处跳过并复位；右键空白区域时标志为 False → 立即弹空白菜单
        if menu_state.get("inner_active"):
            menu_state["inner_active"] = False
            return
        await holder.open(global_position=e.global_position)

    blank_body = ft.GestureDetector(
        content=content,
        on_secondary_tap_down=_on_blank_secondary_down,
        expand=True,
    )
    holder = ft.ContextMenu(
        content=ft.Container(width=0, height=0),
        items=items,
        key="blank-area",
    )
    return blank_body, holder


# ---- 文件树拖拽（VSCode 资源管理器风格） ----

_DRAG_GROUP = "filetree"  # 拖拽组：仅文件树内部可互相拖放


def _drop_allowed(src: str, dst_dir: str) -> bool:
    """拖放合法性（与 file_ops.move_path 校验一致，UI 层提前判断控制高亮）。"""
    if not src or not dst_dir:
        return False
    try:
        src_abs = os.path.abspath(src)
        dst_abs = os.path.abspath(dst_dir)
    except OSError:
        return False
    if not os.path.isdir(dst_abs):
        return False
    if os.path.dirname(src_abs) == dst_abs:
        return False  # 已在目标文件夹中
    if os.path.isdir(src_abs):
        s, d = os.path.normcase(src_abs), os.path.normcase(dst_abs)
        if d == s or d.startswith(s + os.sep):
            return False  # 不能移到自身或其子文件夹
    return True


def _drag_feedback(name: str, icon_name: str, icon_color, c) -> ft.Control:
    """拖拽时跟随指针的紧凑预览标签（图标 + 名称）。"""
    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.95, c.surface),
        border=ft.Border.all(1, c.border),
        border_radius=Radius.MD,
        padding=ft.Padding.symmetric(horizontal=10, vertical=5),
        content=ft.Row(
            controls=[
                ft.Icon(icon_name, size=13, color=icon_color),
                ft.Text(
                    name,
                    size=12,
                    color=c.text,
                    font_family=FONT_MAIN,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
            spacing=Spacing.SM,
            tight=True,
        ),
    )


def _wrap_draggable(
    row: ft.Control,
    ghost: ft.Control,
    feedback: ft.Control,
    drag_registry: dict,
    path: str,
    key: str | None = None,
) -> ft.Draggable:
    """将文件树行包装为可拖拽项。

    - content_when_dragging：拖起后原位置显示半透明占位（VSCode 直觉）；
    - drag_registry：id(Draggable) → 路径，DragTarget 事件经 e.src 反查源路径
      （拖拽会话期间行不重建，id 映射稳定）；
    - key 供 ListView 按路径复用行实例（虚拟化 reconciliation）。
    """
    d = ft.Draggable(
        group=_DRAG_GROUP,
        content=row,
        content_when_dragging=ghost,
        content_feedback=feedback,
        key=key,
    )
    drag_registry[id(d)] = path
    return d


def _wrap_drop_target(
    content: ft.Control,
    dst_dir: str | None,
    drag_registry: dict,
    on_drop: Callable[[str, str], None],
    set_highlight: Callable[[str | None], None],
    key: str | None = None,
) -> ft.DragTarget:
    """将文件夹行（或根区域）包装为放置目标。

    dst_dir=None 时为「拒绝型」目标（文件行占位）：_drop_allowed 恒 False，
    仅用于挡在根目录目标前——Flutter 拖拽命中取「最深同组目标」，若文件行
    无 DragTarget，命中会穿透到外层根目录 DragTarget，松手即把文件移入
    工作区根目录（拖拽抖动误移的根源）；占位目标把穿透挡在行内，
    悬停不高亮、松手静默忽略。

    高亮采用状态驱动：Flet 0.86 组件 render 中创建的控件构建后被冻结，
    禁止命令式 row.update()（抛 RuntimeError），故 on_will_accept/on_leave
    仅调用 set_highlight 更新 Sidebar 的 drop_hover_dir state，由渲染层
    根据该 state 计算行背景（VSCode 资源管理器直觉的合法目标高亮）。
    非法目标（自身/子孙/原地）不高亮，松手静默忽略。
    """
    def _src_of(e) -> str | None:
        src = getattr(e, "src", None)
        return drag_registry.get(id(src)) if src is not None else None

    def on_will_accept(e):
        src = _src_of(e)
        if src is not None and _drop_allowed(src, dst_dir):
            set_highlight(dst_dir)
        else:
            set_highlight(None)

    def on_leave(e):
        set_highlight(None)

    def on_accept(e):
        set_highlight(None)
        src = _src_of(e)
        if src is not None and _drop_allowed(src, dst_dir):
            on_drop(src, dst_dir)

    return ft.DragTarget(
        group=_DRAG_GROUP,
        content=content,
        on_will_accept=on_will_accept,
        on_leave=on_leave,
        on_accept=on_accept,
        key=key,
    )


def _wrap_row_gesture(row: ft.Control, key: str | None = None) -> ft.GestureDetector:
    """行级「竞技场占位」GestureDetector（拖拽模式下行的最外层包装）。

    背景（Flet 0.86 / Flutter 实测行为）：DragTarget 会吞掉其子孙
    GestureDetector 的右键 tap 识别器——行内各 GD 的 secondary_tap_down
    均无法送达 Python；行级菜单靠 ContextMenu 原生 down 触发（Listener
    实现，不受影响）。但若行上无任何存活的 secondary tap 识别器，右键行
    时外层空白菜单 GD 将成为手势竞技场中唯一识别器而获胜，误弹空白菜单。

    此占位 GD 位于行 DragTarget 外层（识别器存活）、比外层空白菜单 GD
    更深（先加入竞技场，右键行时获胜），回调为空操作——实际菜单由行级
    ContextMenu 原生弹出，外层空白 GD 被竞技场拒绝，空白菜单只在真正
    的空白区域触发。key 供 ListView 按路径复用行实例（虚拟化 reconciliation）。
    """
    return ft.GestureDetector(
        content=row,
        on_secondary_tap_down=lambda _e: None,
        key=key,
    )


def _search_box(
    value: str,
    on_change: Callable[[str], None],
    placeholder: str,
    c,
    ref: ft.Ref | None = None,
    native_ref: ft.Ref | None = None,
) -> ft.Control:
    """侧边栏搜索/过滤输入框（下划线边框，紧凑）。

    ref：外部持有输入框控件引用，供 Ctrl+F 聚焦（App → Sidebar 桥接）。
    native_ref：外部输入焦点域 ref（非 None 时挂 on_focus/on_blur 跟踪，
    让 KeyDispatcher 把焦点在本输入框期间的按键与编辑器隔离）。
    """
    focus_h, blur_h = native_focus_hooks(native_ref)
    return ft.TextField(
        value=value,
        hint_text=placeholder,
        prefix_icon=ft.Icons.SEARCH,
        dense=True,
        border=ft.InputBorder.UNDERLINE,
        text_size=12,
        content_padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        on_change=lambda e: on_change(e.control.value or ""),
        on_focus=focus_h,
        on_blur=blur_h,
        ref=ref,
    )


def _empty_hint(text: str, c) -> ft.Control:
    """居中浅色提示。"""
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            value=text,
            size=12,
            color=c.muted,
            font_family=FONT_MAIN,
            text_align=ft.TextAlign.CENTER,
        ),
    )


def _list_item(
    content: ft.Control,
    c,
    on_click: Callable | None = None,
    indent: int = Spacing.XL,
    active: bool = False,
    key: str | None = None,
) -> ft.Control:
    """通用列表项：左侧缩进、hover ink 反馈。

    active=True 时以主题色半透明背景高亮（用于标记当前打开的文件）。
    key 透传至 Container，供 ListView 按唯一标识复用项实例。
    """
    return ft.Container(
        content=content,
        padding=ft.Padding.only(left=indent, top=Spacing.SM, bottom=Spacing.SM, right=Spacing.LG),
        on_click=on_click,
        ink=True,
        bgcolor=ft.Colors.with_opacity(0.12, c.link) if active else None,
        border_radius=Radius.LG,
        key=key,
    )
