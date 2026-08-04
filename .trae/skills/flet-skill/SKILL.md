---
name: flet-skill
description: flet框架专家，严格遵循官方最新 API 规范与工程化最佳实践输出可直接运行的生产级代码，使用flet构建面向优先windows的、声明式UI的程序，保证代码结构清晰、高内聚、可复用、易维护、符合桌面端交互习惯。全程摒弃命令式手动增删控件、手动调用 update 的写法，保证代码结构符合现代前端工程化规范。
---

# flet-skill

## 使用场景
- 用户要求使用 Flet 编写桌面应用、GUI 工具、界面程序
- 用户需要 Flet 控件、布局、路由、状态管理、事件处理相关代码示例
- 用户需要对现有 Flet 代码进行升级、优化、排错、重构
- 用户要求使用声明式、组件化方式开发 Flet 应用

## 核心开发指令
### 1. 基础规范
- 运行环境：Python 3.12 及以上版本，默认基于 Flet 0.86.2 稳定版
- **核心原则**：UI = f(state)，所有界面变化由状态变更触发，禁止手动增删控件、禁止直接修改控件实例属性来更新 UI。
- 导入规范：统一使用 `import flet as ft`，别名固定为 `ft`
- 启动方式：统一使用 `ft.run(main)` 启动，入口函数接收 `page: ft.Page` 参数
- 渲染入口：使用 `page.render(组件)` 或 `page.render_views(根组件)` 挂载界面，替代 `page.add()` 命令式写法
- 命名规范：组件采用大驼峰命名（PascalCase），Hook 调用必须在组件函数顶层
- 桌面端默认设置窗口标题、最小宽高、居中显示，优先适配 Windows 系统

### 2. 代码结构规范
- 简单需求输出单文件可运行代码，包含完整依赖说明与运行命令
- 中大型项目采用模块化拆分：使用 `ft.component` 封装独立控件，使用声明式开发，按功能划分模块目录
- 状态管理优先使用控件自身属性、`page.session` 或 `page.client_storage`，禁止滥用全局变量
- 事件处理函数命名采用 `on_xxx` 格式，如 `on_click_add_item`
- 异步操作必须使用 `async/await` 语法，耗时操作通过 `page.run_task()` 调度，不得阻塞 UI 主线程
- 主入口文件统一命名为 `main.py`
- UI 组件按功能模块化，放在 `views/` 目录下
- 业务逻辑放在 `services/` 目录下
- 静态资源（图片、字体）放在 `assets/` 目录下

### 3. 函数式组件
- 组件函数接收 props 作为参数，返回单个 Flet 控件或控件列表
- 组件内部状态通过 `ft.use_state()` 声明，状态变更自动触发组件重绘
- 禁止在组件内使用全局变量，所有状态通过 Hooks 或 props 传递
- 子组件通过 props 接收数据和回调函数，遵循单向数据流
### 4. 声明式实现
#### use_state 状态钩子
- 语法：`value, set_value = ft.use_state(initial_value)`
- 用于声明组件局部响应式状态，调用 `set_value()` 后组件自动重绘
- 支持传入函数作为初始值（惰性初始化）：`ft.use_state(lambda: 初始值计算)`
- 支持更新函数写法：`set_value(lambda prev: prev + 1)`，用于依赖前一个状态的更新
- 复杂状态推荐拆分为多个细粒度 state，避免单个状态过大导致无效重绘

#### use_effect 副作用钩子
- 语法：`ft.use_effect(setup_fn, dependencies=None)`
- 用于处理副作用：数据请求、订阅、定时器、手动 DOM 操作等
- `dependencies` 为依赖列表：
  - 传 `[]`：仅组件挂载时执行一次，等效于 componentDidMount
  - 传 `[state1, state2]`：依赖项变化时重新执行
  - 传 `None`：每次重绘都执行（不推荐）
- setup 函数可返回清理函数，用于组件卸载或依赖变化前执行清理逻辑

#### Hooks 铁律
- 只能在 `@ft.component` 装饰的函数组件顶层调用 Hooks
- 禁止在条件判断、循环、嵌套函数内调用 Hooks
- 禁止在普通 Python 函数中调用 Hooks

### 5. 布局与控件使用规范
- 优先使用 `ft.Column`、`ft.Row`、`ft.Container`、`ft.Stack` 构建弹性布局，合理使用 `expand` 属性分配空间，`ft.ResponsiveRow` 适配不同屏幕尺寸
- 输入控件必须添加 `label` 和合理的 `hint_text`，关键操作按钮明确禁用态与加载态
- 列表类长内容必须使用 `ft.ListView`、`ft.GridView` 保证滚动性能，禁止直接在 Column 中堆砌大量控件
- 导航场景优先使用 `ft.Router` 配合 `View` 实现页面路由，复用 0.85.x 优化的 `on_view_pop` 与链式返回逻辑
- 弹窗、提示统一使用 `page.snack_bar`、`ft.AlertDialog`、`ft.BottomSheet`，操作结果必须给出明确视觉反馈


### 6. 样式与主题规范
- 颜色使用 `ft.Colors` 常量，避免硬编码
- 阴影使用 `ft.BoxShadow` 并统一参数

### 7. 路由与多页面
- 路由声明使用 `page.router`，通过 `page.go("/path")` 进行页面跳转
- 利用 `outlet=True` 实现布局嵌套，0.85.3 已修复嵌套布局下的返回逻辑异常问题
- 模态路由使用 `modal=True` 参数，弹窗式页面无需替换完整视图
- 页面返回优先使用 `page.pop()`，禁止手动操作路由历史栈

### 8. 健壮性与性能
- 文件读写、网络请求、数据库操作必须添加 `try-except` 异常捕获，错误信息友好展示
- 频繁更新的局部 UI 调用 `control.update()` 局部刷新，减少全量 `page.update()` 调用
- 图片、大资源使用 `assets` 目录管理，启用缓存机制
- 提取子组件，将状态下沉到最细粒度组件，减少不必要的父级重绘
- 表单控件值绑定到 state，通过 `on_change` 事件更新状态，实现受控组件
- 桌面端支持启动隐藏窗口：配合 `AppView.FLET_APP_HIDDEN` 与 `page.window.visible` 属性实现后台启动

## 输出规范
- 复杂项目先说明目录结构，再给出核心文件代码
- 代码采用纯声明式实现，全程不出现命令式 `page.add()`、手动修改控件属性后 `page.update()` 的写法
- 组件职责单一，复杂场景拆分为多个子组件，体现组件化分层思想

## 示例 
### 示例 1：基础计数器（函数式组件 + use_state）
**输入**：用声明式方法实现一个 Flet 计数器
**输出**：
```python
import flet as ft


@ft.component
def App():
    count, set_count = ft.use_state(0)

    return ft.Row(
        controls=[
            ft.Text(value=f"{count}"),
            ft.Button("Add", on_click=lambda: set_count(count + 1)),
        ],
    )


async def main(page: ft.Page):
    page.title = "flet Counter"
    page.window.width = 400
    page.window.height = 300
    await page.window.center()
    page.render(App)


if __name__ == "__main__":
    ft.run(main)

```
### 示例 2：进度条（ft.component + ft.Observable）
**输入**：用声明式方法实现一个动态更新的进度条
**输出**：
```python
import asyncio
from dataclasses import dataclass

import flet as ft


@dataclass
@ft.observable
class AppState:
    counter: float

    async def start_counter(self):
        self.counter = 0
        for _ in range(0, 10):
            self.counter += 0.1
            await asyncio.sleep(0.5)


@ft.component
def App():
    state, _ = ft.use_state(AppState(counter=0))

    return [
        ft.ProgressBar(state.counter),
        ft.Button("Run!", on_click=state.start_counter),
    ]


async def main(page: ft.Page):
    page.title = "flet Counter"
    page.window.width = 400
    page.window.height = 300
    await page.window.center()
    page.render(App)


if __name__ == "__main__":
    ft.run(main)

```
### 示例 2：进度条（ft.component + ft.Router + ft.Route）
**输入**：使用flet的路由功能实现多页面切换，包含 Home 页面和 About 页面
**输出**：

```python
import flet as ft


@ft.component
def Home():
    return ft.Text("Home page", size=24)


@ft.component
def About():
    return ft.Text("About page", size=24)


@ft.component
def App():
    return ft.SafeArea(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Button(
                            "Home", on_click=lambda: ft.context.page.navigate("/")
                        ),
                        ft.Button(
                            "About", on_click=lambda: ft.context.page.navigate("/about")
                        ),
                    ]
                ),
                ft.Router(
                    [
                        ft.Route(index=True, component=Home),
                        ft.Route(path="about", component=About),
                    ]
                ),
            ]
        )
    )


async def main(page: ft.Page):
    page.title = "flet Counter"
    page.window.width = 400
    page.window.height = 300
    await page.window.center()
    page.render(App)


if __name__ == "__main__":
    ft.run(main)

```

### 示例 4：进度条（ft.component + ft.use_dialog）
**输入**： 使用flet的use_dialog实现一个删除文件的确认弹窗，点击按钮后显示弹窗，确认删除后显示删除中状态，2秒后关闭弹窗
**输出**：

```python
import asyncio
import flet as ft


@ft.component
def App():
    show, set_show = ft.use_state(False)
    deleting, set_deleting = ft.use_state(False)

    async def handle_delete():
        set_deleting(True)
        await asyncio.sleep(2)
        set_deleting(False)
        set_show(False)

    ft.use_dialog(
        ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete report.pdf?"),
            content=ft.Text(
                "Deleting, please wait..." if deleting else "This cannot be undone."
            ),
            actions=[
                ft.Button(
                    "Deleting..." if deleting else "Delete",
                    disabled=deleting,
                    on_click=handle_delete,
                ),
                ft.TextButton(
                    "Cancel",
                    on_click=lambda: set_show(False),
                    disabled=deleting,
                ),
            ],
            on_dismiss=lambda: set_show(False),
        )
        if show
        else None
    )

    return ft.Button(
        "Delete File", icon=ft.Icons.DELETE, on_click=lambda: set_show(True)
    )


async def main(page: ft.Page):
    page.title = "flet Counter"
    page.window.width = 400
    page.window.height = 300
    await page.window.center()
    page.render(App)


if __name__ == "__main__":
    ft.run(main)

```
## 注意事项
1. 禁止使用 Flet 已废弃的旧版 API
使用ft.Icons而不是ft.icons，使用ft.Colors而不是ft.colors，使用ft.BoxFit而不是ft.ImageFit
2. 0.85.x 声明式特有说明
page.render() 和 page.render_views() 是声明式范式的专属挂载方法，0.80+ 版本正式稳定
use_state 更新时会做浅比较，值相同不会触发重绘，优化性能
函数式组件每次重绘都会重新执行函数体，不要在组件内执行耗时计算，使用 use_state 惰性初始化或派生状态
3. 禁止使用的命令式写法
禁止使用 page.add()、page.remove()、page.clean() 手动操作控件树
禁止直接修改控件实例属性后调用 page.update() 刷新界面
禁止通过索引、遍历方式查找修改子控件属性
禁止在事件回调中直接操作控件实例来改变 UI 呈现
4. 混合使用场景
若必须调用命令式 API（如弹窗、SnackBar），通过 page 上下文调用，且仅作为副作用处理
旧项目重构建议逐步替换，从底层业务组件开始改造为声明式，上层逐步迁移
第三方命令式控件可封装为声明式组件，内部通过 self.update() 桥接
5. 常见坑规避
Hooks 必须写在组件函数最顶层，条件渲染内调用会导致状态错位
列表渲染必须加唯一 key，否则列表顺序变化时会出现状态错乱
不要在 use_effect 中直接修改 state 且不设依赖，会导致无限重绘