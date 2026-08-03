"""应用设置：默认值、加载、保存、深合并。

依赖项：
- services.shortcuts.DEFAULT_SHORTCUTS：快捷键默认值（深合并保留用户自定义）
- 标准库 json/os

对外接口：
- DEFAULT_SETTINGS：dict，应用级默认设置
- SETTINGS_PATH：str，settings.json 绝对路径
- load_settings() -> dict：读取并深合并用户设置
- save_settings(settings: dict) -> None：持久化设置

设计要点：
- _load_settings 实现深合并 shortcuts：保留用户自定义键位的同时补齐新增默认项
  （避免老 settings.json 缺少 close_tab/next_tab 等新键时 KeyError）
- 所有 IO 异常静默吞掉，回退到默认值（设置文件非关键路径）
"""

import json
import os
from typing import Any

from services.shortcuts import DEFAULT_SHORTCUTS

# PEP 695 类型别名：应用设置字典
type Settings = dict[str, Any]

SETTINGS_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "settings.json"
)

DEFAULT_SETTINGS: Settings = {
    "content_max_width": 1080,
    "content_padding": 36,
    "content_padding_top": 24,
    "show_footer": True,
    "body_font_size": 16,
    "line_height": 1.6,
    "font_family": "Alibaba",
    "auto_save": True,
    "remember_focus_mode": False,
    "show_toolbar": True,
    "word_wrap": True,
    "show_line_numbers": False,
    "code_theme_dark": "ATOM_ONE_DARK",
    "code_theme_light": "GITHUB",
    "export_format": "html",
    "sidebar_open": False,
    "sidebar_panel": "files",
    "sidebar_width": 256,
    "recent_files": [],
    # 工作区文件夹：显式「打开文件夹」后锚定文件树根目录；为 None 时回退到当前文件所在目录
    "workspace_folder": None,
    # 侧边栏搜索选项（默认全 False，符合"搜索当前文档、不区分大小写"惯例）
    "search_folder": False,        # 开启时跨文件搜索（沿用文件树根目录）
    "search_case_sensitive": False,  # 区分大小写
    "search_whole_word": False,    # 查找整个单词（\b 边界）
    "search_regex": False,         # 正则表达式
    "search_replace_expanded": False,  # 替换栏展开状态（VSCode 风格可折叠，Ctrl+H 切换）
    # ============ 自动保存（Typora 风格间隔触发）============
    # auto_save=False 时全部失效；interval=5 表示每 5 分钟对有路径的脏文档自动写盘
    "auto_save_interval": 5,        # 自动保存间隔（分钟），范围 1-30
    "auto_save_on_blur": True,      # 窗口失焦/最小化时立即触发一次自动保存
    # ============ 自动备份与崩溃恢复（独立于自动保存，始终后台运行）============
    "backup_enabled": True,         # 总开关：关闭后不再生成备份，已存在备份仍可恢复
    "backup_interval": 10,          # 定时备份间隔（分钟），范围 5-60
    "backup_retention_days": 30,    # 已命名文档备份保留天数（超期自动清理）
    "recover_untitled_days": 7,     # 未命名草稿保留天数（短于已命名文档）
    "backup_dir": None,             # 自定义备份根目录；None 时使用平台默认路径
    # 外部修改检测：编辑期间监听原文件 mtime 变化，发现外部修改时弹出重载确认
    "detect_external_changes": True,
    "shortcuts": {k: dict(v) for k, v in DEFAULT_SHORTCUTS.items()},
}


def load_settings() -> Settings:
    """读取 settings.json 并与 DEFAULT_SETTINGS 深合并。

    深合并策略：顶层键用用户值覆盖默认值；shortcuts 子键按层深合并，
    保留用户自定义键位的同时补齐新增默认项。读取失败回退到 DEFAULT_SETTINGS。
    """
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_SETTINGS)

        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)

        user_sc = data.get("shortcuts", {})
        merged_sc: dict[str, dict[str, str]] = {}
        if isinstance(user_sc, dict):
            for layer, def_layer in DEFAULT_SETTINGS["shortcuts"].items():
                merged_sc[layer] = {**def_layer, **user_sc.get(layer, {})}
        else:
            merged_sc = {k: dict(v) for k, v in DEFAULT_SETTINGS["shortcuts"].items()}
        merged["shortcuts"] = merged_sc
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: Settings) -> None:
    """持久化设置到 settings.json。IO 失败静默忽略。"""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
