"""Git 侧边栏面板：源代码管理（更改）与历史记录两个视图。

对标 VS Code 的源代码管理视图，并按需补齐 VS Code 之外的两项要求：
「历史记录面板」（含作者 / 文件名 / 关键词筛选）与「仓库一键初始化」。

结构（自上而下）：

    ┌ 仓库头：分支按钮（含领先/落后徽章） · 获取/拉取/推送 · ⋯ 更多
    ├ 提交框：多行信息输入 + [提交暂存区] [提交所有更改] + 「提交后自动推送」
    ├ （更改视图）过滤框 + 合并冲突 / 暂存的更改 / 更改 三个可折叠分区
    └ （历史视图）作者 / 关键词 / 文件三个筛选框 + 分页提交列表

状态划分（性能与正确性双重考虑）：
- **App 侧状态**（props 传入）：仓库探测结果、git status、分支、历史列表与其
  分页游标、打开的 diff、忙碌标志等——它们是异步任务的结果，必须由 App 持有
  才能与状态栏、活动栏角标共享。
- **面板局部状态**（组件内 use_state）：提交信息草稿、过滤词、分区折叠。
  刻意留在局部，避免每敲一个字都触发 App 全量重渲染（编辑器控件树很重）。
  提交信息同时镜像进 ``commit_ref``，供 Ctrl+Enter 等全局快捷键读取最新值。
"""

from __future__ import annotations

import flet as ft

from services.git.models import ChangeKind, FileChange, GitStatus
from services.git.repository import GitRepository
from styles import FONT_MAIN, FONT_MONO, Radius, Spacing, get_colors, git_status_color
from views._widgets import _empty_hint
from views.git_widgets import (
    file_row,
    filter_box,
    git_icon_button,
    git_text_button,
    letter_badge,
    multiline_box,
    section_header,
)

__all__ = ["VIEW_CHANGES", "VIEW_HISTORY", "GitPanel", "git_panel_props"]

VIEW_CHANGES = "changes"
VIEW_HISTORY = "history"

#: 分区标题常量（同时作为折叠集合的键）
_SEC_STAGED = "staged"
_SEC_UNSTAGED = "unstaged"
_SEC_CONFLICTS = "conflicts"
_SEC_UNTRACKED = "untracked"


def git_panel_props(ctx) -> tuple[dict, dict]:
    """把 ``AppContext`` 的 Git 状态与回调打包成 ``(state, actions)``。

    与 ``views/global_menu.build_global_menu(ctx, ...)`` 同一模式：读 ctx 组装
    控件所需的数据，不构造控件。差异在于本面板需要组件内局部状态，故这里只
    提供「状态 + 回调」两本字典。
    """
    state = {
        "available": ctx.git_available,
        "version": ctx.git_version,
        "workspace": ctx.git_workspace,
        "root": ctx.git_root,
        "status": ctx.git_status,
        "error": ctx.git_error,
        "busy": ctx.git_busy,
        "view": ctx.git_view,
        "branches": ctx.git_branches,
        "history": ctx.git_history,
        "history_has_more": ctx.git_history_has_more,
        "history_loading": ctx.git_history_loading,
        "history_filter": ctx.git_history_filter,
        "commit_details": ctx.git_commit_details,
        "expanded_commits": ctx.git_expanded_commits,
        "commit_push": ctx.git_commit_push,
        "commit_seq": ctx.git_commit_seq,
        "commit_ref": ctx.git_commit_message_ref,
        "active_path": ctx.git_active_path,
        # 外部输入焦点域 ref：提交框 / 过滤框聚焦时置 token，令 KeyDispatcher
        # 不把文档编辑快捷键作用到编辑器（见 views/native_scope 文档）。
        "native_ref": getattr(ctx, "native_input_ref", None),
    }
    actions = {
        "refresh": ctx.git_refresh,
        "init_repo": ctx.git_init_repo,
        "set_view": ctx.git_set_view,
        "stage": ctx.git_stage,
        "unstage": ctx.git_unstage,
        "stage_all": ctx.git_stage_all,
        "unstage_all": ctx.git_unstage_all,
        "discard": ctx.git_discard,
        "discard_all": ctx.git_discard_all,
        "open_diff": ctx.git_open_diff,
        "commit": ctx.git_commit,
        "pull": ctx.git_pull,
        "push": ctx.git_push,
        "fetch": ctx.git_fetch,
        "undo_commit": ctx.git_undo_commit,
        "open_branch_dialog": ctx.git_open_branch_dialog,
        "load_history": ctx.git_load_history,
        "load_more_history": ctx.git_load_more_history,
        "toggle_commit": ctx.git_toggle_commit,
        "set_history_filter": ctx.git_set_history_filter,
        "clear_history_filter": ctx.git_clear_history_filter,
        "history_file_diff": ctx.git_history_file_diff,
        "open_in_editor": ctx.git_open_in_editor,
        "jump_to_conflict": ctx.git_jump_to_conflict,
        "toggle_commit_push": ctx.git_toggle_commit_push,
        "abort_operation": ctx.git_abort_operation,
        "open_file_history": ctx.git_open_file_history,
    }
    return state, actions


# ---------------------------------------------------------------------------
# 小组件
# ---------------------------------------------------------------------------


def _branch_button(state: dict, actions: dict, c) -> ft.Control:
    """分支按钮：``⎇ main ↑2 ↓1``，点击唤起分支管理面板。"""
    st: GitStatus | None = state.get("status")
    branch = (st.branch if st else None) or ("（分离 HEAD）" if st and st.detached else "无分支")
    badges: list[ft.Control] = []
    if st and st.ahead:
        badges.append(
            ft.Text(f"↑{st.ahead}", size=10, color=git_status_color("added", c),
                    font_family=FONT_MONO, tooltip=f"领先远端 {st.ahead} 个提交")
        )
    if st and st.behind:
        badges.append(
            ft.Text(f"↓{st.behind}", size=10, color=git_status_color("deleted", c),
                    font_family=FONT_MONO, tooltip=f"落后远端 {st.behind} 个提交")
        )
    return ft.Container(
        height=24,
        border_radius=Radius.SM,
        padding=ft.Padding.symmetric(horizontal=Spacing.MD),
        ink=True,
        tooltip="切换分支 / 新建分支",
        on_click=lambda e: actions["open_branch_dialog"](),
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.ACCOUNT_TREE, size=13, color=c.link),
                ft.Text(
                    branch,
                    size=11,
                    color=c.text,
                    font_family=FONT_MAIN,
                    weight=ft.FontWeight.W_600,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                *badges,
            ],
            spacing=Spacing.SM,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def _more_menu(state: dict, actions: dict, c, on_view_change) -> ft.Control:
    """⋯ 更多菜单：视图切换、撤销提交、中止合并、初始化仓库、刷新。"""
    st: GitStatus | None = state.get("status")
    has_repo = bool(state.get("root"))
    items: list[ft.PopupMenuItem] = [
        ft.PopupMenuItem(
            content="更改视图" if state.get("view") == VIEW_HISTORY else "历史记录",
            icon=ft.Icons.HISTORY if state.get("view") == VIEW_CHANGES else ft.Icons.EDIT_NOTE,
            on_click=lambda e: on_view_change(
                VIEW_CHANGES if state.get("view") == VIEW_HISTORY else VIEW_HISTORY
            ),
        ),
        ft.PopupMenuItem(),
        ft.PopupMenuItem(
            content="撤销上一次提交",
            icon=ft.Icons.UNDO,
            disabled=not has_repo,
            on_click=lambda e: actions["undo_commit"](),
        ),
    ]
    if st is not None and st.in_operation:
        items.append(
            ft.PopupMenuItem(
                content=f"中止{GitRepository.operation_label(st.op)}",
                icon=ft.Icons.CANCEL,
                on_click=lambda e: actions["abort_operation"](),
            )
        )
    items.extend(
        [
            ft.PopupMenuItem(),
            ft.PopupMenuItem(
                content="刷新状态",
                icon=ft.Icons.REFRESH,
                on_click=lambda e: actions["refresh"](),
            ),
        ]
    )
    if state.get("workspace") and not has_repo:
        items.append(
            ft.PopupMenuItem(
                content="初始化仓库…",
                icon=ft.Icons.PLAYLIST_ADD,
                on_click=lambda e: actions["init_repo"](),
            )
        )
    return ft.PopupMenuButton(
        items=items,
        tooltip="更多 Git 操作",
        icon=ft.Icons.MORE_VERT,
        icon_size=16,
    )


def _sync_bar(state: dict, actions: dict, c) -> ft.Control:
    """获取 / 拉取 / 推送 三个核心同步入口。"""
    busy = state.get("busy", False)
    has_repo = bool(state.get("root"))
    disabled = busy or not has_repo
    return ft.Row(
        controls=[
            git_text_button(
                "获取", "获取远端更新（不改动本地分支）",
                actions["fetch"] if not disabled else None, c,
                icon=ft.Icons.CLOUD_SYNC,
            ),
            git_text_button(
                "拉取", "拉取并合并远端更新（Pull）",
                actions["pull"] if not disabled else None, c,
                icon=ft.Icons.CLOUD_DOWNLOAD,
            ),
            git_text_button(
                "推送", "推送本地提交到远端（Push）",
                actions["push"] if not disabled else None, c,
                icon=ft.Icons.CLOUD_UPLOAD,
            ),
        ],
        spacing=Spacing.SM,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _commit_box(state: dict, actions: dict, c, message: str, set_message) -> ft.Control:
    """提交信息输入 + 两个提交按钮 + 自动推送开关。

    - 「提交暂存区」：只提交已暂存内容（``git commit``）
    - 「提交所有更改」：等价 ``git commit -a``（含已跟踪文件的未暂存修改）
    - 「提交后自动推送」：勾选后提交成功即执行 push（VS Code 的 sync 行为）
    """
    st: GitStatus | None = state.get("status")
    staged_count = st.staged_count if st else 0
    changed_count = st.unstaged_count if st else 0
    busy = state.get("busy", False)

    can_commit_staged = bool(staged_count) and not busy
    can_commit_all = bool(staged_count or changed_count) and not busy and st is not None and not st.is_clean

    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.LG, right=Spacing.LG, top=Spacing.MD, bottom=Spacing.MD
        ),
        content=ft.Column(
            controls=[
                multiline_box(
                    message,
                    set_message,
                    "提交信息（Ctrl+Enter 提交）",
                    c,
                    native_ref=state.get("native_ref"),
                ),
                ft.Container(height=Spacing.SM),
                ft.Row(
                    controls=[
                        git_text_button(
                            f"提交暂存区 ({staged_count})",
                            "提交已暂存的更改",
                            (lambda: actions["commit"](False)) if can_commit_staged else None,
                            c,
                            icon=ft.Icons.CHECK,
                            expand=True,
                        ),
                        git_text_button(
                            "提交所有更改",
                            "提交所有更改（含未暂存，等价 git commit -a）",
                            (lambda: actions["commit"](True)) if can_commit_all else None,
                            c,
                            icon=ft.Icons.DONE_ALL,
                            expand=True,
                        ),
                    ],
                    spacing=Spacing.SM,
                ),
                ft.Container(height=Spacing.XS),
                ft.Container(
                    ink=True,
                    on_click=lambda e: actions["toggle_commit_push"](),
                    border_radius=Radius.SM,
                    padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=2),
                    tooltip="提交成功后自动推送到远端",
                    content=ft.Row(
                        controls=[
                            ft.Icon(
                                ft.Icons.CHECK_BOX if state.get("commit_push")
                                else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
                                size=13,
                                color=c.link if state.get("commit_push") else c.muted,
                            ),
                            ft.Text(
                                "提交后自动推送",
                                size=11,
                                color=c.text if state.get("commit_push") else c.muted,
                                font_family=FONT_MAIN,
                            ),
                        ],
                        spacing=Spacing.SM,
                        tight=True,
                    ),
                ),
            ],
            spacing=0,
        ),
    )


def _entry_actions(
    entry,
    *,
    staged: bool,
    actions: dict,
    c,
    busy: bool,
) -> list[ft.Control]:
    """单个文件行的右侧操作按钮（暂存 / 取消暂存 / 丢弃）。"""
    on_click_none = None if busy else True
    name = entry.basename
    if staged:
        return [
            git_icon_button(
                ft.Icons.REMOVE,
                f"取消暂存 {name}",
                (lambda: actions["unstage"]([entry.path])) if on_click_none else None,
                c.muted,
                size=13,
            ),
        ]
    return [
        git_icon_button(
            ft.Icons.UNDO,
            f"丢弃 {name} 的更改",
            (lambda: actions["discard"](entry.path)) if on_click_none else None,
            c.muted,
            size=13,
        ),
        git_icon_button(
            ft.Icons.ADD,
            f"暂存 {name}",
            (lambda: actions["stage"]([entry.path])) if on_click_none else None,
            c.link,
            size=13,
        ),
    ]


def _file_list(
    entries,
    *,
    staged: bool,
    actions: dict,
    c,
    busy: bool,
) -> ft.Control:
    rows: list[ft.Control] = []
    for entry in entries:
        subtitle = None
        if entry.orig_path and entry.orig_path != entry.path:
            subtitle = f"原路径：{entry.orig_path}"
        rows.append(
            file_row(
                name=entry.basename,
                dirname=entry.dirname,
                letter=entry.letter,
                kind=entry.kind,
                c=c,
                on_click=(lambda p=entry.path, s=staged: actions["open_diff"](p, s, False)),
                tooltip=entry.display_name,
                subtitle=subtitle,
                actions=_entry_actions(entry, staged=staged, actions=actions, c=c, busy=busy),
            )
        )
    return ft.Column(controls=rows, spacing=0)


def _collapsible(
    key: str,
    title: str,
    count: int,
    *,
    collapsed: frozenset,
    set_collapsed,
    actions: dict,
    c,
    header_actions: list[ft.Control] | None = None,
) -> list[ft.Control]:
    """折叠分区头；返回 [header, （未折叠时的内容占位）]。

    内容由调用方在返回后追加（保持「头 + 列表」的平铺结构，避免嵌套
    Column 影响 ListView 的虚拟化与滚动）。
    """

    def _toggle():
        cur = set(collapsed)
        cur.discard(key) if key in cur else cur.add(key)
        set_collapsed(frozenset(cur))

    return [
        section_header(
            title,
            count,
            expanded=key not in collapsed,
            on_toggle=_toggle,
            c=c,
            actions=header_actions,
        )
    ]


# ---------------------------------------------------------------------------
# 各视图
# ---------------------------------------------------------------------------


def _render_changes_view(
    state: dict,
    actions: dict,
    c,
    *,
    filter_text: str,
    set_filter_text,
    message: str,
    set_message,
    collapsed: frozenset,
    set_collapsed,
) -> ft.Control:
    """「更改」视图：提交框 + 过滤 + 三个分区。"""
    st: GitStatus | None = state.get("status")
    busy = state.get("busy", False)
    text = filter_text.strip().lower()

    def _match(entry) -> bool:
        if not text:
            return True
        return text in entry.path.lower() or text in entry.basename.lower()

    conflicted = [e for e in (st.conflicted if st else []) if _match(e)]
    staged = [e for e in (st.staged if st else []) if _match(e) and not e.is_conflicted]
    unstaged = [e for e in (st.unstaged if st else []) if _match(e) and not e.is_conflicted]

    rows: list[ft.Control] = [
        _commit_box(state, actions, c, message, set_message),
        ft.Container(height=1, bgcolor=c.border),
        ft.Container(
            padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
            content=ft.Row(
                controls=[
                    ft.Container(
                        expand=True,
                        content=filter_box(
                            filter_text, set_filter_text, "过滤变更文件…", c,
                            native_ref=state.get("native_ref"),
                        ),
                    ),
                    git_icon_button(
                        ft.Icons.REFRESH, "刷新状态", actions["refresh"], c.muted, size=13
                    ),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ),
    ]

    any_section = False

    # ---- 合并冲突（最高优先级）----
    if conflicted:
        any_section = True
        rows.extend(
            _collapsible(
                _SEC_CONFLICTS, "合并冲突", len(conflicted),
                collapsed=collapsed, set_collapsed=set_collapsed, actions=actions, c=c,
                header_actions=[
                    git_icon_button(
                        ft.Icons.CALL_MERGE,
                        "中止进行中的合并/变基",
                        actions["abort_operation"] if st and st.in_operation else None,
                        c.muted, size=13,
                    )
                ],
            )
        )
        if _SEC_CONFLICTS not in collapsed:
            conflict_rows: list[ft.Control] = []
            for entry in conflicted:
                # 默认参数绑定 entry：循环变量的闭包默认晚绑定，不绑定会把所有
                # 行的回调都指向最后一个冲突文件。
                def _open(p=entry.path):
                    return actions["jump_to_conflict"](p)

                def _mark(p=entry.path):
                    return actions["stage"]([p])

                conflict_rows.append(
                    file_row(
                        name=entry.basename,
                        dirname=entry.dirname,
                        letter="!",
                        kind=ChangeKind.CONFLICTED,
                        c=c,
                        on_click=_open,
                        tooltip=f"{entry.display_name}（点击跳转到冲突位置）",
                        subtitle="存在冲突标记，点击跳转到冲突位置",
                        actions=[
                            git_icon_button(
                                ft.Icons.ADD, f"标记 {entry.basename} 已解决（暂存）",
                                _mark if not busy else None,
                                c.link, size=13,
                            ),
                        ],
                    )
                )
            rows.extend(conflict_rows)

    # ---- 暂存的更改 ----
    if staged or not any_section:
        any_section = True
        rows.extend(
            _collapsible(
                _SEC_STAGED, "暂存的更改", len(staged),
                collapsed=collapsed, set_collapsed=set_collapsed, actions=actions, c=c,
                header_actions=[
                    git_icon_button(
                        ft.Icons.REMOVE_DONE,
                        "全部取消暂存",
                        (lambda: actions["unstage_all"]()) if staged and not busy else None,
                        c.muted, size=13,
                    ),
                ],
            )
        )
        if _SEC_STAGED not in collapsed:
            if staged:
                rows.append(_file_list(staged, staged=True, actions=actions, c=c, busy=busy))
            else:
                rows.append(
                    ft.Container(
                        padding=ft.Padding.only(left=Spacing.XL, top=2, bottom=Spacing.SM),
                        content=ft.Text(
                            "暂无暂存的更改", size=11, color=c.muted, font_family=FONT_MAIN
                        ),
                    )
                )

    # ---- 更改（未暂存 + 未跟踪）----
    rows.extend(
        _collapsible(
            _SEC_UNSTAGED, "更改", len(unstaged),
            collapsed=collapsed, set_collapsed=set_collapsed, actions=actions, c=c,
            header_actions=[
                git_icon_button(
                    ft.Icons.UNDO,
                    "丢弃所有工作区更改",
                    (lambda: actions["discard_all"]()) if unstaged and not busy else None,
                    c.muted, size=13,
                ),
                git_icon_button(
                    ft.Icons.ADD,
                    "全部暂存",
                    (lambda: actions["stage_all"]()) if unstaged and not busy else None,
                    c.link, size=13,
                ),
            ],
        )
    )
    if _SEC_UNSTAGED not in collapsed:
        if unstaged:
            rows.append(_file_list(unstaged, staged=False, actions=actions, c=c, busy=busy))
        else:
            rows.append(
                ft.Container(
                    padding=ft.Padding.only(left=Spacing.XL, top=2, bottom=Spacing.SM),
                    content=ft.Text(
                        "没有检测到更改" if not text else "无匹配的变更文件",
                        size=11, color=c.muted, font_family=FONT_MAIN,
                    ),
                )
            )

    return ft.Column(controls=rows, spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)


def _render_history_filters(state: dict, actions: dict, c, local: dict) -> ft.Control:
    """历史筛选：作者 / 关键词 / 文件名，各自「回车生效」。

    输入变化即时写入 App 侧筛选条件（``set_history_filter``），但**不**触发查询
    ——git log 是有成本的子进程调用，逐字符查询会让输入明显掉帧；回车或「应用」
    按钮才真正加载。
    """
    applied = state.get("history_filter") or {}

    def _on_change(field: str, setter):
        def _handler(value: str):
            setter(value)
            actions["set_history_filter"](field, value)

        return _handler

    submit = lambda: actions["load_history"]()  # noqa: E731 - 单表达式回调
    return ft.Container(
        padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Container(
                            expand=True,
                            content=filter_box(
                                local["author"], _on_change("author", local["set_author"]),
                                "按作者筛选…", c,
                                native_ref=state.get("native_ref"),
                                on_submit=submit,
                            ),
                        ),
                    ],
                    spacing=Spacing.SM,
                ),
                ft.Row(
                    controls=[
                        ft.Container(
                            expand=True,
                            content=filter_box(
                                local["keyword"], _on_change("keyword", local["set_keyword"]),
                                "按提交信息筛选（回车生效）…", c,
                                native_ref=state.get("native_ref"),
                                on_submit=submit,
                            ),
                        ),
                    ],
                    spacing=Spacing.SM,
                ),
                ft.Row(
                    controls=[
                        ft.Container(
                            expand=True,
                            content=filter_box(
                                local["path"], _on_change("path", local["set_path"]),
                                "按文件名筛选…", c,
                                native_ref=state.get("native_ref"),
                                on_submit=submit,
                            ),
                        ),
                        git_icon_button(
                            ft.Icons.PLAY_ARROW, "应用筛选", actions["load_history"], c.link, size=13
                        ),
                        git_icon_button(
                            ft.Icons.CLEAR_ALL, "清除筛选", actions["clear_history_filter"], c.muted, size=13
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                *(
                    [
                        ft.Row(
                            controls=[
                                ft.Text(
                                    "已应用筛选：" + "、".join(
                                        f"{k}={v}" for k, v in applied.items() if v
                                    ),
                                    size=10,
                                    color=c.link,
                                    font_family=FONT_MAIN,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                )
                            ]
                        )
                    ]
                    if any(applied.values())
                    else []
                ),
            ],
            spacing=Spacing.XS,
        ),
    )


def _render_history_view(
    state: dict,
    actions: dict,
    c,
    local: dict,
) -> ft.Control:
    """「历史」视图：筛选 + 倒序提交列表（分页加载，点击展开变更文件）。"""
    history = state.get("history") or []
    details = state.get("commit_details") or {}
    expanded = state.get("expanded_commits") or frozenset()

    rows: list[ft.Control] = [
        ft.Container(
            padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.HISTORY, size=14, color=c.muted),
                    ft.Text(
                        "提交历史",
                        size=11,
                        color=c.text,
                        font_family=FONT_MAIN,
                        weight=ft.FontWeight.W_700,
                        expand=True,
                    ),
                    ft.Text(
                        f"{len(history)} 条",
                        size=10,
                        color=c.muted,
                        font_family=FONT_MONO,
                    ),
                    git_icon_button(
                        ft.Icons.REFRESH, "重新加载历史", actions["load_history"], c.muted, size=13
                    ),
                ],
                spacing=Spacing.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ),
        _render_history_filters(state, actions, c, local),
        ft.Container(height=1, bgcolor=c.border),
    ]

    if state.get("history_loading") and not history:
        rows.append(_empty_hint("正在加载提交历史…", c))
        return ft.Column(controls=rows, spacing=0, expand=True)

    if not history:
        rows.append(
            _empty_hint(
                "暂无提交记录" if not state.get("root")
                else "没有符合条件的提交\n可调整上方筛选条件",
                c,
            )
        )
        return ft.Column(controls=rows, spacing=0, expand=True)

    items: list[ft.Control] = []
    for commit in history:
        is_open = commit.sha in expanded
        files = details.get(commit.sha)
        items.append(_commit_row(commit, is_open, actions, c))
        if is_open and files is not None:
            for f in files:
                items.append(_commit_file_row(commit, f, actions, c))
        elif is_open:
            items.append(
                ft.Container(
                    padding=ft.Padding.only(left=Spacing.XXXL, top=2, bottom=Spacing.SM),
                    content=ft.Text("正在加载变更文件…", size=10, color=c.muted,
                                    font_family=FONT_MAIN),
                )
            )

    rows.append(
        ft.ListView(
            controls=items,
            spacing=0,
            expand=True,
            padding=ft.Padding.symmetric(vertical=Spacing.XS),
        )
    )
    if state.get("history_has_more"):
        rows.append(
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
                content=ft.Row(
                    controls=[
                        git_text_button(
                            "加载更多" if not state.get("history_loading") else "加载中…",
                            "继续加载更早的提交",
                            actions["load_more_history"] if not state.get("history_loading") else None,
                            c,
                            icon=ft.Icons.EXPAND_MORE,
                            expand=True,
                        )
                    ]
                ),
            )
        )
    return ft.Column(controls=rows, spacing=0, expand=True)


def _commit_row(commit, is_open: bool, actions: dict, c) -> ft.Control:
    """单条提交行：短哈希 · 摘要 · 作者 · 时间（点击展开变更文件）。"""
    refs = [r for r in commit.refs if r]
    subtitle = f"{commit.author_name} · {commit.time_text}"
    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.LG, right=Spacing.LG, top=Spacing.MD, bottom=Spacing.MD
        ),
        ink=True,
        tooltip=f"{commit.short_sha}\n{subtitle}\n{commit.absolute_time_text}",
        on_click=lambda e: actions["toggle_commit"](commit.sha),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(
                            ft.Icons.ARROW_DROP_DOWN if is_open else ft.Icons.CHEVRON_RIGHT,
                            size=14,
                            color=c.muted,
                        ),
                        ft.Text(
                            commit.summary or "(无提交信息)",
                            size=12,
                            color=c.text,
                            font_family=FONT_MAIN,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    controls=[
                        ft.Container(width=14),
                        ft.Text(
                            commit.short_sha,
                            size=10,
                            color=c.link,
                            font_family=FONT_MONO,
                        ),
                        ft.Text(subtitle, size=10, color=c.muted, font_family=FONT_MAIN,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                        *(
                            [
                                ft.Container(
                                    bgcolor=ft.Colors.with_opacity(0.12, c.link),
                                    border_radius=Radius.SM,
                                    padding=ft.Padding.symmetric(horizontal=4, vertical=0),
                                    content=ft.Text(
                                        refs[0].replace("HEAD -> ", ""),
                                        size=9,
                                        color=c.link,
                                        font_family=FONT_MONO,
                                    ),
                                )
                            ]
                            if refs
                            else []
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=2,
        ),
    )


def _commit_file_row(commit, change: FileChange, actions: dict, c) -> ft.Control:
    """提交详情中的单个变更文件：点击查看该提交下此文件的差异。"""
    stats = ""
    if change.additions is not None or change.deletions is not None:
        stats = f"+{change.additions or 0} -{change.deletions or 0}"
    return ft.Container(
        padding=ft.Padding.only(
            left=Spacing.XXXL, right=Spacing.LG, top=2, bottom=2
        ),
        border_radius=Radius.MD,
        ink=True,
        tooltip=f"{change.display_name}（点击查看该提交下的差异）{(' ' + stats) if stats else ''}",
        on_click=lambda e: actions["history_file_diff"](commit.sha, change.path),
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=12, color=c.muted),
                ft.Text(
                    change.basename,
                    size=11,
                    color=c.text,
                    font_family=FONT_MAIN,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    change.dirname,
                    size=10,
                    color=c.muted,
                    font_family=FONT_MAIN,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                *(
                    [
                        ft.Text(stats, size=10, color=git_status_color(change.kind, c),
                                font_family=FONT_MONO)
                    ]
                    if stats
                    else []
                ),
                letter_badge(change.letter, change.kind, c),
            ],
            spacing=Spacing.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def _render_gate(state: dict, actions: dict, c) -> ft.Control:
    """前置状态：git 缺失 / 无工作区 / 非仓库 → 引导态。"""
    if not state.get("available"):
        return ft.Column(
            controls=[
                ft.Container(height=Spacing.XXL),
                _empty_hint(
                    "未检测到 Git\n\n请先安装 Git 并确保其在 PATH 中，\n"
                    "安装后点击下方按钮重新检测。",
                    c,
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=Spacing.XXL, vertical=Spacing.LG),
                    content=git_text_button(
                        "重新检测", "重新探测 git 可执行文件",
                        actions["refresh"], c, icon=ft.Icons.REFRESH, expand=True,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )

    workspace = state.get("workspace")
    if not workspace:
        return ft.Column(
            controls=[
                ft.Container(height=Spacing.XXL),
                _empty_hint(
                    "尚未打开文件夹\n\n打开一个文件夹作为工作区后，\n此处会显示 Git 更改。",
                    c,
                ),
            ],
            spacing=0,
            expand=True,
        )

    root = state.get("root")
    if not root:
        return ft.Column(
            controls=[
                ft.Container(height=Spacing.XXL),
                _empty_hint(
                    f"当前文件夹不是 Git 仓库\n\n{workspace}",
                    c,
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=Spacing.XXL, vertical=Spacing.LG),
                    content=ft.Column(
                        controls=[
                            git_text_button(
                                "初始化仓库",
                                "在当前位置执行 git init",
                                actions["init_repo"], c, icon=ft.Icons.PLAYLIST_ADD, expand=True,
                            ),
                            ft.Container(height=Spacing.SM),
                            ft.Text(
                                "也可用「打开文件夹」切换到已有仓库的上级目录",
                                size=10,
                                color=c.muted,
                                font_family=FONT_MAIN,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        spacing=0,
                    ),
                ),
            ],
            spacing=0,
            expand=True,
        )
    return git_icon_button(ft.Icons.REFRESH, "刷新", actions["refresh"], c.muted, size=13)


# ---------------------------------------------------------------------------
# 主组件
# ---------------------------------------------------------------------------


@ft.component
def GitPanel(
    state: dict,
    actions: dict,
    theme_mode: ft.ThemeMode,
) -> ft.Control:
    """Git 侧边栏面板。

    ``state`` / ``actions`` 由 :func:`git_panel_props` 从 AppContext 打包
    （见模块 docstring 的状态划分说明）。
    """
    c = get_colors(theme_mode)

    # ---- 局部状态：提交信息草稿 / 过滤词 / 分区折叠 / 历史筛选输入 ----
    # 提交信息草稿从 commit_ref 恢复：面板在「文件 ↔ 源代码管理」之间切换时会
    # 卸载重挂（use_state 归零），只有 ref 里的镜像能保住用户已经敲了一半的信息。
    commit_ref = state.get("commit_ref")
    message, set_message = ft.use_state(
        lambda: (commit_ref.current or "") if commit_ref is not None else ""
    )
    filter_text, set_filter_text = ft.use_state("")
    author_text, set_author_text = ft.use_state("")
    keyword_text, set_keyword_text = ft.use_state("")
    path_text, set_path_text = ft.use_state("")
    collapsed, set_collapsed = ft.use_state(frozenset())

    # 提交信息镜像到 App 的 ref：Ctrl+Enter 等全局快捷键经 ref 读取最新草稿
    if commit_ref is not None:
        commit_ref.current = message

    # 提交成功后 App 递增 commit_seq → 清空输入框（失败则保留，避免丢失草稿）
    commit_seq = state.get("commit_seq", 0)

    def _clear_after_commit():
        if commit_seq:
            set_message("")
            if commit_ref is not None:
                commit_ref.current = ""

    ft.use_effect(_clear_after_commit, [commit_seq])

    def _on_view_change(view: str):
        actions["set_view"](view)
        if view == VIEW_HISTORY:
            actions["load_history"]()

    # ---- 门控态 ----
    if not state.get("available") or not state.get("workspace") or not state.get("root"):
        return _render_gate(state, actions, c)

    view = state.get("view") or VIEW_CHANGES
    st: GitStatus | None = state.get("status")

    # ---- 仓库头 ----
    header = ft.Container(
        padding=ft.Padding.only(
            left=Spacing.LG, right=Spacing.SM, top=Spacing.MD, bottom=Spacing.MD
        ),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        _branch_button(state, actions, c),
                        ft.Container(expand=True),
                        git_icon_button(
                            ft.Icons.EDIT_NOTE if view == VIEW_CHANGES else ft.Icons.HISTORY,
                            "切换到历史记录" if view == VIEW_CHANGES else "切换到更改视图",
                            (lambda: _on_view_change(
                                VIEW_HISTORY if view == VIEW_CHANGES else VIEW_CHANGES
                            )),
                            c.link if view == VIEW_HISTORY else c.muted,
                            size=15,
                        ),
                        _more_menu(state, actions, c, _on_view_change),
                    ],
                    spacing=Spacing.XS,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=Spacing.SM),
                _sync_bar(state, actions, c),
                *(
                    [
                        ft.Container(height=Spacing.SM),
                        ft.Container(
                            bgcolor=ft.Colors.with_opacity(0.10, git_status_color("modified", c)),
                            border_radius=Radius.MD,
                            padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
                            content=ft.Row(
                                controls=[
                                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=13,
                                            color=git_status_color("modified", c)),
                                    ft.Text(
                                        f"{st.op_label if st else '操作'}进行中",
                                        size=10,
                                        color=git_status_color("modified", c),
                                        font_family=FONT_MAIN,
                                    ),
                                ],
                                spacing=Spacing.SM,
                                tight=True,
                            ),
                        ),
                    ]
                    if st is not None and st.in_operation
                    else []
                ),
            ],
            spacing=0,
        ),
    )

    body: ft.Control
    if view == VIEW_HISTORY:
        body = _render_history_view(
            state, actions, c,
            {
                "author": author_text, "set_author": set_author_text,
                "keyword": keyword_text, "set_keyword": set_keyword_text,
                "path": path_text, "set_path": set_path_text,
            },
        )
    else:
        body = _render_changes_view(
            state, actions, c,
            filter_text=filter_text, set_filter_text=set_filter_text,
            message=message, set_message=set_message,
            collapsed=collapsed, set_collapsed=set_collapsed,
        )

    children: list[ft.Control] = [header]
    if state.get("error"):
        children.append(
            ft.Container(
                bgcolor=ft.Colors.with_opacity(0.10, git_status_color("deleted", c)),
                padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=Spacing.SM),
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.ERROR_OUTLINE, size=13,
                                color=git_status_color("deleted", c)),
                        ft.Text(
                            state["error"],
                            size=10,
                            color=git_status_color("deleted", c),
                            font_family=FONT_MAIN,
                            max_lines=3,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                    ],
                    spacing=Spacing.SM,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            )
        )
    if state.get("busy"):
        children.append(
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=Spacing.LG, vertical=2),
                content=ft.Text(
                    "正在执行 Git 操作…", size=10, color=c.muted, font_family=FONT_MAIN
                ),
            )
        )
    children.append(body)

    return ft.Column(controls=children, spacing=0, expand=True)
