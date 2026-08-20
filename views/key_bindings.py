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
from datetime import datetime

import flet as ft

from core.actions import EditorActions
from models import BlockType
from services.clipboard_html import get_clipboard_html_async
from services.html_to_markdown import html_to_markdown
from services.shortcuts import ShortcutManager, matches


def _combo(e) -> str:
    """把 KeyboardEvent 规范化为 "ctrl+shift+key" 形式的小写字符串。

    与 services.shortcuts.normalize 配套：ctrl+comma 在 normalize 中转为 ctrl+,，
    此处也把 "comma" 映射为 ","，保证 matches() 比对一致。

    Flet 的 KeyboardEvent.key 对部分标点返回键名而非字符（逗号→"comma"、
    句号→"period"），此处统一映射为字符，使 combo 输出与 settings 中的
    字符形式（"ctrl+," / "ctrl+."）可比较。其他标点（/ \\ ` ; 等）Flet
    直接返回字符，无需映射。
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
        "period": ".",
        "escape": "esc",
        "enter": "enter",
        ":": ";",  # Shift+; 产生 ":"（US 键盘），归一化为 ";" 保证 Ctrl+Shift+; 匹配
        "+": "=",  # Shift+= 产生 "+"（US 键盘），映射为 "=" 保证 Ctrl+Shift+= 匹配
        ")": "0",  # Shift+0 产生 ")"（US 键盘），映射为 "0" 保证 Ctrl+Shift+0 匹配
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
        capturing: tuple[str | None, str | None] = (None, None),
        on_capture: Callable[[str, str, str], None] | None = None,
        on_cancel_capture: Callable[[], None] | None = None,
        arrow_repeat_ref: ft.Ref | None = None,
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
        self._arrow_repeat_ref = arrow_repeat_ref

    # ---- 上/下键长按自驱动重复 ----
    _REPEAT_DELAY = 0.35
    _REPEAT_INTERVAL = 0.04

    def _start_arrow_repeat(self, norm: str) -> None:
        """上/下键 KeyDown 启动自驱动重复定时器。

        Flet 客户端 TextField 的 ignore_up_down_keys 对上/下键返回
        KeyEventResult.handled，Flutter 焦点链分发是叶子优先（leaf→root），
        TextField 先吞掉上/下键 → 编辑器 KeyboardListener 永远收不到上/下键事件。
        因此上/下键重复必须由页面级 KeyDispatcher 驱动（HardwareKeyboard 全局
        处理器，在焦点分发之前调用，能看到所有 KeyDown）。KeyUp 不被
        ignore_up_down_keys 拦截，仍通过 KeyboardListener 到达编辑器 _on_key_up。
        """
        if self._arrow_repeat_ref is None:
            return
        if norm not in ("arrowup", "arrowdown"):
            return
        if self._arrow_repeat_ref.current is not None:
            return
        actions = self._actions_ref.current
        if actions is None or getattr(actions, "cursor_li", None) is None:
            return
        if self._native_field_focused(actions):
            return

        async def _loop():
            stall = 0
            prev_pos: tuple | None = None
            try:
                await asyncio.sleep(self._REPEAT_DELAY)
                while self._arrow_repeat_ref is not None and self._arrow_repeat_ref.current is not None:
                    actions = self._actions_ref.current
                    if actions is None or getattr(actions, "cursor_li", None) is None:
                        break
                    cs = actions.cursor_ref.current if actions.cursor_ref else None
                    cur = (actions.cursor_li, cs.base if cs is not None else None)
                    if prev_pos is not None and cur == prev_pos:
                        stall += 1
                        if stall >= 3:
                            break
                    else:
                        stall = 0
                    prev_pos = cur
                    shift = bool(actions.shift_pressed_ref.current) if actions.shift_pressed_ref else False
                    if shift:
                        handler = {
                            "arrowup": getattr(actions, "extend_outward_up", None),
                            "arrowdown": getattr(actions, "extend_outward_down", None),
                        }[norm]
                        if handler is not None:
                            handler()
                    else:
                        {"arrowup": actions.move_up, "arrowdown": actions.move_down}[norm]()
                    await asyncio.sleep(self._REPEAT_INTERVAL)
            finally:
                if self._arrow_repeat_ref is not None:
                    self._arrow_repeat_ref.current = None

        # 任务引用存入共享 ref（防 GC + 供 while 条件检查运行状态）
        self._arrow_repeat_ref.current = asyncio.create_task(_loop())

    def _stop_arrow_repeat(self) -> None:
        if self._arrow_repeat_ref is not None:
            self._arrow_repeat_ref.current = None

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

    @staticmethod
    def _begin_paste(actions: EditorActions | None) -> None:
        """Ctrl+V / Ctrl+Shift+V 粘贴前设置进行中标志，拦截原生 TextField 单行粘贴 on_change 干扰。

        单行 TextField（multiline=False）会把多行文本的 \\n 移除拼接成一行触发
        on_change → handle_char_input，与 _do_paste_check 的 handle_paste 形成
        重复插入 + 行拼接 Bug。此处置 paste_in_progress=True，handle_char_input
        入口检测到 True 则跳过，由 _do_paste_check 统一走 handle_paste 处理。

        Ctrl+Shift+V 在 Windows/Linux 上可能被 Flutter TextField 拦截为"粘贴并
        匹配样式"（粘贴纯文本），同样会触发 on_change 干扰，故一并设置。
        浏览态（cursor_li is None）原生 TextField 未聚焦也不会触发 on_change，
        但为防御性仍设置（开销可忽略）。
        """
        if actions is None:
            return
        ref = getattr(actions, "paste_in_progress_ref", None)
        if ref is not None:
            ref.current = True

    @staticmethod
    def _end_paste(actions: EditorActions | None) -> None:
        """粘贴结束（无论是否成功）重置 paste_in_progress 标志。

        handle_paste 内部插入完成后会自行重置（含重建 TextField 清空 Flutter 端
        value），此处 try/finally 兜底确保异常或提前 return 路径也能重置，
        避免 handle_char_input 永久被拦截。
        """
        if actions is None:
            return
        ref = getattr(actions, "paste_in_progress_ref", None)
        if ref is not None:
            ref.current = False

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

        # 上/下键长按自驱动重复：非上/下键按下时停止（防 KeyUp 丢失后残留滚动）
        if norm not in ("arrowup", "arrowdown") and self._arrow_repeat_ref is not None:
            self._stop_arrow_repeat()

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
        # 同步 Alt 状态到 editor 的 alt_pressed_ref（KeyboardEvent.alt 可靠）。
        # editor 的 KeyboardListener KeyDownEvent.key 对 Alt 可能返回 "Alt Left" /
        # "Alt Right"，导致 _on_key_down 的 key == "alt" 匹配失败；此处 e.alt 是
        # Flet 从 Flutter 修饰键状态直接读取，始终可靠。供 RenderedLine._on_tap
        # 分发 Alt+Click / Alt+Shift+Click 多光标操作。
        if actions is not None and getattr(actions, "alt_pressed_ref", None) is not None:
            actions.alt_pressed_ref.current = bool(e.alt)

        # 多光标：Escape 清空所有副光标（优先于 toggle_sidebar / clear_outward_sel）
        # KeyDispatcher 早于 editor 的 KeyboardListener 执行，此处拦截避免
        # Escape 同时触发 clear_secondary_cursors + toggle_sidebar 的双重行为。
        if (
            norm == "escape"
            and actions is not None
            and getattr(actions, "has_secondary_cursors", None) is not None
            and actions.has_secondary_cursors()
        ):
            actions.clear_secondary_cursors()
            return

        # 多光标剪贴板：Ctrl+C/X/V 在多光标模式 + 有选区时同步操作所有光标选区
        # （优先于原生 TextField 和 outward_sel 路由，副光标无 TextField 无法走原生）
        if (
            actions is not None
            and not (e.alt or e.meta)
            and getattr(actions, "has_secondary_cursors", None) is not None
            and actions.has_secondary_cursors()
        ):
            browse_sc = self._shortcut_mgr.get("browse")
            # Ctrl+C：有选区时复制所有选区文本
            if matches(combo, browse_sc.get("copy", "ctrl+c")):
                if (
                    getattr(actions, "has_multi_cursor_selection", None) is not None
                    and actions.has_multi_cursor_selection()
                    and actions.copy_multi_cursor_selection is not None
                ):
                    page = self._page_ref.current
                    if page is not None:
                        page.run_task(actions.copy_multi_cursor_selection)
                    return
            # Ctrl+X：有选区时剪切所有选区，无选区时剪切各光标所在行（回退原生）
            if matches(combo, browse_sc.get("cut", "ctrl+x")):
                if (
                    getattr(actions, "has_multi_cursor_selection", None) is not None
                    and actions.has_multi_cursor_selection()
                    and actions.cut_multi_cursor_selection is not None
                ):
                    page = self._page_ref.current
                    if page is not None:
                        page.run_task(actions.cut_multi_cursor_selection)
                    return
            # Ctrl+V：读取剪贴板后智能粘贴到所有光标
            if matches(combo, browse_sc.get("paste", "ctrl+v")):
                if actions.paste_to_multi_cursors is not None:
                    self._paste_old_draft.current = ""
                    self._begin_paste(actions)
                    page = self._page_ref.current
                    if page is not None:
                        page.run_task(self._do_multi_cursor_paste)
                    return
            # Ctrl+Shift+V：纯文本粘贴到所有光标（剥离 Markdown 语法）
            if matches(combo, browse_sc.get("paste_plain", "ctrl+shift+v")):
                if actions.paste_to_multi_cursors_plain is not None:
                    self._paste_old_draft.current = ""
                    self._begin_paste(actions)
                    page = self._page_ref.current
                    if page is not None:
                        page.run_task(self._do_multi_cursor_paste_plain)
                    return

        # 代码块 CodeEditor / 表格 TableView 聚焦时：文本编辑键（无修饰键）与剪贴板
        # 组合交由原生控件处理（Tab 缩进、方向键移动、Backspace、Ctrl+C 复制等），
        # 跳过全局导航/选区/剪贴板逻辑避免冲突。全局快捷键（Ctrl+S/Z、Ctrl+Tab 切换
        # 等）不在跳过清单内，仍正常处理。表格的 Tab/Escape 由 editor.py 的 _on_key_down
        # 通过 table_nav_ref 路由到 TableView 单元格导航逻辑。
        if self._native_field_focused(actions):
            # Typora 式：空代码块聚焦时按 Backspace（无修饰键）→ 删除整个代码块。
            # 必须在 _NATIVE_NAV_KEYS 放行之前拦截：backspace 在放行清单内，否则会
            # 直接 return 交由原生 CodeEditor 处理（空内容时原生 Backspace 无效果）。
            # code_focus_ref.current 非 None 精确锁定代码块（表格/公式走各自 ref）。
            if (
                norm == "backspace"
                and not (e.ctrl or e.meta or e.alt)
                and getattr(actions, "handle_code_backspace", None) is not None
                and getattr(actions, "code_focus_ref", None) is not None
                and actions.code_focus_ref.current is not None
            ):
                if actions.handle_code_backspace(actions.code_focus_ref.current):
                    return
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
                self._begin_paste(actions)
                page = self._page_ref.current
                if page is not None:
                    page.run_task(self._do_paste_check)
                return
            # Ctrl+Shift+V：先删除选区，再在删除点粘贴纯文本（剥离 Markdown）
            if matches(combo, browse_sc.get("paste_plain", "ctrl+shift+v")):
                actions.handle_outward_delete()
                self._paste_old_draft.current = ""
                self._begin_paste(actions)
                page = self._page_ref.current
                if page is not None:
                    page.run_task(self._do_paste_plain_check)
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
            if norm == "escape":
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
                    self._start_arrow_repeat(norm)
                    return
                if norm == "arrowdown" and actions.extend_outward_down is not None:
                    actions.extend_outward_down()
                    self._start_arrow_repeat(norm)
                    return
                if norm == "home" and actions.extend_outward_home is not None:
                    actions.extend_outward_home()
                    return
                if norm == "end" and actions.extend_outward_end is not None:
                    actions.extend_outward_end()
                    return
            # 非 Shift 方向键/Home/End：取消选区（v1 不做光标落点激活，用户可点击重新激活）
            if norm in ("arrowleft", "arrowright", "arrowup", "arrowdown", "home", "end"):
                if actions.clear_outward_sel is not None:
                    actions.clear_outward_sel()
                return
            # Enter：删除选区后在删除点换行（Typora 式：选中→Enter 替换为换行）
            if norm == "enter" and actions.handle_outward_enter is not None:
                actions.handle_outward_enter()
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

        # Ctrl+O / Ctrl+Shift+O：打开文件 / 打开文件夹（两层均生效）。
        # 属于全局文件操作，与编辑状态无关，置于 layer 判定之前确保编辑态也能触发。
        if matches(combo, browse_sc.get("open", "ctrl+o")):
            page = self._page_ref.current
            if page is not None:
                page.run_task(cb["open"])
            return
        if matches(combo, browse_sc.get("open_folder", "ctrl+shift+o")):
            page = self._page_ref.current
            if page is not None:
                page.run_task(cb["open_folder"])
            return

        # Ctrl+Shift+R 切换自动换行：两层均生效（VSCode 风格），置于 layer 判定之前。
        if matches(combo, browse_sc.get("toggle_word_wrap", "ctrl+shift+r")):
            cb["toggle_word_wrap"]()
            return

        # Typora 式缩放：Ctrl+Shift+= 放大 / Ctrl+Shift+- 缩小 / Ctrl+Shift+0 实际大小
        # 两层均生效，置于 layer 判定之前确保编辑态也能触发。
        if matches(combo, browse_sc.get("zoom_in", "ctrl+shift+=")):
            _fn = cb.get("zoom_in")
            if _fn is not None:
                _fn()
            return
        if matches(combo, browse_sc.get("zoom_out", "ctrl+shift+-")):
            _fn = cb.get("zoom_out")
            if _fn is not None:
                _fn()
            return
        if matches(combo, browse_sc.get("zoom_reset", "ctrl+shift+0")):
            _fn = cb.get("zoom_reset")
            if _fn is not None:
                _fn()
            return

        # Ctrl+\ 向右拆分编辑器：两层均生效（VSCode 风格），多视口查看同一文档。
        if matches(combo, browse_sc.get("toggle_split_editor", "ctrl+\\")):
            cb["toggle_split_editor"]()
            return

        # 视图菜单快捷键：两层均生效（与菜单项标签一致）。
        # 编辑态 toggle_raw=Ctrl+Enter 在 _handle_shortcuts edit 分支处理；
        # toggle_sidebar 两层统一用 Ctrl+Shift+B（Esc 不再切换侧边栏），
        # 在此处处理，与 edit 分支互不冲突。
        # Ctrl+Shift+B 切换侧边栏
        if matches(combo, browse_sc.get("toggle_sidebar", "ctrl+shift+b")):
            cb["toggle_sidebar"]()
            return
        # Alt+T 切换主题
        if matches(combo, browse_sc.get("toggle_theme", "alt+t")):
            cb["toggle_theme"]()
            return
        # Ctrl+/ 源码模式
        if matches(combo, browse_sc.get("toggle_raw", "ctrl+/")):
            if actions is not None:
                actions.toggle_raw()
            return
        # Shift+Alt+F 全文 Markdown 格式化：两层均生效（与 toggle_raw 同级，
        # 代码块/表格聚焦时也格式化整篇文档）。
        if matches(combo, browse_sc.get("format_markdown", "shift+alt+f")):
            if actions is not None and getattr(actions, "format_document", None) is not None:
                actions.format_document()
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
        # 这样鼠标选中文本后按 Ctrl+B/I/Shift+H/`/K 不会被
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

        # Ctrl+F：聚焦搜索面板（两层均生效，VSCode 风格）
        if matches(combo, browse_sc.get("focus_search", "ctrl+f")):
            cb["focus_search"]()
            return

        # Ctrl+H：展开/收起替换栏（两层均生效，VSCode 风格）
        if matches(combo, browse_sc.get("toggle_replace_bar", "ctrl+h")):
            cb["toggle_replace_bar"]()
            return

        # Alt+Enter：替换当前匹配（两层均生效，搜索面板未激活时 no-op）
        if matches(combo, browse_sc.get("replace_current", "alt+enter")):
            cb["replace_current"]()
            return

        # Ctrl+Alt+Enter：全部替换（两层均生效）
        if matches(combo, browse_sc.get("replace_all", "ctrl+alt+enter")):
            cb["replace_all"]()
            return

        layer = "edit" if actions is not None and actions.cursor_li is not None else "browse"
        shortcuts = self._shortcut_mgr.get(layer)

        if layer == "edit" and actions is not None:
            if self._handle_edit_nav(actions, e, norm):
                # 上/下键编辑态导航成功：启动长按自驱动重复（页面级，不依赖 KeyboardListener）
                if norm in ("arrowup", "arrowdown"):
                    self._start_arrow_repeat(norm)
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
            if e.shift and not e.ctrl:
                # 多光标模式：所有光标选区扩展到行首（主+副），不走 outward_sel 路径
                if (
                    getattr(actions, "has_secondary_cursors", None) is not None
                    and actions.has_secondary_cursors()
                    and getattr(actions, "extend_selection_home", None) is not None
                ):
                    actions.extend_selection_home()
                elif actions.extend_outward_home is not None:
                    actions.extend_outward_home()
            else:
                actions.move_doc_start() if e.ctrl else actions.move_home()
            return True
        if norm == "end":
            if e.shift and not e.ctrl:
                # 多光标模式：所有光标选区扩展到行尾（主+副），不走 outward_sel 路径
                if (
                    getattr(actions, "has_secondary_cursors", None) is not None
                    and actions.has_secondary_cursors()
                    and getattr(actions, "extend_selection_end", None) is not None
                ):
                    actions.extend_selection_end()
                elif actions.extend_outward_end is not None:
                    actions.extend_outward_end()
            else:
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
            # 链接段内 Tab/Shift+Tab 字段跳转（Typora 式 text↔url↔段尾），
            # 避免在 []() 内插入空格破坏语法；非链接段走默认缩进/插空格。
            if actions.link_tab_jump is not None and actions.link_tab_jump(-1 if e.shift else 1):
                return True
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
            if e.shift:
                # 多光标模式：扩展所有光标选区（主+副），不走 outward_sel 路径
                if (
                    getattr(actions, "has_secondary_cursors", None) is not None
                    and actions.has_secondary_cursors()
                    and getattr(actions, "extend_selection_left", None) is not None
                ):
                    actions.extend_selection_left()
                elif actions.extend_outward_left is not None:
                    actions.extend_outward_left()
            else:
                actions.move_left()
            return True
        if norm == "arrowright":
            if e.shift:
                # 多光标模式：扩展所有光标选区（主+副），不走 outward_sel 路径
                if (
                    getattr(actions, "has_secondary_cursors", None) is not None
                    and actions.has_secondary_cursors()
                    and getattr(actions, "extend_selection_right", None) is not None
                ):
                    actions.extend_selection_right()
                elif actions.extend_outward_right is not None:
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
        # Ctrl+Shift+L：当前行转为任务列表项（- [ ] content），浏览/编辑两态均生效，
        # 与 Ctrl+0~6 切换标题行为一致（浏览态用 cursor_line 兜底目标行）。
        # 代码块/表格聚焦时跳过（避免在原生控件内误触发行级转换）。
        if matches(combo, shortcuts.get("format_task", "ctrl+shift+l")):
            if actions is not None and not self._native_field_focused(actions):
                actions.format_task()
            return
        # Ctrl+T：当前行转为 2×2 表格（浏览/编辑两态均生效），与 format_math_block
        # 行为一致。代码块/表格聚焦时跳过（避免在原生控件内误触发行级转换）。
        if matches(combo, shortcuts.get("format_table", "ctrl+t")):
            if actions is not None and not self._native_field_focused(actions):
                actions.format_table()
            return
        # Ctrl+L：当前行转为无序列表（浏览/编辑两态均生效）。
        if matches(combo, shortcuts.get("format_list", "ctrl+l")):
            if actions is not None and not self._native_field_focused(actions):
                actions.set_block(BlockType.LIST_UO)
            return
        # Ctrl+Shift+U：当前行转为水平分割线（浏览/编辑两态均生效）。
        if matches(combo, shortcuts.get("format_hr", "ctrl+shift+u")):
            if actions is not None and not self._native_field_focused(actions):
                actions.set_block(BlockType.HR)
            return
        # Ctrl+Shift+F：全局查找（切到侧边栏搜索面板）。
        if matches(combo, shortcuts.get("global_find", "ctrl+shift+f")):
            cb["focus_search"]()
            return
        # Alt+C：切换当前任务列表项勾选状态，浏览/编辑两态均生效。
        # 非任务行静默忽略（toggle_task_at_cursor 内部守卫），无副作用。
        if matches(combo, shortcuts.get("toggle_task", "alt+c")):
            if actions is not None and not self._native_field_focused(actions):
                actions.toggle_task_at_cursor()
            return
        # Ctrl+;：插入当前日期（YYYY-MM-DD），浏览/编辑两态均生效。
        # 代码块/表格聚焦时跳过（交由原生 TextField）。
        if matches(combo, shortcuts.get("insert_date", "ctrl+;")):
            if actions is not None and not self._native_field_focused(actions):
                actions.insert_text(datetime.now().strftime("%Y-%m-%d"))
            return
        # Ctrl+Shift+;：插入当前日期时间（YYYY-MM-DD HH:mm:ss），浏览/编辑两态均生效。
        if matches(combo, shortcuts.get("insert_datetime", "ctrl+shift+;")):
            if actions is not None and not self._native_field_focused(actions):
                actions.insert_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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
            elif matches(combo, shortcuts.get("save_as", "ctrl+shift+s")):
                page.run_task(cb["save_as"])
            elif matches(combo, shortcuts.get("new", "ctrl+n")):
                cb["new"]()
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
                # 浏览态也触发：图片粘贴（paste_image_from_clipboard）在浏览/编辑
                # 两态均生效（浏览态插入到 cursor_line 行尾）；文本粘贴仅编辑态
                # （_do_paste_check 内部按 cursor_li 判断）
                if actions is not None:
                    self._paste_old_draft.current = ""
                    self._begin_paste(actions)
                    page.run_task(self._do_paste_check)
            elif matches(combo, shortcuts.get("paste_plain", "ctrl+shift+v")):
                # 纯文本粘贴：仅编辑态（cursor_li is not None），不触发图片粘贴
                if actions is not None:
                    self._paste_old_draft.current = ""
                    self._begin_paste(actions)
                    page.run_task(self._do_paste_plain_check)
            return
        # edit 层
        if matches(combo, shortcuts.get("save", "ctrl+s")):
            page.run_task(cb["save"])
        elif matches(combo, shortcuts.get("save_as", "ctrl+shift+s")):
            page.run_task(cb["save_as"])
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
        elif matches(combo, shortcuts.get("copy", "ctrl+c")):
            if actions is None or actions.cursor_li is None:
                page.run_task(self._do_copy)
        elif matches(combo, shortcuts.get("cut", "ctrl+x")):
            # 编辑态走 cut_current_line（剪切当前行），浏览态走 handle_cut（选区剪切）
            page.run_task(self._do_cut)
        elif matches(combo, shortcuts.get("paste", "ctrl+v")):
            # 图片粘贴优先于文本粘贴（_do_paste_check 内部按 cursor_li 判断
            # 走图片还是文本路径），编辑/浏览两态均触发
            if actions is not None:
                self._paste_old_draft.current = ""
                self._begin_paste(actions)
                page.run_task(self._do_paste_check)
        elif matches(combo, shortcuts.get("paste_plain", "ctrl+shift+v")):
            # Ctrl+Shift+V：纯文本粘贴（剥离 Markdown 语法）
            # 非原生粘贴快捷键，Flutter TextField 不拦截 → 无双重插入风险
            if actions is not None:
                self._paste_old_draft.current = ""
                self._begin_paste(actions)
                page.run_task(self._do_paste_plain_check)

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
        """Ctrl+V 后异步检查剪贴板：优先图片粘贴，否则智能文本粘贴。

        优先级（Typora 式）：
        1. paste_image_from_clipboard：剪贴板含图片/图片文件 → 落盘 ./assets/ 插入 ![](...)
           浏览/编辑两态均生效（浏览态插入到 cursor_line 行尾）
        2. 智能文本粘贴（仅编辑态）：
           a. 优先读取 HTML Format（Windows API），转换为 Markdown（保留链接/格式/换行）
           b. HTML 不存在或转换失败 → 回退纯文本（Flet clipboard.get()）
           统一走 handle_paste 拆分多行，原生 TextField 的 on_change 已被
           paste_in_progress 标志拦截，无重复插入风险
        """
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        try:
            # 1. 图片粘贴优先（浏览/编辑两态）
            if actions.paste_image_from_clipboard is not None:
                try:
                    handled = await actions.paste_image_from_clipboard()
                except Exception:
                    handled = False
                if handled:
                    return
            # 2. 文本粘贴仅编辑态
            if actions.cursor_li is None:
                return
            # 2a. 优先尝试 HTML Format 智能转换（Typora 式：保留链接/格式/换行）
            text = await self._get_smart_paste_text()
            if not text:
                return
            try:
                actions.handle_paste(text, self._paste_old_draft.current)
            except Exception:
                return
        finally:
            # 兜底重置 paste_in_progress（handle_paste 内部已重置，此处防御性）
            self._end_paste(actions)

    async def _get_smart_paste_text(self) -> str:
        """智能获取粘贴文本：优先 HTML→Markdown 转换，回退纯文本。

        浏览器复制富文本时同时写入 CF_UNICODETEXT（纯文本）和 HTML Format。
        纯文本丢失链接 URL 和格式信息，HTML Format 保留完整结构。
        此方法优先读 HTML Format 转为 Markdown，回退纯文本。
        """
        # 优先尝试 HTML Format（Windows API，非 Windows 返回 None）
        try:
            html = await get_clipboard_html_async()
        except Exception:
            html = None
        if html and html.strip():
            md = html_to_markdown(html)
            if md and md.strip():
                return md.rstrip("\n")
        # 回退纯文本（Flet clipboard.get）
        clipboard = self._clipboard_ref.current
        if clipboard is None:
            return ""
        try:
            return await clipboard.get() or ""
        except Exception:
            return ""

    async def _do_multi_cursor_paste(self) -> None:
        """多光标 Ctrl+V：读取剪贴板文本，智能粘贴到所有光标。

        VSCode 智能粘贴：剪贴板行数 == 光标数时逐行分配（第 i 行→第 i 个光标），
        否则全文插入到主光标并清除副光标（回退单光标粘贴）。
        图片粘贴优先于多光标文本粘贴。
        优先 HTML→Markdown 转换（保留链接/格式），回退纯文本。
        """
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        try:
            # 图片粘贴优先
            if actions.paste_image_from_clipboard is not None:
                try:
                    handled = await actions.paste_image_from_clipboard()
                except Exception:
                    handled = False
                if handled:
                    return
            text = await self._get_smart_paste_text()
            if not text:
                return
            try:
                actions.paste_to_multi_cursors(text)
            except Exception:
                return
        finally:
            self._end_paste(actions)

    async def _do_paste_plain_check(self) -> None:
        """Ctrl+Shift+V：纯文本粘贴（剥离 Markdown 语法后插入）。

        Typora 式 Ctrl+Shift+V：读取剪贴板 → strip_markdown 去除所有语法标记
        → handle_paste_plain 插入纯文本。

        与 _do_paste_check 的区别：
        - 不触发图片粘贴（纯文本模式，仅文本）
        - 不跳过单行文本（Ctrl+Shift+V 非原生粘贴快捷键，Flutter TextField
          不拦截 → 无双重插入风险，单行 Markdown 也能被剥离）
        - 仅编辑态生效（cursor_li is not None）
        """
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        try:
            # 纯文本粘贴仅编辑态（浏览态无光标位置）
            if actions.cursor_li is None:
                return
            clipboard = self._clipboard_ref.current
            if clipboard is None:
                return
            try:
                text = await clipboard.get()
            except Exception:
                return
            if not text:
                return
            try:
                actions.handle_paste_plain(text, self._paste_old_draft.current)
            except Exception:
                return
        finally:
            self._end_paste(actions)

    async def _do_multi_cursor_paste_plain(self) -> None:
        """多光标 Ctrl+Shift+V：纯文本粘贴到所有光标。

        先 strip_markdown 剥离语法，再走 paste_to_multi_cursors_plain 智能分配。
        不触发图片粘贴（纯文本模式）。
        """
        await asyncio.sleep(0.05)
        actions = self._actions_ref.current
        if actions is None:
            return
        try:
            clipboard = self._clipboard_ref.current
            if clipboard is None:
                return
            try:
                text = await clipboard.get()
            except Exception:
                return
            if not text:
                return
            try:
                actions.paste_to_multi_cursors_plain(text)
            except Exception:
                return
        finally:
            self._end_paste(actions)
