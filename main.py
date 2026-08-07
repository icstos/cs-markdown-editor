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
"""

import flet as ft

from app import App


async def main(page: ft.Page):
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
    page.window.width = 1200
    page.window.height = 720
    page.window.min_width = 720
    page.window.min_height = 480
    await page.window.center()
    page.render(App)


def main_sync():
    """同步入口，供 console_scripts 调用。"""
    ft.run(main)


if __name__ == "__main__":
    ft.run(main)
