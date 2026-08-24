"""底部状态栏：贯穿侧边栏 + 编辑区全宽。

从 main.py 的 _build_footer 抽出，封装为独立组件。显示侧边栏切换、脏标记、
文件名、光标行列、字数与字符数，以及自动保存 / 备份的轻量状态消息。

性能设计（组件内 state 隔离，仅本组件重渲染）：
- 光标位置 / 字数统计为 StatusBar 内部 use_state，由 App 经 status_ref 注入
  更新器 update_cursor / update_counts 调用 set_state。更新只触发 StatusBar
  本组件重渲染（~10 控件重建，微秒级），不波及 App / 编辑器，光标移动 / 打字
  不会重建编辑器控件树。
- 切标签 / 打开文件时 use_effect 按 document 身份重置 state，从新文档重算初值。
- 状态消息（"已自动保存" / "保存失败" 等）由父组件经 status_message prop
  传入，3 秒后自动清空（use_effect 计时器）。
- 其余低频控件（侧边栏切换、脏标记图标、文件名、换行/拆分开关）随 props 声明式更新。

注：Flet 0.86 声明式模型渲染后控件被冻结（Frozen controls cannot be updated），
故不能再用 ref.value=…; ref.update() 命令式改属性，须走 set_state 触发重渲染。
"""

import asyncio
import os
import re
from collections.abc import Awaitable, Callable

import flet as ft

from models import BlockType, Document
import parser
from styles import FONT_MAIN, Radius, Spacing, get_colors, only_border

# 中英文词数统计正则：英文连续字母数字下划线算一词，中文每字算一词
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")

# 状态消息类型 → 颜色映射
_STATUS_COLOR = {
    "info": None,    # 使用 c.muted
    "success": "#35C759",  # 绿色
    "warn": "#FF9F0A",     # 橙色
    "error": "#E5484D",    # 红色
}

# 状态消息显示时长（秒），超时后自动清空
_STATUS_TTL_SEC = 3.0


def _file_name(path: str | None) -> str:
    return os.path.basename(path) if path else "未命名.md"


def _compute_counts(document: Document) -> tuple[int, int, int, int]:
    """从 document 计算 (word_count, char_count, para_count, reading_min)。

    供状态栏初值渲染与防抖异步任务复用。
    """
    md_text = parser.serialize(document)
    char_count = len(md_text)
    word_count = len(_WORD_RE.findall(md_text))
    para_count = sum(
        1 for ln in document.lines
        if ln.block_type == BlockType.PARAGRAPH and (ln.raw or "").strip()
    )
    reading_min = max(1, round(word_count / 300)) if word_count > 0 else 0
    return word_count, char_count, para_count, reading_min


@ft.component
def StatusBar(
    document: Document,
    file_path: str | None,
    dirty: bool,
    sidebar_open: bool,
    theme_mode: ft.ThemeMode,
    on_toggle_sidebar: Callable[[], None],
    display_name: str | None = None,
    word_wrap: bool = True,
    on_toggle_word_wrap: Callable[[], None] | None = None,
    split_editor: bool = False,
    on_toggle_split_editor: Callable[[], None] | None = None,
    outline_open: bool = True,
    on_toggle_outline: Callable[[], None] | None = None,
    status_ref: ft.Ref | None = None,
    status_message: tuple[str, str] | None = None,
    on_status_clear: Callable[[], None] | None = None,
):
    """底部状态栏。

    光标位置 / 字数统计为本组件内部 use_state：App 经 status_ref 注入的更新器
    update_cursor / update_counts 调用 set_state，仅触发本组件重渲染，不波及
    App / 编辑器（避免光标移动触发编辑器控件树重建）。切标签时 use_effect 按
    document 身份重置，从新文档重算初值。

    status_message: (msg, kind) 元组，由 App 通过 set_status_message 写入 state。
    kind ∈ info/success/warn/error，影响颜色。显示 _STATUS_TTL_SEC 秒后由
    on_status_clear 回调清空（App 重置 state）。None 时隐藏状态消息。
    """
    c = get_colors(theme_mode)

    # 高频更新状态（仅本组件重渲染）：cursor_pos=(row,col)；counts=None 时从
    # document 派生初值，tuple 时用命令式更新值。
    cursor_pos, set_cursor_pos = ft.use_state((1, 1))
    counts, set_counts = ft.use_state(None)

    # document 变化（切标签 / 打开文件）时重置高频状态，从新文档重算初值。
    # 用 id(document) 作依赖：document 引用变即触发。
    def _reset_on_doc_change():
        set_cursor_pos((1, 1))
        set_counts(None)

    ft.use_effect(_reset_on_doc_change, [id(document)])

    # 状态消息自动清空计时器：status_message 变化时启动 _STATUS_TTL_SEC 秒倒计时，
    # 超时后调 on_status_clear 让 App 清空 state。无消息或无回调时不启动。
    def _auto_clear_status():
        if status_message is None or on_status_clear is None:
            return
        page = ft.context.page
        if page is None:
            return

        async def _delayed_clear():
            try:
                await asyncio.sleep(_STATUS_TTL_SEC)
                on_status_clear()
            except Exception:
                pass

        try:
            page.run_task(_delayed_clear)
        except Exception:
            pass

    # 依赖 status_message 元组身份（App 每次推送新元组），消息不变时不重启计时器
    ft.use_effect(_auto_clear_status, [status_message])

    # 字数统计初值：counts=None 从 document 算；否则用命令式更新值
    if counts is None:
        word_count, char_count, para_count, reading_min = _compute_counts(document)
    else:
        word_count, char_count, para_count, reading_min = counts

    row, col = cursor_pos
    # .lnk 快捷方式打开时显示链接文件名（file_path 为目标路径）
    fname = display_name or _file_name(file_path)

    # ============ 更新器注册 ============
    # 注册进 status_ref.current 供 App 调用：update_cursor / update_counts。
    # 更新器调 set_state（仅本组件重渲染），async 以满足 App 侧 await 契约。
    # render 期写入保证 status_ref 始终最新（同 tabs_ref.current = tabs 模式）。
    #
    # session 销毁防御：标签关闭 / 应用退出后，防抖中的 _do_count 协程（0.3s 后
    # 醒来）仍可能调用 update_counts/update_cursor，此时 set_state →
    # _schedule_update → page.session.schedule_update 抛 RuntimeError("destroyed
    # session")。组件卸载后的状态更新本就无意义，静默丢弃即可（与 _run_task_safe
    # 在调度前检查 session 的防御互补：这里覆盖协程执行中 session 销毁的窗口）。
    if status_ref is not None:
        async def _update_cursor(r: int, cc: int):
            try:
                set_cursor_pos((r, cc))
            except RuntimeError:
                pass

        async def _update_counts(w: int, ch: int, pa: int, rm: int):
            try:
                set_counts((w, ch, pa, rm))
            except RuntimeError:
                pass

        status_ref.current = _StatusBarUpdaters(_update_cursor, _update_counts)

    # 状态消息控件：仅 status_message 非空时显示
    status_msg_text = ""
    status_msg_color = c.muted
    if status_message is not None:
        status_msg_text = status_message[0]
        kind = status_message[1] if len(status_message) > 1 else "info"
        status_msg_color = _STATUS_COLOR.get(kind, None) or c.muted

    status_widget = (
        ft.Container(
            content=ft.Text(
                value=status_msg_text,
                size=11,
                color=status_msg_color,
                font_family=FONT_MAIN,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
            border_radius=ft.BorderRadius.all(4),
            bgcolor=ft.Colors.with_opacity(0.06, status_msg_color),
            visible=bool(status_msg_text),
        )
        if status_msg_text
        else ft.Container(width=0, height=0)
    )

    def _compact_icon_btn(icon: str, tooltip: str, on_click, color: str) -> ft.Control:
        """紧凑图标按钮：固定 22px 触控区，替代 IconButton（默认 48px 最小尺寸
        会把状态栏撑高到 ~46px）。ink 水波反馈 + tooltip，桌面状态栏直觉。"""
        return ft.Container(
            width=22,
            height=22,
            border_radius=Radius.SM,
            alignment=ft.Alignment.CENTER,
            ink=True,
            tooltip=tooltip,
            on_click=lambda e: on_click() if on_click else None,
            content=ft.Icon(icon, size=14, color=color),
        )

    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.03, c.text),
        border=only_border(top=ft.BorderSide(1, c.border)),
        # 紧凑高度：垂直内边距压到最小，文字/图标缩小，保持界面紧凑
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.XS),
        content=ft.Row(
            controls=[
                _compact_icon_btn(
                    ft.Icons.VIEW_SIDEBAR if not sidebar_open else ft.Icons.MENU_OPEN,
                    "切换侧边栏",
                    on_toggle_sidebar,
                    c.link if sidebar_open else c.muted,
                ),
                ft.Icon(
                    icon=ft.Icons.CIRCLE,
                    size=8,
                    color="#FF9F0A" if document.dirty else "#35C759",
                ),
                ft.Text(
                    value=fname,
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Container(width=Spacing.LG),
                status_widget,
                ft.Container(expand=True),
                ft.Text(
                    value=f"行 {row}  列 {col}",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=Spacing.LG),
                ft.Container(
                    content=ft.Text(
                        value="换行: 开" if word_wrap else "换行: 关",
                        size=11,
                        color=c.link if word_wrap else c.muted,
                        font_family=FONT_MAIN,
                    ),
                    on_click=lambda e: on_toggle_word_wrap() if on_toggle_word_wrap else None,
                    ink=True,
                    tooltip="自动换行 (Alt+Z)",
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
                    border_radius=ft.BorderRadius.all(4),
                ),
                ft.Container(width=Spacing.LG),
                ft.Container(
                    content=ft.Text(
                        value="拆分: 开" if split_editor else "拆分: 关",
                        size=11,
                        color=c.link if split_editor else c.muted,
                        font_family=FONT_MAIN,
                    ),
                    on_click=lambda e: on_toggle_split_editor() if on_toggle_split_editor else None,
                    ink=True,
                    tooltip="向右拆分编辑器 (Ctrl+\\)",
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
                    border_radius=ft.BorderRadius.all(4),
                ),
                ft.Container(width=Spacing.LG),
                ft.Text(
                    value=f"{para_count} 段",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=Spacing.LG),
                ft.Text(
                    value=f"{word_count} 词",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=Spacing.MD),
                ft.Text(
                    value=f"{char_count} 字符",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=Spacing.MD),
                ft.Text(
                    value=f"阅读 {reading_min} min" if reading_min > 0 else "阅读 0 min",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MAIN,
                ),
                ft.Container(width=Spacing.LG),
                # 大纲开合入口（最右侧，与左侧「切换侧边栏」对称）：
                # 参考侧边栏切换的交互直觉，收起/展开第四列大纲
                _compact_icon_btn(
                    ft.Icons.FORMAT_LIST_BULLETED,
                    "切换大纲",
                    on_toggle_outline,
                    c.link if outline_open else c.muted,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


class _StatusBarUpdaters:
    """状态栏更新器容器，写入 status_ref.current 供 App 调用。

    update_cursor(row, col) / update_counts(word, char, para, reading) 均为
    async：调 set_state 触发仅本组件重渲染（不波及 App / 编辑器）。async 以
    满足 App 侧 await 契约。
    """

    __slots__ = ("update_cursor", "update_counts")

    def __init__(self, update_cursor, update_counts):
        self.update_cursor = update_cursor
        self.update_counts = update_counts
