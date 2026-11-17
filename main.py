"""项目入口：为 src 布局补 sys.path，再委托给应用 bootstrap。

隔离打包（flet / serious_python / PyInstaller）不会执行 pip install -e .，
包被放到 <app>/src/cs_markdown_editor/，而运行时 sys.path 只有 <app>/ 根。
这段引导在开发态与打包态都成立，删掉即打包后 ModuleNotFoundError。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path and (_SRC / "cs_markdown_editor").exists():
    sys.path.insert(0, str(_SRC))

from cs_markdown_editor.bootstrap import (  # noqa: E402
    _dpi_scale,
    _heartbeat,
    main,
    main_sync,
    run,
)

__all__ = ["main", "main_sync", "run", "_dpi_scale", "_heartbeat"]

if __name__ == "__main__":
    run()
