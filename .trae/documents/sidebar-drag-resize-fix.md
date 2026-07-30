# 侧边栏宽度可拖拽调整修复

## Context

项目已有侧边栏拖拽调宽功能代码，但**完全不可用**：拖拽手柄完全透明（`bgcolor` 透明度 0.0）且被外层 Container 的 `clip_behavior=HARD_EDGE` 裁剪，用户看不到也抓不到；拖拽过程中外层容器宽度不跟随（来自 settings，仅拖拽结束时更新），内容被裁剪；拖拽结束后还有 200ms 不自然动画。用户期望侧边栏宽度可正常拖拽调整。

## 根因分析

| 问题 | 根因位置 |
|------|----------|
| 手柄不可见 | `views/sidebar.py:853` `bgcolor=with_opacity(0.0, c.link)` 透明度 0 |
| 手柄被裁剪不可交互 | 内部 Row 总宽 = `width + 4px`，超出外层 `sidebar_container.width`（来自 settings），被 `HARD_EDGE` 裁剪 |
| 拖拽时外层不跟随 | 外层 `app/_render.py:95-97` `width` 来自 `ctx.settings.get("sidebar_width")`，仅 `_on_pan_end` 经 `update_setting` 更新；内部 `width` state 实时变化但被外层 clip |
| 动画干扰 | 外层 `animate=Animation(200)`，拖拽结束 `update_setting` 触发外层 width 跳变，产生 200ms 动画 |

核心矛盾：**外层 Container 宽度控制（_render.py）与内部 width state（sidebar.py）分离**，两者不同步。

## 修复方案

把外层 `sidebar_container` 的 `width/animate/clip` 逻辑从 `app/_render.py` 移入 `Sidebar` 组件内部，让内部 `width` state 同时控制外层 Container 和内容，完美同步。新增 `dragging` state 控制拖拽时禁用动画。

改动仅涉及 **2 个文件**，无需 App 级 state / ctx / _context.py 变更。

## 改动细节

### 文件 1: `views/sidebar.py`

**Sidebar 签名**（L718-732）：新增 `sidebar_open: bool = True` prop

**width state 之后**（L758 后）新增：
- `dragging, set_dragging = ft.use_state(False)` — 控制拖拽时禁用动画
- settings 同步 effect（修复 reset_settings 后 width 不同步）：
  ```python
  _ext_w = settings.get("sidebar_width", 256)
  def _sync_from_settings():
      if width_ref.current != _ext_w:
          width_ref.current = _ext_w
          set_width(_ext_w)
  ft.use_effect(_sync_from_settings, [_ext_w])
  ```

**拖拽回调**（L835-844）：
- 新增 `_on_pan_start`：`set_dragging(True)`
- `_on_pan_end` 改为：`set_dragging(False)` + 持久化回调（顺序不变）

**drag_handle**（L846-858）：
- GestureDetector 加 `on_pan_start=_on_pan_start`
- 内层 Container `width=4` → `width=6`
- `bgcolor=with_opacity(0.0, c.link)` → `with_opacity(0.15, c.text)` — 可见淡色，清爽不突兀
- `use_memo` deps 保持 `[theme_mode]`（`set_dragging`/`set_width` 身份稳定，`width_ref`/`_cb_ref` 为 ref 总读最新值）

**返回值**（L924-938）改为外层 Container（替换原 `ft.Row`）：
```python
return ft.Container(
    width=width if sidebar_open else 0,
    animate=None if dragging else ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    clip_behavior=ft.ClipBehavior.HARD_EDGE,
    content=ft.Row(
        controls=[
            ft.Container(
                expand=True,  # 撑满减去 drag_handle 的空间，避免溢出裁剪
                bgcolor=c.surface,
                content=ft.Column(controls=[tabs, panel], spacing=0, expand=True),
            ),
            drag_handle,
        ],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,  # 手柄全高可抓
    ),
)
```

关键点：内容 Container 从 `width=width` 改为 `expand=True`，使 Row 总宽 = (width-6) + 6 = width = 外层 width，**零溢出**，drag_handle 完全在 clip 区域内可见可交互。

### 文件 2: `app/_render.py`

**L83-115**：
- 移除 `sidebar_width` 读取和 `sidebar_container = ft.Container(...)` 包裹
- 直接构造 `Sidebar`，新增 `sidebar_open=sidebar_open` prop
- `body` Row 中 `sidebar_container` 改为 `sidebar`
- 更新 L19-21、L85-88 注释（外层 Container 逻辑已移入 Sidebar）

### 不需要改动的文件
- `app/_settings_controller.py`：`change_sidebar_width` 逻辑不变
- `app/_context.py` / `app/__init__.py`：装配不变
- `config/settings.py`：默认值不变

## 边缘情况处理

| 情况 | 处理 |
|------|------|
| `sidebar_open=False` 时拖拽 | 外层 width=0 + clip 裁剪全部内容，手柄不可见不可交互 — 正确行为 |
| `reset_settings` 后 width 不同步 | 新增 `use_effect([_ext_w])` 同步 settings → width state |
| drag_handle 垂直高度不足 | Row 加 `vertical_alignment=STRETCH` 让手柄全高 |
| 拖拽中 animate 与 width 变化冲突 | `dragging=True` 时 `animate=None`，即时跟随；拖拽结束 width 已到位，恢复动画无跳变 |
| `open_folder` 原子设置 sidebar_open | Sidebar 接收 prop，App 重渲染时 0→width 动画过渡 — 正确 |

## 验证方法

1. **启动应用**：`python main.py`
2. **手柄可见性**：打开侧边栏，右侧边缘应可见淡色细线（6px），鼠标悬停时光标变为 `RESIZE_COLUMN`
3. **拖拽调宽**：拖动手柄，侧边栏宽度实时跟随（无裁剪、无延迟），范围 180-600px
4. **拖拽结束**：松手后宽度保持，无 200ms 动画跳变；重启应用后宽度保持（已持久化）
5. **展开/折叠动画**：点击状态栏侧边栏按钮，0↔width 平滑 200ms 动画
6. **reset_settings**：设置中恢复默认，侧边栏宽度应回到 256px（同步生效，无需重启）
7. **折叠时不可拖**：侧边栏关闭时手柄不可见不可拖拽
