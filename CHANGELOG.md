# 更新日志

本项目的显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

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
