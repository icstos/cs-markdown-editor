"""项目级路径出口。

源码进 src 布局后，所有“向上数 N 层找 roots”的代码都会失效且不一定报错。
这里集中解析，调用方只依赖本模块。
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve()


def project_root() -> Path:
    """返回仓库/打包应用根：优先找 pyproject.toml，找不到则按固定层级回退。"""
    for parent in _HERE.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    # src/<project>/_paths.py -> parents[2] = 项目根
    return _HERE.parents[2]


def assets_dir() -> Path:
    """返回资源目录；向上查找真实存在的 assets/，未找到则回退到项目根下。"""
    for parent in _HERE.parents:
        candidate = parent / "assets"
        if candidate.is_dir():
            return candidate
    return project_root() / "assets"


def settings_path() -> Path:
    """返回用户设置文件位置（保持项目根 settings.json 的既有约定）。"""
    return project_root() / "settings.json"
