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
  - watchfiles ≥ 1.0.0
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
   python -m pytest tests/ -q
   # 单模块：python -m pytest tests/test_parser_roundtrip.py -q
   # 按主题：python -m pytest tests/ -q -k "sidebar or parser"
   ```
3. **核心功能主流程验证**（GUI 冒烟，`python main.py` 或入口命令 `cs-markdown-editor`）：
   - 打开/新建 `.md` → 中文 IME 连续输入（不丢字、光标不跳）→ 行内格式 Toggle（Ctrl+B 两次包裹/取消）→ Backspace 行首合并 / Delete 行尾合并
   - Ctrl+Z/Ctrl+Y 撤销重做 → Ctrl+S 保存落盘 → Ctrl+\ 拆分视口 → 侧边栏文件树右键/拖拽 → Alt+Z 软换行下长行光标上下移动
   - 亮/暗主题切换后代码块高亮联动、`settings.json` 写入合法
4. **代码规范/类型检查**（ruff 配置于 `pyproject.toml`：line-length=100、target=py312、select=E/F/W/I/UP/B/C4/SIM/RUF，忽略 E501/RUF001-003）：
   ```bash
   ruff check .
   ruff format --check .   # 如使用格式化
   ```
