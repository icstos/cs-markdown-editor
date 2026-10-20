"""快捷键数据：**运行时从 `services.shortcuts.ACTION_REGISTRY` 读取**。

为什么不抄一份静态表
--------------------
文档最容易腐烂的地方就是「键位表」。站点既然是仓库的一部分，就让它在启动时
直接读注册表——编辑器改了默认键，站点下一帧就跟着改，不存在两份数据打架。

读不到时（例如站点被单独拷走、没有 services 包）回退到内置的常用键位子集，
保证页面仍能打开——介绍页不该因为缺少业务模块而白屏。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ShortcutGroup", "ShortcutRow", "humanize", "load_groups"]


@dataclass(frozen=True, slots=True)
class ShortcutRow:
    label: str
    combo: str
    desc: str


@dataclass(frozen=True, slots=True)
class ShortcutGroup:
    name: str
    rows: tuple[ShortcutRow, ...]


#: 分组顺序（注册表里的 category 是中文短词，按使用频率排）
_ORDER: tuple[str, ...] = (
    "文件",
    "编辑",
    "视图",
    "格式",
    "行内格式",
    "插入",
    "写作",
    "设置",
    "Git",
)

_KEY_ALIASES: dict[str, str] = {
    "ctrl": "Ctrl",
    "shift": "Shift",
    "alt": "Alt",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "enter": "Enter",
    "tab": "Tab",
    "escape": "Esc",
    "space": "Space",
    "backquote": "`",
    "semicolon": ";",
    "equal": "=",
    "minus": "-",
}


def humanize(combo: str) -> str:
    """把 `ctrl+shift+s` 变成 `Ctrl + Shift + S`（纯展示用）。"""
    if not combo:
        return "—"
    out: list[str] = []
    for part in (p.strip() for p in combo.split("+")):
        if not part:
            continue
        low = part.lower()
        out.append(_KEY_ALIASES.get(low, part.upper() if len(part) == 1 else part.capitalize()))
    return " + ".join(out) if out else "—"


def _default_combo(default: dict[str, str] | None) -> str:
    """取该动作的默认键位：优先浏览层，其次编辑层。"""
    if not default:
        return ""
    return default.get("browse") or default.get("edit") or ""


def load_groups() -> tuple[ShortcutGroup, ...]:
    """读取注册表并分组；失败时回退内置子集。"""
    try:
        from services.shortcuts import ACTION_REGISTRY

        buckets: dict[str, list[ShortcutRow]] = {}
        for action in ACTION_REGISTRY:
            row = ShortcutRow(
                action.label,
                humanize(_default_combo(getattr(action, "default", None))),
                action.description,
            )
            buckets.setdefault(action.category, []).append(row)
    except Exception:  # 站点被单独拷走 / 依赖缺失：用内置子集兜底
        return _FALLBACK

    groups = [ShortcutGroup(name, tuple(buckets[name])) for name in _ORDER if name in buckets]
    # 注册表里出现新分类时不静默丢弃
    groups += [
        ShortcutGroup(name, tuple(rows)) for name, rows in buckets.items() if name not in _ORDER
    ]
    return tuple(groups)


_FALLBACK: tuple[ShortcutGroup, ...] = (
    ShortcutGroup(
        "文件",
        (
            ShortcutRow("保存", "Ctrl + S", "保存当前文档到磁盘。"),
            ShortcutRow("打开", "Ctrl + O", "打开 Markdown 文件。"),
            ShortcutRow("打开文件夹", "Ctrl + Shift + O", "打开文件夹作为工作区。"),
            ShortcutRow("新建", "Ctrl + N", "创建空白文档。"),
        ),
    ),
    ShortcutGroup(
        "编辑",
        (
            ShortcutRow("撤销", "Ctrl + Z", "回退上一笔编辑。"),
            ShortcutRow("重做", "Ctrl + Y", "恢复最近撤销的编辑。"),
            ShortcutRow("全选", "Ctrl + A", "选中整个文档内容。"),
        ),
    ),
    ShortcutGroup(
        "视图",
        (
            ShortcutRow("切换侧边栏", "Ctrl + Shift + B", "显示或隐藏侧边栏。"),
            ShortcutRow("切换主题", "Alt + T", "在亮色与暗色主题间切换。"),
            ShortcutRow("自动换行", "Alt + Z", "切换长行是否自动换行。"),
        ),
    ),
)
