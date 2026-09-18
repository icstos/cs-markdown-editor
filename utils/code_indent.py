"""代码块文本缩进变换（纯函数）。

用途：代码块编辑态下 Tab / Shift+Tab 的缩进与反缩进。原生多行 TextField 不会插入
制表符——Flutter 把 Tab 当作焦点遍历键（NextFocusIntent），Flet 1.0 既没有
Focus / Shortcuts 控件，TextField 也没有 on_key_down，控件层无从吞掉该事件
（真机探针实测：回调返回 True 也拦不住遍历，KEYDOWN Tab 与 BLUR 同帧发生）。
故缩进只能由代码块组件自行改写文本；本模块只做"文本 + 选区 → 文本 + 选区"的
纯计算，不碰控件、不碰文档，便于独立单测。

依赖项：无。
对外接口：
- INDENT：缩进单位（4 个空格）
- apply_indent(value, base, extent, direction) -> (new_value, new_base, new_extent)
"""

# 缩进单位：4 个空格。Markdown 代码块的事实标准；不用制表符是为了避免不同渲染器
# 下制表位宽度不一致（代码块浏览态用等宽字体渲染，制表符宽度由字体自行决定）。
INDENT = "    "


def _line_start(value: str, pos: int) -> int:
    """pos 所在行的行首偏移。"""
    return value.rfind("\n", 0, pos) + 1


def _dedent_width(value: str, start: int) -> int:
    """行首可删除的缩进宽度：优先一个制表符，否则至多 4 个空格。"""
    if value.startswith("\t", start):
        return 1
    width = 0
    while width < len(INDENT) and value.startswith(" ", start + width):
        width += 1
    return width


def _selected_line_starts(value: str, lo: int, hi: int) -> list[int]:
    """选区覆盖到的所有行行首偏移（含 lo 所在行）。

    选区终点恰落在某行行首时该行不计入：那一行一个字符都没被选中，跟着缩进会让
    用户觉得"多缩进了一级"（桌面编辑器通行语义）。
    """
    starts = [_line_start(value, lo)]
    nxt = value.find("\n", lo)
    while 0 <= nxt and nxt + 1 < hi:
        starts.append(nxt + 1)
        nxt = value.find("\n", nxt + 1)
    return starts


def apply_indent(
    value: str, base: int, extent: int, direction: int
) -> tuple[str, int, int]:
    """Tab（direction=+1）/ Shift+Tab（direction=-1）变换文本与选区。

    折叠光标：
    - Tab 在光标处插入 4 个空格，光标停在缩进之后；
    - Shift+Tab 删除光标所在行行首的一段缩进，光标随之左移（落在被删区间内则贴到行首）。
    跨行选区：选区覆盖到的每一行整体增缩进，选区端点按插入/删除量平移，缩进因此落在
    选区之内（桌面编辑器通行语义）。
    无变换（行首本就无缩进可删、空文本）时原样返回，调用方据此跳过入历史。
    """
    n = len(value)
    base = max(0, min(int(base), n))
    extent = max(0, min(int(extent), n))

    if base == extent:
        if direction > 0:
            caret = base + len(INDENT)
            return value[:base] + INDENT + value[base:], caret, caret
        start = _line_start(value, base)
        width = _dedent_width(value, start)
        if width == 0:
            return value, base, extent
        caret = base - min(width, base - start)
        return value[:start] + value[start + width :], caret, caret

    lo, hi = (base, extent) if base < extent else (extent, base)
    starts = _selected_line_starts(value, lo, hi)

    if direction > 0:
        new_value = value
        for start in reversed(starts):
            new_value = new_value[:start] + INDENT + new_value[start:]
        base_shift = len(INDENT) * sum(1 for s in starts if s <= base)
        extent_shift = len(INDENT) * sum(1 for s in starts if s <= extent)
        return new_value, base + base_shift, extent + extent_shift

    removals = [(s, _dedent_width(value, s)) for s in starts]
    new_value = value
    for start, width in reversed(removals):
        if width:
            new_value = new_value[:start] + new_value[start + width :]

    def _move(pos: int) -> int:
        delta = 0
        for start, width in removals:
            if not width:
                continue
            if pos >= start + width:
                delta -= width
            elif pos > start:
                # 落在被删除的缩进内部：贴近行首（与原行首字符同一列）
                delta -= pos - start
        return pos + delta

    return new_value, _move(base), _move(extent)
