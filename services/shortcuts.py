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

import flet as ft

# 浏览态 / 编辑态两层快捷键默认值（原 main.py _DEFAULT_SETTINGS["shortcuts"]）
DEFAULT_SHORTCUTS: dict[str, dict[str, str]] = {
    "browse": {
        "save": "ctrl+s",
        "open": "ctrl+o",
        "new": "ctrl+n",
        "undo": "ctrl+z",
        "redo": "ctrl+y",
        "redo_alt": "ctrl+shift+z",
        "toggle_sidebar": "ctrl+b",
        "toggle_theme": "ctrl+shift+l",
        "toggle_raw": "ctrl+/",
        "open_settings": "ctrl+comma",
        "focus_mode": "ctrl+k",
        "close_tab": "ctrl+w",
        "next_tab": "ctrl+tab",
        "prev_tab": "ctrl+shift+tab",
    },
    "edit": {
        "save": "ctrl+s",
        "undo": "ctrl+z",
        "redo": "ctrl+y",
        "redo_alt": "ctrl+shift+z",
        "toggle_raw": "ctrl+enter",
        "toggle_sidebar": "escape",
        "focus_mode": "ctrl+k",
    },
}


@dataclass(frozen=True)
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
    ActionDef("open", "打开", "browse", "文件", "打开 Markdown 文件。",
              {"browse": "ctrl+o"}),
    ActionDef("new", "新建", "browse", "文件", "创建空白文档。",
              {"browse": "ctrl+n"}),
    ActionDef("undo", "撤销", "both", "编辑", "回退上一笔编辑。",
              {"browse": "ctrl+z", "edit": "ctrl+z"}),
    ActionDef("redo", "重做", "both", "编辑", "恢复最近撤销的编辑。",
              {"browse": "ctrl+y", "edit": "ctrl+y"}),
    ActionDef("redo_alt", "重做（备用）", "both", "编辑", "兼容 VS Code 风格的重做键位。",
              {"browse": "ctrl+shift+z", "edit": "ctrl+shift+z"}),
    ActionDef("toggle_sidebar", "切换侧边栏", "both", "视图", "显示或隐藏侧边栏。",
              {"browse": "ctrl+b", "edit": "escape"}),
    ActionDef("toggle_theme", "切换主题", "browse", "视图", "在亮色与暗色主题间切换。",
              {"browse": "ctrl+shift+l"}),
    ActionDef("toggle_raw", "原文模式", "both", "写作", "在可视化编辑与原始 Markdown 间切换。",
              {"browse": "ctrl+/", "edit": "ctrl+enter"}),
    ActionDef("open_settings", "打开设置", "browse", "设置", "进入设置中心。",
              {"browse": "ctrl+comma"}),
    ActionDef("focus_mode", "聚焦模式", "both", "视图", "切换窗口全屏聚焦写作。",
              {"browse": "ctrl+k", "edit": "ctrl+k"}),
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
    ActionDef("format_quote", "引用", "edit", "格式", "将当前行切换为引用块。", {}),
    ActionDef("format_code_block", "代码块", "edit", "格式", "将当前行切换为代码块。", {}),
    ActionDef("format_hr", "分隔线", "edit", "格式", "将当前行切换为分隔线。", {}),
    ActionDef("format_bold", "加粗", "edit", "行内格式", "切换当前段落的加粗。", {}),
    ActionDef("format_italic", "斜体", "edit", "行内格式", "切换当前段落的斜体。", {}),
    ActionDef("format_code", "行内代码", "edit", "行内格式", "切换当前段落的行内代码。", {}),
    ActionDef("format_link", "链接", "edit", "行内格式", "为当前段落插入或移除链接。", {}),
    ActionDef("format_strike", "删除线", "edit", "行内格式", "切换当前段落的删除线。", {}),
]

_LAYERS = ("browse", "edit")


def normalize(combo: str) -> str:
    """规范化快捷键字符串：去空格、小写、ctrl+comma → ctrl+,。"""
    combo = (combo or "").strip().lower().replace(" ", "")
    if combo == "ctrl+comma":
        return "ctrl+,"
    return combo


def matches(combo: str, target: str) -> bool:
    """判断 combo 是否匹配 target（兼容 ctrl+comma ↔ ctrl+, 写法）。"""
    return combo == target or (
        target == "ctrl+," and combo in {"ctrl+comma", "ctrl+,"}
    )


class ShortcutManager:
    """快捷键管理器：读取/更新/重置/冲突检测。

    main.py 持有实例，SettingsDialog 通过实例方法渲染动作行，KeyDispatcher
    通过 get(layer) 读取当前键位。
    """

    def __init__(self, settings: dict, update_setting: Callable[[str, object], None]):
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
