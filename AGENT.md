# AGENT.md - 项目AI专属导航文档

## 1. 项目核心定义

- **定位**：对标 Typora 的光标级所见即所得（WYSIWYG）Markdown 桌面编辑器。核心能力：Stack 双层架构（底层渲染层 + 顶层透明 TextField 光标层）、像素级光标对齐（HarfBuzz）、IME 友好输入、软换行 2D 视觉行布局、多文档标签、文件对比 diff、拆分编辑器（左右独立标签组，同文件副本共享 document 实时同步）、侧边栏文件树（.lnk 快捷方式支持 + 外部变化实时监测）/大纲/搜索、快捷键自定义、自动保存与崩溃恢复。
- **技术栈与版本**：
  - Python ≥ 3.12（`requires-python`，模型层用 `StrEnum`）
  - Flet ≥ 0.86.2（声明式组件：`@ft.component` + `use_state`/`use_effect` + `@ft.observable`/`@ft.memo`，启动 `ft.run(main)` + `page.render(App)`）
  - mistune ≥ 3.3.4（行内 AST 解析 + HTML 导出）
  - uharfbuzz ≥ 0.40.0（文本整形测量，与 Skia/Flutter 同引擎）
  - Pillow ≥ 12.3.0（图片尺寸读取）
  - flet-code-editor ≥ 0.86.2（代码块语法高亮编辑岛屿）
  - flet-datatable2 ≥ 0.86.2（表格编辑岛屿）
  - watchdog ≥ 4.0.0（外部修改检测，原生文件通知）
- **运行环境与前提**：Windows 优先的桌面应用；字体 `assets/fonts/AlibabaPuHuiTi-3-55-Regular.otf`（注册名 "Alibaba"）；用户设置持久化于项目根 `settings.json`（由 `config/settings.py` 深合并管理，非源码，禁止提交改动假设）；备份目录由 `services/backup.py` 管理。

## 2. 任务-目录-文档映射表

| 任务场景 | 对应代码目录/核心文件 | 必须前置阅读的文档 |
|---|---|---|
| 入口/窗口/主题/字体变更 | `main.py`、`app/__init__.py` | `README.md` |
| 架构分层调整/大重构 | `app/`、`views/editor/`、`core/` | `.trae/documents/架构重构收尾计划-最终版.md`、`.trae/documents/重构架构说明书.md`、`README.md`「架构设计」 |
| 光标/IME 输入体验修改 | `views/editor/_cursor.py`、`views/cursor_layer.py`、`views/editor/_focus.py` | `.trae/documents/fix-stack-overlay-bugs.md`、`.trae/documents/input-layer-simplification-plan.md`、`.trae/documents/stack-cursor-refactor-plan.md` |
| 光标导航（方向键/Home/End/翻页/记忆列） | `views/editor/_navigation.py`、`views/key_bindings.py` | `.trae/documents/cursor-selection-system-refinement.md` |
| 像素对齐/命中测试/软换行 | `views/pixel_layout.py`、`utils/text_layout.py`、`views/editor/_scroll.py` | `.trae/documents/soft-wrap-2d-cursor.md` |
| Markdown 解析/序列化/HTML 导出 | `parser/`（`_engine.py`/`inline.py`/`block.py`/`reparse.py`/`selection.py`/`serialize.py`） | `parser/__init__.py` 模块文档字符串、`README.md` |
| 数据模型变更（Segment/Line/Document） | `models/document.py` | `README.md`「三级状态模型」 |
| 撤销/重做 | `core/history.py`、`views/editor/_history.py` | `README.md`「撤销/重做（混合快照）」 |
| 快捷键路由/自定义键位 | `views/key_bindings.py`、`views/editor/_key.py`、`services/shortcuts.py`、`views/settings_dialog.py` | `.trae/documents/快捷键自定义功能实现计划.md`、`README.md`「键盘事件分发」 |
| 行内格式（加粗/斜体/链接等 Toggle） | `views/editor/_inline_format.py`、`parser/selection.py` | `README.md`「关键设计决策」 |
| 向外选区（Shift+方向键/跨段选区/剪切） | `views/editor/_outward.py`、`views/editor/_clipboard.py` | `.trae/documents/向外选区键盘路由补全.md`、`.trae/documents/段级编辑-向外选区与剪切删除支持.md` |
| 代码块/表格/公式围栏岛屿 | `views/editor/_fence.py`、`views/table_view.py`、`utils/table_helpers.py` | `.trae/documents/table-refactoring-plan.md`、`.trae/documents/公式功能实现计划.md` |
| 标签栏/多文档管理 | `app/_tab_management.py`、`app/_tab_helpers.py`、`views/tab_bar.py` | `.trae/documents/top-tab-bar-multi-doc.md` |
| 文件对比（diff 标签） | `app/_diff_controller.py`、`views/diff_view.py`、`app/diff_scroll_sync.py`、`views/diff_markers.py` | `.trae/documents/diff-as-tab.md` |
| 拆分编辑器（左右独立标签组/共享document同步） | `app/_split_editor.py`、`app/_tab_management.py`、`app/_focus_router.py`、`app/_tab_helpers.py` | `README.md`「向右拆分编辑器（左右独立标签组）」 |
| 快捷方式（.lnk）解析/操作语义 | `services/shortcut.py`、`views/sidebar.py`、`app/_file_dialogs.py` | `tests/test_shortcut.py`、`README.md`「文件与导出」 |
| 文件夹实时监测（文件树刷新） | `views/sidebar.py`（`poll_fs_changes`）、`tests/test_fs_watch.py` | `README.md`「文件与导出」 |
| 侧边栏文件树/拖拽/右键菜单/大纲/搜索 | `views/sidebar.py`、`app/_file_dialogs.py` | `.trae/documents/vscode-style-file-tree.md`、`.trae/documents/sidebar-search-enhancement.md`、`.trae/documents/sidebar-drag-resize-fix.md` |
| 文件 IO/打开/保存/导出/最近文件 | `app/_file_io_ops.py`、`services/file_io.py`、`services/export.py`、`services/clipboard_html.py`、`services/html_to_markdown.py` | `README.md`「文件与导出」 |
| 自动保存/备份/崩溃恢复 | `app/autosave.py`、`services/backup.py`、`services/recovery.py`、`app/_backup_controller.py`、`views/recovery_dialog.py` | `config/settings.py` 注释、`README.md` |
| 设置面板/配置项增删 | `views/settings_dialog.py`、`config/settings.py`、`app/_settings_controller.py` | `README.md`、`config/settings.py` 模块文档字符串 |
| 主题配色/字号阶梯/间距常量 | `styles.py` | `README.md`「样式系统」 |
| 搜索替换 | `views/editor/_replace.py` | `.trae/documents/搜索替换功能实现计划.md` |
| 性能优化（大文档/重渲染） | `views/editor/_render.py`、`views/line_view.py`（`@ft.memo`）、`parser/reparse.py` | `.trae/documents/性能优化-响应卡顿与大文档假死.md`、`.trae/documents/incremental-rendering-optimization.md` |
| 原文模式（源码编辑） | `views/editor/_raw_mode.py`、`views/raw_editor.py` | `README.md` |
| 图片交互（粘贴/右键菜单/另存为） | `views/editor/_image.py`、`views/rendered_line.py`、`utils/text_layout.py`（image_fit_size） | `README.md`「关键设计决策」 |
| 测试编写 | `tests/`（按 `test_<模块>.py` 命名） | 同目录下同类测试文件、`pyproject.toml` `[tool.pytest.ini_options]` |
| 依赖更新 | `pyproject.toml` | `README.md`「技术栈」 |
| 打包发布 | `pyproject.toml` `[tool.flet.*]` | — |

## 3. 全局架构与调用边界

**分层依赖顺序（上层 → 下层，禁止反向）**：

1. `main.py` — 入口：注册字体/主题、`page.render(App)`
2. `app/` — App 组件层（`AppContext` 状态容器 + 控制器模式）：唯一持有 `page` 全局操作权的层（`page.on_keyboard_event` 绑定、`page.theme_mode` 同步写入、对话框/overlay 挂载）
3. `views/` — 声明式视图层：`views/editor/`（MarkdownEditor 根组件包，`EditorContext` + 工厂模式，约 20 个 `_*.py` 工厂模块）+ 顶层视图组件（`sidebar.py`/`tab_bar.py`/`line_view.py`/`rendered_line.py`/`cursor_layer.py`/`pixel_layout.py`/`segment_view.py`/`key_bindings.py` 等）
4. `core/` — 编辑器核心状态契约：`actions.py`（EditorActions dataclass，editor → App/key_bindings 动作契约）、`cursor.py`（CursorState）、`history.py`（EditHistory）
5. `parser/` — Markdown 解析层（包内单向 DAG：`_engine` ← `inline` ← `block` ← `reparse` ← `selection`；`serialize` ← `_engine`）
6. `models/` — 数据模型层（`@ft.observable`：Segment/Line/Document 三级状态）
7. `services/` — 业务逻辑层（shortcuts/file_io/backup/recovery/export/file_ops/clipboard_html 等）
8. `utils/` — 通用工具层（`segment_helpers`/`text_layout`/`table_helpers`/`file_helpers`，**无任何项目内依赖**）
9. `config/` — 配置层（DEFAULT_SETTINGS/load/save 深合并）+ `styles.py`（主题配色与排版常量，根目录）

**禁止的调用规则**：

- 禁止 `services/`、`parser/`、`models/`、`core/`、`utils/`、`config/` 导入 `app/` 或 `views/`（反向依赖）；后果：架构循环依赖、组件无法独立测试。
- 禁止 `utils/` 内出现任何 `import models/parser/services/views/app`；后果：破坏叶子层纯函数定位，工具函数被状态耦合污染。
- 禁止绕过 `parser/__init__.py` 直接 `from parser.block import ...`（包外部调用方）；后果：绕过唯一聚合入口产生循环依赖风险。包内部子模块按 DAG 方向单向依赖。
- 禁止视图层（`views/`）内实现 Markdown 解析/序列化逻辑；解析一律走 `parser`。后果：解析口径分裂，roundtrip 测试失效。
- 禁止 UI 更新走命令式路径：不得手动增删控件、不得在组件渲染后手动 `control.update()`/`page.update()` 改 UI；一切界面变化由 `@ft.observable` 字段变更或 `use_state` 触发。后果：Flet 0.86 组件在 render 中构建后被冻结，命令式 `row.update()` 直接抛 `RuntimeError`。
- 禁止修改 `page.theme_mode` 于渲染期之外（`App` 渲染期间同步写入保证 `_current_colors()` 取色一致）。
- 键盘事件只经 `KeyDispatcher`（`views/key_bindings.py`）分发：`page.on_keyboard_event → KeyDispatcher.handle(e)`，`actions_ref` 每次渲染按优先级 diff > split > 单编辑器 绑定（`app/_focus_router.py` 的 `_get_active_nav()`）；禁止在别处直接挂接键盘事件处理编辑动作。

**全局扩展入口与规范**：

- 编辑器新动作：在 `views/editor/_actions.py` 实现 → 写入 `EditorActions`（`core/actions.py`）对应字段（必填，不许 Optional）→ `views/key_bindings.py` 或 `app/_keyboard.py` 路由。
- 新增设置项：`config/settings.py` 的 `DEFAULT_SETTINGS` 加默认值（深合并自动补齐老 `settings.json`）→ `views/settings_dialog.py` 加 UI → `app/_settings_controller.py` 接更新。
- 新增行内段类型：`models/document.py` `SegType` 加枚举 → `parser/_engine.py` 包裹器表 → `styles.py` `segment_style` 加样式 → `utils/segment_helpers.py` 更新 `PREFIX_SEGTYPES`/`MONO_SEGTYPES`/`WRAP_SYNTAX`/`display_text` → 补 `tests/test_parser_roundtrip.py` 用例。
- 新增块类型：`models/document.py` `BlockType` → `parser/block.py` 块级正则 → `views/editor/_render.py`/`views/line_view.py` 渲染分支。
- 新增测试：`tests/test_<模块>.py`，命令 `python -m tests.test_<name>` 或统一 pytest。
- 面向 Flet 写代码时遵循 `.trae/skills/flet-skill/SKILL.md`（声明式范式、Hooks 铁律、`ft.run`/`page.render` 启动约定）。

## 4. 顶层红线规则（禁止修改清单）

- 禁止给透明光标 TextField（`views/cursor_layer.py`）设置 `value` 属性或改用受控值同步，后果：Flet 重渲染同步 value 打断 IME 组合态，中文输入丢字/跳变；清空内部 value 必须维持 `use_effect([clear_value_seq])` 异步清空机制。
- 禁止拆散 `views/editor/_cursor.py`（光标/IME 输入核心组为紧耦合设计），后果：跨文件状态同步引入光标跳动类回归。
- 禁止改动 `utils/text_layout.py` 与 `views/pixel_layout.py` 的 HarfBuzz 整形测量与 letter_spacing=0.25/字形补偿、CJK 字体回退切分逻辑，后果：光标 X 与渲染层 TextSpan 像素级对齐失效，偏移随字符数线性累积。
- 禁止在渲染层与光标层使用不同的断行算法：两者必须共用 `_line_visual_layout`（软换行），后果：换行点不一致导致光标 Y 定位与渲染文本错位。
- 禁止在高频编辑路径用 `reparse_line` 替代 `reparse_line_atomic`，后果：单次编辑触发 2-7 次 observable 通知，大文档输入卡顿。
- 禁止原地修改 `lines[i].raw`（或任何 observable 集合元素字段）后直接 `document.lines = lines` 而不创建新 `Line`/`Segment` 对象，后果：observable 浅比较判定未变化，UI 不重渲染（表格 `add_col`/`set_align` 等结构操作已踩过此坑）。
- 禁止删除 `core/actions.py` `EditorActions` 任何字段或改为可选默认值，后果：构造时缺失参数直接 `TypeError`，全部键盘动作与 App 层联动失效。
- 禁止移动 `views/key_bindings.py` `KeyDispatcher.handle` 中「向外选区拦截块」到 layer 判定之后，后果：outward_sel 激活时 `cursor_li is None` → layer=browse，Backspace 误路由到 SelectionArea 删除分支。
- 禁止将 `core/history.py` `EditHistory` 固定容量 50 改为无界，或把行级 `LineEditSnapshot` 改为全文 `EditorSnapshot`，后果：大文档撤销栈内存膨胀。
- 禁止使同行输入时 `cursor_text_field` 的 `key` 发生变化（key 基于 `li + nav_seq`，仅撤销/重做等强制重建场景递增 `nav_seq`），后果：TextField 重建打断 IME 组合态。
- 禁止移除渲染层 Text 与 cursor TextField 共用的同一 `StrutStyle`（`force_strut_height=True`），后果：光标 baseline 与渲染文字 baseline 错位。
- 禁止绕过 `ft.memo` 缓存约定随意增删 `views/line_view.py` 的 prop（非激活行 prop 集合必须稳定），后果：光标移动触发全列表重渲染，性能退化。
- 禁止在 `views/editor/_fence.py` 之外给 CODE/TABLE/MATH 岛屿接入 active/draft 编辑系统，后果：独立岛屿架构被破坏，光标跳动；岛屿聚焦期间必须保留 `code_focus_ref`/`table_focus_ref` 守卫让 KeyDispatcher 放行原生键。
- 禁止用 `ft.PopupMenuButton` 实现右键菜单（项目约定统一 `ft.ContextMenu`），后果：与既有菜单体系（手动 `open(global_position=...)`、`secondary_trigger` 控制）不兼容。
- 禁止直接解析/编辑 `settings.json` 而不经 `config/settings.py` 深合并，后果：新增默认键缺失导致老用户 `KeyError`。
- 必须将 flet 的 `DeprecationWarning` 视为错误（`pyproject.toml` 已配置 `filterwarnings = ["error::DeprecationWarning:flet.*"]`），后果：使用废弃 API 时测试直接失败。
- 必须保持 `models/__init__.py` 重新导出全部公共符号（`from models import Document` 等引用兼容），后果：外部引用断链。
- 必须使用 `dispatcher_ref`（每次渲染同步最新 KeyDispatcher 实例）而非空依赖 effect 闭包捕获，后果：快捷键修改后不生效。
- 禁止控制器闭包运行时调用与自身装配槽同名的 `ctx.set_*`（如 `ctx.set_active_pane` 在 `app/__init__.py` 装配后已被控制器函数覆盖），必须在 `build_*(ctx)` 构造期先捕获原始 state setter 再使用，后果：闭包读到自身 → 无限自调用 `RecursionError`（已踩坑，`tests/test_split_editor.py` 有装配覆盖回归测试）。
- 必须维护拆分编辑组不变式：`active_index == 焦点侧组激活索引`（经 `app/_tab_management.py` 的 `activate_index` 统一维护）；拆分态下右组不可为空（`do_close_many` 右组清空时自动收起拆分）。后果：手写 `set_active_index` 绕过统一入口会破坏标签行 / 编辑器 / 焦点三处状态一致性。
- 同文件多副本必须共享同一 `document` 对象（拆分开启 / 跨组打开时直接引用，非 re-parse 复制），且所有元数据变更（dirty / mtime / 路径 / 外部重载）必须按 document 身份（`is` 比较）同步所有副本、自动保存与定时备份必须按 document 身份去重。后果：副本内容与脏标记不一致——关闭确认误判、同内容双写盘、另一副本保存被误判为外部修改。

## 5. 标准验证流程

按顺序执行（工作目录 = 项目根）：

1. **依赖一致性检查**：
   ```bash
   pip install -e .
   # 或仅校验已装版本满足约束：
   pip check
   ```
2. **单元测试执行**（`pyproject.toml` 已配 `pythonpath=["."]`、`testpaths=["tests"]`）：
   ```bash
   python -m pytest tests/ -q -p no:cacheprovider
   # 单模块：python -m pytest tests/test_parser_roundtrip.py -q
   # 按主题：python -m pytest tests/ -q -k "sidebar or parser"
   ```
   **受限环境注意事项**：若 `%TEMP%` 不可写（沙箱 / 安全软件），依赖 `tmp_path`
   的用例会在 setup 阶段报 `PermissionError: [WinError 5]`（属环境问题，非代码缺陷）。
   此时改用工作区内临时目录：`python -m pytest tests/ -q --basetemp=tests/.tmp
   -p no:cacheprovider`，并把 `tests/.tmp` 加入忽略。
3. **进程内集成冒烟**（`tests/test_boot_smoke.py`，无需 Flutter 前端）：
   ```bash
   python -m pytest tests/test_boot_smoke.py tests/test_harness.py -q -p no:cacheprovider
   ```
   夹具 `tests/harness.py` 在进程内渲染真实 `App` 组件树，可点击控件、经
   `page.on_keyboard_event` 派发按键、驱动 effect 与重渲染。这是覆盖「组件装配
   断链 / 回调丢失 / 快捷键失效」的唯一自动化手段，改动 `app/`、`views/` 装配层
   后必须跑通。
4. **核心功能主流程验证**（GUI 冒烟，`python main.py` 或入口命令 `cs-markdown-editor`）：
   - 打开/新建 `.md` → 中文 IME 连续输入（不丢字、光标不跳）→ 行内格式 Toggle（Ctrl+B 两次包裹/取消）→ Backspace 行首合并 / Delete 行尾合并
   - Ctrl+Z/Ctrl+Y 撤销重做 → Ctrl+S 保存落盘 → Ctrl+\ 拆分视口 → 侧边栏文件树右键/拖拽 → Alt+Z 软换行下长行光标上下移动
   - 亮/暗主题切换后代码块高亮联动、`settings.json` 写入合法
5. **代码规范/类型检查**（ruff 配置于 `pyproject.toml`：line-length=100、target=py312、select=E/F/W/I/UP/B/C4/SIM/RUF，忽略 E501/RUF001-003）：
   ```bash
   ruff check .
   ruff format --check .   # 如使用格式化
   ```

## 6. 重构基线（当前进度）

**验证基线**：`python -m pytest tests/ -q -p no:cacheprovider` → 1002 passed +
182 环境性 `tmp_path` 报错（受限环境专属，非代码缺陷）。

**已完成**：

- 删除根目录命令式草稿 `test.py`（遗留 `page.add()` 实验文件）。
- 新增 `tests/harness.py`（进程内声明式渲染夹具）+ `tests/test_harness.py`
  （夹具自检）+ `tests/test_boot_smoke.py`（真实 App 启动 / 快捷键冒烟）。
- **删除 `models/__init__.py` 兼容 re-export 垫片**：62 处 `from models import …`
  改为直接的 `from models.document import …`。
- **拆分 `views/sidebar.py`（2181 → 2090 行），消除视图层反向私有依赖**：

  | 新模块 | 承接内容 |
  |---|---|
  | `services/file_tree.py` | 文件类型图标映射、`scan_files` / `tree_signature` / `poll_fs_changes` / `filter_tree` / `flatten_tree` / `collect_md_paths` |
  | `services/search.py` | `build_query_regex` / `match_lines` / `search_in_file` / 反向引用替换一族 + 上限常量 |
  | `services/clipboard.py` | 剪贴板读写辅助（原在 `views/line_view.py`） |
  | `utils/toc.py` | `compute_toc` 标题派生（纯函数） |
  | `views/toc.py` | 大纲标题树渲染 + 级别色条（自包含行工厂） |

  随之消除的反向依赖：`views/doc_search.py` → `services.search`（原 `views.sidebar._*`）、
  `views/outline_panel.py` → `utils.toc` + `views.toc`（原 `views.sidebar._*`）、
  `views/table_view.py` → `services.clipboard`（原 `views.line_view._*`）、
  `views/editor/_blocks.py` / `_fence.py` → `utils.table_helpers`（原 `views.table_view._*`）。
  表格工具（`split_row` / `join_row` / `align_marker` / `is_table_separator`）已统一到
  `utils/table_helpers.py` 单一来源，视图层不再自带副本。

- **消灭编辑器/应用层的逐槽手工装配（Service Locator 的机械化部分）**：

  | 文件 | 变化 | 做法 |
  |---|---|---|
  | `views/editor/__init__.py` | 856 → 731 行 | 137 处 `ctx.x = cbs["y"]` → 单个循环；契约集合 `EditorContext.wiring_slots()` |
  | `app/__init__.py` | 926 → 877 行 | 80 处同型赋值 → 单个循环（含 `_RENAMED` / `_UNSTORED` / `_HAND_WIRED` 三类例外） |

  新增守护测试：`tests/test_editor_wiring.py`、`tests/test_app_wiring.py`（共 12 条），
  用 AST 静态校验「控制器返回 key == ctx 字段名」约定，改名漏改立即失败。

  过程中修出两个**真实缺陷**：
  1. `handle_outward_enter` 从未装配到 ctx（`ctx.handle_outward_enter` 退化为默认 no-op，
     即向外选区按 Enter 不换行）——已补进契约与字段。
  2. 装配顺序必须早于 `build_keyboard`：KeyDispatcher 构造期立即求值 `ctx.toggle_theme`
     等回调，循环放在其后会让 dispatcher 捕获默认 no-op（Alt+T / Ctrl+Shift+B 静默失效）。
     该顺序由 `test_wiring_runs_before_keyboard_construction` 守护。

- **编辑器工厂显式依赖契约**：新增 `views/editor/_contracts.py`（19 个 `Protocol`，
  共 515 行），每个 `build_xxx(ctx: XxxEnv)` 的签名即依赖清单（只列该工厂实际读取的
  ctx 字段）。字段集合由 AST 静态提取，`tests/test_editor_contracts.py`（5 条）守护
  「契约 == 实际读取」；`EditorContext` 结构性满足全部契约，运行期零变化。

- **修正 README 快捷键表失真**：浏览态/编辑态两张表改为从
  `services/shortcuts.py::ACTION_REGISTRY` 生成（唯一事实来源）；新增「编辑态补充」
  收录未登记进注册表的固定键；并修正三处与实际实现不符的描述：
  删除线实际是 `Ctrl+D`（原写 `Alt+Shift+5`）、**`Escape` 已不再切换侧边栏**
  （源码注释明确 "Esc 不再切换侧边栏"，侧边栏固定 `Ctrl+Shift+B`）、
  下划线 `Ctrl+U` 与插入图片 `Ctrl+Shift+I` 的默认绑定已不在 `DEFAULT_SHORTCUTS` 中。
  另补登记 `paste_plain`（原先有默认绑定但设置页不可改）。

- **应用层控制器显式依赖契约**：新增 `app/_contracts.py`（9 个 `Protocol`，260 行），
  `build_xxx(ctx: XxxEnv)` 签名即依赖清单。`tests/test_app_contracts.py`（6 条）守护。
  过程中修出**真实缺口**：7 个槽位（`doc_search_*` / `open_doc_search` /
  `close_doc_search` / `global_search` / `open_external`）被 `build_keyboard` 读取，
  但 `AppContext` 从未声明它们——运行期靠动态 `setattr` 存在，静态不可见；已补进声明。

- **继续拆分巨型文件**：

  | 文件 | 变化 | 迁出内容 |
  |---|---|---|
  | `views/sidebar.py` | 2085 → 1671 | 424 行通用 UI 部件工厂 → `views/_widgets.py`（右键菜单 / 拖拽 / 行容器 / 搜索框 / 空态） |
  | `views/key_bindings.py` | 1306 → 1229 | 组合键原语 → `views/_combo.py`（`combo` / `extract_printable_char` / `NON_PRINTABLE_KEYS`） |

  累计：`sidebar.py` 2181 → 1671（-23%）、`key_bindings.py` 1219 → 1229（净持平，
  原基线为 1219，后因补功能增至 1306，本轮拆出 88 行）。

- **拆解 `KeyDispatcher.handle()` 单体（423 → 239 行）**：按关注点抽出四个方法，
  每个都是原代码的等价搬移（`if matches(...) → 动作 → return` 序列改为返回 bool +
  单处早退调用，逐段用 AST 校验「每个子句都以 return 结束」后才落盘）：

  | 新方法 | 行数 | 关注点 |
  |---|---|---|
  | `_handle_global_shortcuts` | 143 | 两层均生效的全局键链（文件/标签/视图/行内格式/搜索替换） |
  | `_handle_multi_cursor_clipboard` | 51 | 多光标下 Ctrl+C/X/V 同步所有光标选区 |
  | `_sync_modifier_keys` | 21 | 页面级事件 → editor `*_pressed_ref` 的修饰键同步 |
  | （原 `handle` 保留） | 239 | 捕获模式 / 焦点门控 / layer 判定 / 分发 |

- **拆分 `views/line_view.py`（1820 → 1054，-42%）**：先下沉 `wrap_block` 解除导入环，
  再迁出前置元数据整组：

  | 新模块 | 行数 | 内容 |
  |---|---|---|
  | `views/_block_frame.py` | 109 | `wrap_block`（块级容器包裹：缩进 / 引用边框 / 高亮 / diff 背景）——**必须先下沉它**，否则 `_frontmatter` 依赖它会与 `line_view` 成环 |
  | `views/_frontmatter.py` | 697 | `render_frontmatter`（原 612 行）+ `parse_yaml_pairs` / `pairs_to_yaml` / `drag_src_idx` / `reorder_pairs` |

- **修复行渲染分支的 3 处真实缺陷**（新增 `tests/test_line_render_branches.py` 时发现）：

  1. `_render_code_block` / `_render_frontmatter` 调用 `_wrap_block` 时**漏传必填位置
     参数 `base`** → 任何代码块 / YAML 前置元数据渲染都必然抛 `TypeError`；
  2. 由此连带 `on_code_blur` 与 `on_change_lang` **位置参数整体错位一位**（失焦回调
     被当作换语言回调）；
  3. 修复：两个渲染函数签名补上 `base: int`（与调用点参数顺序一致）。

  这 3 处此前**零测试覆盖**（渲染层 `ft.Control` 无法断言内容），因此长期潜伏。
  新测试用「调用点位置参数 vs 签名逐位比对」+「在组件渲染上下文内真实调用一次」
  双重锁定；已反向验证：把 `base` 从签名移除时该测试立即失败。

- **拆分 `views/rendered_line.py`（1200 → 668，-44%）**：

  | 新模块 | 行数 | 内容 |
  |---|---|---|
  | `views/_line_helpers.py` | 81 | 行级小工具：可见性 / 行内公式 / 图片段 / 行样式 / 链接打开 |
  | `views/_spans.py` | 483 | TextSpan 变换与视觉行切片：选区高亮 / 任务淡化 / 前缀剥离 / 搜索着色 / raw→flat 映射 / 软换行切片 |

  两组都不引用 `RenderedLine`（已用 AST 逐一确认），因此抽出后 `rendered_line`
  单向依赖它们，无导入环。调用点统一带模块前缀（`_spans.xxx` / `_line_helpers.xxx`）。

- **收紧静默异常（7 处）**：`views/editor/_scroll.py` 的 5 处与
  `views/key_bindings.py`、`views/editor/_clipboard.py` 各 1 处，原来是
  `except Exception: pass`——现在改为只容忍**预期**异常
  （`AttributeError` / `RuntimeError`：控件未挂载、会话已销毁），其余照常抛出。
  原先「翻页不动」「跳转位置错」「剪切无效」这类回归完全没有痕迹。
  剩余 22 处多为刻意的容错（自动保存失败不阻塞切标签、全屏切换不受支持时忽略、
  平台探测失败回退），保留但在源码中带原因注释。

- **放弃的两项（记录判断依据，避免后人重复踩）**：

  1. **按「未被读取」删除 ctx 字段**：写了两版扫描器都无法自证正确
     （第一版把明显被读取的 `content_max_width` 也判为 0；改成逐名 grep 后仍有
     47/141 个槽位被判「无消费者」，而 `on_key_down` 明明读取自 `_key.py` 的
     `ctx.on_key_down`）。根因是这类值大量经**下标或 prop 传入**被消费
     （如 `key_cbs["on_key_down"]` → `KeyboardListener(on_key_down=...)`），
     静态「找 `.name`」的启发式与真实消费模式不匹配。**结论：不做该类删除**——
     误删被读取的字段不会被任何测试拦住，风险远大于收益。
  2. **`EditorContext` / `AppContext` 字段拆分为多个契约对象**：`document` 一个字段
     就有 106 处 `ctx.document` 读取，全量拆分会触及数千处调用点；而 Service Locator
     的**实际危害**（依赖隐式、改名静默失效）已由两份 `_contracts.py` + 装配契约测试
     消除。按 objective 的「最简单实现」原则，不再为形式上的拆分引入大范围回归风险。

- **统一全局快捷键动作表（`key_bindings.py` 1300 → 1166）**：原先
  `_handle_global_shortcuts`（编辑器内焦点）与 `_handle_foreign_only`（搜索框 /
  对话框等原生输入框焦点）**各自维护一份 18 项重复 if 链**，且调用方式不一致
  （`open` / `open_folder` 在前者直接调用、在后者走 `page.run_task`）。这导致
  「新增一个全局快捷键只加进一条路径 → 在某类输入框里静默失效」。
  现抽为模块级单一来源：

  ```python
  _GLOBAL_ACTIONS = (("close_tab", "ctrl+w", "cb"), ("open", "ctrl+o", "task"), ...)
  #   "cb" 同步回调 / "task" 协程交 page.run_task / "raw" 作用于编辑器自身
  ```

  两条路径都改为遍历该表（`_run_global_action` 统一三种调用方式），并由
  `tests/test_global_actions_table.py`（6 条）守护：表结构合法、两条路径都遍历表、
  Ctrl+F / Ctrl+Shift+F 的浮层优先分支早于表循环、默认键无冲突、
  编辑器内路径不再长出重复 if 链。

  改造中被**既有测试抓到 1 次真实回归**：第一版把 Ctrl+F/Ctrl+Shift+F 的
  「浮层优先」写成表内分支，被同表内前序项抢先命中（`test_ctrl_f_routes_doc_search_when_available`
  失败）。修正为特例判断前置，两条路径对称。

- **补齐分发器缺失覆盖（`tests/test_key_dispatch_parity.py`，10 条）**：覆盖此前
  **无直接测试**的三处：

  | 覆盖对象 | 行数 | 测试要点 |
  |---|---|---|
  | `_handle_multi_cursor_clipboard` | 51 | Ctrl+C/X/V 的消费条件与守卫；无选区时不消费；无关组合键不吞 |
  | `_sync_modifier_keys` | 21 | 三个修饰键 ref 全同步；`actions=None` 不抛；每次 dispatch 都同步 |
  | 两路径行为等价性 | — | 13 个全局动作在「编辑器内」与「原生输入框内」触发同一 app 回调 |

  等价性测试是 `_GLOBAL_ACTIONS` 单一来源的核心保证：若某动作只加进一条路径，
  它就会在另一类焦点下静默失效。**已反向验证**：把 `toggle_theme` 从动作表移除后
  该套测试立即 `2 failed`；恢复后全绿。

- **修复 `focus_mode` 未接入外来输入域路径（真实缺陷）**：新增
  `tests/test_global_action_ids.py` 时发现——`Ctrl+Shift+K`（聚焦模式）已在
  `ACTION_REGISTRY` 登记且 `scope="both"`，但只存在于 `_handle_shortcuts`，
  **未接入 `_GLOBAL_ACTIONS`**。后果：焦点在搜索框 / 对话框时按 `Ctrl+Shift+K`
  完全无反应。根因是该动作走 `EditorActions.toggle_focus_mode`，而外来输入域路径
  拿不到编辑器动作实例。

  修复：`AppContext` 新增 `focus_mode` 槽位（桥接到当前焦点编辑器的
  `toggle_focus_mode`），并在 `_GLOBAL_ACTIONS` 登记。

- **动作表 id 一致性守护（`tests/test_global_action_ids.py`，4 条）**：
  `browse_sc.get(name, default)` 在 `name` 不存在时会**静默回退默认键**，因此
  动作 id 拼错或未登记会让用户的自定义键位被无声忽略（快捷键"部分失效"且无报错）。
  测试断言：表内 id 都在 `ACTION_REGISTRY` 与 `DEFAULT_SHORTCUTS` 中、默认键与
  registry 一致、**已登记的全局类动作必须接入表**（正是这条抓出了 `focus_mode`）。
  已反向验证：移除 `focus_mode` 条目后该测试 `1 failed`。

- **行视图端到端渲染测试（`tests/test_line_view_render.py`，4 条）+ 修出第 3 处
  `base` 漏传**：新增「构造真实 `LineView` 组件并渲染」的测试（覆盖 12 种块类型 ×
  浏览态 / 激活态，走完整用户路径：组件渲染 → 分支选择 → 岛渲染 → 块级容器包裹）。
  该测试立刻暴露出**代码块主分支**（`line_view.py` 的 `_render_code_block` 收尾处）
  同样漏传 `wrap_block` 的必填 `base`——这是第 6 轮同类缺陷的第三处，此前两处
  （`_render_code_block` / `_render_frontmatter` 的子函数调用）已修，但这一处
  位于真正被 `LineView` 调用的路径上，**意味着代码块渲染一直是坏的**。

  同时新增 AST 守护 `test_every_wrap_block_call_passes_base`：扫描全仓所有
  `wrap_block(...)` 调用，断言前 3 个位置参数齐全（或显式传 `base=`）。
  已反向验证：还原缺陷后该测试 `1 failed`。

  > 教训：把函数抽成模块并给参数加类型/位置约束时，**必须同时审计所有调用点**。
  > `wrap_block` 从 `line_view` 迁到 `_block_frame` 时加了 `base: int`，但只改了
  > 报错可见的那几处；渲染层无法断言内容，漏传只能靠端到端渲染或 AST 检查兜住。

- **补齐编辑器输入核心覆盖（`handle_char_input` / `on_tap_line`，共 26 条）**：

  | 新测试 | 条数 | 覆盖 |
  |---|---|---|
  | `tests/test_char_input_delta.py` | 15 | IME 热路径 delta：ASCII 追加 / composing 增长 / 上屏替换 / 连续上屏 / composing 取消与放弃 / 无变化 no-op / 完美翻倍折叠 / 行中会话；守卫：浏览态、围栏行、向外选区、粘贴中、越界、多光标选区替换 |
  | `tests/test_cursor_tap_routing.py` | 11 | `on_tap_line` 全部分支：越界、常规定位+重聚焦+按需滚动、选区清除、多光标退出、Alt+Click 副光标、Alt+Shift+Click 列光标、围栏块点击、公式编辑态退出/保留 |

  写这组测试时**花了 4 轮迭代才把 `handle_char_input` 的会话模型弄对**（我最初
  猜错了 ``start_off`` / ``last_value`` / ``value`` 三者的语义，产出的断言与实现
  不符）。最终由代码与实测确定并写入测试文档字符串：
  ``start_off`` = 会话在该行的**绝对**起点；``last_value`` = 上一次 TextField value
  （会话局部）；文档在会话期间**已包含** ``last_value``。
  **有一条我至今无法确证的「行中新会话」场景（`start_off>0` 且 `last_value=""`）
  被我删除而非写成猜测性断言**——测未定义行为比不测更糟。

- **补齐 `backspace_core` / `delete_core` 语义覆盖
  （`tests/test_backspace_delete_semantics.py`，20 条）**：既有测试只覆盖
  「软换行收拢时重聚焦」，**行首合并 / 行尾合并 / 边界行为**无直接测试，
  而它们正是 Typora 式编辑的核心手感。新测试固定：段内删字符与光标位移、
  行首与上一行合并（含接缝落点、上一行为空行）、首行行首 / 末行行尾无操作、
  相邻为围栏块不合并、光标在围栏行不处理、HR 行首/行尾转为空行
  （注意新行是 `BlockType.BLANK` 而非 `PARAGRAPH`——由 `parse_markdown("")` 产出）、
  向外选区激活时委托给选区删除、浏览态无操作。

**待重构的具名技术债**（审计结论，按优先级）：

| 问题 | 位置 | 目标 |
|---|---|---|
| `EditorContext` / `AppContext` 仍是单一大 dataclass | `views/editor/_context.py`(448) / `app/_context.py`(304) | **有意保留**（见上「放弃的两项」）；依赖已由 `_contracts.py` 显式化 |
| 巨型文件（剩余） | `views/_frontmatter.py` 697 / `views/sidebar.py` 1672（`_render_files_panel` 395） | 继续按职责拆分 |
| `key_bindings.py` 剩余 | `_handle_shortcuts`(160) / `_handle_edit_nav`(106) | 全局键已表驱动；这两支属「编辑态快捷键 + 光标导航」，语义不同，暂不强合 |
| 静默容错（剩余 22 处） | 自动保存 / 全屏 / 平台探测等 | 多为刻意容错，保留；新增时须带原因注释并只捕获预期异常 |

**重构操作注意（踩坑记录）**：

- 批量改名/迁移必须先用 `ruff check --select E9,F821,F811` 做语法与未定义名门禁，
  再跑全量测试；正则跨行匹配极易破坏多行括号导入（已踩坑并回滚重做）。
- 大文件按行号删除区块时须用「def 名锚点 + 不吞尾随空行」推导区间：硬编码行号会
  随文件变化漂移并误删相邻函数（`sidebar.py` 拆分时曾误删 `_resolve_files_root`）。
- 用锚点定位代码区块时**不要用注释文本**：同一个注释常出现在模块 docstring 里，
  曾导致整段被误删；改用唯一的代码行作锚点。
- 把「逐槽赋值」改成「循环装配」时，必须先确认该槽位没有在别处被手工覆盖
  （`_HAND_WIRED`），否则循环会把真实实现覆盖成默认 no-op。
- 提取函数到新模块时**重命名要检查同名局部变量**：把 `_combo` 改名为 `combo` 后，
  `combo = combo(e)` 变成自引用 → `UnboundLocalError`，一次性挂掉 90 个测试。
  改用别名导入（`from views._combo import combo as key_combo`）。
- 迁移符号后必须全仓搜索旧导入路径（不只搜源码，也要搜 `tests/`）：
  `_drop_allowed` 迁到 `_widgets` 后，`tests/test_sidebar_file_tree.py` 仍在
  `from views.sidebar import`，导致整份测试收集中断。
- 函数边界自动推导易错：`[def 行, 下一个顶层语句之前]` 这类规则在没有顶层语句时
  会把文件尾部整段吞掉。**用 AST 的 `lineno` / `end_lineno` 定位区间**，不要手写扫描。
- 自动抽取的判据必须**唯一标识目标，并加形状守卫**（长度 / 语句类型断言）：
  用 `has_secondary_cursors()` 这个谓词定位分支时，命中了另一个同谓词分支；
  因插入位置独立计算，结果同一条件在 `handle()` 里出现两次，而 ruff 与 111 个
  测试全部通过——只有人工核对调用点才发现。**测试全绿 ≠ 改对了**。
- 迁移模块后必须**全仓搜索旧导入路径（含 `tests/`）并更新函数新名**：
  `_wrap_block` → `_block_frame.wrap_block`、`_render_frontmatter` →
  `_frontmatter.render_frontmatter`，漏改会让整份测试收集中断。
- 拆分前先确认被迁函数**没有依赖留在原模块的私有常量**：`_FM_DRAG_GROUP`
  随 `render_frontmatter` 一起被引用，首轮漏迁导致 `F821`。
- 收紧 `except Exception: pass` 时，注释**不能单独构成 except 体**：
  `except X:\n    # 说明` 会报 `Expected an indented block`。
  正确写法是 `pass  # 说明` 或「注释 + pass」两行。
- 静态「找 `.字段名`」的启发式**无法**判断 ctx 字段是否被消费：这些值大量经
  下标或 prop 传递（`cbs["on_key_down"]` → `Component(on_key_down=...)`），
  因此不要据此删除字段——误删不会被任何测试拦住。
- 把重复的 if 链抽成「动作表」时，**特例分支的优先级必须显式保留**：
  Ctrl+F 在两个路径中都需要「浮层优先于侧边栏搜索面板」，若把它写成表内普通项，
  会被同表内前序项抢先命中。改造后应由测试断言「特例分支位置早于表循环」。

**重构硬约束（不可回退项）**：`transparent cursor TextField 不设 value`、
`nav_seq` 仅撤销/重做递增（保 IME 组合态）、`reparse_line_atomic` 热路径、
`ft.memo` 行级缓存 prop 稳定性、`active_index == 焦点侧组激活索引` 不变式、
同文件多副本共享 `document` 身份。详见第 4 节红线规则。
