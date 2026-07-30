"""底部状态栏：贯穿侧边栏 + 编辑区全宽。

从 main.py 的 _build_footer 抽出，封装为独立组件。显示侧边栏切换、脏标记、
文件名、光标行列、字数与字符数。

性能设计（命令式局部更新，跳过 set_state 全量重建）：
- 5 个高频 Text（cursor/word/char/para/reading）各挂 ft.use_ref，由 App 经
  status_ref 注入命令式更新器 update_cursor / update_counts，直接改 .value
  + await control.update()，避免光标移动/打字时整棵状态栏重建。
- 首次渲染 / 切标签 / 切主题 时仍从 document 声明式算初值，保证正确性；
  其后高频增量由命令式更新器覆盖。
- 其余低频控件（侧边栏切换、脏标记图标、文件名、换行/拆分开关）保持声明式。
"""

import contextlib
import os
import re
from collections.abc import Awaitable, Callable

import flet as ft

from models import BlockType, Document
import parser
from styles import FONT_MAIN, Spacing, get_colors, only_border

# 中英文词数统计正则：英文连续字母数字下划线算一词，中文每字算一词
_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


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
    word_wrap: bool = True,
    on_toggle_word_wrap: Callable[[], None] | None = None,
    split_editor: bool = False,
    on_toggle_split_editor: Callable[[], None] | None = None,
    status_ref: ft.Ref | None = None,
):
    """底部状态栏。

    cursor_row_col 已移除：光标位置改由 App 经 status_ref 命令式更新器
    update_cursor(row, col) 实时推送，避免光标移动触发整页重建。
    字数/字符/段数/阅读时长同理由 update_counts 命令式推送（防抖）。
    """
    c = get_colors(theme_mode)

    # 高频 Text 控件引用：命令式局部 update 的锚点
    cursor_text_ref = ft.use_ref(None)
    word_text_ref = ft.use_ref(None)
    char_text_ref = ft.use_ref(None)
    para_text_ref = ft.use_ref(None)
    reading_text_ref = ft.use_ref(None)

    # 初值（首屏 / 切标签 / 切主题 声明式渲染保证正确，命令式更新器随后覆盖）
    word_count, char_count, para_count, reading_min = _compute_counts(document)
    fname = _file_name(file_path)

    # ============ 命令式更新器注册 ============
    # 注册进 status_ref.current 供 App 调用：update_cursor / update_counts。
    # 更新器闭包捕获 use_ref 对象（稳定），调用时读 .current 拿最新控件实例，
    # 故主题切换重建 Text 后仍指向新实例。render 期写入保证 status_ref 始终最新
    # （同 tabs_ref.current = tabs 的代码库模式）。
    if status_ref is not None:
        async def _update_cursor(row: int, col: int):
            ref = cursor_text_ref.current
            if ref is None:
                return
            ref.value = f"行 {row}  列 {col}"
            with contextlib.suppress(Exception):
                await ref.update()

        async def _update_counts(w: int, ch: int, pa: int, rm: int):
            for ref, val in (
                (word_text_ref.current, f"{w} 词"),
                (char_text_ref.current, f"{ch} 字符"),
                (para_text_ref.current, f"{pa} 段"),
                (reading_text_ref.current, f"阅读 {rm} min" if rm > 0 else "阅读 0 min"),
            ):
                if ref is not None:
                    ref.value = val
            # 4 个控件一次性 patch（逐个 await update）
            for ref in (word_text_ref, char_text_ref, para_text_ref, reading_text_ref):
                ctrl = ref.current
                if ctrl is not None:
                    with contextlib.suppress(Exception):
                        await ctrl.update()

        status_ref.current = _StatusBarUpdaters(_update_cursor, _update_counts)

    return ft.Container(
        bgcolor=ft.Colors.with_opacity(0.03, c.text),
        border=only_border(top=ft.BorderSide(1, c.border)),
        padding=ft.Padding.symmetric(horizontal=Spacing.XL, vertical=Spacing.LG),
        content=ft.Row(
            controls=[
                ft.IconButton(
                    icon=ft.Icons.VIEW_SIDEBAR if not sidebar_open else ft.Icons.MENU_OPEN,
                    tooltip="切换侧边栏",
                    on_click=lambda e: on_toggle_sidebar(),
                    icon_size=16,
                    style=ft.ButtonStyle(
                        color=c.link if sidebar_open else c.muted,
                        padding=Spacing.SM,
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
                    value="行 1  列 1",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    ref=cursor_text_ref,
                ),
                ft.Container(width=Spacing.XXL),
                ft.Container(
                    content=ft.Text(
                        value="换行: 开" if word_wrap else "换行: 关",
                        size=12,
                        color=c.link if word_wrap else c.muted,
                        font_family=FONT_MAIN,
                    ),
                    on_click=lambda e: on_toggle_word_wrap() if on_toggle_word_wrap else None,
                    ink=True,
                    tooltip="自动换行 (Alt+Z)",
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
                    border_radius=ft.BorderRadius.all(4),
                ),
                ft.Container(width=Spacing.XXL),
                ft.Container(
                    content=ft.Text(
                        value="拆分: 开" if split_editor else "拆分: 关",
                        size=12,
                        color=c.link if split_editor else c.muted,
                        font_family=FONT_MAIN,
                    ),
                    on_click=lambda e: on_toggle_split_editor() if on_toggle_split_editor else None,
                    ink=True,
                    tooltip="向右拆分编辑器 (Ctrl+\\)",
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=Spacing.XS),
                    border_radius=ft.BorderRadius.all(4),
                ),
                ft.Container(width=Spacing.XXL),
                ft.Text(
                    value=f"{para_count} 段",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    ref=para_text_ref,
                ),
                ft.Container(width=Spacing.XXL),
                ft.Text(
                    value=f"{word_count} 词",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    ref=word_text_ref,
                ),
                ft.Container(width=Spacing.XL),
                ft.Text(
                    value=f"{char_count} 字符",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    ref=char_text_ref,
                ),
                ft.Container(width=Spacing.XL),
                ft.Text(
                    value=f"阅读 {reading_min} min" if reading_min > 0 else "阅读 0 min",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    ref=reading_text_ref,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


class _StatusBarUpdaters:
    """状态栏命令式更新器容器，写入 status_ref.current 供 App 调用。

    update_cursor(row, col) / update_counts(word, char, para, reading) 均为
    async：直接改 Text.value + await control.update()，跳过 set_state 全量重建。
    """

    __slots__ = ("update_cursor", "update_counts")

    def __init__(self, update_cursor, update_counts):
        self.update_cursor = update_cursor
        self.update_counts = update_counts
