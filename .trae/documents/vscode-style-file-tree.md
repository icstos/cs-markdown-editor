# VSCode 风格资源管理器文件树

## Context

当前侧边栏文件树（`views/sidebar.py`）存在三个差距，与 VSCode 资源管理器体验不符：
1. **只扫描 `.md/.markdown` 文件**（`_scan_markdown_files` L92 的 `_MD_EXTS` 过滤），看不到图片、代码等其他文件
2. **全量扁平化渲染**（`_flatten_tree` 一次性展开所有层级），文件夹不可点击展开/折叠
3. **无文件类型图标映射**，所有文件用同一个 `INSERT_DRIVE_FILE_OUTLINED` 图标

目标：参考 VSCode 资源管理器，打开文件夹时侧边栏显示完整文件夹内容的可展开/折叠文件树，符合桌面端交互直觉。

## 方案概要

**保留不变**：嵌套元组数据结构 `("dir", name, children)` / `("file", name, abs_path)`、异步扫描 + `scan_token` 防竞态、`fs_version` 驱动重扫、ListView 虚拟化、跨文件搜索仅搜 `.md` 的约束。

**核心改动**：扫描全类型 → 按 `expanded_dirs` 集合动态扁平化 → 文件夹点击 toggle → 文件类型图标映射 → 非 md 文件系统打开。

---

## 1. 扫描层：`_scan_markdown_files` → `_scan_files`

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) L59-96

- 重命名 `_scan_markdown_files` → `_scan_files`（2 处调用点同步：L1463 跨文件搜索、L1756 文件树）
- **移除 L92 扩展名过滤**：所有 `entry.is_file()` 一律收录
- `_MAX_DEPTH` 3 → 8（支持更深层级，防无限递归）
- 新增 `_MAX_FILES = 5000` 上限保护（闭包计数器，超限停止追加）
- **保留空目录**（移除 L90 `if children:` 过滤，VSCode 显示空目录）
- 跳过隐藏目录和 `.git`/`node_modules`/`__pycache__` 不变

## 2. `_collect_md_paths` 加 md 过滤

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) L362-378

扫描改为全类型后，跨文件搜索的 `_collect_md_paths` 必须加 `.md` 扩展名过滤：
```python
if node[0] == "file" and node[1].lower().endswith(_MD_EXTS):
    paths.append(node[2])
```

## 3. 展开/折叠状态管理

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) `Sidebar` 组件内 L1742 附近

- `expanded_dirs, set_expanded_dirs = ft.use_state(frozenset())`（frozenset 不可变，`==` 比较内容触发更新）
- 默认：根级直接子项可见、子目录折叠（空集合即实现，因为树从根的子项开始）
- `_toggle_dir(dir_path)`：toggle 集合中的路径
- **root_dir 切换时重置**：`ft.use_effect(lambda: set_expanded_dirs(frozenset()), [root_dir])`
- **reveal 当前文件**：`ft.use_effect` 监听 `file_path` 变化，自动展开祖先目录链（VSCode reveal 行为），用 `os.path.relpath` 计算祖先路径，try/except 包裹跨盘符异常
- **不持久化**到 settings（内存状态，简化）

## 4. 动态扁平化：`_flatten_tree` 签名扩展

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) L116-128

新增参数 `expanded: frozenset[str] | None = None`（None=全展开，兼容旧语义）、`force_expand: bool = False`（过滤模式强制全展开）：
```python
should_recurse = force_expand or expanded is None or dir_path in expanded
if should_recurse:
    out.extend(_flatten_tree(node[2], depth + 1, dir_path, expanded, force_expand))
```

Sidebar 内 `flat` memo（L1771-1774）依赖加 `expanded_dirs` 和 `_has_filter`，有过滤词时 `force_expand=True`。

## 5. 文件类型图标映射

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) L38 后新增

`_FILE_ICON_MAP` 字典 + `_file_icon(name, c) -> tuple[str, str]` 函数：
- `.md/.markdown` → `DESCRIPTION` + `c.link`（主题色突出可编辑文件）
- `.png/.jpg/.gif/.svg/.webp/.bmp` → `IMAGE`
- `.py/.json/.yaml/.toml` → `CODE`
- `.js/.ts` → `JAVASCRIPT`，`.html` → `HTML`，`.css` → `CSS`
- `.pdf` → `PICTURE_AS_PDF`，`.zip/.tar/.gz` → `FOLDER_ZIP`
- `.mp3/.wav` → `MUSIC_NOTE`，`.mp4/.mov` → `MOVIE`
- 兜底 → `INSERT_DRIVE_FILE_OUTLINED`
- 非图标颜色统一 `c.muted`（避免色彩过载）

## 6. 渲染层：`_render_files_panel` 改造

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) L712-806

### 6.1 新增参数
```python
expanded_dirs: frozenset[str],
on_toggle_dir: Callable[[str], None],
on_open_external: Callable[[str], None] | None = None,
```

### 6.2 文件夹行（L769-798）
- chevron 图标：展开 `EXPAND_MORE`（▾），折叠 `CHEVRON_RIGHT`（▸）
- 文件夹图标：展开 `FOLDER_OPEN_OUTLINED`，折叠 `FOLDER_OUTLINED`
- `on_click=lambda e, p=abspath: on_toggle_dir(p)`（整行点击 toggle）

### 6.3 文件行（L733-768）
- 用 `_file_icon(name, c)` 替换固定图标
- 文件行加 `ft.Container(width=14)` 占位与文件夹 chevron 对齐
- 点击分流：`.md` → `on_open_file(p)`；非 `.md` → `on_open_external(p)`
- active 高亮仅对 `.md` 文件生效（非 md 不在编辑器打开）

## 7. 非 md 文件系统打开

### 7.1 新增 `open_external`

**文件**：[services/file_ops.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/services/file_ops.py)（末尾，紧邻 `reveal_in_explorer`）

```python
def open_external(path: str) -> None:
    """用系统默认程序打开文件（资源管理器双击直觉）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    system = platform.system()
    if system == "Windows":
        os.startfile(path)
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
```

### 7.2 装配与透传
- [app/__init__.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/__init__.py)：`ctx.open_external` 封装（try/except + `show_snack` 错误提示）
- [app/_render.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/_render.py) L111-134：Sidebar 调用增加 `on_open_external=ctx.open_external`
- [views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) `Sidebar` 签名（L1300-1324）新增 `on_open_external` prop

## 8. 右键菜单分流

### 8.1 文件夹菜单加「打开」

**文件**：[views/sidebar.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/views/sidebar.py) `_wrap_context_menu` L454

当前 `if not is_dir:` 才有「打开」。改为文件夹分支也加「打开」（语义：在系统资源管理器中打开该文件夹）。

### 8.2 `action == "open"` 分流

**文件**：[app/_file_dialogs.py](file:///c:/Users/aigcs/CSTOS/projects/Tools/cs-markdown-editor/app/_file_dialogs.py) L276-278

```python
if action == "open":
    if is_dir:
        file_ops.reveal_in_explorer(path)
    elif path.lower().endswith((".md", ".markdown")):
        ctx.open_file_by_path(path)
    else:
        ctx.open_external(path)  # 非 md 用系统默认程序
```

---

## 9. 测试

### 新增 `tests/test_sidebar_file_tree.py`

纯函数测试，不依赖 UI：
- `_scan_files`：全类型收录 / 跳过隐藏与忽略目录 / 保留空目录 / 深度上限 / 文件数上限 / 目录在前排序 / 无效根返回空 / OSError 静默
- `_flatten_tree`：expanded 集合控制递归 / force_expand 全展开 / expanded=None 兼容旧语义 / depth 正确 / dir_path 拼接
- `_collect_md_paths`：混入非 md 节点只返回 md / 深度优先字母序 / 空树
- `_file_icon`：.md 返回 DESCRIPTION+link 色 / 已知扩展名映射 / 未知兜底 / 大小写不敏感
- `open_external`：mock `platform.system` 验证 Windows/macOS/Linux 分流 / 文件不存在抛错

### 修改 `tests/test_sidebar_search.py`

`_collect_md_paths` 测试补充混入非 md 节点的过滤用例。

---

## 10. 实施顺序

1. **数据层**：`_scan_files` 重命名 + 全类型 + 上限；`_collect_md_paths` 加 md 过滤
2. **扁平化层**：`_flatten_tree` 签名扩展；Sidebar `expanded_dirs` state + `_toggle_dir` + root_dir 重置 effect + reveal effect
3. **图标层**：`_FILE_ICON_MAP` + `_file_icon`；渲染层文件行用图标映射、文件夹行加 chevron + 动态 folder icon
4. **交互层**：文件夹 on_click toggle；文件点击 md/非 md 分流；`open_external` 服务 + 装配 + 透传；右键菜单分流
5. **测试**：新增 `test_sidebar_file_tree.py` + 修改 `test_sidebar_search.py`

## 11. 验证

```bash
# 语法检查
python -m py_compile views/sidebar.py services/file_ops.py app/__init__.py app/_render.py app/_file_dialogs.py

# 新增测试
python -m pytest tests/test_sidebar_file_tree.py -v

# 回归测试（排除预先存在的 test_key_bindings 失败）
python -m pytest tests/ --ignore=tests/test_key_bindings.py

# 端到端手工验证
# 1. 打开文件夹 → 侧边栏显示所有文件类型（含 .png/.py/.json 等）
# 2. 子目录默认折叠，点击文件夹行展开/折叠
# 3. 点击 .md 文件 → 编辑器打开
# 4. 点击 .png 文件 → 系统默认程序打开
# 5. 切换标签打开深层 .md 文件 → 祖先目录自动展开
# 6. 文件过滤框输入关键词 → 全展开显示匹配项
# 7. 右键文件夹 → 「打开」在系统资源管理器中打开
# 8. 不同扩展名显示不同图标
```
