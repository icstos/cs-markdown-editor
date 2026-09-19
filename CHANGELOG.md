# 更新日志

本项目的显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 2026-09-19 视觉：标签行与大纲头部等高，顶栏连成一条水平带

诉求：「标签行高度与大纲顶部高度保持一致，使整体视觉效果更协调」。改前真机实测
（`PrintWindow` 整窗抓屏 + 逐列行程编码）：标签行内容 44px、大纲头部内容 32px，
两条底边线相差 12px——编辑区与大纲列交界处有一条可见台阶，顶栏被切成两段。

- **根因**：标签行高度由**最高子项**决定，而关闭 / 「+」按钮原用 `ft.IconButton`
  ——Material 的最小点击区给它 40px 固有高（`visual_density=COMPACT` 也只降到 32），
  把标签行顶到 `40 + 上下 padding 2×2 = 44`；压内边距完全无效。与
  `views/code_block.py` 头部「顶部行过高」是同一款故障（其注释里已记录该结论）。
- **改法**：新增跨模块常量 `styles.TOPBAR_H = 32`（= 大纲头部的内容固有高），标签行与
  大纲头部都显式定高到它；标签行的图标按钮改为固定 22 见方的 `Container(ink=True)`
  （点击水波、悬停 tooltip 不变，只是不再有 Material 的 40px 下限）。
- **两处构造细节**：
  - 标签容器加 `height=TOPBAR_H` 后铺满整条顶栏，激活态背景**直达底边线**（与下方
    编辑区的「无缝连接」视觉保持）；
  - 底边线挂在定高内容带**之外**：`Container(height=H, border=bottom 1px)` 的总高
    **就是 H**（边线被算进 H 内，内容带只剩 H-1）。标签栏的边线本就加在内容带之外，
    大纲头部若把边线套在定高盒里就会差 1px（真机实测 33 vs 32），故大纲头部改为
    「定高内容带 + 外挂 1px 边线」。
- **真机复验**：标签行与大纲头部的内容带均 56 物理 px（dpr=1.75 → 32 逻辑 px），
  底边线**同为第 108 物理行（差值 0）**；标签标题、大纲标题、「+」按钮的墨迹中心
  同落在 `y=16`（内容带中线）；标签底色直达底边线、无缝隙；关闭按钮与「+」未被裁切。
- 新增 `tests/test_topbar_height.py`（9 项）锁定三条不变式：两处内容带定高同源、
  标签行内无 Material 固有尺寸控件（`IconButton`/`Dropdown`）、两列底边线共线
  （内容高 + 上下 padding + 边线宽三者相同）。对比标签（双文件名）一并覆盖。
- 功能零变化：标签点击 / 关闭 / 悬停显隐 / 右键菜单 / 新建按钮行为与改动前一致。

### 2026-09-19 性能：大文件打开提速 + 编辑重渲染 39.6ms → 4.5ms（功能不增不减）

用户提出两条相邻诉求——「加快大文件打开速度」与「保持功能不变，提高整体操作响应速度」。
两条诉求的根因同一：**Python 侧控件对象个数**。flet 的
`ListView.build_controls_on_demand` 只让 **Flutter 客户端**懒建 widget，Python 侧
`controls` 列表里的控件对象仍是全量构造的。实测 3 千行文档构造控件树约 2.4s，其中约
95% 花在 flet 内部的 `_configure_dataclass`（每个新增控件都要遍历它的 dataclass
字段子树）。所以只能靠「不构造视口外的行」来解决，分三步落地。

**优化 1 — 解析走快速构造器（打开大文件）**
- `models/document.py` 新增 `new_segment` / `new_line` / `new_document`：用
  `object.__new__` + `object.__setattr__` 直填字段，跳过 dataclass 构造与
  observable 逐字段通知；`segments` / `lines` 仍包装为 `ObservableList`，
  与常规构造的字段类型逐一对齐（编辑期赋值/替换语义完全不变）
- `parser/inline.py`、`parser/block.py` 的解析路径改走这三个构造器

**优化 2 — 行视图窗口化（打开大文件）**
- 只构造 `[0, hi)` 内的行，`hi` 随滚动/跳转按块外扩（`_WINDOW_INITIAL=160` /
  `_WINDOW_MARGIN=40` / `_WINDOW_CHUNK=120`，行数 ≤ `_WINDOW_ACTIVATE=200` 时不启用，
  小文档行为与旧版逐字一致）
- 表格是「连续 TABLE 行合并为单个 TableView」渲染的，窗口边界必须对齐到表格块边界
  （`_render._snap_window`），否则会渲染出只有后半截的表格
- 窗口上界单调不减（`window_hi_ref` 即时镜像防止同一帧重复扩窗）→ 视口上方永远是
  真实行，不出现估算误差导致的跳动
- 对比标签（`diff_marks` / `diff_gaps`）不窗口化：间隙高度不在行偏移前缀和里，
  窗口外的留白会算错总高

**优化 3 — 未构建区用 ListView padding，不用占位控件（编辑响应速度）**
这是本轮的关键修正。窗口化的第一版用「逐行等高占位容器」补齐未构建区，实测在真机上
**滚动范围严重退化**：
- 占位控件是**列表项**，而 `build_controls_on_demand` 下 Flutter 的 `maxScrollExtent`
  由 `SliverChildBuilderDelegate._extrapolateMaxScrollOffset` 外推
  （`已布局项总高 / 已布局项数 × 剩余项数`）——**只认"真正布局过"的那些项的高度**。
  占位容器落在布局窗口之外就永远不被计入 → 实测 1555 行文档 `max` 只有 4793
  （真值 ≈50000），**文档后 90% 滚不到**
- 单个大占位容器更差（永远在布局窗口外）；逐行占位虽能收敛到正确值，但项数 = 行数，
  等于没省下 diff 成本
- 改为 `ListView.padding`（`_render.line_padding()`）：padding 是**常量**，
  Flutter 无需布局即知高度、直接计入总高 → 项数 = 窗口行数

真机实测（真实 App，1750 行文档，逐段驱动滚动）：

| 指标 | 实测 |
|---|---|
| 首屏构建项数 | 138（窗口 162 行，表格合并为 1 项）—— 无任何填充项 |
| 首屏滚动范围 | `max=60482`（文档前缀总高 60302）；旧占位方案同规模只有 4793 |
| 滚到底 | `offset` 追平 `max=65302`，`padding` 归零，窗口覆盖 `(0,1750)` |
| 窗口外扩 | 162 → 408 → 758 → 1486 → 1750，全程单调不回缩 |
| 总高守恒 | `pad.bottom` 54744 → 46238 → 34178 → 9094 → 0，与窗口同步收缩 |

单次重渲染基准（1555 行文档）：

| 指标 | 全量构造 | 占位方案（v1） | padding 方案 |
|---|---|---|---|
| 列表项数 | 1555 | 1555 | **160** |
| 单次重渲染 | 39.58ms | 19.7ms | **4.46ms** |
| ├ build_line_controls | 1.85ms | — | 1.45ms |
| └ flet 控件树 diff | 17.46ms | 18.57ms | 2.47ms |

代价：窗口行的总高仍走同一条外推（平均高 × 项数），误差只作用在「窗口那几屏」上
（实测 <1% 文档总高，且随窗口增长自行收敛）；换来的是控件树 diff 成本与文档总行数解耦。

**测试**
- `tests/test_large_doc_open.py` 重写：删除占位断言，改为 padding 断言 ——
  `test_windowed_doc_has_no_filler_items`（列表项必须全是真实行/表视图）、
  `test_padding_equals_unbuilt_line_offset_span`（总高守恒，与被测代码共用
  `_build_offset_prefix` 同一把尺子）、`test_padding_shrinks_as_window_grows`、
  `test_padding_accounts_for_table_snapped_window`（表格边界对齐）、
  `test_small_doc_not_windowed`、`test_large_doc_first_paint_builds_only_window`、
  `test_scroll_extends_window_and_never_shrinks`、
  `test_scroll_to_end_materialises_all_and_clears_padding`、
  `test_diff_mode_is_not_windowed`
- 全量 `1407 passed`

### 2026-09-18 修复 Alt+Z 自动换行失效；清理幽灵快捷键；全局动作认两层自定义键位

**问题**：`Alt+Z` 切自动换行完全无反应（VSCode 约定、README 核心特性段与状态栏
tooltip 都这么写）。顺着这条线做了一次「注册 → 默认值 → 分发 → 文档」四方闭环梳理，
共暴露三类缺陷。

**缺陷 1 — `toggle_word_wrap` 键位漂移（用户报告的 BUG）**
- 实际绑定是 `Ctrl+Shift+R`，而 README 核心特性段 / 状态栏 tooltip
  「自动换行 (Alt+Z)」/ `_settings_controller.toggle_word_wrap` 文档串**三处都写 Alt+Z**，
  上下文菜单又写 `Ctrl+Shift+R` —— 四处说法不一，用户按的 `Alt+Z` **根本没有被绑定**
- 排除了"按键被吞"：`combo(evt("z", alt=True))` 输出 `alt+z` 完全正常
  （`Alt+T` 切主题、`Alt+C` 切任务一直是好的），确认是**未绑定**而非事件链路问题
- 修复：两层默认键统一为 `alt+z`（`DEFAULT_SHORTCUTS` / `ACTION_REGISTRY` /
  `_GLOBAL_ACTIONS` / `settings.json`），并同步菜单标签与 README 表格
- ⚠️ `settings.json` **必须同步**：`load_settings()` 深合并会让残留的旧键位
  永久覆盖新默认值（不同步则本机仍按 `Ctrl+Shift+R`）

**缺陷 2 — 11 个「幽灵动作」占据设置面板（有键位、无实现、无分发）**
- `delete_line`(Ctrl+Shift+D) / `copy_rich`(Ctrl+Shift+C) / `format_underline`(Ctrl+U) /
  `insert_image`(Ctrl+Shift+I) / `clear_format`(Ctrl+R)，以及无默认键的
  `format_h1` / `format_h2` / `format_h3` / `format_paragraph` / `format_quote` /
  `format_code_block` —— 全部只有注册表条目，**没有 `EditorActions` 实现、
  没有分发分支**。设置面板却为它们渲染「修改 / 恢复默认」按钮
- 上一次清理只删了 `DEFAULT_SHORTCUTS`，注册表与 `settings.json` 都还留着，
  于是面板照旧显示、`reset()` 还能把它们还原成"有键位的死项"
- 修复：三处同步移除；`ACTION_REGISTRY` 顶部写明「登记即必须接线」的不变量。
  上下文菜单里那几条 `disabled=True` 的占位项保留（本就是灰显的规划位）
- 标题级别（`Ctrl+0~6`）改由既有的「固定键盘（不可自定义）」提示说明

**缺陷 3 — 全局动作只认浏览层配置，编辑态的自定义键位被静默忽略**
- `_GLOBAL_ACTIONS` 里 23 个全局窗口级动作统一在 layer 判定**之前**执行，
  却只拿 `ShortcutManager.get("browse")` 做匹配。而设置面板对 `scope=both` 的动作
  会分别渲染浏览态 / 编辑态两行 —— 用户在**编辑态那行**改的键位一点用都没有
- 修复：新增 `KeyDispatcher._global_targets()`，目标键位取
  **浏览层 ∪ 编辑层 ∪ 表内默认**，并**剔除空串**
  （`matches("", "")` 为真，而纯修饰键事件的 combo 恰是空串，保留空键位会让
  动作在每次单按 Ctrl/Shift/Alt 时误触发）
- 两条路径（编辑器内 / 原生输入框内）共用该方法，行为保持一致

**顺带清理**
- 删除 `_handle_shortcuts` 中已被动作表覆盖的**死分支**（browse 的
  save / save_as / new / open_settings / focus_mode，edit 的 save / save_as）。
  这些分支里的硬编码兜底默认键（`focus_mode` 写 `"ctrl+k"`，实际是 `ctrl+shift+k`）
  正是「改键后行为不一致」的来源
- `toggle_split_editor` 补上 edit 层默认键（`Ctrl+\` 本就两层可用，注册表却只声明
  browse，导致设置面板编辑态那行显示「未绑定」）
- README 快捷键表全面校对：补 `Ctrl+Shift+V` 纯文本粘贴（两处缺失）、
  删除 5 个幽灵动作行、`Ctrl+Shift+R` → `Alt+Z`；「编辑态补充」一节改名为
  「其他固定键（不可自定义）」——原表把 `paste_plain` / `close_tab` / `Ctrl+\` /
  `toggle_raw` 等**已登记**的动作误列为「未登记」

**测试**
- 新增 `tests/test_shortcut_reachability.py`（15 条）：注册动作必须可分发、
  `DEFAULT_SHORTCUTS` 无孤儿键、registry 与默认值双向一致、同层无重复键、
  `Alt+Z` 绑定锁定、全局动作认两层配置、空键位不匹配纯修饰键
- `tests/test_key_bindings.py` 新增 `Alt+Z` 两个用例 + 旧键位 `Ctrl+Shift+R` 已解绑护栏；
  `tests/test_key_dispatch_parity.py` 的 parity 用例改为 `Alt+Z`
- **变异测试验证护栏有效**：把动作表改回"只读浏览层"→
  `test_global_action_honours_edit_layer_binding` 精确失败；把键位改回
  `ctrl+shift+r` → `test_toggle_word_wrap_bound_to_alt_z` 精确失败
- 全量 **1391 通过**（修改前 1376）

### 2026-09-18 修复代码块编辑态光标纵向漂移（换行开启时越往下越偏）

- **问题**：代码块开启换行后进入编辑态，**越靠后的行，可见文字相对光标越往上偏**
  —— 即光标看起来"逐行向上飘"，行号越大偏移越明显，违背桌面编辑器的直觉
- **根因（真机对照探针锁定，非猜测）**：`TextField` 未指定 `strut_style` 时，
  Flutter 会**自造一个 `force_strut_height=True` 的 strut**，把每行高度钉死为
  `size × height`（16 × 1.5 = 24px）；而浏览层高亮用的 `ft.Text` 没有 strut，
  行盒取"该行所有 run 的自然行高最大值"。两者**只在纯 ASCII 下相等** ——
  行内一旦出现需要**字体回退**的字形（中文注释、emoji），回退字体的度量更大，
  `ft.Text` 行盒变成 **25px**，编辑框仍是 **24px**
- 于是每经过一个含中文/emoji 的行，可见文字相对光标下移 1px（折行的中文长注释
  一次下移 2px）→ **逐行累积的纵向错位**。这也解释了为什么它是"透明叠层"改造后
  才暴露的：两层一旦同行同列，行盒就必须逐行严格相等，而非"看起来差不多"
- **修复**：新增 `_edit_strut(size)`，编辑框显式带上
  `force_strut_height=False` 的 strut —— strut 只保证**下限** 24px，不再压制回退
  字号的自然行高，编辑框行盒因此退化为与 `ft.Text` 相同的"自然最大值"
- **真机实测 6 组样本（同宽同样式，`Text` vs `TextField` 行盒对照）**：

  | 样本 | `ft.Text`（浏览层） | 修复前编辑框 | 修复后编辑框 |
  |---|---|---|---|
  | 纯 ASCII 1 行 | 24 | 24 | 24 ✅ |
  | 中文 1 行 | **25** | 24 ❌ | **25** ✅ |
  | 中文 2 行 | **50** | 48 ❌ | **50** ✅ |
  | `x = 1  # 注释` 混合 2 行 | **51** | 48 ❌ | **51** ✅ |
  | emoji 1 行 | **27** | 24 ❌ | **27** ✅ |

  修复后编辑框与浏览层**逐行逐一相等**，且 `_edit_strut` 的字体族/字号/行高倍数
  与正文 `_span_style` 同源同值
- **与单行光标层的区别（易混淆点，已在 `AGENT.md` 显式隔离）**：
  `views/cursor_layer.py` 的单行浮层光标用 `force_strut_height=True`，而代码块叠层
  必须用 `False`，**二者取相反值且不可统一** —— 单行层是"TextField 浮在一个视觉行
  上"，两层都能钉死同一行高；代码块叠层是"多行 TextField 压在**多行 `ft.Text`**
  上"，而 **`ft.Text` 没有 `strut_style` 字段**，只能反过来让 TextField 不强制行高
- 测试：`tests/test_code_block_native.py` 新增 2 项（编辑框声明非强制 strut、
  `_edit_strut` 字段与正文样式同源同值）；全量 **1374 通过**

### 2026-09-18 压缩代码块头部行高（48 → 22），整体更紧凑

- **问题**：代码块顶部工具栏占一整行 48px，视觉上像"顶上多了一条空白带"，
  与正文的紧凑排版不协调
- **根因（真机探针逐控件量高，非猜测）**：行高由**最高子项**决定，与内边距无关。
  实测 `ft.Dropdown` **恒为 48px** —— 即使 `dense=True` / `text_size=12` /
  `content_padding` 全部归零，内部 `InputDecorator` 的固有高度不减；`ft.IconButton`
  默认 40px（`visual_density=COMPACT` 也只降到 32）。二者就是行高的唯一下限，
  把内边距压到最小也不会有任何变化（已实测确认）
- **语言选择器：`ft.Dropdown` → 自绘 `ft.PopupMenuButton`**
  - 给 `Dropdown` 设 `height=` 能改外框，但**会裁切内部文字**（真机截图：压到 28px
    文字下坠错位、24px 时直接溢出到下一行标签上）→ 此路不通，只能整体换掉
  - 新触发器是紧凑标签（当前语言展示名 + 小箭头），22px 高；菜单
    `menu_position=UNDER`（不遮住代码）、项高 28、**勾选当前语言**（打开即知当前状态）
  - 当前语言不在常用清单内时（如围栏写了 `py3`）仍追加为末项并勾选
  - 选中走 `PopupMenuItem.on_click`（可闭包捕获标识），与项目既有的
    `views/tool_area.py` / `views/tab_bar.py` 同一写法；`on_select` 只给控件 ID 字符串
- **折叠 / 复制按钮：`ft.IconButton` → 固定尺寸 `Container(ink=True)`**
  - 与 `views/status_bar.py` 的紧凑按钮同一套做法，22×22、14px 图标、
    水波反馈 + tooltip，全局观感一致
  - 头部行高额外用 `Row(height=_HEADER_H)` **硬锁**，并配套断言"每个子项声明高度
    ≤ 该值"，避免将来有人塞进高子项被静默裁切
- **移除装饰性 `DATA_OBJECT` 图标**：语言标签紧邻其右，语义重复；紧凑头部里
  多一个字形只会让起点更乱
- **真机验证**：同一条渲染路径做 A/B（只改 `_HEADER_H` 常量，两块并列渲染），
  块高 **260 → 286（Δ=26px）**，与 48−22 严格相符；`PrintWindow` 高保真截图
  逐项确认标签 / 图标 / 行数在 22px 内垂直居中、无裁切
- **契约不变**：`on_change_lang(line_idx, 标识)` 语义与旧 `Dropdown` 完全一致，
  `views/editor/_fence.py` 与 `views/editor/_render.py` 无需改动
- 测试：`tests/test_code_block_native.py` 新增 12 项（头部行高锁定、头部不得出现
  Material 固有尺寸控件、子项高度不超上限、图标按钮形态与提示、行数标签、
  语言标签显示展示名、菜单项与勾选态、选中回传契约、清单外语言追加、
  `_lang_display` / `_lang_entries` 纯函数、折叠可用）；全量 **1372 通过**

### 2026-09-18 修复代码块编辑态行宽异常；编辑时保留语法高亮

- **问题**：代码块进入编辑态后**每行可用宽度明显小于浏览态**（真机实测编辑框文本区
  300px，浏览态代码列 728px），长行折行点大幅提前、块高多出 2 个视觉行；且编辑时
  语法高亮完全消失
- **根因（真机探针对比量测）**：`ft.KeyboardListener(expand=True)` **只把自己撑开，
  传给 `content` 的是松约束** —— 里面的多行 `TextField` 据此缩到内在宽度。该监听器
  原先是"直接包住编辑框"用来接 Tab 的（见下方 Tab 缩进说明），于是顺带把宽度也
  收窄了。次要因素两处：编辑框右侧 `content_padding` 多留了一个 `Spacing.MD`
  （每行少 8px），以及编辑框未显式指定 `letter_spacing`（`TextStyle` 默认 `None`
  走继承，与渲染层可能取到不同字距 → 光标随字数线性漂移）
- **改法一：编辑态改为叠层 —— 编辑时语法高亮持续可见（Typora 式）**
  - 浏览态那段高亮正文层（`_read_column()`，逐逻辑行 `ft.Text(spans=...)`）同时作为
    **编辑态的底层**，上面叠一个**文字透明**的原生 `ft.TextField`；字符仍由高亮层
    呈现，编辑框只提供原生光标 / 选区 / IME / 撤销
  - 两层吃同一份容器约束、同一套字体度量（字体族 / 字号 / 行高倍数 / `letter_spacing`
    全部显式同值），因此**宽度、折行点、行高逐字对齐**，进出编辑态零位移
  - `_build_read_body()` 拆出 `_read_column()`，两种状态共用同一层实现
- **改法二：宽度约束归位**
  - `KeyboardListener` 移到**最外层**包住整个正文（不再只包编辑框）
  - 编辑框改由 `Row([Container(expand=True, ...)])` 承载以获得**紧宽度**，
    右侧 `content_padding` 归零 → 文本区与浏览态代码列严格同 x 同宽
  - `Stack` 用默认 `LOOSE`：本组件位于滚动 `Column` 内、交叉轴约束无界，
    `StackFit.EXPAND` 会把高度算成 `inf`（整块渲不出来）
  - 关闭换行时，高亮层与编辑层放进**同一个横向滚动容器**，滚动位置天然同步
- **真机验证**：编辑态块高与浏览态完全一致（`SIZE li=2 h=219.0` 不再变化，修复前
  为 259.0）、折行点相同、语法高亮可见、Tab / Shift+Tab 缩进与焦点保持照常；
  光标位置经逐帧差分确认（帧间差分 bbox 为 5px 竖条，落点与插入缩进后的列位一致）
- **测试**：`tests/test_code_block_native.py` 新增 5 项并改写 3 项（叠层结构、
  编辑框由 flex 赋予紧宽度、字体度量逐项一致、文本区与代码列对齐、
  关闭换行时两层共享滚动），全量 **1360 通过**

## [未发布]

### 2026-09-17 代码块重写：Flet 原生双态实现，支持块内软换行 + 语法高亮

- **问题根因**：代码块原用 `flet-code-editor` 的 `CodeEditor`，但上游
  `flutter_code_editor` 的 `CodeField.wrap` 是"已声明未实现"的空属性（对应 PR
  仍为 Draft），Flet 侧也未暴露任何换行开关 —— 开启换行时"代码块内也折行"在
  产品上无法达成（改 Python 属性无效，改 Dart 需整体 `flet build`）
- **移除依赖**：`flet-code-editor` 从 `pyproject.toml` 与 README 依赖表移除，
  改由 `pygments` 提供分词（`views/line_view.py` 中的惰性 `_ce()` 导入与
  `_code_language` 语言枚举映射一并删除）
- **新增 `views/code_block.py`（Flet 原生双态）**：
  - **浏览态**：逐逻辑行 `ft.Text(spans=...)` 高亮渲染，语义类别（关键字 / 字符串 /
    注释 / 数字 / 函数 / 类型 / 变量 / 运算符 / 词法错误）经 `styles.Colors.code_syntax`
    上色（亮色 GitHub Light 语义色阶 / 暗色 One Dark 统一降饱和）；行号列位数自适应，
    折行时底色带连续、编号仍与首行基线对齐
  - **编辑态**：原生多行 `ft.TextField`（等宽字体、独立行高倍数与浏览态一致），
    选区 / IME / 撤销 / 软换行全部交给框架；点击块体进入（`use_effect` 移交焦点），
    失焦回到高亮浏览态
  - **Tab / Shift+Tab 缩进**：原生多行 `TextField` 不消费 Tab —— Flutter 把它当焦点
    遍历键，flet 1.0 既无 Focus / Shortcuts 控件、`TextField` 也无 `on_key_down`，
    控件层无从拦截（真机探针实测：回调返回 `True` 也拦不住遍历，`KEYDOWN Tab` 与
    `BLUR` 同帧发生）。故组件内嵌 `ft.KeyboardListener` 包住编辑框，捕获 Tab 后自行
    插入 4 空格缩进（Shift+Tab 反向去缩进，多行选区逐行处理），改写走 `on_change_code`
    从而与手工输入共享撤销 / 标脏 / 重渲染路径；随后的遍历失焦被识别为副作用（不退出
    编辑态），焦点在本次重渲染提交后由 `use_effect` 收回，缩进后的光标位置随渲染参数
    下发。`Ctrl+Tab` 属全局标签切换，不插入缩进
  - **软换行**：`word_wrap=True` 时文本按容器宽度折行、续行与首行同列；`False` 时
    单行不折、整块横向滚动（编辑态按最长行撑开宽度后横向滚动，与浏览态行为一致）
  - 保留头部语言下拉 / 折叠 / 复制 / 行数，保留 diff 背景、当前行高亮、高度上报
- **新增 `services/code_highlight.py`**：纯逻辑分词服务（Pygments 惰性导入，结果
  按 (语言, 代码) FIFO 缓存）。**不变式**：`highlight_lines` 行数恒等于
  `code.split("\n")` 行数、逐行拼接恒等于原文 —— 行号、逻辑行坐标与边界方向键
  跳出都依赖它；未知语言 / Pygments 缺失 / 超长代码（>200k 字符）/ 分词行数不符
  四种情况统一回退纯文本（只失去上色，内容与行数不变）
- **新增 `utils/code_indent.py`**：Tab / Shift+Tab 的缩进变换纯函数
  （`apply_indent(value, base, extent, direction) -> (value, base, extent)`）。
  收敛折叠光标插入 / 去缩进（优先 4 空格，退化为制表符）、多行选区逐行缩进
  （排除起点恰好等于选区终点的行）、偏移量裁剪与"无可去缩进时零改动"等边界
- **新增 `styles.Colors.code_syntax`**：语义类别 → 颜色映射（亮 / 暗各一套），
  配色属主题、分词属服务，同一份分词结果可跨主题复用
- **契约零改动**：`on_change / on_focus / on_blur / on_selection_change` 四件套
  与旧 `CodeEditor` 语义完全一致，`views/editor/_fence.py` 的围栏闭包组与
  `views/key_bindings.py` 的"边界方向键跳出 / 空块 Backspace 删除 / Tab 放行"
  未作任何修改；`LineView` 新增 `word_wrap` prop（`views/editor/_render.py` 透传
  `ctx.word_wrap`）。`on_code_focus` 改为幂等（重复聚焦不重启撤销会话）
- **flet 1.0 坑位修复（顺带）**：`ref` 是 `InitVar`，只在构造时绑定，事后
  `control.ref = x` 永远不生效 —— `views/code_block.py` 与 `views/line_view.py`
  的公式编辑框均改为构造参数传入；渲染后的控件被标记为冻结，事后改属性会抛
  `Frozen controls cannot be updated.`，故光标位置只能经渲染参数下发
- 测试：新增 `tests/test_code_block_native.py` 25 项（分词不变式与回退、缓存命中、
  换行开关下的 expand/no_wrap 与横向滚动容器、空行行盒、主题语义色、点击进入
  编辑态、四个回调透传、`code_field_ref` 赋值、Tab / Shift+Tab 缩进与光标保持、
  Tab 引发的失焦不退出编辑态、真实失焦仍退出、依赖清理守卫）；
  新增 `tests/test_code_indent.py` 21 项（折叠光标插入/去缩进、部分缩进、制表符、
  多行选区、偏移裁剪与往返一致性）；pytest 1356 通过

## [未发布]

### 2026-09-05 搜索体验升级：文档内搜索浮层（Ctrl+F）+ 侧边栏全局文件夹搜索（Ctrl+Shift+F）

- **Ctrl+Shift+F**：激活侧边栏搜索面板、自动开启「文件夹范围」并聚焦搜索输入框
  （与 Ctrl+F 严格分流；KeyDispatcher 正常域/外来域四处路由重排，未装配新动作
  时回退旧行为，兼容用户改键）
- **Ctrl+F → 文档内搜索浮层**（新增 views/floating_search.py）：悬浮于编辑区
  右上角的小尺寸浅灰半透明圆角搜索条，包含搜索框（唤起即聚焦、可直接打字）、
  大小写 Aa、正则 .*、上一个/下一个按钮、实时计数「第 N / 共 M 个匹配」、
  无匹配「0/0 未找到结果」、关闭按钮；Enter=下一个、Shift+Enter=上一个、
  Esc=关闭并交还焦点给编辑器；输入框经 native_focus_hooks 接入外来输入焦点域
  （Ctrl+A/C/V 等只作用输入框，不再波及编辑器文档）
- **匹配高亮（纯装饰层）**：全量匹配在编辑器文档内以浅黄背景高亮（亮色
  #FFE082 / 暗色 #5D4E1A），当前选中匹配以橙黄更深高亮（新增 search_active_bg
  亮 #FFB300 / 暗 #A67B12）；文字颜色不变、不改文档数据；注入点在
  rendered_line 的 TextSpan.bgcolor（flat 区间切分，跨软换行由既有切片管线保留
  style），HarfBuzz 测量/换行/光标像素对齐零影响（红线内）
- **行级数据流**：App 层状态（open/query/case/regex/active/焦点序号）→
  use_memo 行级命中图（含版本号）→ MarkdownEditor props → LineView/RenderedLine；
  @ft.memo 浅比较友好（命中行数据变化才重建），搜索作用文档按焦点视口选择，
  仅身份一致的编辑器获得高亮数据（拆分/对比安全）
- **跳转定位**：EditorActions 新增 jump_to_line_center（视口中部平滑滚动：
  估算→构建→实测精修 150/250ms 两步）与 focus_document（关闭浮层后焦点交还）；
  actions.py 仅追加可选字段，向后兼容
- 已知限制：代码块/表格/公式块等独立岛屿与 >3000 行大文档/RawEditor 模式无
  行内 spans 可注入，仅提供计数与跳转定位（不绘制字符级高亮）
- 测试：tests/test_key_bindings.py 新增 16 项（浮层 Enter/Shift+Enter/Esc、
  关闭态放行、Ctrl+F 浮层分流、Ctrl+Shift+F 全局分流、外来域隔离回归）；
  pytest 987 通过（另 182 项为沙箱 PermissionError 环境限制，与基线一致）；
  ruff 无新增告警

## [未发布]

### 2026-09-05 界面紧凑化：去除窗口四周边缘空白带（功能不增不减）

- **根因**：Flet 根视图默认 `padding=10` / `spacing=10`（page.padding 代理根视图
  padding），且未显式覆盖 → 四列布局整体被 10px 内边距抬离窗口边缘：最左
  活动栏左边有空白、顶栏与顶部程序框架之间有缝隙、大纲列右侧与窗口最右
  分离、底部状态栏下方悬空（main.py）
- **修复**：`page.padding = 0`、`page.spacing = 0`（main.py，随窗口配置设置，
  渲染前生效）——四列布局与窗口四边严格贴合（活动栏贴左、标签/编辑区贴顶、
  大纲贴右、状态栏贴底），符合 VSCode / Obsidian 式整窗紧凑布局
- 校验：ruff / py_compile 通过；纯布局配置改动，无单测覆盖

## [未发布]

### 2026-09-05 修复：快捷键焦点域隔离——非编辑区聚焦时不误操作编辑器内容

- **根因**：页面级 KeyDispatcher 对所有按键无差别按「编辑器当前状态」分发，
  不校验键盘焦点归属。焦点切到侧边栏搜索/替换/过滤输入框、文件对话框输入框等
  原生输入控件后，Ctrl+A/C/V/X/Z、方向键/Backspace/Delete、打字等仍被路由到
  编辑器——例如搜索时 Ctrl+A 会误全选（并选中/改动）编辑器文档，而非只全选
  搜索框文本（views/key_bindings.py）
- **修复（焦点域门控）**：
  - KeyDispatcher 新增 `native_input_ref` 外部输入焦点域：任一非编辑器原生输入框
    on_focus 时置 token（views/native_scope.py 生成带独立 token 的
    on_focus/on_blur 闭包，防 A→B 焦点切换竞态误清空）
  - 外部输入域聚焦时：文档编辑/选区/导航/剪贴板/撤销/行内格式等快捷键一律
    不消费（交原生输入框——搜索框内 Ctrl+A 只全选搜索框文本、Ctrl+Z 只撤销
    框内编辑、方向键只移动框内光标）；仅放行不触碰文档的全局窗口级快捷键
    （新建/打开/保存/另存为/设置、切标签、侧边栏/主题/缩放/换行/拆分、
    Ctrl+F/H 与替换动作）
  - 接入输入框：侧边栏搜索/过滤/替换三框、文件操作对话框输入框（app 层
    ctx.native_input_ref 贯穿；收起侧边栏/切换面板时主动清空防 token 残留）
- 测试：tests/test_key_bindings.py 新增 10 项外部输入焦点域用例（Ctrl+A 不再
  全选文档、剪贴板/撤销不调度、导航/删除键不放行到文档、打字不碰 outward
  选区、PageUp/Down 不滚编辑区、Ctrl+S 与窗口级快捷键仍生效、失焦后恢复），
  模块 99 项全部通过；ruff 无新增告警

## [未发布]

### 2026-09-05 修复：活动栏底部 ≡ 菜单按钮未居中 / 视觉偏右并被右侧截断（功能不增不减）

- **根因（最终确认）**：≡ 触发按钮是 `MenuBar`/`SubmenuButton`，若不给它显式
  定宽，桌面客户端按其最小交互宽度展开（Material 默认 ≥48px，宽于 44px 活动栏）
  ——按钮比列宽，靠任何 Row/Column 对齐都无法居中，内容视觉偏右、右侧溢出/
  被截断（60px 旧列宽恰好容纳所以此前正常，列收窄到 44px 后问题显现）
  （views/global_menu.py build_global_menu）
- **修复**：SubmenuButton 显式定宽 `_MENU_TRIGGER_W = 44`（= 活动栏列宽），使
  MenuBar 恰好等于列宽、不再溢出；按钮样式加 `alignment=ft.Alignment.CENTER`
  强制 ≡ 图标在按钮内严格居中。配合活动栏底部菜单行的整宽居中 Row
  （views/activity_bar.py，无 expand/flex、沉底交给 SPACE_BETWEEN），图标最终
  落在 44px 列正中
- 测试：ruff / py_compile 通过；纯视图布局改动，无单测覆盖

## [未发布]

### 2026-09-05 界面紧凑化：活动栏（第一列）宽度 60 → 44（功能不增不减）

- **功能栏（活动栏）收窄**：最左侧图标式功能区列固定宽度由 60px 收窄至 44px——
  对齐 VSCode / Obsidian 风格活动栏带宽（仅容纳 20px 图标 + 两侧 ~12px 呼吸），
  消除图标两侧大面积留白，横向空间让给文档编辑区；桌面交互直觉不变
  （文件 / 搜索切换面板、再点当前图标收起、底部 ≡ 全局菜单沉底）（views/activity_bar.py）
- 活动栏内部布局自动适配新带宽：激活 3px 强调条 + 图标仍在剩余空间严格居中
- 测试：全量通过；ruff 无新增告警

## [未发布]

### 2026-08-27 任务列表复选框首行对齐（Typora 式）

- **复选框固定第一行**：开启软换行、任务内容折成多视觉行时，复选框不再被上下居中到整块多行文字中部，而是始终停留在第一行、与首行文字对齐（views/rendered_line.py 任务行 Row 由 CENTER 改为 START 垂直对齐，与 Typora 任务列表交互直觉一致）；单行任务视觉不变
- 测试：全量 1153 通过（经 flet 0.86.2 渲染实测：CENTER 居中 ↔ START 首行对比验证）

## [未发布]

### 2026-08-27 代码块边界方向键跳出（Typora 式）

- **代码块边界方向键跳出**：光标在代码块第一行按 `↑`、第一行行首按 `←` 时跳出到代码块上一行行尾；最后一行按 `↓`、最后一行行尾按 `→` 时跳出到下一行行首——光标自然移出代码块继续文档编辑，无需点击外部（views/editor/_fence.py handle_code_exit + views/key_bindings.py 路由）
- **自动补行**：代码块前/后无行（文档首/末行，或相邻行为代码块/公式/表格等岛屿块）时自动创建新空段落行承接光标，不落死角；新行入撤销历史（Ctrl+Z 可回退）
- **实现细节**：CodeEditor `on_selection_change` 实时跟踪光标/选区（`code_caret_ref`），KeyDispatcher 在原生控件聚焦守卫内拦截无修饰方向键，非边界/有选区/表格公式聚焦时原样放行原生导航；跳出后清理代码块聚焦态，光标 TextField 无缝接管
- 测试：新增 tests/test_fence_code_exit.py（23 项）+ test_key_bindings.py 路由用例，全量 1153 通过

## [未发布]

### 2026-08-24 修复：程序无法运行 + 高分屏 DPI 布局错乱（功能不增不减）

- **修复启动即退出**：中断的 let build windows（复制阶段 PermissionError）删除了
  uild\windows\data\app.so（Flutter AOT 客户端），导致 exe 无法启动、python main.py
  静默退出（exit 0 无输出）。完整重建后恢复；另需 --clear-cache 强制重编内嵌 Python
  字节码（否则复用 build\flutter\build\build_python_3.14.6 的旧 pyc）
- **修复高分屏 DPI 布局错乱**：exe 为 PerMonitorV2 DPI 感知，但窗口按物理像素创建导致
  逻辑布局被压缩到 物理宽/DPR；main.py 读取系统 DPI（注册表 AppliedDPI）按比例放大
  窗口尺寸，使 物理尺寸/DPR = 期望逻辑尺寸（100% 缩放下行为不变）
- 已知限制（flet 0.86.5 桌面客户端组件渲染 bug，非本项目代码问题）：Row/Column/Stack 中
  expand 子控件之后的兄弟控件、以及右侧/底部 padding/margin/定位在桌面端不渲染
  （Web 端正常）——右侧大纲列、底部状态栏、活动栏底部菜单、标签栏 + 按钮、对话框
  在桌面端不可见；最小复现已确认，建议向 flet-dev/flet 反馈
- 测试：全量 1123 通过

## [未发布]

### 2026-08-24 界面重构（四）：滚动条贴列最右缘，状态栏/活动栏精修（功能不增不减）

- **文档编辑区滚动条贴紧列最右缘**：WYSIWYG 编辑器水平内边距从外层 Container 移入
  ListView 自身（views/editor/__init__.py），原文模式移入 TextField content_padding
  （views/raw_editor.py）——滚动条不再浮在内容区内 36px 留白处，与 VSCode / Typora
  直觉一致；文本起始位置与换行宽度保持不变（仍为 content_padding 缩进）
- **状态栏紧凑化**：IconButton（默认 48px 最小触控区）替换为 22px 紧凑 ink 图标按钮，
  状态栏高度由 ~46px 降至 ~26px（views/status_bar.py），信息密度不变
- **活动栏激活态改为 VSCode 风格**：去除整块半透明圆角底色，改为左侧 3px 主题色
  强调条 + 主题色图标（views/activity_bar.py），与冷灰底色更协调
- **大纲列左侧 1px 分割线**：与侧边栏右缘对称，列边界清晰（views/outline_panel.py）
- Web 模式跳过 window.center()（无原生窗口，避免 invoke_method 超时，main.py）
- 测试：全量 1123 通过，ruff 无新增告警

### 2026-08-23 界面重构：VSCode / Obsidian 风格横向四列布局（功能不增不减）

- **第一列 功能栏**（新增 views/activity_bar.py ActivityBar）：图标式大功能选项，顶部 文件 / 搜索，底部 设置；选中项主题色高亮。点击当前活动图标 = 一键收起第二列（VSCode 直觉），点击其他图标 = 切换面板并展开（若已收起）；设置按钮打开设置对话框。
- **第二列 管理面板**：原 Sidebar 精简为文件 / 搜索两面板（顶部三面板 Tab 行移除），面板切换改由功能栏驱动；拖拽调宽、sidebar_open 收起动画、文件树/搜索/替换/跨文件搜索全部功能原样保留（views/sidebar.py）。
- **第三列 文档编辑区**：单编辑器 / 拆分 / 对比标签三种模式不变。
- **第四列 大纲**（新增 views/outline_panel.py OutlinePanel）：从侧边栏独立为右侧常驻大纲列（复用 _compute_toc / _render_outline_panel，标题签名 use_memo 缓存），标题点击跳转；左侧常驻竖条 + 头部按钮一键收起/展开（outline_open 设置持久化，默认展开，200ms 动画 + HARD_EDGE 裁剪）。
- **全局菜单 视图 组**新增「切换大纲」；Ctrl+F 仍自动展开第二列并切到搜索。
- 新增设置 outline_open（config/settings.py）；AppContext 新增 toggle_outline
（app/_settings_controller.py / _context.py / __init__.py）。

### 2026-08-23 界面重构（二）：去除顶部行，标签行移入第三列，菜单收纳进功能栏底部

- **去除顶部行**：原顶栏（菜单 ≡ + 标签行）整体移除，主布局只剩 功能栏/管理面板/
  编辑区/大纲 + 底部状态栏；窗口竖向空间让给编辑区。
- **菜单按钮进功能栏底部**：≡ 全局菜单（文件/编辑/段落/格式/视图/帮助，设置功能
  在 文件→设置 / Ctrl+,）从标签行移到第一列底部，替代原设置按钮；菜单样式透明、
  与功能栏背景融合（app/_render.py / views/activity_bar.py）。
- **标签行移入第三列**：TabBar 从全窗宽顶行改为编辑区上方的局部行，宽度与文档编辑区
  一致（含拆分模式双 TabBar + 中缝分隔线，对比标签全宽）；TabBar 不再携带菜单 leading
  （views/tab_bar.py leading 参数保留但传 None）。
- 测试：全量 1122 通过；ruff 无新增告警。

### 2026-08-24 外部修改重新加载后自动切换到对应文件

- 检测到外部修改（后台监测弹窗 / 保存前校验两条路径共用同一处理分支）选择「重新加载」时，除刷新标签文档为磁盘最新内容并清脏外，额外调用 select_tab 切换到该文件（后台检测场景下来源标签可能非激活；已激活则 no-op，拆分模式下跨组切换自动聚焦对应组）
（app/_file_dialogs.py）
- 测试：tests/test_file_dialogs.py 新增 reload_external 回归用例（文档刷新/清脏/mtime 更新/select_tab 切换），全量 1123 通过；ruff 无新增告警。

### 2026-08-24 界面重构（三）：功能栏独占整列高度，状态栏仅存在于第二~四列下方并紧凑化

- **功能栏独占整列**：第一列活动栏改为 STRETCH 撑满窗口整高（VSCode 式整高活动栏，
  菜单按钮仍沉底）；主布局重组为 活动栏 + 右侧列（管理面板/编辑区/大纲 + 状态栏），
  底部状态栏只存在于第二~四列下方，不再横穿第一列（app/_render.py）
- **状态栏紧凑化**：垂直内边距 LG(8)→XS(2)、文字 12→11、图标 16→14、按钮内边距收窄、
  段间距 XXL(16)→LG/MD，整条高度约降低 40%，界面更紧凑（views/status_bar.py）
- 测试：全量 1123 通过；ruff 无新增告警（status_bar 既有 4 项基线未动）。

### 2026-08-24 界面重构（四）：大纲开合入口移入状态栏最右侧，去除大纲列内嵌按钮

- 去除第四列大纲的「展开/收起」竖条与头部 chevron 按钮（views/outline_panel.py）：
  收起/展开统一收敛到底部状态栏最右侧的新按钮（参考「切换侧边栏」交互，与左侧
  侧边栏切换按钮对称；图标 FORMAT_LIST_BULLETED，展开时主题色高亮，tooltip 切换大纲）
（views/status_bar.py 新增 outline_open / on_toggle_outline props；app/_render.py 装配）
- 测试：全量 1123 通过；ruff 无新增告警（status_bar 既有 5 项基线未动）。

### 2026-08-22 修复：侧边栏文件树拖拽精确落到文件夹所在行，消除抖动误移

- **根因**：文件行没有行级 DragTarget，Flutter 拖拽命中取「最深同组目标」——指针在文件行（或行间空隙）上时命中穿透到包裹整个 ListView 的根目录 DragTarget，松手即把文件移入工作区根目录；拖拽过程中高亮在「悬停文件夹行」与「根目录行」间来回切换，表现为抖动异常移动。
- **修复**：文件行在拖拽模式下包「拒绝型」DragTarget（dst_dir=None，_drop_allowed 恒 False）——占位目标把命中挡在行内，悬停不高亮、松手静默忽略；文件夹行目标不变（悬停高亮、松手移入该文件夹）；根空白区仍可拖到根目录（仅真正的空白区域触发）。命中路径为最深目标优先，因此只要指针在某一行上，该行（或其拒绝型占位）必胜，根目录目标永远不会在行内被选中
（views/sidebar.py _wrap_drop_target / _render_files_panel）
- 测试：tests/test_sidebar_file_tree.py 新增 5 用例（_drop_allowed 拒绝型目标/原地/自身/子孙；文件行占位不触发移动且清高亮；文件夹行合法时高亮+移动）

### 2026-08-22 修复：软换行触发时保持光标稳定与 IME 组合态，修复整行内容丢失

- **换行瞬间 IME 组合态保持（五笔/拼音不被上屏）**：输入使行视觉行数/光标视觉行变化（换行/收拢）时，会话值跨视觉行——单行 TextField 的 value 从 left 线性布局，旧代码 caret 渲染在距文字约一个 value 宽度之外且被字段裁切 → 光标"丢失"、IME 候选框 弹错位置。_cursor_overlay 改用 _value_linear_width（views/pixel_layout.py，与渲染层 同源 offsets_x 跨视觉行累加）定位 left = caret_x - textwidth，caret 精确落回光标像素 位置；handle_char_input / backspace_core / delete_core 编辑前后对比视觉行签名（_vline_signature），变化时仅递增 focus_seq 重聚焦——不重建 TextField、不结束会话， 正在拼写的编码不被打断、不上屏，可继续选择候选字（views/editor/_cursor.py）

- **换行后焦点/可见性兜底**：客户端在下一帧才应用 Stack children move + left/top 属性 更新，若移除焦点发生在重建之后，立即 focus() 已在其之前执行（no-op）→ 焦点丢失、 无法继续编辑。_refocus_on_wrap_change 延迟 0.1s 再聚焦一次，并调用 ensure_visible 确保光标所在视觉行可见（换行使行变高，视口底部输入时光标可能被推出可视区）（views/editor/_cursor.py）

- **换行后文字被选中、继续输入覆盖选区**：换行触发（1→2 视觉行）时 Stack children 数量 变化，diff 对 cursor overlay 产生 move 操作 → Flutter 元素重挂载 → 焦点/IME 组合态被打断， Windows IME 提交并选中刚输入的文本 → 继续输入覆盖选区。① _maybe_stack_multi 把视觉行 Text 放入内层 Stack，overlay 作为外层 Stack 独立子项（index 稳定）——换行时外层 children 不变，不产生 move，overlay 不移动/不重挂载（views/rendered_line.py）；② _refocus_on_wrap_change 递增 wrap_sel_seq → 渲染层给 cursor TextField 传 collapsed 选区（caret 在 value 末尾）， 清掉 IME 提交并选中的文本；use_effect 随即复位（仅一次，平时不携带 selection prop， 不干扰 IME 组合态）（views/editor/_cursor.py / line_view.py / cursor_layer.py）

- **继续编辑整行丢失（IME 翻倍误判）**：_fix_ime_doubling 非 ASCII 分支把合法连续输入误判为 IME 翻倍折叠——逐字累积（"你你你你" ← "你你你"）与上屏后行内容形如 X+X（"你"*14 ← "你"*13+"i"）时吞掉已输入内容，且折叠值回推客户端触发 on_change 级联清空整行。新增与 ASCII 分支同型的"逐字累积"守卫（len(value)==len(last_value)+1 且 startswith），仅全 ASCII composing 转上屏或新会话空 last_value 时才折叠（views/_editor_helpers.py）

- 测试：tests/test_cursor_wrap_refocus.py（10 用例：换行触发重聚焦且会话保持 / 文档不丢 / 连续输入不误折叠 / IME 上屏不误折叠 / Backspace、Delete 收拢）+ tests/test_soft_wrap.py（_value_linear_width 5 用例）+ tests/test_editor_helpers.py（3 用例）

### 2026-08-21 增强自动保存：关闭/切换/失焦即时触发

- **关闭程序时**：窗口 close 事件与 websocket 断连钩子在写退出哨兵前，先同步把所有脏且有路径的标签写回原文件（`app/_backup_controller.py` `autosave_on_exit`，受 `auto_save` 主开关控制；未命名文档仍由备份 + 启动恢复面板兑底）
- **关闭文档时**：`request_close` 先同步自动保存待关闭标签——有路径的脏标签保存后变干净直接关闭、不再弹确认框；未命名标签无法自动保存，保持脏状态走既有确认流程（`app/_tab_management.py`）
- **切换文档时**：`activate_index` 在切换前同步自动保存即将离开的文档（全局焦点侧 + 拆分下目标组被替换的标签，共享 document 去重只存一次）
- **光标从文档移出时**：新增 `MarkdownEditor.on_editor_blur` 回调，cursor TextField 真实失焦（焦点移到侧边栏 / 菜单 / 另一窗格 / 另一窗口）时触发即时自动保存；编辑器内部点击（`suppress_blur`）不触发（`views/editor/_raw_mode.py` / `app/_render.py`）
- **新设置** `auto_save_on_switch`（默认开启）：切换 / 关闭文档时保存的总闸，需 `auto_save` 主开关开启；设置面板「切换/关闭文档时立即保存」开关可调（`config/settings.py` / `views/settings_dialog.py`）
- **同步保存原语**：`save_doc_sync`（`app/_file_io_ops.py`）同步静默写盘（原子写入 + 覆盖前备份 + 失败兑底，无对话框）；`autosave_all_dirty_sync`（`app/autosave.py`）支持 indices 范围扫描，供事件回调阻塞落盘后继续流程
- 测试：`tests/test_autosave.py`（同步保存 / 开关门控 / indices 过滤）+ `tests/test_tab_management.py`（关闭直关 / 未命名仍确认 / 切换保存 / 开关门控）

### 2026-08-20 Shift+Alt+F 全文 Markdown 格式化

- 新增 `services/markdown_format.py` 纯函数格式化器，5 条规则：清理行尾多余空格与末尾统一换行；行内代码统一反引号包裹（内容首尾含反引号时升级分隔符并保留空格）；任务列表 `- [ ]`/`- [x]` 规范统一（含引用行内）；引用 `>` 后统一加空格、嵌套 `> >` 合并为连续前缀；中英文混排加半角空格（行内代码/代码块/frontmatter 内容不被改动）
- 快捷键 `shift+alt+f`（browse/edit 两层，`services/shortcuts.py` DEFAULT_SHORTCUTS + ACTION_REGISTRY）；KeyDispatcher 两层路由 → 编辑器动作 `format_document`（`views/editor/_format.py`：全文格式化 + 推全文撤销快照 + 重建 lines + 退出编辑态，原文模式格式化 raw_draft）；编辑菜单「格式化文档」同步可用
- 测试：`tests/test_markdown_format.py`（23 用例）+ `tests/test_editor_format.py`（6 用例）

### 2026-08-20 快捷方式（.lnk）打开的文件：标签栏显示链接文件名

- 打开指向 .md 的 .lnk 时，标签栏与状态栏显示链接文件名（如 `Deepseek-cordis.md.lnk`），而非目标文件名；`file_path` 仍存目标路径——编辑 / 保存 / 去重 / 外部修改监测全部作用于目标文档，仅显示名用链接名（`app/_file_io_ops.py` 打开时记录 `display_name`，`views/tab_bar.py` / `views/status_bar.py` / `app/_tab_helpers.py` 优先展示）
- 新增测试：`tests/test_tab_helpers.py`（display_name 优先/回退）+ `tests/test_open_dedupe.py`（.lnk 打开带 display_name、复用空白标签写入 display_name、普通 .md 不带）

### 2026-08-19 Ctrl+F：侧边栏切换到搜索面板并自动聚焦搜索输入框

- `focus_search`（App 稳定闭包）在切换 `sidebar_panel=search` 后递增 `search_focus_seq`；Sidebar 收到序号变化后经 `use_effect` 聚焦搜索输入框（`views/sidebar.py` `_focus_search_field` + `search_field_ref`），菜单「查找 Ctrl+F」/「全局查找 Ctrl+Shift+F」同样生效
- 搜索输入框 `_search_box` 增加 `ref` 参数（默认 None 不破坏文件过滤框等既有调用）
- **修复测试污染 BUG**：`tests/test_open_folder.py` 的 `open_folder` 会经 `save_settings` 把最小 settings 写回真实 `settings.json`（曾把用户配置覆盖成残缺内容）；新增 autouse fixture 把 `save_settings` 重定向到 pytest 临时目录

### 2026-08-19 修复：首部元数据（YAML frontmatter）删除行内容后无法撤销（Ctrl+Z）

- **修复未聚焦操作的撤销缺失**：直接点 × 删除行 / 拖拽排序 / 粘贴 / 剪切行时，若从未聚焦过属性字段（无聚焦快照），修改不会写入历史栈，Ctrl+Z 无效；现改为修改前惰性捕获快照并推入历史，每次离散操作独立撤销条目（`views/editor/_fence.py` `on_change_code`）
- **修复撤销后表格不刷新**：撤销 / 重做恢复文档后，frontmatter 表格的本地编辑态（`editing_pairs`）不同步，界面仍显示删除后的旧内容，看起来“撤销无效”；新增 `use_effect` 监听文档内容变化，仅在内容与编辑态序列化不一致时同步（不打断输入中的内容与待定空键行，`views/line_view.py`）
- **修复撤销后继续编辑无法再次撤销**：撤销 / 重做恢复后旧聚焦快照已失效，继续编辑不再产生新撤销条目；恢复时清空会话态，下次修改惰性重新捕获（`views/editor/_history.py` `_restore_snapshot`）
- 新增 `_pairs_to_yaml` 序列化助手（写回与同步判定共用同一口径）及 `tests/test_frontmatter_undo.py`（12 个用例）

### 2026-08-16 拆分编辑器：左右独立标签组

- **左右独立标签**（VSCode「向右拆分」）：`Ctrl+\` 开启拆分后标签行与编辑区同步分左右两栏，两组拥有完全独立的标签列表，可打开不同文件并行编辑；`tab.group` 字段（0=左 / 1=右）驱动组感知的标签管理
- **开启拆分默认复制当前文件**：右组以当前焦点侧激活标签创建副本（共享 document 对象，含未保存修改），焦点切到右组；合并时右组空白丢弃、非空白并入左组
- **同文件多副本实时同步**：同一文件左右各开一份时两侧共享同一 Document 对象——任一侧编辑实时同步到另一侧（光标 / 滚动 / 撤销历史仍独立）；脏状态、保存、外部重载、自动保存、定时备份全部按 document 身份联动 / 去重
- **组感知交互语义**：点击某组标签即聚焦该组；`Ctrl+Tab` 仅焦点组内循环；右键「关闭其他 / 关闭全部」仅影响当前组；点哪组「+」就在哪组新建；打开 / 新建 / 恢复备份定向到焦点侧组；右组清空自动收起拆分
- **修复**：修复拆分后点击编辑器触发 `RecursionError`（控制器闭包运行时读取被装配槽覆盖的同名 setter 导致无限自调用；改为构造期捕获原始 state setter）

### 2026-08-15 文件夹实时监测

- 打开文件夹后后台轮询监测外部变化（创建 / 删除 / 重命名 / 移动等树结构变化），文件树自动刷新
- 零依赖方案：后台线程重扫 + 签名对比，2 秒间隔天然节流；应用内文件操作触发的重扫同步监测基准，不重复上报；切换目录时旧监测自动退出

### 2026-08-15 快捷方式（.lnk）支持

- 指向 `.md` 的 Windows 快捷方式在文件树与「打开」对话框中与常规 `.md` 一致：打开 / 编辑作用于目标实际文件（保存写回原文件），复制 / 移动 / 重命名 / 删除仅操作快捷方式本身
- 纯 Python 解析 MS-SHLLINK 二进制格式（LocalBasePath ANSI/Unicode + RelativePath 回退），PowerShell COM 作为解析失败回退；基于 mtime + size 的缓存避免重复 IO；链式快捷方式自动展开（上限 5 层）；跨文件搜索以目标路径参与并与树内文件去重
