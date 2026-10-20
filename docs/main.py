"""文档站点启动脚本。

用法::

    python docs/main.py                  # 浏览器打开（默认 8550 端口）
    python docs/main.py --port 9000      # 指定端口
    python docs/main.py --desktop        # 桌面窗口打开（调试布局用）

为什么站点放在 `docs/` 而不是应用包里
------------------------------------
它不是编辑器的一部分。`pyproject.toml` 的打包排除项里已有 `docs`，
因此不会被打进安装包；它只**单向**读取 `styles.py` 的配色 token 与
`config/app_meta.py` 的版本号，不反向依赖 `app/` 或 `views/`。

脚本在 `docs/` 下直接运行，Python 默认只把脚本目录放进 `sys.path`，
所以这里显式把仓库根目录插进去——否则 `import styles` 会失败。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import flet as ft

# 仓库根目录（docs/ 的上一级）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cmsite import main as site_main  # noqa: E402  （必须晚于 sys.path 调整）
from cmsite import set_start_anchor, set_start_size, set_start_theme  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="docs",
        description="CS Markdown Editor 文档站点（Flet 声明式单页应用）",
    )
    parser.add_argument("--port", type=int, default=8550, help="Web 端口（默认 8550，0 表示随机）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（局域网共享用 0.0.0.0）")
    parser.add_argument("--desktop", action="store_true", help="用桌面窗口打开（调试布局）")
    parser.add_argument(
        "--theme", choices=("light", "dark"), default="light", help="打开时的主题（默认亮色）"
    )
    parser.add_argument("--width", type=int, default=1280, help="桌面窗口宽度（预览窄屏布局）")
    parser.add_argument("--height", type=int, default=860, help="桌面窗口高度")
    parser.add_argument(
        "--start",
        default=None,
        metavar="ANCHOR",
        help="打开时直接滚到某个区块（hero / features / demo / docs / architecture / about）",
    )
    parser.add_argument(
        "--cdn",
        action="store_true",
        help="从公网 CDN 加载 Flutter 渲染内核（默认走本地，内网 / 离线也能打开）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """站点入口。"""
    args = parse_args(argv)
    set_start_anchor(args.start)
    set_start_theme(args.theme)
    set_start_size(args.width, args.height)
    view = ft.AppView.FLET_APP if args.desktop else ft.AppView.WEB_BROWSER
    ft.run(
        site_main,
        view=view,
        host=None if args.desktop else args.host,
        port=0 if args.desktop else args.port,
        # 字体与主程序同源：assets/fonts/
        assets_dir=str(_ROOT / "assets"),
        # 介绍站点更常见的场景是内网演示，默认本地加载渲染内核
        no_cdn=not args.cdn,
    )


if __name__ == "__main__":
    main()
