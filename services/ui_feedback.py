"""UI 反馈工具：Flet 0.86 兼容的 SnackBar 提示。

依赖项：flet。
对外接口：show_snack(page, msg)。

设计要点：
- Flet 0.86.2 的 Page 对象无 open() 方法，SnackBar 通过 overlay + open=True 实现，
  on_dismiss 时从 overlay 移除避免列表无限增长（项目 Hard Constraint）。
- page 为 None 时静默跳过，调用方无需前置判空。
- 从 main.py 的 App 闭包剥离：原 _show_snack 嵌套定义在闭包内，此处统一为
  可复用、可独立测试的模块函数，供 main.py 及未来其他视图共享。
"""

import flet as ft


def show_snack(page: ft.Page, msg: str) -> None:
    """在页面底部弹出 SnackBar 提示。

    page 为 None 时静默跳过。
    """
    if page is None:
        return
    snack = ft.SnackBar(content=ft.Text(msg))

    def _on_dismiss(e):
        try:
            page.overlay.remove(snack)
        except (ValueError, AttributeError):
            pass

    snack.on_dismiss = _on_dismiss
    snack.open = True
    page.overlay.append(snack)
    page.update()
