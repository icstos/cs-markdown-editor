"""组合键与按键原语（纯函数，无状态）。

从 views/key_bindings.py 迁出：按键规范化（`combo`）与可打印字符提取
（`extract_printable_char`）是纯函数工具，被分发器与测试直接使用，
与键盘分发的状态机无关。
"""

__all__ = ["NON_PRINTABLE_KEYS", "combo", "extract_printable_char"]

def combo(e) -> str:
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


NON_PRINTABLE_KEYS = frozenset({
    "shift", "control", "alt", "meta",
    "tab", "enter", "escape",
    "backspace", "delete", "insert", "printscreen", "pause", "menu",
    "home", "end", "pageup", "pagedown",
    "arrowleft", "arrowright", "arrowup", "arrowdown",
    "capslock", "numlock", "scrolllock",
    "controlleft", "controlright", "shiftleft", "shiftright",
    "altleft", "altright", "metaleft", "metaright",
})


def extract_printable_char(e) -> str | None:
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
    if kl in NON_PRINTABLE_KEYS:
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
