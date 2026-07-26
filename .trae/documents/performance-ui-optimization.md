# 性能与体验优化方案（#3 字体缓存 / #4 输入防抖 / #5 界面美观）

## Context

用户要求 5 项性能与体验优化。#1（增量渲染，`@ft.memo`）与 #2（虚拟滚动，`ft.ListView`）已完成。本方案覆盖剩余三项：

- **#3 字体缓存**：当前 `_char_width_cache` 仅缓存单字符；多字符文本每次走 HarfBuzz 整形，激活行重渲染时非激活段重复测量
- **#4 输入防抖**：`reparse_line` 对 Line 的多字段赋值触发多次 observable 通知（已验证 `Observable.__setattr__` 用 `value_equal` 短路，但 `raw` + `segments` 必变 → 至少 2 次通知）
- **#5 界面美观**：间距/圆角魔法数字散布各视图；代码块/表格缺阴影层次；代码块行号区过宽（最小 56px）

## 已验证的关键事实

通过阅读 Flet 源码（`flet.Observable`）确认：

```python
class Observable:
    def __setattr__(self, name, value):
        # ... value = self._wrap_if_collection(name, value)
        old = object.__getattribute__(self, name) if hasattr(self, name) else None
        object.__setattr__(self, name, value)
        if not value_equal(old, value):  # 值相等则不通知
            self._notify(name)

    def notify(self):
        """手动触发通用通知（field=None）。"""
        self._notify(None)
```

- `object.__setattr__(obj, name, value)` 绕过通知（仅更新 `__dict__`）
- `obj.notify()` 公开方法触发一次通用通知
- `segments` 赋值时 `_wrap_if_collection` 包裹为 `ObservableList`，但项目代码从不 mutate `line.segments`（仅替换引用），故 `object.__setattr__` 设纯 list 安全

## #3 字体缓存：多字符文本测量 LRU

### 修改文件
- `utils/text_layout.py`

### 实现

1. 顶部新增 LRU 容器与常量：
```python
from collections import OrderedDict

_MEASURE_CACHE_MAXLEN = 4096          # 条目上限（~3MB 内存）
_MEASURE_CACHE_MAX_TEXT_LEN = 256     # 单条文本长度上限
_measure_cache: OrderedDict[tuple[str, str, int], object] = OrderedDict()
```

2. `measure_text_width` 多字符分支加 LRU 查询（key=`(text, font_family, size)`，命中 `move_to_end`，超限 `popitem(last=False)`）

3. `measure_text_offsets` 多字符分支：抽出 `_compute_offsets(text, font_family, size)` 内部函数（原多字符逻辑），缓存 `tuple[float, ...]`（不可变），返回 `list(cached)` 副本

4. 新增 `clear_text_layout_cache()` 公共函数，供字体/主题变更时清空

### 要点
- 单字符仍走 `_char_width_cache`（独立小缓存，命中率更高）
- 缓存 key 不含 `letter_spacing`（模块常量 0.25 固定）/ `weight`（不影响 advance）
- 超长段落（>256 字符）不缓存（代码块走 CodeEditor 独立路径，不影响）
- `measure_text_offsets` 返回 list 副本防外部篡改缓存

## #4 输入防抖：observable 通知批量化

### 修改文件
- `parser.py`：新增 `reparse_line_atomic`
- `views/editor.py`：高频路径改用 `reparse_line_atomic`

### 实现

1. `parser.py` 新增原子化重解析函数：
```python
def reparse_line_atomic(line: Line, new_raw: str) -> None:
    """原子化重解析：所有字段批量更新，仅触发一次 observable 通知。

    用 object.__setattr__ 绕过逐字段通知，最后 line.notify() 触发唯一一次
    通用通知。替代 reparse_line(line, new_raw) 在高频编辑路径中的调用。
    segments 设为纯 list（不经 _wrap_if_collection），项目代码不 mutate
    line.segments，安全。
    """
    object.__setattr__(line, "raw", new_raw)
    # ... 按 block_type 分支，用 object.__setattr__ 更新 segments/lang 等
    # 普通块：_build_line 后批量赋值 6 字段
    line.notify()  # 唯一一次通知
```

2. `views/editor.py` 高频路径迁移（保留 `reparse_line` 向后兼容低频路径）：

| 调用点 | 行号近似 | 改为 |
|--------|----------|------|
| `handle_char_input` | 403 | `reparse_line_atomic` |
| `backspace_core` | 462 | `reparse_line_atomic` |
| `delete_core` | 501 | `reparse_line_atomic` |
| `handle_paste` | 423, 430 | `reparse_line_atomic` |
| `indent_or_outdent` 系列 | 720+ | `reparse_line_atomic` |

低频路径（`toggle_task` / `change_lang` / 撤销重做）保留 `reparse_line`。

3. `mark_dirty()` 加 `if not document.dirty:` 守卫，避免 `True→True` 通知。

### 要点
- 不加 `version` 字段：`notify()` 已递增 `__version__`，无需额外字段
- `segments` 用纯 list：项目代码从不 `append`/`extend`/`remove` `line.segments`
- IME 友好性不变：`handle_char_input` 仍不调 `set_cursor_off`
- `@ft.memo` 版本号 prop（`line_raw_version`/`line_seg_count`）仍有效：`len(line.raw)`/`len(line.segments)` 在原子更新后变化，memo 检测到

## #5 界面美观：Design Token + 视觉层次

### 修改文件
- `styles.py`：新增 Spacing/Radius/Elevation/shadow token
- `views/toolbar.py`、`views/line_view.py`、`views/table_view.py`、`views/sidebar.py`、`views/status_bar.py`、`views/tab_bar.py`、`views/settings_dialog.py`、`views/rendered_line.py`：迁移魔法数字到 token

### 实现

1. `styles.py` 新增 Design Token（同文件，避免新文件分散）：
```python
class Spacing:
    XS, SM, MD, LG, XL, XXL, XXXL = 2, 4, 6, 8, 12, 16, 24

class Radius:
    SM, MD, LG, XL, XXL, XXXL = 4, 6, 8, 12, 16, 18

class Elevation:
    NONE, LOW, MEDIUM, DIALOG = 0, 1, 2, 8

def card_shadow(elevation: int, is_dark: bool = False) -> list[ft.BoxShadow]:
    # LOW: opacity 0.06(亮)/0.30(暗), blur 6/8, offset_y 1
    # DIALOG: opacity 0.18, blur 24, offset_y 8
```

2. 圆角规范化（嵌套规则：外层 = 内层 + padding）：

| 控件 | 当前 | 改为 |
|------|------|------|
| 行内代码/公式 | 6 | `Radius.MD=6`（不变） |
| 按钮/当前行高亮 | 8 | `Radius.LG=8`（不变） |
| DataTable | 12 | `Radius.XL=12`（不变） |
| 表格容器 | 14 | `Radius.XXL=16`（统一） |
| 表格当前行高亮 | 16 | `Radius.XXL=16`（统一） |
| 设置对话框 | 18 | `Radius.XXXL=18`（不变） |
| 设置面板卡片 | 10 | `Radius.XL=12`（统一） |

3. 间距 token 化（值不变，仅消除魔法数字）：
   - `padding=4` → `Spacing.SM`
   - `spacing=2` → `Spacing.XS`
   - `spacing=6` → `Spacing.MD`
   - `vertical=8` → `Spacing.LG`
   - `horizontal=12` → `Spacing.XL`

4. 视觉层次增强（克制使用阴影，符合「清爽、科技」偏好）：
   - 代码块容器：`card_shadow(Elevation.LOW, is_dark)`
   - 表格容器：`card_shadow(Elevation.LOW, is_dark)`
   - 工具栏/当前行高亮：不变（保持扁平）
   - 设置对话框：`card_shadow(Elevation.DIALOG)`

5. 代码块行号区缩窄（memory 偏好「行号区宽度尽量小」）：
   - 当前：`max(48, 24 + digits * 12) + 8`（最小 56px）
   - 改为：`max(28, 12 + digits * 8) + Spacing.SM`（最小 32px，1 位数字 32px / 2 位 40px / 3 位 48px）
   - `GutterStyle.margin` 从 8 改为 `Spacing.XS=2`

### 要点
- 阶段 1（token 化）保持视觉不变，仅消除魔法数字
- 阶段 2（阴影+行号区）引入视觉变化
- 暗色主题阴影 opacity 更高（0.30 vs 0.06）保证暗背景可见性
- 不用 `Container.elevation`（Material 风格过重），坚持 `BoxShadow` 手动配置

## 实施顺序

1. **#3 字体缓存**（`utils/text_layout.py`）— 纯缓存，无行为变化，风险最低
2. **#4 通知批量化**（`parser.py` + `views/editor.py`）— 需验证 observable 行为
3. **#5 Design Token**（`styles.py` + 各视图）— 分阶段：先 token 化（无视觉变化），再阴影+行号区

## 验证

### #3 验证
- 语法检查：`python -c "import ast; ast.parse(open('utils/text_layout.py').read())"`
- 导入测试：`python -c "import utils.text_layout"`
- 功能验证：启动应用，连续输入 100 字符，光标与文字间隙像素级对齐（无累积偏差）
- 缓存命中：临时在 `_hb_shape_width` 加计数器，确认非激活段命中缓存跳过 HarfBuzz

### #4 验证
- 语法检查 + 导入测试
- 通知次数：临时在 `Observable._notify` 加日志，单字符输入通知数从 2+ 降至 1
- IME 回归：拼音 `nihao→你好`、五笔 `wq→你`（无翻倍）、日文 `a→あ`
- 撤销/重做：Ctrl+Z / Ctrl+Y 正确
- `@ft.memo` 回归：多行文档编辑某行，仅激活行重渲染

### #5 验证
- 启动应用，亮/暗主题切换，所有控件视觉正确
- 代码块行号区：1/100/1000 行代码行号区宽度合理（32/40/48px）
- 代码块/表格阴影：亮色微妙、暗色可见
- 滚动 1000 行文档（含代码块/表格），阴影不导致掉帧

### 整体验证
```powershell
python -c "import ast; ast.parse(open('main.py').read()); print('OK')"
python -c "import views.editor, views.line_view, views.table_view, utils.text_layout, parser; print('OK')"
python main.py  # 启动应用，手动验证输入流畅度与视觉
```
