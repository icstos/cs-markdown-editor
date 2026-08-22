# 更新日志

本项目的显著变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 2026-08-22 修复：软换行触发时光标丢失、继续编辑整行内容丢失

- **软换行触发/收拢时光标丢失**：输入使行视觉行数/光标视觉行变化（换行/收拢）时，cursor TextField 的 top 从 0 跳变到 vline_idx*text_h，Flutter 属性更新移除焦点；且会话值跨视觉行时单行 TextField 无法正确布局（光标脱离文字约一个 value 宽度）。handle_char_input / backspace_core / delete_core 编辑前后对比视觉行签名（_vline_signature，与渲染层共用 _line_visual_layout），变化时结束会话 + nav_seq 重建 + 重聚焦（与块级前缀结构变化同一模式），光标直接定位到当前位置；无活动会话时仅递增 focus_seq 重聚焦（views/editor/_cursor.py）

- **继续编辑整行丢失（IME 翻倍误判）**：_fix_ime_doubling 非 ASCII 分支把合法连续输入误判为 IME 翻倍折叠——逐字累积（"你你你你" ← "你你你"）与上屏后行内容形如 X+X（"你"*14 ← "你"*13+"i"）时吞掉已输入内容，且折叠值回推客户端触发 on_change 级联清空整行。新增与 ASCII 分支同型的"逐字累积"守卫（len(value)==len(last_value)+1 且 startswith），仅全 ASCII composing 转上屏或新会话空 last_value 时才折叠（views/_editor_helpers.py）

- 测试：tests/test_cursor_wrap_refocus.py（10 用例：换行触发重建重聚焦 / 文档不丢 / 连续输入不误折叠 / IME 上屏不误折叠 / Backspace、Delete 收拢会话结束与不结束）+ tests/test_editor_helpers.py（3 用例）

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
