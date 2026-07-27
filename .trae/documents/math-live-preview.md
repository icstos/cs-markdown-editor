# 公式编辑实时预览

## Context

当前块级公式（`$$...$$`）编辑时只显示源码 TextField，无法同时看到渲染效果。用户编辑复杂 LaTeX 时需要在编辑态和浏览态之间反复切换才能确认渲染结果，体验不佳。

需求：编辑时同时显示编辑内容（源码）与渲染内容（LaTeX 预览），实时更新。

## 方案

仅修改 `views/line_view.py` 的 `_render_math_block` 函数编辑分支，在源码编辑器下方添加 `ft.Markdown` 实时预览。零改动 editor.py——`on_change_math` 已更新 `line.segments[0].text` 触发 observable 重渲染，预览的 `ft.Markdown` 在重渲染时自动读到最新 formula。

### 布局（垂直堆叠）

```
┌──────────────────────────────────────┐
│ ƒ 公式编辑          点击外部完成      │  header
│ 源码                                  │  label
│ ┌──────────────────────────────────┐ │
│ │ \frac{a}{b} = c^2                │ │  TextField (monospace)
│ └──────────────────────────────────┘ │
│ ──────────────────────────────────── │  divider
│ 预览                                  │  label
│   a                                   │
│   ─ = c²                              │  ft.Markdown 实时渲染
│   b                                   │
└──────────────────────────────────────┘
```

### 改动文件

**`views/line_view.py` — `_render_math_block` 编辑分支（第 300-332 行）**

替换编辑分支为：
- header：`ft.Icons.FUNCTIONS` 图标 + "公式编辑" + "点击外部完成"
- source_label：`ft.Text("源码", size=10, color=c.muted)`
- text_field：保持现有 TextField 配置，`max_lines` 从 10 降至 6（控制高度）
- divider：`ft.Divider(color=with_opacity(0.2, c.math_fg))`
- preview_label：`ft.Text("预览", size=10, color=c.muted)`
- preview_md：`ft.Markdown(value=f"$$\n{formula}\n$$", selectable=False, latex_style=TextStyle(color=c.text))`
- Column：`tight=True` 紧凑布局
- 外层 Container：保持 `bgcolor=c.math_bg` + `border=only_border(left=ft.BorderSide(3, c.math_fg))`

浏览分支（第 333-346 行）不变。

### 不改动

- **editor.py**：`on_change_math` / `math_focus_li` / `on_math_focus` / `on_math_blur` 全部复用
- **行内公式**：当前编辑态显示源码 + math_fg/math_bg 着色已足够，强加预览会破坏段落流式布局
- **无效 LaTeX**：`ft.Markdown` 内部优雅降级（红字/原文），无需额外处理

### 视觉规格

| 元素 | 字号 | 颜色 | 字体 |
|------|------|------|------|
| header 标题 | 11 | c.muted | FONT_MONO W_600 |
| 源码/预览标签 | 10 | c.muted | FONT_MONO W_500 |
| 源码正文 | 14 | c.math_fg | FONT_MONO |
| 预览 LaTeX | 自适应 | c.text | ft.Markdown 默认 |
| 分隔线 | 1px | math_fg @ 0.2 透明度 | - |

间距：外层 padding `XL/LG`，Column spacing `SM`，源码区内 padding `SM/XS`，预览区内 padding `MD/SM`。

## 验证

1. 点击块级公式 → 编辑态显示源码 + 预览
2. 输入 `\frac{a}{b}` → 预览实时更新为分数
3. 输入无效 `\fra{a}` → 预览显示错误反馈，不崩溃
4. 点击外部 → 退出编辑态回到浏览
5. 亮/暗主题 → 配色正确
6. `python -m tests.test_soft_wrap` → 37 项测试仍通过
7. `python -c "import main"` → 导入正常
