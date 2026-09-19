"""Markdown 编辑器入口。

- 注册本地字体 AlibabaPuHuiTi-3-55-Regular
- 声明式渲染：page.render(App)
- App 组件已拆分到 app/ 包（AppContext + 控制器模式）：
  - app.App：根组件（hooks + ctx + 装配 + render）
  - app._context.AppContext：状态容器
  - app._tab_management / _file_io_ops / _file_dialogs / _diff_controller /
    _settings_controller / _split_editor / _focus_router / _keyboard：控制器
  - app._render：渲染树构造
  - app._tab_helpers / autosave / diff_scroll_sync：已抽取的纯函数/状态机
- 文档状态上抛到 App 层，便于 New / Open / Save
- 段级编辑、Typora 式实时渲染由 views/editor 负责

日志（异常取证）：应用启动的第一件事就是安装日志（早于 `flet` / `app` 的导入），
并在 `main()` 里挂上「全局异常钩子 + 主线程心跳 + 卡顿看门狗」。日志落在项目内
`logs/`（开发态）或平台标准目录（打包态），详见 utils/log.py 与 AGENT.md
「异常排查」一节。
"""

import asyncio
import ctypes
import logging
import os
import sys

from utils import diagnostics
from utils.log import attach_asyncio, attach_flet, session_header, setup

# 日志必须最先安装：早于 `import flet` / `import app`，否则 flet 与 app 在导入期、
# 启动期抛出的异常只能表现为「窗口没出来」，没有任何可供定位的痕迹。
LOG = setup()

import flet as ft  # noqa: E402
from app import App  # noqa: E402

log = logging.getLogger(__name__)


# Flet 0.86 桌面客户端在 Windows 高分屏（DPI > 100%）下的已知问题：
# window_manager 以物理像素创建窗口，而 Flutter 视口按设备像素比(DPR)换算逻辑
# 尺寸 —— 若按逻辑像素设窗口大小，实际逻辑布局会被压缩到 物理宽度/DPR，
# 导致界面整体被放大、右侧大纲/底部状态栏/滚动条偏离窗口边缘。
# 修复：按系统 DPI 缩放系数放大窗口尺寸，使 物理尺寸 / DPR = 期望逻辑尺寸。
# 100% 缩放下系数为 1.0，行为不变。
def _dpi_scale() -> float:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Desktop\WindowMetrics",
            ) as key:
                dpi, _ = winreg.QueryValueEx(key, "AppliedDPI")
                if isinstance(dpi, int) and dpi > 0:
                    return dpi / 96.0
        except Exception:
            pass
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
            if dpi > 0:
                return dpi / 96.0
        except Exception:
            pass
    return 1.0


async def _heartbeat() -> None:
    """主线程心跳。

    事件循环被同步长任务占住时，这个协程就排不上队 —— 看门狗据此判定「界面
    未响应」并抓栈。反过来，主线程空闲时它会稳定每秒跑一次，所以空闲不会误报。

    `arm()` 放在第一拍里而不是 `main()` 里：只有心跳真的跑起来了才允许看门狗
    判定，否则启动早期（心跳尚未就绪）会满屏误报。
    """
    first = True
    while True:
        diagnostics.beat()
        if first:
            diagnostics.arm()
            first = False
            log.debug("心跳任务已就绪，看门狗进入判定状态")
        await asyncio.sleep(1.0)


async def main(page: ft.Page):
    # ---- 诊断接线：会话头 → 异常钩子 → 看门狗 → 心跳 ----
    log.info(session_header())
    attach_flet(page)
    try:
        attach_asyncio(asyncio.get_running_loop())
    except RuntimeError:  # 理论上不会发生（main 已在事件循环内）
        log.warning("拿不到事件循环，asyncio 异常钩子未挂载", exc_info=True)
    diagnostics.install()
    diagnostics.start_watchdog()
    page.run_task(_heartbeat)
    diagnostics.mark("启动 · 主入口")

    with diagnostics.timed("启动 · 页面配置", slow_ms=500):
        page.title = "Markdown 编辑器"
        page.fonts = {"Alibaba": "fonts/AlibabaPuHuiTi-3-55-Regular.otf"}
        # 亮/暗两套主题，由 App 的 theme_mode state 切换
        # 背景色由 App.apply_theme 通过 page.bgcolor 单独设置，不放在 ColorScheme
        # ColorScheme.surface 与 styles._LIGHT/_DARK.bg 对齐，保证 Flet 原生控件
        # （按钮 / 对话框 / 弹窗）与文档画布同色，无色块错位
        page.theme = ft.Theme(
            font_family="Alibaba",
            color_scheme=ft.ColorScheme(
                surface="#FAFBFC",
                on_surface="#1F2329",
                primary="#1677FF",
            ),
        )
        page.dark_theme = ft.Theme(
            font_family="Alibaba",
            color_scheme=ft.ColorScheme(
                surface="#14161A",
                on_surface="#E6EDF3",
                primary="#6BA0F5",
            ),
        )
        page.theme_mode = ft.ThemeMode.LIGHT
        # 去四周边缘留白：Flet 根视图默认 padding/spacing 各 10px，会在窗口四周留下
        # 空白带——最左活动栏不贴左、顶栏与窗口框架之间有空隙、大纲列右侧与窗口
        # 边缘分离、状态栏下方悬空。置 0 后四列布局与窗口四边严格贴合（VSCode /
        # Obsidian 式整窗布局，紧凑、专业）。
        page.padding = 0
        page.spacing = 0
        _dpr = _dpi_scale()
        page.window.width = round(1200 * _dpr)
        page.window.height = round(720 * _dpr)
        page.window.min_width = round(720 * _dpr)
        page.window.min_height = round(480 * _dpr)
        # Web 模式下无原生窗口，跳过居中（避免 invoke_method 超时）
        if not getattr(page, "web", False):
            await page.window.center()
        page.render(App)

    log.debug("首次渲染已提交，等待 App 挂载")


def main_sync():
    """同步入口，供 console_scripts 调用。"""
    ft.run(main)


if __name__ == "__main__":
    try:
        ft.run(main)
    except BaseException:
        # `ft.run` 自身抛错（端口占用、Flutter 客户端缺失等）也要留下记录：
        # 此时 sys.excepthook 尚未被 Python 调用到，得在这里主动记一笔。
        log.critical("应用启动失败", exc_info=True)
        raise
