# 文档站点

CS Markdown Editor 的**单页介绍网站**，本身就是一个 Flet 应用——用 Flet 1.0 的
声明式组件（`@ft.component` / `use_state` / `use_effect` / `page.render`）构建，
跑在浏览器里，也可以开成桌面窗口调试。

它与编辑器主程序的关系很简单：**共用一套设计 token，零功能耦合**。

```
docs/
├── main.py             # 启动脚本：CLI 参数 → 运行时开关 → ft.run(Web / 桌面)
└── cmsite/
    ├── __init__.py     # 公开 API：main / SiteShell / inject_runtime_facts / set_start_*
    ├── _content.py     # 全部文案与事实数据（唯一来源，零 flet 依赖）
    ├── _theme.py       # 设计 token：配色复用 styles.Colors，补 Layout / Type / tint
    ├── _ui.py          # 无状态呈现原语（区块 / 卡片 / 徽标 / 键帽 / 按钮 / 网格…）
    ├── _widgets.py     # 有状态小部件（目前只有 CodePanel：复制按钮 + 1.2s 反馈）
    ├── _sections.py    # Hero / 特性 / 块级与行内 / 架构 / 关于
    ├── _demo.py        # 演示区：源码 ⇄ 渲染左右对照（唯一持有展示状态的区块）
    ├── _docs.py        # 文档区：左目录 + 右正文；快捷键页运行时读注册表
    ├── _shortcuts.py   # 快捷键分组（读 services.shortcuts.ACTION_REGISTRY）
    └── _shell.py       # 外壳：顶栏导航 + 滚动锚点 + 主题切换 + 页脚
```

依赖关系是单向的：`cmsite` → `styles` / `config` / `services.shortcuts`，
**不** import `app/` 或 `views/`。因此改编辑器 UI 不会把站点改坏，反之亦然。

## 运行

```bash
# 浏览器打开（默认 http://127.0.0.1:8550）
python docs/main.py

# 桌面窗口打开（调布局，不必开浏览器）
python docs/main.py --desktop

# 局域网演示
python docs/main.py --host 0.0.0.0 --port 9000
```

全部参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--port` | `8550` | Web 端口（`0` = 随机） |
| `--host` | `127.0.0.1` | 监听地址，局域网共享用 `0.0.0.0` |
| `--desktop` | 关 | 用桌面窗口而非浏览器打开 |
| `--theme {light,dark}` | `light` | 首帧主题，方便一上来就核对暗色 |
| `--width` / `--height` | `1280` / `860` | 桌面窗口初始尺寸（窄屏布局靠它预览） |
| `--start ANCHOR` | 首屏 | 打开即滚到某区块：`hero` `features` `demo` `docs` `architecture` `about` |
| `--cdn` | 关 | 从公网 CDN 取 Flutter 渲染内核；默认走本地，内网 / 离线也能打开 |

`--theme` / `--width` / `--height` / `--start` 走的是 `cmsite.set_start_*()` 写进
`_content` 的模块级开关，`SiteShell` 在挂载期读一次——所以它们是**启动参数**而非
运行时可切换项，运行时切主题仍由顶栏那个开关负责。

依赖与主程序相同（`flet==1.0.4`）。Web 模式还需 `flet-web`：

```bash
pip install "flet[web]==1.0.4"
```

## 设计要点

**配色与编辑器同源。** 站点不定义任何颜色常量——标题六级色阶、链接蓝、代码底色、
暗色降饱和策略全部取自 `styles.Colors`。因此「网站里看到的蓝」与「应用里看到的蓝」
是同一个值，切主题时两处一起变。字体同理，直接引用 `assets/fonts/` 下的
AlibabaPuHuiTi 与 NotoSansMonoCJKsc。

**内容只有一个来源。** 所有文案与数字集中在 `_content.py`，且该模块**不 import
flet**——可以在没有 Flutter 的环境里直接断言内容。运行时事实（版本号、环境摘要）
经 `config.app_meta` 注入，与编辑器「关于」页同源；快捷键表运行时读
`services.shortcuts.ACTION_REGISTRY`（51 条动作 / 9 个分类），不会随代码演进漂移。

**状态集中在外壳。** 整站只有三个状态：当前导航项、主题模式、视口宽度，都在
`SiteShell` 里。各区块不持有全局状态（演示页签、文档目录这类纯展示状态除外），
重渲染范围因此完全可预测。主题在**渲染期**同步给 `page`——放在 `use_effect` 里会
晚一帧，切换瞬间会闪一帧旧配色。

**宽度按视口算，不写死。** Flet 的 `Container` 没有 max-width 语义，写死 1120 在
800px 窗口里会横向溢出。所以内容宽 `= min(1120, 视口 - 留白)`，由外壳经
`page.on_resize` 维护，再作为参数显式传给每个区块——「这个区块多宽」在调用处一眼可见。

**滚动用 ScrollKey 而非像素。** 每个区块根容器带 `key=ft.ScrollKey(anchor)`，
导航点击直接 `scroll_to(scroll_key=...)`——响应式导致区块高度变化时锚点依然准确，
不需要自己累加高度。同时 `page.scroll` 必须设为 `None`：只要页面级滚动还开着，
内容区就变成「整页一起滚」，顶栏会跟着滚出去，`scroll_to` 也会落到外层容器上。

**顶栏不用 Material 固有尺寸控件。** 主题开关是固定 30 见方的 `Container(ink=True)`，
不是 `ft.IconButton`（后者固有高 40，压内边距无效，会顶破定高 60 的顶栏）。
同一条红线也用于紧凑工具栏，与主程序一致。

## Flet 1.0.4 的几个坑（这里都踩过）

| 症状 | 真因 | 做法 |
| --- | --- | --- |
| 渐变泛绿、按钮成灰条 | 8 位色写成 CSS 的 `#RRGGBBAA` | Flet 是 `#AARRGGBB`（alpha 在前），半透明一律经 `tint()` |
| 区块里出现灰色 ErrorWidget | `ft.Row(wrap=True)` 在 Flutter 侧是 `Wrap`，**不接受 `expand=True` 子项** | 换行容器里只放定宽子项，或改用 `SPACE_BETWEEN` 对齐 |
| 导航点了没反应 | `ScrollableControl.scroll_to` 是**协程**，没 await 只留一条 RuntimeWarning | 封装成 `async def _scroll_to()` 再 `page.run_task` |
| 出现红色报错占位块 | `ft.Clipboard` 是 Service，不能进控件树 | 在 `use_effect` 里实例化，塞进 `use_ref` |
| 图标全空白 | 字符串图标名不渲染字形 | 统一走 `_ui.icon()` → `getattr(ft.Icons, NAME)` |
| 首屏泛桃色 | 渐变取了 `heading_colors[5]`（暖棕 #B54708） | `hero_gradient()` 只取蓝与紫 |
| 点页签 / 目录报 `int() argument must be ... not 'Event'` | Flet 派发事件时**必定传一个 `Event`**，规则是 `get_param_count(fn) == 0 ? fn() : fn(event)`；`get_param_count` 数的是**声明参数个数（含默认值）**，所以 `lambda k=key: ...` 会被 Event 顶掉 | 带参回调写成 `lambda _e=None, k=key: on_select(k)`；无参回调交给 `_ui.on_event()` 统一吞掉事件 |

## 验证

站点不在 `tests/` 下留自动化用例——`docs/` 在 `.gitignore` 模板里，克隆出去的仓库
没有这个目录，放进去的用例会直接 import 失败。验证靠五个探针（在
`.workbuddy/probe/`，同为忽略目录），除视觉探针外都返回 0/1：

```bash
# 1) 结构：进程内渲染真实组件树，抓装配类错误
python .workbuddy/probe/site_render.py

# 2) Web 会话：像浏览器那样连 /ws，走注册握手 + 首帧下发
python .workbuddy/probe/web_session.py

# 3) 事件：按 Flet 的真实派发规则点每一个绑定事件的可点控件
python .workbuddy/probe/events_smoke.py

# 4) 真机点击：真实鼠标点一次主题开关，整页亮度必须翻转（端到端）
python .workbuddy/probe/click_e2e.py

# 5) 视觉：起桌面窗口 + PrintWindow 截图 + 多帧择优
python .workbuddy/probe/site_shot.py --start features --theme dark
python .workbuddy/probe/site_shot.py --width 430 --height 1800 --tag mobile
```

`site_render.py` 复用主程序的 `tests/harness.py::RenderHarness`——在纯 Python 里
渲染组件、驱动 `use_state` 重渲染，无需 Flutter 前端。

`web_session.py` 补的是 `curl` 覆盖不到的那一段：静态资源 200 只证明文件在，
**握手之后**才跑 `main(page)` 和 `page.render(SiteShell)`。它按
`flet_web.fastapi.flet_app` 的线格式（WebSocket 帧 = `[type:u8][msgpack payload]`，
无长度前缀）手工发 `REGISTER_CLIENT`，再收后续的 `PATCH_CONTROL`，断言各区块的
代表文案都在。两个坑值得记住：注册响应只带页面骨架，控件树在第 2 帧之后才来；
`main()` 里抛的异常**不会**回填到响应的 `error` 字段，只能从服务端 stderr 认。

`events_smoke.py` 为什么必须有：`tests/harness.py::invoke` 会**按签名自适应传参**
（`if not params: fn()`），于是 `lambda k=key: ...` 在夹具里永远拿到 `key`、永远通过；
而 Flet 运行时是 `get_param_count(fn) == 0 ? fn() : fn(event)`，`k` 会被 Event 顶掉。
**夹具把真实派发规则的差异抹平了**，所以这条只能靠探针按 Flet 的规则派发一次。
它还顺手验证两类带参回调不是静默 no-op：点演示页签后右侧渲染源真换成新样例、
点文档目录后正文真换成该页摘要（`int(Event)` 那种可能在 `next(..., DOCS[0])`
兜底下不抛异常，只表现为「点了没反应」，只看有没有异常是不够的）。

`click_e2e.py` 是最外一层的端到端：真鼠标点右上角主题开关，整页平均亮度必须从
233.8 翻到 46.2。它验证的是「窗口 → Flutter 手势 → 线上消息 → `dispatch_event`
→ 回调 → 重渲染」整条链，前三个探针都覆盖不到这一段。三条真机纪律（踩过）：
**抢前台要 `AttachThreadInput`**（单靠 `SetForegroundWindow` 一律被拒；抢不到就
**一步都不投递**，注入是全局的会点到用户别的窗口上）；**几何要在抢完前台之后重读**
（应用会自己对 FOCUS 做 `ensure_window_on_screen`，窗口真的会走）；**切换类交互只点一次**
（点两次互相抵消，看起来像没反应）。

视觉探针只改窗口尺寸不够，必须配合 `--width/--height` 让 Flet 内部窗口一起变，
否则拍到的还是 1280 布局。另一个更隐蔽的坑：**只按标题找窗口会抓到浏览器**——
浏览器标签标题同样是「CS Markdown Editor …」，`PrintWindow` 拍出来的是浏览器画面，
墨迹占比照样 PASS，于是拿到一张「假证据」。探针现在按窗口类名 + 进程可执行名排除
浏览器，找不到真正的应用窗口就直接 FAIL。

同理，Web 模式默认会 `open_in_browser`，跑一次探针就在桌面弹一个标签页；探针通过
`FLET_DISPLAY_URL_PREFIX`（有值即只打印 URL）把它关掉了。

主程序的全量回归照旧：

```bash
python -m pytest -q
```

> **改了像素级不变量就升级成 PASS/FAIL**：探针打印的数字只能看，返回 0/1 的才能守。
> 新护栏必须做一次变异验证。`web_session.py` 本轮验了七条——把首屏 / 演示 / 文档 /
> 架构 / 关于各删一次、导航不渲染、`main()` 抛异常——全部 KILLED。
> 而且**探针自身的「代表性文案」也得验**：首屏最初用 `APP_TAGLINE`，它同时进了
> `page.title`，于是删掉首屏照样通过——换成只在首屏出现的 `HERO_LEAD` 才守住。
>
> `events_smoke.py` 验了四条：页签 / 目录各自退回成 `lambda k=key`（原来的 bug）、
> 页签 / 目录各自改成空回调，四条全 KILLED。`click_e2e.py` 验了一条：把 `on_event`
> 改成 no-op 后亮度差恰好 0.0 → FAIL（同状态两次渲染是逐像素确定的，0.0 是真 0）。
> 写变异脚本时注意本仓库文件是 **CRLF**，多行片段要用 `\r\n` 拼，否则一律 SKIP；
> 另外别在 Bash heredoc 里写 `\n`——本环境会吃掉反斜杠，`"\n"` 变成字面 `/n`，
> 匹配静默失败（用 `chr(10)` 或写成脚本文件）。
