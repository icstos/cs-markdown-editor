"""快捷键管理：动作注册、键位匹配、冲突检测。

从 main.py 抽出，消除 main.py 中 ~200 行快捷键相关代码。原 _ACTION_REGISTRY、
_DEFAULT_SETTINGS["shortcuts"]、_normalize_shortcut、_shortcut_conflicts、
_conflict_map、_conflict_summary、_first_conflict_target、get_shortcuts、
update_shortcut、_reset_action 等函数全部封装为 ShortcutManager 类方法。

main.py 仅持有 ShortcutManager 实例，设置面板（SettingsDialog）与 KeyDispatcher
通过实例方法访问。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

# 浏览态 / 编辑态两层快捷键默认值（原 main.py _DEFAULT_SETTINGS["shortcuts"]）
DEFAULT_SHORTCUTS: dict[str, dict[str, str]] = {
    "browse": {
        "save": "ctrl+s",
        "save_as": "ctrl+shift+s",
        "open": "ctrl+o",
        "open_folder": "ctrl+shift+o",
        "new": "ctrl+n",
        "undo": "ctrl+z",
        "redo": "ctrl+y",
        "redo_alt": "ctrl+shift+z",
        "toggle_sidebar": "ctrl+shift+b",
        "toggle_theme": "ctrl+shift+l",
        "toggle_raw": "ctrl+/",
        "open_settings": "ctrl+comma",
        "focus_mode": "ctrl+shift+k",
        "close_tab": "ctrl+w",
        "next_tab": "ctrl+tab",
        "prev_tab": "ctrl+shift+tab",
        "toggle_word_wrap": "alt+z",
        "toggle_split_editor": "ctrl+\\",
        "copy": "ctrl+c",
        "cut": "ctrl+x",
        "paste": "ctrl+v",
        "paste_plain": "ctrl+shift+v",
        "select_all": "ctrl+a",
        "format_math_block": "ctrl+shift+m",
        "format_table": "ctrl+alt+t",
        "toggle_task": "alt+c",
        "focus_search": "ctrl+f",
        "toggle_replace_bar": "ctrl+h",
        "replace_current": "alt+enter",
        "replace_all": "ctrl+alt+enter",
        "insert_date": "ctrl+;",
        "insert_datetime": "ctrl+shift+;",
    },
    "edit": {
        "save": "ctrl+s",
        "save_as": "ctrl+shift+s",
        "undo": "ctrl+z",
        "redo": "ctrl+y",
        "redo_alt": "ctrl+shift+z",
        "toggle_raw": "ctrl+enter",
        "toggle_sidebar": "escape",
        "focus_mode": "ctrl+shift+k",
        "toggle_word_wrap": "alt+z",
        "format_bold": "ctrl+b",
        "format_italic": "ctrl+i",
        "format_highlight": "ctrl+shift+h",
        "format_strike": "alt+shift+5",
        "format_code": "ctrl+`",
        "format_link": "ctrl+k",
        "format_inline_math": "ctrl+m",
        "format_math_block": "ctrl+shift+m",
        "format_table": "ctrl+alt+t",
        "copy": "ctrl+c",
        "cut": "ctrl+x",
        "paste": "ctrl+v",
        "paste_plain": "ctrl+shift+v",
        "select_all": "ctrl+a",
        "toggle_task": "alt+c",
        "format_task": "ctrl+shift+t",
        "focus_search": "ctrl+f",
        "toggle_replace_bar": "ctrl+h",
        "replace_current": "alt+enter",
        "replace_all": "ctrl+alt+enter",
        "insert_date": "ctrl+;",
        "insert_datetime": "ctrl+shift+;",
    },
}


@dataclass(frozen=True, slots=True)
class ActionDef:
    """单个动作的元信息（原 _ACTION_REGISTRY 列表元素）。"""

    id: str
    label: str
    scope: str  # "both" | "browse" | "edit"
    category: str
    description: str
    default: dict[str, str]  # {layer: combo}


# 动作注册表（原 main.py _ACTION_REGISTRY）
ACTION_REGISTRY: list[ActionDef] = [
    ActionDef("save", "保存", "both", "文件", "保存当前文档到磁盘。",
              {"browse": "ctrl+s", "edit": "ctrl+s"}),
    ActionDef("save_as", "另存为", "both", "文件", "另存为新文件（Ctrl+Shift+S），保存后切换到新路径。",
              {"browse": "ctrl+shift+s", "edit": "ctrl+shift+s"}),
    ActionDef("open", "打开", "browse", "文件", "打开 Markdown 文件。",
              {"browse": "ctrl+o"}),
    ActionDef("open_folder", "打开文件夹", "browse", "文件", "打开文件夹作为工作区，锚定侧边栏文件树根目录。",
              {"browse": "ctrl+shift+o"}),
    ActionDef("new", "新建", "browse", "文件", "创建空白文档。",
              {"browse": "ctrl+n"}),
    ActionDef("undo", "撤销", "both", "编辑", "回退上一笔编辑。",
              {"browse": "ctrl+z", "edit": "ctrl+z"}),
    ActionDef("redo", "重做", "both", "编辑", "恢复最近撤销的编辑。",
              {"browse": "ctrl+y", "edit": "ctrl+y"}),
    ActionDef("redo_alt", "重做（备用）", "both", "编辑", "兼容 VS Code 风格的重做键位。",
              {"browse": "ctrl+shift+z", "edit": "ctrl+shift+z"}),
    ActionDef("toggle_sidebar", "切换侧边栏", "both", "视图", "显示或隐藏侧边栏。",
              {"browse": "ctrl+shift+b", "edit": "escape"}),
    ActionDef("toggle_theme", "切换主题", "browse", "视图", "在亮色与暗色主题间切换。",
              {"browse": "ctrl+shift+l"}),
    ActionDef("toggle_raw", "原文模式", "both", "写作", "在可视化编辑与原始 Markdown 间切换。",
              {"browse": "ctrl+/", "edit": "ctrl+enter"}),
    ActionDef("open_settings", "打开设置", "browse", "设置", "进入设置中心。",
              {"browse": "ctrl+comma"}),
    ActionDef("focus_mode", "聚焦模式", "both", "视图", "切换窗口全屏聚焦写作。",
              {"browse": "ctrl+shift+k", "edit": "ctrl+shift+k"}),
    ActionDef("toggle_word_wrap", "自动换行", "both", "视图", "切换文档长行是否自动换行（VSCode 风格 Alt+Z）。",
              {"browse": "alt+z", "edit": "alt+z"}),
    ActionDef("toggle_split_editor", "拆分编辑器", "both", "视图", "向右拆分编辑器，多视口查看同一文档（VSCode 风格 Ctrl+\\）。",
              {"browse": "ctrl+\\"}),
    ActionDef("close_tab", "关闭标签", "browse", "视图", "关闭当前标签（全局生效，脏标签走确认）。",
              {"browse": "ctrl+w"}),
    ActionDef("next_tab", "下一个标签", "browse", "视图", "切换到右侧标签（循环）。",
              {"browse": "ctrl+tab"}),
    ActionDef("prev_tab", "上一个标签", "browse", "视图", "切换到左侧标签（循环）。",
              {"browse": "ctrl+shift+tab"}),
    ActionDef("format_h1", "一级标题", "edit", "格式", "将当前行切换为一级标题。", {}),
    ActionDef("format_h2", "二级标题", "edit", "格式", "将当前行切换为二级标题。", {}),
    ActionDef("format_h3", "三级标题", "edit", "格式", "将当前行切换为三级标题。", {}),
    ActionDef("format_paragraph", "正文段落", "edit", "格式", "将当前行切换为普通段落。", {}),
    ActionDef("format_list", "无序列表", "edit", "格式", "将当前行切换为无序列表。", {}),
    ActionDef("format_task", "任务列表", "edit", "格式", "将当前行切换为任务列表项（- [ ]）。", {"edit": "ctrl+shift+t"}),
    ActionDef("toggle_task", "切换任务状态", "both", "编辑", "勾选/取消勾选当前任务列表项。",
              {"browse": "alt+c", "edit": "alt+c"}),
    ActionDef("format_quote", "引用", "edit", "格式", "将当前行切换为引用块。", {}),
    ActionDef("format_code_block", "代码块", "edit", "格式", "将当前行切换为代码块。", {}),
    ActionDef("format_math_block", "公式块", "both", "格式", "将当前行切换为块级公式（$$...$$），并进入编辑态。",
              {"browse": "ctrl+shift+m", "edit": "ctrl+shift+m"}),
    ActionDef("format_hr", "分隔线", "edit", "格式", "将当前行切换为分隔线。", {}),
    ActionDef("format_table", "表格", "both", "格式",
              "将当前行切换为 2×2 表格（1 表头 + 1 数据行），并进入表格编辑态。",
              {"browse": "ctrl+alt+t", "edit": "ctrl+alt+t"}),
    ActionDef("format_bold", "加粗", "edit", "行内格式", "选中文本包裹 **，无选中插入空标记。", {"edit": "ctrl+b"}),
    ActionDef("format_italic", "斜体", "edit", "行内格式", "选中文本包裹 *，无选中插入空标记。", {"edit": "ctrl+i"}),
    ActionDef("format_highlight", "高亮", "edit", "行内格式", "选中文本包裹 ==，无选中插入空标记。", {"edit": "ctrl+shift+h"}),
    ActionDef("format_code", "行内代码", "edit", "行内格式", "选中文本包裹 `，无选中插入空标记。", {"edit": "ctrl+`"}),
    ActionDef("format_link", "链接", "edit", "行内格式", "选中文本包裹为 [text](url)，无选中插入空链接。", {"edit": "ctrl+k"}),
    ActionDef("format_inline_math", "行内公式", "edit", "行内格式", "选中文本包裹 $，无选中插入空标记。", {"edit": "ctrl+m"}),
    ActionDef("format_strike", "删除线", "edit", "行内格式", "选中文本包裹 ~~，无选中插入空标记。", {"edit": "alt+shift+5"}),
    ActionDef("copy", "复制", "both", "编辑", "复制选区文本到剪贴板。",
              {"browse": "ctrl+c", "edit": "ctrl+c"}),
    ActionDef("cut", "剪切", "both", "编辑", "复制选区文本到剪贴板并删除选中内容。",
              {"browse": "ctrl+x", "edit": "ctrl+x"}),
    ActionDef("paste", "粘贴", "both", "编辑", "在光标处插入剪贴板内容，多行自动拆分。",
              {"browse": "ctrl+v", "edit": "ctrl+v"}),
    ActionDef("select_all", "全选", "both", "编辑", "选中整个文档内容。",
              {"browse": "ctrl+a", "edit": "ctrl+a"}),
    ActionDef("focus_search", "聚焦搜索", "both", "视图", "切到侧边栏搜索面板并聚焦搜索框。",
              {"browse": "ctrl+f", "edit": "ctrl+f"}),
    ActionDef("toggle_replace_bar", "切换替换条", "both", "视图",
              "展开/收起替换条（VSCode 风格 Ctrl+H），自动切到搜索面板。",
              {"browse": "ctrl+h", "edit": "ctrl+h"}),
    ActionDef("replace_current", "替换当前匹配", "both", "编辑",
              "替换当前匹配项并跳到下一个匹配。",
              {"browse": "alt+enter", "edit": "alt+enter"}),
    ActionDef("replace_all", "全部替换", "both", "编辑",
              "替换当前文档/工作区内所有匹配项。",
              {"browse": "ctrl+alt+enter", "edit": "ctrl+alt+enter"}),
    ActionDef("insert_date", "插入日期", "both", "插入",
              "在光标处插入当前日期（YYYY-MM-DD），浏览态在当前行末尾插入。",
              {"browse": "ctrl+;", "edit": "ctrl+;"}),
    ActionDef("insert_datetime", "插入日期时间", "both", "插入",
              "在光标处插入当前日期时间（YYYY-MM-DD HH:mm:ss），浏览态在当前行末尾插入。",
              {"browse": "ctrl+shift+;", "edit": "ctrl+shift+;"}),
]

_LAYERS = ("browse", "edit")

# 行内格式动作 id 列表（与 ACTION_REGISTRY 中 format_* 对应）。
# 用于动态构建 combo→fmt_name 映射，替代 key_bindings.py 的硬编码 _INLINE_COMBO_MAP。
# fmt_name = action_id[len("format_"):]，与 EditorActions.apply_inline_format 入参一致。
_INLINE_FORMAT_ACTIONS: tuple[str, ...] = (
    "format_bold",
    "format_italic",
    "format_highlight",
    "format_strike",
    "format_code",
    "format_link",
    "format_inline_math",
)


# 键别名词典：把用户/系统多种写法归一化为 _combo 输出的规范形式。
# - escape ↔ esc：_combo 把 KeyboardEvent.key="escape" 映射为 "esc"，
#   而 DEFAULT_SHORTCUTS 中 toggle_sidebar(edit) 用 "escape"，需归一化才能匹配。
# - comma ↔ ,：open_settings 默认 "ctrl+comma"，_combo 把 "," 保持为 ","，
#   归一化后 "ctrl+comma" 与 "ctrl+," 等价。
# - : ↔ ;：Shift+; 在 US 键盘产生 ":"，Ctrl+Shift+; 的 _combo 输出为 "ctrl+shift+:"，
#   归一化后与 "ctrl+shift+;" 等价，保证 insert_datetime 快捷键匹配。
_KEY_ALIASES: dict[str, str] = {"escape": "esc", "comma": ",", ":": ";"}


def normalize(combo: str) -> str:
    """规范化快捷键字符串：去空格、小写、按 '+' 拆分后逐段应用键别名。

    例：ctrl+comma → ctrl+,；escape → esc；Ctrl+Shift+Z → ctrl+shift+z。
    使 _combo（KeyboardEvent 规范化）输出与 settings 中的多种写法可比较，
    matches 据此实现对称匹配。
    """
    combo = (combo or "").strip().lower().replace(" ", "")
    if not combo:
        return ""
    parts = [_KEY_ALIASES.get(p, p) for p in combo.split("+")]
    return "+".join(parts)


def matches(combo: str, target: str) -> bool:
    """判断 combo 是否匹配 target（双侧规范化后比较，对称且兼容别名写法）。"""
    return normalize(combo) == normalize(target)


class ShortcutManager:
    """快捷键管理器：读取/更新/重置/冲突检测。

    main.py 持有实例，SettingsDialog 通过实例方法渲染动作行，KeyDispatcher
    通过 get(layer) 读取当前键位。
    """

    def __init__(self, settings: dict[str, Any], update_setting: Callable[[str, object], None]):
        self._settings = settings
        self._update_setting = update_setting

    # ---- 读取 ----
    def get(self, layer: str) -> dict[str, str]:
        """返回某层的 {action_id: combo} 字典。"""
        return dict(self._settings.get("shortcuts", DEFAULT_SHORTCUTS).get(layer, {}))

    def shortcut(self, layer: str, action_id: str) -> str:
        """读取单个动作的当前键位。"""
        return self.get(layer).get(action_id, "")

    def action_def(self, action_id: str) -> ActionDef | None:
        for a in ACTION_REGISTRY:
            if a.id == action_id:
                return a
        return None

    def layers(self) -> tuple[str, ...]:
        return _LAYERS

    def actions_for_layer(self, layer: str) -> list[ActionDef]:
        return [a for a in ACTION_REGISTRY if a.scope in ("both", layer)]

    def inline_format_combos(self) -> dict[str, str]:
        """返回 {normalize(combo): fmt_name}，从 edit 层配置动态构建。

        替代 key_bindings.py 中硬编码的 _INLINE_COMBO_MAP：用户在设置页修改
        format_bold 等键位后，此处读取最新配置，行内格式快捷键随之生效。
        fmt_name = action_id[len("format_"):]（如 "format_bold" → "bold"），
        与 EditorActions.apply_inline_format 入参一致。未绑定的动作不包含在结果中。
        """
        edit = self.get("edit")
        result: dict[str, str] = {}
        for action_id in _INLINE_FORMAT_ACTIONS:
            combo = edit.get(action_id, "")
            if combo:
                result[normalize(combo)] = action_id[len("format_"):]
        return result

    # ---- 修改 ----
    def update(self, layer: str, action: str, combo: str):
        shortcuts = dict(self._settings.get("shortcuts", DEFAULT_SHORTCUTS))
        layer_map = dict(shortcuts.get(layer, {}))
        layer_map[action] = combo
        shortcuts[layer] = layer_map
        self._update_setting("shortcuts", shortcuts)

    def reset(self, layer: str, action_id: str):
        action = self.action_def(action_id)
        if action is None:
            return
        shortcuts = dict(self._settings.get("shortcuts", DEFAULT_SHORTCUTS))
        layer_map = dict(shortcuts.get(layer, {}))
        layer_map[action_id] = action.default.get(layer, "")
        shortcuts[layer] = layer_map
        self._update_setting("shortcuts", shortcuts)

    def reset_all(self):
        """恢复全部快捷键到默认。"""
        self._update_setting("shortcuts", {k: dict(v) for k, v in DEFAULT_SHORTCUTS.items()})

    # ---- 冲突检测 ----
    def conflicts(self, layer: str) -> list[tuple[str, str, str]]:
        """返回 [(combo, action_a, action_b), ...]。"""
        items = list(self.get(layer).items())
        seen: dict[str, str] = {}
        conflicts: list[tuple[str, str, str]] = []
        for action, combo in items:
            norm = normalize(combo)
            if not norm:
                continue
            if norm in seen:
                conflicts.append((norm, seen[norm], action))
            else:
                seen[norm] = action
        return conflicts

    def conflict_map(self, layer: str) -> dict[str, list[str]]:
        cmap: dict[str, list[str]] = {}
        for combo, a, b in self.conflicts(layer):
            cmap.setdefault(combo, []).extend([a, b])
        return cmap

    def conflict_summary(self, layer: str) -> str | None:
        conflicts = self.conflicts(layer)
        if not conflicts:
            return None
        parts = [f"{combo}({a}/{b})" for combo, a, b in conflicts[:3]]
        extra = f" 等{len(conflicts) - 3}项" if len(conflicts) > 3 else ""
        return f"检测到冲突：{'、'.join(parts)}{extra}"

    def first_conflict_target(self) -> tuple[str | None, str | None]:
        """返回 (layer, action_id) 或 (None, None)，用于设置面板定位第一个冲突。"""
        for layer in self.layers():
            cmap = self.conflict_map(layer)
            if cmap:
                combo = next(iter(cmap))
                return layer, cmap[combo][0]
        return None, None
