"""键盘事件分发器：替代 main.py 的 on_key 闭包。

把 main.py 中 ~240 行的 _do_copy / _do_cut / _do_paste_check / _combo / _matches
/ on_key 整块抽成 KeyDispatcher 类。main.py 持有实例并绑定到 page.on_keyboard_event。

设计要点：
- 持有 actions_ref（原 nav_ref）引用。editor.py 每次渲染写入最新 EditorActions，
  dispatcher 读 actions_ref.current 即最新值，无需 on_key_ref 中转层。
- actions.cursor_ref.current.base / .extent / .draft_len 实时读取光标位置
  （这些值在 on_selection_change 中直接修改，非 set_state 触发，不能用渲染期快照）。
- _combo 为模块级函数；matches 复用 services.shortcuts.matches。

依赖项：
- models.BlockType（块类型判断）
- core.actions.EditorActions（编辑器动作集合类型注解）
- services.shortcuts.ShortcutManager / matches（快捷键匹配）
"""

import asyncio
from collections.abc import Callable

import flet as ft

from core.actions import EditorActions
from models import BlockType
from services.shortcuts import ShortcutManager, matches


def _combo(e) -> str:
    """把 KeyboardEvent 规范化为 "ctrl+shift+key" 形式的小写字符串。

    与 services.shortcuts.normalize 配套：ctrl+comma 在 normalize 中转为 ctrl+,，
    此处也把 "comma" 映射为 ","，保证 matches() 比对一致。
    """
    parts: list[str] = []
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


# 不应触发"打字替换 outward 选区"的按键（修饰/导航/功能键等）
_NON_PRINTABLE_KEYS = frozenset({
    "shift", "control", "alt", "meta",
    "tab", "enter", "escape",
    "backspace", "delete", "insert", "printscreen", "pause", "menu",
    "home", "end", "pageup", "pagedown",
    "arrowleft", "arrowright", "arrowup", "arrowdown",
    "capslock", "numlock", "scrolllock",
    "controlleft", "controlright", "shiftleft", "shiftright",
    "altleft", "altright", "metaleft", "metaright",
})


def _extract_printable_char(e) -> str | None:
    """从 KeyboardEvent 提取可打印字符，用于"打字替换 outward 选区"。

    排除：Ctrl/Meta/Alt 组合键、功能键 F1-F12、修饰键本身、导航键、空格键特殊处理。
    单字符可打印 → 返回（字母按 shift 决定大小写）；space → 返回 " "；其余 None。

    注意：IME 组合态首字符不触发 KeyDownEvent（走 TextField.on_change），
    故中文输入法首字符无法触发替换——这是已知限制，URL 几乎均为 ASCII 可接受。
    """
    if getattr(e, "ctrl", False) or getattr(e, "meta", False) or getattr(e, "alt", False):
        return None
    key = (getattr(e, "key", "") or "")
    if not key:
        return None
    kl = key.lower()
    if kl in _NON_PRINTABLE_KEYS:
        return None
    # F1-F12
    if len(kl) >= 2 and kl[0] == "f" and kl[1:].isdigit():
        return None
    if kl == "space":
        return " "
    if len(key) == 1 and key.isprintable():
        # 字母：未按 shift → 小写（Flet key 默认大写）；按 shift 已是大写
        if key.isalpha() and not getattr(e, "shift", False):
            return key.lower()
        return key
    return None


class KeyDispatcher:
    """键盘事件分发器：浏览态 / 编辑态两层快捷键 + 编辑态光标导航。

    main.py 每次渲染重建实例（ShortcutManager 是无状态读取器，重建无副作用）。
    page.on_keyboard_event = dispatcher.handle 直接绑定，无需 on_key_ref 中转。
    """

    # 原生编辑控件聚焦时需放行的纯导航键（无修饰键）
    _NATIVE_NAV_KEYS = frozenset({
        "backspace", "delete", "enter", "tab", "home", "end",
        "pageup", "pagedown",
        "arrowleft", "arrowright", "arrowup", "arrowdown",
    })
    # 原生编辑控件聚焦时需放行的剪贴板组合键
    _NATIVE_CLIPBOARD_COMBO = frozenset({"ctrl+c", "ctrl+x", "ctrl+v", "ctrl+a"})

    def __init__(
        self,
        shortcut_mgr: ShortcutManager,
        actions_ref: ft.Ref,  # ft.Ref[EditorActions | None]
        clipboard_ref: ft.Ref,
        page_ref: ft.Ref,
        paste_old_draft: ft.Ref,
        app_callbacks: dict[str, Callable[[], None]],
        # 期望键：save / new / open / toggle_sidebar / toggle_theme / open_settings
        capturing: tuple = (None, None),
        on_capture: Callable[[str, str, str], None] | None = None,
        on_cancel_capture: Callable[[], None] | None = None,
    ):
        self._shortcut_mgr = shortcut_mgr
        self._actions_ref = actions_ref
        self._clipboard_ref = clipboard_ref
        self._page_ref = page_ref
        self._paste_old_draft = paste_old_draft
        self._app_callbacks = app_callbacks
        # 捕获模式：(layer, action_id) | (None, None)。非空时 handle 顶部拦截，
        # 把下一个组合键经 on_capture 写入配置并退出捕获，不走常规分发。
        self._capturing = capturing
        self._on_capture = on_capture
        self._on_cancel_capture = on_cancel_capture

    # ---- 共享工具 ----
    @staticmethod
    def _native_field_focused(actions: EditorActions | None) -> bool:
        """代码块 CodeEditor / 表格 TableView / 公式 TextField 的原生编辑控件是否聚焦。

        聚焦时文本编辑键与剪贴板组合交由原生控件处理，跳过全局导航/选区/剪贴板逻辑。
        """
        if actions is None:
            return False
        code_ref = getattr(actions, "code_focus_ref", None)
        if code_ref is not None and code_ref.current is not None:
            return True
        table_ref = getattr(actions, "table_focus_ref", None)
        if table_ref is not None and table_ref.current is not None:
            return True
        math_ref = getattr(actions, "math_focus_ref", None)
        if math_ref is not None and math_ref.current is not None:
            return True
        return False

    # ---- 主入口 ----
    def handle(self, e) -> None:
        combo = _combo(e)
        key = e.key or ""
        norm = key.replace(" ", "").lower()

        # 捕获模式：设置页"修改"按钮触发，捕获下一个组合键写入配置。
        # 优先级最高，拦截所有按键不走常规分发。
        if self._capturing != (None, None) and self._on_capture is not None:
            layer, action_id = self._capturing
            if not combo:
                # 纯修饰键（单按 Ctrl/Shift/Alt），等待完整组合
                return
            if norm == "escape":
                # Esc 取消捕获
                if self._on_cancel_capture is not None:
                    self._on_cancel_capture()
                return
            if norm == "backspace":
                # Backspace 清空绑定
                self._on_capture(layer, action_id, "")
                return
            # 捕获到组合键，写入并退出
            self._on_capture(layer, action_id, combo)
            return

        actions: EditorActions | None = self._actions_ref.current

        # 用 KeyboardEvent.shift 可靠同步 Shift 状态到 shift_pressed_ref。
        # KeyboardListener 的 KeyDownEvent.key 对 Shift 可能返回 "Shift Left" /
        # "Shift Right"（而非 "shift"），导致 _on_key_down 的 key == "shift" 匹配
        # 失败；此处 e.shift 是 Flet 从 Flutter 修饰键状态直接读取，始终可靠。
        if actions is not None and actions.shift_pressed_ref is not None:
            actions.shift_pressed_ref.current = bool(e.shift)
        # 同步 Ctrl 状态到 editor 的 ctrl_pressed_ref（KeyboardEvent.ctrl 可靠）。
        # editor 的 KeyboardListener KeyDownEvent 无 ctrl 字段，需此处同步，供
        # _on_key_down 的 tab 分支判断 Ctrl+Tab（避免代码块/表格缩进与标签切换冲突）。
        if actions is not None and getattr(actions, "ctrl_pressed_ref", None) is not None:
            actions.ctrl_pressed_ref.current = bool(e.ctrl)

        # 代码块 CodeEditor / 表格 TableView 聚焦时：文本编辑键（无修饰键）与剪贴板
        # 组合交由原生控件处理（Tab 缩进、方向键移动、Backspace、Ctrl+C 复制等），
        # 跳过全局导航/选区/剪贴板逻辑避免冲突。全局快捷键（Ctrl+S/Z、Ctrl+Tab 切换
        # 等）不在跳过清单内，仍正常处理。表格的 Tab/Escape 由 editor.py 的 _on_key_down
        # 通过 table_nav_ref 路由到 TableView 单元格导航逻辑。
        if self._native_field_focused(actions):
            if not (e.ctrl or e.meta or e.alt):
                if norm in self._NATIVE_NAV_KEYS:
                    return
            if combo in self._NATIVE_CLIPBOARD_COMBO:
                return

        # 向外选区激活时（active is None, outward_sel is not None）：
        # 优先路由 BackSpace/Delete/Ctrl+C/Ctrl+X/Ctrl+V/Escape/Shift+Arrow 到 outward handlers，
        # 绕过 layer 判定（此时 layer=browse 会误路由到 SelectionArea 删除分支）
        if (
            actions is not None
            and actions.outward_sel is not None
            and not actions.raw_mode
        ):
            browse_sc = self._shortcut_mgr.get("browse")
            # Ctrl+C：复制 outward_sel 选区文本（不删除）
            if matches(combo, browse_sc.get("copy", "ctrl+c")):
                if actions.handle_outward_copy is not None:
                    page = self._page_ref.current
                    if page is not None:
                        page.run_task(actions.handle_outward_copy)
                return
            # Ctrl+V：先删除选区，再在删除点粘贴
            if matches(combo, browse_sc.get("paste", "ctrl+v")):
                actions.handle_outward_delete()
                self._paste_old_draft.current = ""
                page = self._page_ref.current
                if page is not None:
                    page.run_task(self._do_paste_check)
                return
            if norm in ("backspace", "delete"):
                if actions.handle_outward_delete is not None:
                    actions.handle_outward_delete()
                return
            if matches(combo, browse_sc.get("cut", "ctrl+x")):
                if actions.handle_outward_cut is not None:
                    page = self._page_ref.current
                    if page is not None:
                        page.run_task(actions.handle_outward_cut)
                return
            if norm == "esc":
                if actions.clear_outward_sel is not None:
                    actions.clear_outward_sel()
                return
            if e.shift:
                if norm == "arrowleft" and actions.extend_outward_left is not None:
                    actions.extend_outward_left()
                    return
                if norm == "arrowright" and actions.extend_outward_right is not None:
                    actions.extend_outward_right()
                    return
                if norm == "arrowup" and actions.extend_outward_up is not None:
                    actions.extend_outward_up()
                    return
                if norm == "arrowdown" and actions.extend_outward_down is not None:
                    actions.extend_outward_down()
                    return
            # 非 Shift 方向键/Home/End：取消选区（v1 不做光标落点激活，用户可点击重新激活）
            if norm in ("arrowleft", "arrowright", "arrowup", "arrowdown", "home", "end"):
                if actions.clear_outward_sel is not None:
                    actions.clear_outward_sel()
                return
            # 可打印字符：打字替换 outward 选区（通用基础编辑行为，桌面端直觉）
            char = _extract_printable_char(e)
            if char is not None and actions.handle_outward_type_char is not None:
                actions.handle_outward_type_char(char)
                return

        # 全局标签快捷键：Ctrl+W / Ctrl+Tab / Ctrl+Shift+Tab 在两层均生效，
        # 置于 layer 判定之前拦截，避免被 edit 层 tab 缩进逻辑吃掉。
        cb = self._app_callbacks
        browse_sc = self._shortcut_mgr.get("browse")
        if matches(combo, browse_sc.get("close_tab", "ctrl+w")):
            cb["close_tab"]()
            return
        if matches(combo, browse_sc.get("next_tab", "ctrl+tab")):
            cb["next_tab"]()
            return
        if matches(combo, browse_sc.get("prev_tab", "ctrl+shift+tab")):
            cb["prev_tab"]()
            return

        # Alt+Z 切换自动换行：两层均生效（VSCode 风格），置于 layer 判定之前。
        if matches(combo, browse_sc.get("toggle_word_wrap", "alt+z")):
            cb["toggle_word_wrap"]()
            return

        # PageUp / PageDown：两层均生效（编辑态光标翻页跟随，浏览态纯滚动）。
        # 置于 layer 判定之前，确保浏览态也能响应。outward_sel 激活时顶部拦截块
        # 不匹配 pageup/pagedown 会 fall-through 到此，active is None → 浏览态纯滚动。
        if norm == "pageup" and actions is not None:
            actions.page_up()
            return
        if norm == "pagedown" and actions is not None:
            actions.page_down()
            return

        # 行内格式快捷键优先级必须高于浏览态全局快捷键：
        # 这样鼠标选中文本后按 Ctrl+B/I/U/Shift+S/`/K 不会被
        # 侧边栏切换、聚焦模式等浏览态快捷键抢先消费。
        # combo→fmt_name 映射从 ShortcutManager 动态读取（用户自定义键位生效）。
        inline_map = self._shortcut_mgr.inline_format_combos()
        if combo in inline_map:
            if actions is not None and not self._native_field_focused(actions):
                selection_fmt = getattr(actions, "apply_inline_format_to_selection", None)
                if actions.cursor_li is None and selection_fmt is not None:
                    selection_fmt(inline_map[combo], combo)
                else:
                    actions.apply_inline_format(inline_map[combo])
            return

        # Ctrl+A 全选：两层均生效，原生控件聚焦时放行交由原生处理
        if matches(combo, browse_sc.get("select_all", "ctrl+a")):
            if actions is not None and not self._native_field_focused(actions):
                if actions.select_all is not None:
                    actions.select_all()
            return

        layer = "edit" if actions is not None and actions.cursor_li is not None else "browse"
        shortcuts = self._shortcut_mgr.get(layer)

        if layer == "edit" and actions is not None:
            if self._handle_edit_nav(actions, e, norm):
                return

        # 浏览态 Backspace：删除 SelectionArea 选区文本
        if (
            norm == "backspace"
            and actions is not None
            and actions.cursor_li is None
            and not actions.raw_mode
        ):
            plain = actions.selection_text_ref.current or ""
            if plain:
                actions.handle_delete_selection(plain)
                return

        page = self._page_ref.current
        if page is None:
            return
        self._handle_shortcuts(page, actions, combo, shortcuts, layer)

    # ---- 编辑态光标导航（home/end/up/down/backspace/delete/tab/越界 arrow）----
    def _handle_edit_nav(self, actions: EditorActions, e, norm: str) -> bool:
        """处理编辑态纯导航键。返回 True 表示已消费，False 继续走快捷键分支。

        注：outward_sel 激活时 active is None → layer=browse → 本函数不被调用，
        outward_sel 相关键由 handle() 顶部拦截块处理。此处 Shift+Arrow 仅负责
        从编辑态起始 outward 选区（active is not None, outward_sel is None）。
        """
        if norm == "home":
            actions.move_doc_start() if e.ctrl else actions.move_home()
            return True
        if norm == "end":
            actions.move_doc_end() if e.ctrl else actions.move_end()
            return True
        if norm == "arrowup":
            if e.shift and actions.extend_outward_up is not None:
                actions.extend_outward_up()
            else:
                actions.move_up()
            return True
        if norm == "arrowdown":
            if e.shift and actions.extend_outward_down is not None:
                actions.extend_outward_down()
            else:
                actions.move_down()
            return True
        if norm == "backspace":
            actions.backspace_core()
            return True
        if norm == "delete":
            actions.delete_core()
            return True
        if norm == "tab" and not e.ctrl:
            # Ctrl+Tab 已在 handle() 顶部拦截为标签切换，此处仅处理普通 Tab。
            # 代码块 Tab 由 CodeEditor 原生处理（缩进），此处跳过不拦截。
            # 表格 Tab 由 editor.py _on_key_down 通过 table_nav_ref 路由到
            # TableView 单元格导航（table_focus_ref 守卫已跳过此处，但用户从
            # 非编辑态按 Tab 时 active 可能指向 TABLE 行，此处拦截防止 indent）。
            active_bt = getattr(actions.active_line, "block_type", None) if actions.active_line else None
            if active_bt in (BlockType.CODE, BlockType.TABLE):
                return True
            # 链接编辑视为常规文本编辑：Tab 不做字段跳转，按默认缩进/插空格处理。
            if e.shift:
                if actions.indent_or_outdent:
                    actions.indent_or_outdent(-1)
                else:
                    actions.move_left()
            else:
                if actions.indent_or_outdent:
                    actions.indent_or_outdent(1)
                else:
                    actions.move_right()
            return True
        if norm == "arrowleft":
            if e.shift and actions.extend_outward_left is not None:
                actions.extend_outward_left()
            else:
                actions.move_left()
            return True
        if norm == "arrowright":
            if e.shift and actions.extend_outward_right is not None:
                actions.extend_outward_right()
            else:
                actions.move_right()
            return True
        return False

    # ---- 快捷键分发（浏览态 / 编辑态各自匹配）----
    def _handle_shortcuts(
        self,
        page: ft.Page,
        actions: EditorActions | None,
        combo: str,
        shortcuts: dict[str, str],
        layer: str,
    ) -> None:
        cb = self._app_callbacks
        # Ctrl+0~6：切换当前行标题级别（0=普通段落，1~6=H1~H6），浏览/编辑两态均生效。
        # 代码块/表格聚焦时跳过（避免把代码块/表格行误转为标题），return 阻止后续判定。
        if combo in ("ctrl+0", "ctrl+1", "ctrl+2", "ctrl+3",
                     "ctrl+4", "ctrl+5", "ctrl+6"):
            if actions is not None and not self._native_field_focused(actions):
                digit = int(combo[-1])
                if digit == 0:
                    actions.set_block(BlockType.PARAGRAPH)
                else:
                    actions.set_block(BlockType.HEADING, digit)
            return
        # Ctrl+Shift+M：将当前行切换为块级公式（浏览/编辑两态均生效）。
        # 公式 TextField 聚焦时跳过（避免编辑公式时误触发行级转换）。
        if matches(combo, shortcuts.get("format_math_block", "ctrl+shift+m")):
            if actions is not None and not self._native_field_focused(actions):
                actions.set_block(BlockType.MATH)
            return
        # 行内格式快捷键：编辑态包裹选区或插入空语法。
        # 代码块/表格聚焦时跳过（交由原生 TextField）。浏览态无 active 时静默返回。
        # combo→fmt_name 从 ShortcutManager 动态读取（用户自定义键位生效）。
        inline_map = self._shortcut_mgr.inline_format_combos()
        if combo in inline_map:
            if actions is not None and not self._native_field_focused(actions):
                actions.apply_inline_format(inline_map[combo])
            return
        if layer == "browse":
            if matches(combo, shortcuts.get("save", "ctrl+s")):
                page.run_task(cb["save"])
            elif matches(combo, shortcuts.get("new", "ctrl+n")):
                cb["new"]()
            elif matches(combo, shortcuts.get("open", "ctrl+o")):
                page.run_task(cb["open"])
            elif matches(combo, shortcuts.get("toggle_sidebar", "ctrl+b")):
                cb["toggle_sidebar"]()
            elif matches(combo, shortcuts.get("toggle_theme", "ctrl+shift+l")):
                cb["toggle_theme"]()
            elif matches(combo, shortcuts.get("toggle_raw", "ctrl+/")):
                if actions is not None:
                    actions.toggle_raw()
            elif matches(combo, shortcuts.get("open_settings", "ctrl+comma")):
                cb["open_settings"]()
            elif matches(combo, shortcuts.get("focus_mode", "ctrl+k")):
                if actions is not None:
                    actions.toggle_focus_mode()
            elif matches(
                combo, shortcuts.get("redo", "ctrl+y")
            ) or matches(combo, shortcuts.get("redo_alt", "ctrl+shift+z")):
                if actions is not None:
                    actions.redo()
            elif matches(combo, shortcuts.get("undo", "ctrl+z")):
                if actions is not None:
                    actions.undo()
            elif matches(combo, shortcuts.get("copy", "ctrl+c")):
                if actions is None or actions.cursor_li is None:
                    page.run_task(self._do_copy)
            elif matches(combo, shortcuts.get("cut", "ctrl+x")):
                if actions is None or actions.cursor_li is None:
                    page.run_task(self._do_cut)
            elif matches(combo, shortcuts.get("paste", "ctrl+v")):
                if actions is not None and actions.cursor_li is not None:
                    self._paste_old_draft.current = ""
                    page.run_task(self._do_paste_check)
            return
        # edit 层
        if matches(combo, shortcuts.get("save", "ctrl+s")):
            page.run_task(cb["save"])
        elif matches(combo, shortcuts.get("undo", "ctrl+z")):
            if actions is not None:
                actions.undo()
        elif matches(
            combo, shortcuts.get("redo", "ctrl+y")
        ) or matches(combo, shortcuts.get("redo_alt", "ctrl+shift+z")):
            if actions is not None:
                actions.redo()
        elif matches(combo, shortcuts.get("toggle_raw", "ctrl+enter")):
            if actions is not None:
                actions.toggle_raw()
        elif matches(combo, shortcuts.get("toggle_sidebar", "escape")):
            cb["toggle_sidebar"]()
        elif matches(combo, shortcuts.get("copy", "ctrl+c")):
            if actions is None or actions.cursor_li is None:
                page.run_task(self._do_copy)
        elif matches(combo, shortcuts.get("cut", "ctrl+x")):
            # 编辑态走 cut_current_line（剪切当前行），浏览态走 handle_cut（选区剪切）
            page.run_task(self._do_cut)
        elif matches(combo, shortcuts.get("paste", "ctrl+v")):
            if actions is not None and actions.cursor_li is not None:
                self._paste_old_draft.current = ""
                page.run_task(self._do_paste_check)

    # ---- 剪贴板异步操作 ----
    async def _do_copy(self) -> None:
        """Ctrl+C：用 SelectionArea 选区文本计算 Markdown 覆盖剪贴板。

        用 selection_text_ref（on_change 上报的选区纯文本）而非 clipboard.get()
        读取：原生 SelectionArea 复制到剪贴板的时序不可靠（sleep 0.2s 仍可能读到
        空或旧值），且 selection_text_ref 与 BackSpace 删除选区共用同一数据源，
        行为一致更可靠。
        """
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        plain = actions.selection_text_ref.current or ""
        if not plain:
            return
        try:
            md = actions.compute_markdown_from_text(plain)
            if md and md != plain:
                clipboard = self._clipboard_ref.current
                if clipboard is not None:
                    await clipboard.set(md)
        except Exception:
            return

    async def _do_cut(self) -> None:
        """Ctrl+X：有选区时剪切选区；无选区时剪切当前行（VSCode 行为）。"""
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        # 编辑态（cursor_li is not None）：直接剪切当前行。
        # 编辑态下 SelectionArea 不工作，selection_text_ref 可能有残留旧值，
        # 不能据此判断有无选区——编辑态无行内选区，直接走 cut_current_line。
        if actions.cursor_li is not None:
            if actions.cut_current_line is not None:
                try:
                    await actions.cut_current_line()
                except Exception:
                    pass
            return
        # 浏览态：有 SelectionArea 选区文本时剪切选区
        plain = actions.selection_text_ref.current or ""
        if not plain:
            return
        try:
            await actions.handle_cut(plain)
        except Exception:
            return

    async def _do_paste_check(self) -> None:
        """Ctrl+V 后异步检查剪贴板是否含多行内容，若是则拆分为多行插入。"""
        await asyncio.sleep(0.05)
        clipboard = self._clipboard_ref.current
        if clipboard is None:
            return
        try:
            text = await clipboard.get()
        except Exception:
            return
        if not text or "\n" not in text:
            return
        actions = self._actions_ref.current
        if actions is None:
            return
        try:
            actions.handle_paste(text, self._paste_old_draft.current)
        except Exception:
            return
