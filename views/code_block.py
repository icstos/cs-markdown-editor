"""原生代码块（Typora 式双态）：高亮浏览态 + 原生编辑态。

背景：原实现直接内嵌 flet-code-editor 的 CodeEditor，但上游 `flutter_code_editor`
的 `CodeField.wrap` 至今是"已声明未实现"的空属性（对应 PR 仍是 Draft），Flet 侧
也没有暴露任何换行开关——"开启换行时代码块内也软换行"在产品上无法达成。故整体
改用 Flet 原生控件重建代码块。

两态（共用同一份行内容与同一套字体度量，宽度 / 折行点 / 行盒因此严格一致）：
- 浏览态：逐逻辑行 `ft.Text(spans=...)` + Pygments 语义色
  （services.code_highlight 分词 → styles.Colors.code_syntax 取色）。
  word_wrap=True 时文本按容器宽度自然折行，续行与首行同列；False 时单行不折、
  整块横向滚动。
- 编辑态：`ft.Stack` 把**同一个高亮正文层**垫在下面，上面叠一个文字透明的
  `ft.TextField(multiline=True)`——字符仍由底层高亮层呈现，编辑框只提供原生光标、
  选区、IME 与撤销。两层同宽（同一容器约束）、同字距（显式 `letter_spacing`）、
  同行高，故折行点与光标位置逐字对齐，且**编辑过程中语法高亮持续可见**。

叠加层的三个实测约束（真机探针，勿凭直觉改）：
- `KeyboardListener` 必须放在**最外层**包住整个正文，不能只包编辑框：它只把自己
  撑开，传给 `content` 的是**松约束**，多行 `TextField` 会据此缩到内在宽度
  （实测 300px vs 应有的 728px），折行因此提前、块高多出 2 个视觉行——这正是
  "编辑态行宽异常"的根因。
- `Stack` 用默认的 LOOSE：本组件位于滚动 `Column` 内、交叉轴约束无界，
  `StackFit.EXPAND` 会把高度约束成 infinity（整块高度 inf、块体渲不出来）。
- 编辑框必须显式带 `strut_style=_edit_strut(size)`：否则 Flutter 自造的强制 strut
  会把含中文/emoji（字体回退字形）的行压到 24px，而高亮层同一行是 25px，
  光标因此**逐行累积上飘**（详见 `_edit_strut` 的实测数据）。

第四个约束（真机实测 + flet 源码，症状很会骗人）：**编辑框的 `value` 属性必须以
"客户端此刻手里的文本"为准，绝不能取我方上次传进去的那个字符串**。
症状：在代码块里连打回车，光标异常跳到代码块末端、刚敲的回车一起丢失。真机探针实测
（连打 6 次回车、光标在正文中段）：前 3 个落在光标处，第 4 个落到代码块末尾，之后输入
的字符也跟着落在末尾。

机制（三层，缺一层就推不出正确写法）：
1. flet 的补丁是**差分**出来的：拿本次渲染的控件对象与上一次渲染的控件对象逐属性比，
   不同才发补丁（`Session.patch_control` → `ObjectPatch.from_diff`）。
2. 客户端上报的 `UPDATE_CONTROL_PROPS` 会被 `Session.apply_patch` → `patch_dataclass`
   **直接写回上一次渲染的那个控件对象**；`patch_dataclass` 的 docstring 明说它绕过
   frozen 检查（"avoids ... frozen-checks ... when applying patches that originate
   *from Dart*"）。于是"上一次渲染的编辑框"里躺着的是**客户端手里的文本**（可能比文档
   还新），而不是我们上次传给它的字符串。
3. 重渲染时若把文档文本当 `value` 发下去，就等于"服务端命令客户端把文本改成这样"。
   慢速输入时它恰好等于客户端手里的文本（差分不出补丁，看不出问题）；**连打回车**时
   客户端已经跑到前面、服务端手里还是旧文本 → 差分出一条把旧文本推回去的补丁 →
   Flutter 重设文本后光标落到末尾。

结论：只有**我方**改写文本时才下发 `value`（Tab 缩进 / 撤销 / 外部改写），判据是"文档
文本 != 我们最后确知客户端手里有的文本"（`client_text_ref`，客户端回报与我方下发两个
来源都写它）；而**不下发时该传什么**，必须取上一次渲染那个编辑框对象的 `value`
（`edit_field_ref.current`）——它才是客户端手里的那份。两条合起来见
`_build_edit_body` 的 `field_value`。

头部行高 = **最高子项**，故紧凑与否由子项的固有高度决定（不是内边距）：
- `ft.Dropdown` 恒为 48px（即使 `dense` / `text_size=12` / 内边距归零——内部
  `InputDecorator` 的固有高度），且用 `height=` 强压会裁切其文字（压到 24px 时
  文字溢出到下一行标签上）→ 只能整体换掉，改自绘的 `PopupMenuButton` 触发器。
- `ft.IconButton` 默认 40px（`visual_density=COMPACT` 也只降到 32）→ 改用固定尺寸的
  `Container(ink=True)`，与 `views/status_bar.py` 的紧凑按钮同一套做法。
真机 A/B（同一条渲染路径，只改 `_HEADER_H`）实测：头部 48 → 22 时**块高恰好减少
26px**，且随后的截图确认标签 / 图标 / 行数三者在 22px 内垂直居中、无裁切。

契约不变（换实现不换接口）：`on_change / on_focus / on_blur / on_selection_change`
四件套的语义与旧 CodeEditor 完全一致，因此 `views/editor/_fence.py` 的围栏闭包组
与 `views/key_bindings.py` 的"边界方向键跳出 / 空块 Backspace 删除 / Tab 放行"
无需任何改动。

Tab 缩进由本组件自行处理（组件内嵌 `ft.KeyboardListener`，焦点在编辑框内才触发）：
原生多行 TextField 不会插入制表符——Flutter 把 Tab 当焦点遍历键，Flet 1.0 没有
Focus / Shortcuts 控件、TextField 也没有 on_key_down，控件层无从吞掉该事件
（真机探针实测：回调返回 True 也拦不住遍历，`KEYDOWN Tab` 与 `BLUR` 同帧发生）。
故按下 Tab 时自行插入缩进，并把随后的失焦识别为遍历副作用、立即收回焦点。

编辑态由本组件内部的 `ft.use_state` 驱动（点击进入、失焦退出），不引入编辑器级
state，从而不扰动既有的 `code_focus_ref` 路由与 `ft.memo` 依赖。

依赖项：
- models（Line）
- services.code_highlight（分词，惰性 Pygments，未知语言回退纯文本）
- services.clipboard（复制代码到系统剪贴板）
- styles（FONT_MONO / Radius / Spacing / Elevation / card_shadow / code_syntax）
  —— FONT_MONO 是随包分发的 Noto Sans Mono CJK SC（含 CJK 字形、拉丁 0.5em /
  CJK 1.0em 严格等宽），故代码块里的中文注释不会触发字体回退、也不会破坏列对齐。
- utils.code_indent（Tab / Shift+Tab 的缩进变换，纯函数）
- utils.text_layout（`_FLET_DEFAULT_LETTER_SPACING`：与渲染层一致的字距，
  高亮层与编辑层必须同值，否则折行点与光标随字数线性漂移）
- views.pixel_layout（`_wrap_offsets_into_visual_lines` / `hit_test_line_x_raw`：
  与正文共用的换行与 X 命中算法，见下）
- views._block_frame（块级包裹：缩进 / diff / 当前行高亮 / 高度上报）

点击定位（`_caret_offset_in_code`）：
点击进入编辑时，光标必须落在**鼠标实际点击的位置**，而不是 Flutter 给新建
`TextField` 的默认位置（文末）——后者正是"点哪都跳到代码块最后一行"的根因。
映射分两级，逐级都只用确定信息：
1. **行**：每个逻辑行的 `Row` 各自套一层点击容器，因此回调的 `local_position`
   天然相对**本行**（行容器左缘 = 行号列左缘），行号由闭包直接给出，不需要按累计
   高度反推（累计高度依赖"我们算出的折行数 == Flutter 实际折行数"，一旦不符就会
   整块错位）。
2. **行内**：`y // 行高` 得到视觉行序号，再用与正文同源的
   `_wrap_offsets_into_visual_lines` 切出该视觉行，最后用 `hit_test_line_x_raw`
   （中点吸附）把 x 映射为行内字符偏移。

坐标从哪来（真机实测 + 类型契约，勿想当然）：
`ft.Container.on_click` 的声明是 `ControlEventHandler["Container"]`，收到的是
`ControlEvent`（只有 `name` / `data` / `control` / `page`），**没有 `local_position`**；
带坐标的是 `on_tap_down: EventHandler[TapEvent["Container"]]`（`TapEvent.local_position`
是 `Optional[Offset]`）。真机探针实测确认：点击落在行容器上时 `on_click` 确实触发，
但 `e.local_position is None` → 映射拿不到输入 → 只能退化为"进编辑态"，Flutter
于是把光标甩到文末（`SEL base=38`）——这就是本缺陷的形状。
故位置与动作**分成两个 handler**：
- `on_tap_down` → `_record_tap`：只把 `(行号, x, y)` 写进 `tap_pos_ref` 快照；
- `on_click` → `_click_row`：取快照算偏移、派发 `selection`、进入编辑态。
这样拆还能顺带修掉一个副作用——`on_tap_down` 在"按下后被判为拖动"时也会触发，
若在那儿直接进编辑态，用户在代码块上拖动滚动文档就会误入编辑；而 `on_click`
只在真正的点击（抬起且未被取消）时触发，语义正是我们要的。
一次点击必然先经过同一行的 `on_tap_down`，故快照必是本次手势的、且行号相符。

第 2 级依赖"本项目的折行算法与 Flutter 一致"——这正是 `views/pixel_layout` 换行
函数对自己的定位（渲染与光标共用、模拟 Skia/Flutter CJK 换行）。**不折行的行
（占绝大多数，且"关闭换行"模式下恒成立）第 2 级退化为恒等映射，完全精确**；
只有"开启换行 + 该行确实折行了"时，列位置才存在项目的折行算法与 Flutter
之间的理论偏差。第 1 级的行定位在任何情况下都是精确的。

三个"进得去、出得来、选得对"的交互（桌面编辑器直觉，全部有测试与真机探针守护）：

**出得来**（代码块内 → 外）：编辑框聚焦时按 ↑/←/↓/→ 到边界即跳出到相邻行，
由 `views/editor/_fence.py` 的 `handle_code_exit` 负责（本组件只上报光标的
(value, base, extent)，不参与判定）。左右键与本组件无关，由原生编辑框处理行内
移动；Tab 见下。

**进得去**（代码块外 → 内）：光标在代码块上一行按 ↓、下一行按 ↑ 时直接进入代码块
（↓ 落首行行首、↑ 落末行行尾），由 `views/editor/_navigation.py` 的 `_move_vline`
发起。发起方只交出 `(行号, 初始光标偏移)`，经编辑器 state 透传成本组件的
`enter_seq/enter_off`，由 `_consume_enter_request` 消费一次并切到编辑态——
`is_editing` 是组件内部状态，外部只"请求"，不直接改。

**Esc**：编辑态按 Esc 回到浏览态（Typora 同款）。在组件内嵌的 `KeyboardListener`
里处理，不需要新增全局动作、也不需要把 `is_editing` 暴露给外部。

**选得对**：行号必须是 `selectable=True`，否则跨行拖选代码会把行号一起选进剪贴板
（见 `_gutter_cell` 的真机实测表）。
"""

import contextlib
from collections.abc import Callable

import flet as ft

from models.document import Line
from services.clipboard import copy_code_to_clipboard
from services.code_highlight import highlight_lines
from styles import (
    FONT_MONO,
    Elevation,
    Radius,
    Spacing,
    _current_colors,
    card_shadow,
    only_border,
)
from utils.code_indent import apply_indent
from utils.text_layout import _FLET_DEFAULT_LETTER_SPACING, measure_text_offsets
from views import _block_frame
from views.pixel_layout import _wrap_offsets_into_visual_lines, hit_test_line_x_raw

# 代码块行高倍数：等宽字体下 1.5 倍行距，紧凑与可读的平衡点。
# 浏览态（TextStyle.height）与编辑态（TextField.text_style.height）共用同一常量，
# 保证"点击进入编辑"时文字基线不跳动。
_CODE_LINE_HEIGHT = 1.5

# 不换行（word_wrap=False）模式下编辑框的最小宽度：短代码也要有可用的输入区。
_EDIT_MIN_WIDTH = 420.0

# 头部工具栏行高（与状态栏紧凑图标按钮同值）。
# 头部行高 = 其**最高子项**，所以这里的每个交互元素都必须显式压到这个高度：
# Material 的固有尺寸会把行高重新顶大（实测 IconButton 40、Dropdown 48），
# 这才是"顶部行过高"的真实成因——不是内边距。
_HEADER_H = 22.0

# 代码块语言选择下拉框的常用语言清单
# （key = Markdown 围栏语言标识，需与 Pygments get_lexer_by_name 的名称对齐）
_COMMON_LANGS: list[tuple[str, str]] = [
    ("", "Plain text"),
    ("python", "Python"),
    ("javascript", "JavaScript"),
    ("typescript", "TypeScript"),
    ("java", "Java"),
    ("kotlin", "Kotlin"),
    ("swift", "Swift"),
    ("go", "Go"),
    ("rust", "Rust"),
    ("c", "C"),
    ("cpp", "C++"),
    ("csharp", "C#"),
    ("php", "PHP"),
    ("ruby", "Ruby"),
    ("html", "HTML"),
    ("css", "CSS"),
    ("json", "JSON"),
    ("yaml", "YAML"),
    ("xml", "XML"),
    ("sql", "SQL"),
    ("bash", "Bash / Shell"),
    ("powershell", "PowerShell"),
    ("markdown", "Markdown"),
    ("dockerfile", "Dockerfile"),
    ("ini", "INI"),
    ("diff", "Diff"),
]


def _lang_display(lang: str) -> str:
    """语言标识 → 展示名（如 "python" → "Python"）。

    未知标识原样回显：围栏里可以写 Pygments 认识的别名（如 `py3`），
    没有展示名不等于无语言，不能显示成空。
    """
    if not lang:
        return dict(_COMMON_LANGS)[""]
    for key, text in _COMMON_LANGS:
        if key == lang:
            return text
    return lang


def _lang_entries(current_lang: str) -> list[tuple[str, str]]:
    """语言菜单项 `(标识, 展示名)` 列表。

    当前语言不在常用清单内时追加为末项 —— 否则"当前值"不存在于选项中，
    用户点开菜单会看不到自己现在的选择，也无法确认当前状态。
    """
    entries = list(_COMMON_LANGS)
    known = {k for k, _ in _COMMON_LANGS}
    if current_lang and current_lang not in known:
        entries.append((current_lang, current_lang))
    return entries


def _span_style(color: str, size: int) -> ft.TextStyle:
    """代码片段文字样式：等宽 + 固定行高倍数 + 语义色 + 显式字距。

    字体族 / 字号 / 行高 / 字距在 span 上重复声明（而非仅依赖 Text 的 style 继承）：
    Flet 序列化只发送非空字段，继承链一旦在某个版本上表现不一致，就会退化成
    默认字体并让行高与行号错位，显式声明可完全规避该类风险。

    `letter_spacing` 尤其不能省：`ft.TextStyle.letter_spacing` 默认是 None（走继承），
    而编辑态叠加的 `TextField` 若同样留空，两侧可能取到不同的字距，每个字形差 0.25px
    → 折行点与光标位置随字符数线性漂移（长行尤其明显）。两层显式同值即彻底对齐。
    """
    return ft.TextStyle(
        color=color,
        font_family=FONT_MONO,
        size=size,
        height=_CODE_LINE_HEIGHT,
        letter_spacing=_FLET_DEFAULT_LETTER_SPACING,
    )


def _syn_color(c, kind: str) -> str:
    """语义类别 → 主题色（未收录类别回退代码块正文色）。"""
    return c.code_syntax.get(kind, c.code_block_fg)


def _edit_strut(size: int) -> ft.StrutStyle:
    """编辑框的行盒 strut：**必须显式给出，且 `force_strut_height=False`**。

    为什么这不是可选项：`TextField` 在未指定 strut 时，Flutter 会自造一个
    `force_strut_height=True` 的 strut，把每行高度钉死在 `size × height`（16×1.5=24px）；
    而高亮浏览层的 `ft.Text` 没有 strut，行盒取"该行所有 run 的自然行高最大值"。
    两者只在纯 ASCII 下相等——**一旦行内出现需要字体回退的字形（中文注释、emoji），
    回退字体的度量更大，`ft.Text` 的行盒就变成 25px，编辑框仍是 24px**：
    真机实测（同宽同样式）中文 1 行 25 / 2 行 50、`x = 1  # 注释` 混合行 25、emoji 25，
    而编辑框对应恒为 24/48。

    后果是**逐行累积的纵向错位**：每经过一个含中文/emoji 的行，可见文字相对光标
    下移 1px（折行的中文长注释一次下移 2px），越往下越明显——即用户报的
    "越往后面的行光标越往上偏"。这正是当初把编辑框换成"透明叠层"后才暴露的：
    两层一旦同行同列，行盒就必须逐行严格相等，而不是"看起来差不多"。

    给出 `force_strut_height=False` 的 strut 后，编辑框的行盒退化为与 `ft.Text` 相同的
    "自然最大值"（strut 只保证下限 24px，不再压制回退字形的行高）。真机实测 6 组样本
    与浏览层**逐一相等**：纯 ASCII 1 行 24 / 2 行 48、中文 1 行 25 / 2 行 50、
    `x = 1  # 注释` 混合 2 行 51、emoji 1 行 27。
    """
    return ft.StrutStyle(
        font_family=FONT_MONO,
        size=size,
        height=_CODE_LINE_HEIGHT,
        force_strut_height=False,
    )


def _code_line_h(size: int) -> int:
    """单个视觉行的像素高度（浏览态行盒 / 编辑态 strut 下限共用同一值）。"""
    return max(1, round(size * _CODE_LINE_HEIGHT))


def _caret_offset_in_code(
    code: str,
    row_idx: int,
    x: float,
    y: float,
    size: int,
    text_width: float,
) -> int:
    """把「代码文本区内的点击坐标」映射为代码全文的字符偏移（纯函数，便于单测）。

    Args:
        code: 代码块全文（`line.segments[0].text`，与编辑框 value 同一坐标系）。
        row_idx: 被点击的逻辑行序号（由行级点击容器闭包给出，精确）。
        x: 点击点到**代码文本左缘**的水平距离（负值=落在行号列上）。
        y: 点击点到**本逻辑行顶部**的垂直距离。
        size: 代码字号。
        text_width: 代码文本的可用宽度（换行宽度）；`inf` 表示不折行。

    算法（见模块 docstring「点击定位」）：
    - 行内视觉行序号 v = y // 行高，越界钳制到 [0, N-1]；
    - 用 `_wrap_offsets_into_visual_lines` 把该逻辑行按 text_width 切成 N 个视觉行
      —— 与正文渲染/光标测量同一函数，保证折行点同源；
    - 视觉行内用 `hit_test_line_x_raw`（中点吸附）把 x 映射为字符偏移：
      点在一字的前半 → 落在该字之前，后半 → 落在该字之后，符合文本编辑直觉；
    - x 超出该视觉行右端 → 落在**该视觉行**末尾（不是全文末尾）；
    - 空行恒有 1 个视觉行（与浏览态"空格占位撑行盒"一致），偏移恒为 0。

    返回值为 [0, len(code)] 内的绝对偏移，可直接用于 `ft.TextSelection`。
    """
    lines = code.split("\n")
    if not lines:
        return 0
    row_idx = max(0, min(int(row_idx), len(lines) - 1))
    row_text = lines[row_idx]
    # 该逻辑行在全文中的起点：前面每行都要额外算上一个换行符
    base_off = sum(len(t) + 1 for t in lines[:row_idx])

    if not row_text:
        # 空行：浏览态用空格 span 撑行盒，光标只能落在偏移 0
        return min(base_off, len(code))

    offsets = measure_text_offsets(row_text, FONT_MONO, size)
    vlines = _wrap_offsets_into_visual_lines(offsets, row_text, text_width)
    vline_idx = max(0, min(int(y // _code_line_h(size)), len(vlines) - 1))
    vline = vlines[vline_idx]
    # 该视觉行内的局部 X 数组（rebased 到 0，长度 = 该视觉行字符数 + 1）
    local_x = [
        offsets[j] - offsets[vline.start_raw]
        for j in range(vline.start_raw, vline.end_raw + 1)
    ]
    local_off = hit_test_line_x_raw(local_x, x)
    return max(0, min(base_off + vline.start_raw + local_off, len(code)))


def _measure_mono_width(text: str, size: int) -> float:
    """等宽字体下单行文本的像素宽度（不换行模式用于撑开编辑框）。

    优先用项目自带的 HarfBuzz 精确测量；测量不可用时退化为"字符数 × 0.5 字号"
    的等宽估算。0.5 是 FONT_MONO（Noto Sans Mono CJK SC）的实测拉丁字宽比
    （upem=1000 下拉丁恒 0.5em；CJK 恒 1.0em，估算因此偏窄，但只影响"不换行"
    模式下的横向撑开量，偏窄由后续的显式 4 空格余量吸收）。
    """
    if not text:
        return 0.0
    try:
        from utils.text_layout import measure_text_width

        return float(measure_text_width(text, FONT_MONO, size))
    except Exception:
        return len(text) * size * 0.5


def render_code_block(
    line: Line,
    line_idx: int,
    base: int,
    content_width: float | None,
    clipboard_ref: ft.Ref | None,
    on_change_code: Callable[[int, str], None] | None,
    on_code_focus: Callable[[int], None] | None,
    on_code_blur: Callable[[int], None] | None,
    on_change_lang: Callable[[int, str], None] | None,
    on_code_selection: Callable[..., None] | None,
    code_field_ref: ft.Ref | None,
    is_current_line: bool,
    is_flash: bool = False,
    on_line_size_change: Callable[[int, float], None] | None = None,
    diff_mark: str | None = None,
    word_wrap: bool = True,
    enter_seq: int = 0,
    enter_off: int | None = None,
    on_enter_consumed: Callable[[int], None] | None = None,
) -> ft.Control:
    """渲染代码块（CODE 围栏岛屿）：浏览态语法高亮 + 点击进入原生编辑。

    软换行：`word_wrap=True` 时浏览态按容器宽度折行（续行与首行同列），
    `False` 时单行不折、整块横向滚动。编辑器把 word_wrap=False 映射为
    `content_width = inf`（见 views/editor/__init__.py），此处以该不变量判定，
    避免再多传一个 prop。

    高度自适应：内容变化 → 行数变化 → 行高变化，`wrap_block` 的 on_size_change
    上报纸质高度供滚动定位（与旧实现一致）。

    Args:
        base: 段落左侧基准内边距（块级容器的必填位置参数，勿漏传）。
        code_field_ref: 编辑器共享的编辑框 ref（保留契约；聚焦改用组件内 ref，
            因为该 ref 被文档内每个代码块依次赋值，多块共存时指向最后渲染者）。
        enter_seq: 外部"进入编辑态"请求的序号（0 = 未请求）。方向键把光标从相邻行
            送进代码块时由导航层递增；组件只在发现序号非 0 时消费一次，见
            `_consume_enter_request`。
        enter_off: 随本次请求一并给出的初始光标偏移（↓ 进入取 0=首行行首、
            ↑ 进入取 len(code)=末行行尾），与"跳出"两侧对称。
        on_enter_consumed: 兑现请求后回报给请求方（编辑器），由它作废那条一次性待办。
            **不回报的后果**：请求永远挂在父层 state 上，本行组件一旦因滚出/滚回渲染
            窗口而重建，挂载期 effect 会拿同一个序号再消费一次 —— 代码块自己跳进编辑
            态。组件内部记不住这条信息（重建时标记一起重生），所以必须回报父层。
    """
    c = _current_colors()
    code = line.segments[0].text if line.segments else ""
    lang = line.lang or ""
    page = ft.context.page
    is_dark = page is not None and page.theme_mode == ft.ThemeMode.DARK

    # ---- 组件内状态：浏览 ⇄ 编辑 / 复制反馈 / 折叠 ----
    is_editing, set_editing = ft.use_state(False)
    copied, set_copied = ft.use_state(False)
    is_collapsed, set_collapsed = ft.use_state(False)
    edit_field_ref = ft.use_ref(None)

    # ---- Tab 缩进所需的本地状态 ----
    # caret_ref：(文本, 选区起点, 选区终点)——组件内留一份最近的光标位，Tab 缩进据此
    #   定位；不读 ctx.code_caret_ref，以免把围栏层的状态机卷进组件。
    # shift_ref / ctrl_ref：Shift、Ctrl 是否按下。KeyboardListener 的 KeyDownEvent
    #   只带 key、不带修饰键状态（与页面级 KeyboardEvent 不同），Shift+Tab 反缩进与
    #   Ctrl+Tab（全局标签切换，不能当缩进）都只能靠跟踪修饰键自身按键判定。
    # tab_pending_ref：本次失焦是否由 Tab 的焦点遍历副作用引起（见 _exit_edit）。
    # pending_caret_ref：渲染时待写入编辑框的光标 (起点, 终点)。
    # tap_pos_ref：行级点击位置快照 (行号, 代码文本内 x, 行内 y)。由 on_tap_down 写、
    #   由 on_click 读——坐标只在 TapEvent 上，而 on_click 收的是不带坐标的 ControlEvent
    #   （见模块 docstring「点击定位 · 坐标从哪来」）。
    # client_text_ref：客户端最近一次回报的编辑框文本。它是"这段文本是不是客户端自己
    #   敲的"的判据（与文档文本比对），见 `_build_edit_body` 里的下发规则。
    #   注意它**不是**"待下发的文本"：不下发时要传的是 `edit_field_ref.current.value`
    #   （客户端手里的那份），见该处注释——两者差一层 flet 的差分语义，混用即回灌。
    caret_ref = ft.use_ref(None)
    shift_ref = ft.use_ref(False)
    ctrl_ref = ft.use_ref(False)
    tab_pending_ref = ft.use_ref(False)
    pending_caret_ref = ft.use_ref(None)
    tap_pos_ref = ft.use_ref(None)
    client_text_ref = ft.use_ref(None)
    tab_epoch, set_tab_epoch = ft.use_state(0)
    # 编辑框是否已真正取得焦点（进入编辑态时复位）。
    # 待写入的光标只能在**聚焦之后**下发，原因见 `_build_edit_body`：
    # 挂载当次带 selection，Flutter 会在随后聚焦时把光标重置到文末。
    edit_focused, set_edit_focused = ft.use_state(False)
    # 编辑态光标所在逻辑行（行号高亮用）。
    # 必须是 state 而不是渲染期现算：`on_selection_change` 只写 ref、不触发重渲染，
    # 用 ref 现算的话，在代码块内按 ↑/↓ 移动光标时行号高亮会停在旧行——比不做更糟。
    # 成本受控：只在**折叠光标跨了逻辑行**时才 set_state（见 `_remember_caret`），
    # 所以正常打字（行号不变）与拖选（base != extent）都不产生额外渲染。
    caret_line, set_caret_line = ft.use_state(-1)

    def _consume_enter_request() -> None:
        """方向键把光标从相邻行送进本代码块 → 切到编辑态并把光标放到请求位置。

        触发时机：`enter_seq` 非 0。挂载当次 effect 也会执行，但父层没发请求时
        `enter_seq` 恒为 0，因此不会把用户刚点开文档时的代码块直接拽进编辑态。

        **不要在这里用"已消费序号"去重**：effect 只在 `enter_seq` 变化时重跑，
        序号没变就不会再进来，去重判断永远命中不了（曾写过一版，变异验证证明是
        死代码）；而真正会重复消费的路径是**组件重建后重新挂载**，那种情况下组件
        自己的任何标记都已重生，同样拦不住。所以"只消费一次"由父层作废请求来保证
        （`on_enter_consumed` → 编辑器清空 state），见 `render_code_block` 的 Args。

        放在 `use_effect`（提交渲染后）里而不是渲染期：渲染期调 set_state 会破坏
        flet 的单向数据流（控件在渲染后才解冻），这也正是 `_enter_edit` 只能在
        事件回调里调用的原因。
        """
        if not enter_seq:
            return
        # 先回报再动手：请求在"即将被兑现"的这一刻就作废，后续渲染（包括本行可能因
        # 滚动被卸载重建）都不再看到它。
        if on_enter_consumed is not None:
            on_enter_consumed(line_idx)
        if enter_off is not None:
            off = max(0, min(int(enter_off), len(code)))
            pending_caret_ref.current = (off, off)
        # 复用点击进入的同一条路径（折叠态先展开、复位过期光标与修饰键状态）；
        # 光标本身照旧等"聚焦之后"再下发，理由见 _build_edit_body。
        _enter_edit()

    ft.use_effect(_consume_enter_request, [enter_seq])

    # 软换行开关：word_wrap=False 时 content_width 为 inf（编辑器侧不变量）
    wrap = bool(word_wrap) and content_width != float("inf")

    code_size = base
    text_h = round(code_size * _CODE_LINE_HEIGHT)
    line_count = max(1, code.count("\n") + 1)
    digits = len(str(line_count))
    # 行号列宽 = 位数 × 单字宽 + 与代码的间距。单字宽取 0.5 字号：FONT_MONO
    # （Noto Sans Mono CJK SC）的拉丁字宽实测恒为 0.5em，行号是纯数字故适用。
    gutter_w = max(26, round(digits * code_size * 0.5) + Spacing.LG)
    gutter_bg = ft.Colors.with_opacity(0.18 if is_dark else 0.04, c.text)
    border_color = ft.Colors.with_opacity(0.08 if is_dark else 0.06, c.text)

    # 代码文本的可用宽度（点击定位算折行点用，与浏览态的实际排版宽度同源）。
    # 减去的两段 Spacing.MD 是：行号列与代码之间的 Row spacing、代码列右缘的行内边距。
    # **块容器不留左右内边距**了——行号色带与头部细分隔线要贴到块边框上（见组装处
    # Container 的说明），原先左侧那段 Spacing.MD 已还给代码列，所以这里是 2 段而不是
    # 3 段；右侧那段改由行内边距承担（浏览态在行容器上、编辑态在编辑框的 content_padding）。
    # 不折行模式给 inf —— 此时每逻辑行恒为 1 个视觉行，映射退化为恒等。
    text_area_w: float = (
        max(1.0, float(content_width) - 2 * Spacing.MD - gutter_w)
        if wrap and content_width is not None
        else float("inf")
    )

    # ---- 头部交互元素 ----
    # 两者都必须显式压到 _HEADER_H，否则头部会被它们的固有尺寸顶高：
    # - 不用 `ft.Dropdown`：它即使 `dense=True` / `text_size=12` / 内边距归零，
    #   高度仍恒为 48px（内部 InputDecorator 的固有高度），是头部行高的**唯一**
    #   决定项。用 `height=` 强行压缩会裁切其文字（真机探针实测：文字下坠错位，
    #   压到 24px 时直接溢出到下一行标签上），故此路不通。
    #   改用 `PopupMenuButton` + 自绘紧凑触发器，并顺手拿到菜单定位与选中态。
    # - 不用 `ft.IconButton`：Material 的最小点击区把它撑到 40px
    #   （`visual_density=COMPACT` 也只降到 32）。改用固定尺寸的
    #   `Container(ink=True)` —— 与 `views/status_bar.py` 的紧凑按钮同一套做法，
    #   点击有水波反馈、悬停有 tooltip，全局观感一致。
    def _header_icon(
        icon: str, tooltip: str, color: str, on_click, bgcolor: str | None = None
    ) -> ft.Control:
        """固定 _HEADER_H 见方的头部图标按钮。

        bgcolor 用于"已复制"这类一次性的状态反馈：静态赋值，不引入 on_hover
        ——悬停反馈如果走 on_hover 就必须 set_state，而一次 set_state 会把整块
        代码（含每一行高亮 span）重建一遍，几十行以上的代码块在快速划过时会明显
        掉帧。头部按钮自带水波（ink=True），已经够表达"可按"。
        """
        return ft.Container(
            width=_HEADER_H,
            height=_HEADER_H,
            border_radius=Radius.SM,
            bgcolor=bgcolor,
            alignment=ft.Alignment.CENTER,
            ink=True,
            tooltip=tooltip,
            on_click=lambda e: on_click(),
            content=ft.Icon(icon, size=14, color=color),
        )

    # 语言选择器：当前语言是可点的紧凑标签（Typora 同款位置与交互直觉）。
    # 菜单项用 `on_click` 而非 `on_select`：后者只给被选项的控件 ID 字符串，
    # 还得反查映射；前者可闭包捕获标识，是项目既有写法（tool_area / tab_bar）。
    lang_button = ft.PopupMenuButton(
        height=_HEADER_H,
        padding=ft.Padding.symmetric(horizontal=Spacing.SM, vertical=0),
        tooltip="选择代码语言",
        # UNDER：菜单向下展开，不遮住代码本身（默认 OVER 会盖住正文前几行）
        menu_position=ft.PopupMenuPosition.UNDER,
        shape=ft.RoundedRectangleBorder(radius=Radius.MD),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=Radius.SM),
            padding=ft.Padding.all(0),
            # 淡底 pill：让"当前语言"在紧凑头部里成为一个可点的实体，而不是一段
            # 悬空的文字（VSCode 的 language mode 指示器同理）；hover 叠一层更亮的
            # overlay，按钮的悬停反馈由客户端完成，不产生任何服务端往返。
            bgcolor=ft.Colors.with_opacity(0.05, c.text),
            overlay_color=ft.Colors.with_opacity(0.10, c.text),
        ),
        content=ft.Row(
            controls=[
                ft.Text(
                    value=_lang_display(lang),
                    size=12,
                    color=c.text,
                    font_family=FONT_MONO,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=14, color=c.muted),
            ],
            spacing=1,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        items=[
            ft.PopupMenuItem(
                # 压缩项高（默认 48）：26 个语言的菜单因此不必滚动太多
                height=28,
                content=ft.Text(value=text, size=12, font_family=FONT_MONO),
                # 勾选当前语言：菜单一打开就能确认当前状态，无需记忆
                checked=key == lang,
                on_click=(
                    (lambda e, k=key: on_change_lang(line_idx, k))
                    if on_change_lang is not None
                    else None
                ),
            )
            for key, text in _lang_entries(lang)
        ],
    )

    # ---- 复制按钮 ----
    copy_btn = _header_icon(
        ft.Icons.CHECK if copied else ft.Icons.CONTENT_COPY,
        "已复制" if copied else "复制代码",
        ft.Colors.GREEN if copied else c.muted,
        lambda: (
            page.run_task(copy_code_to_clipboard, clipboard_ref, code, set_copied)
            if page is not None and not copied
            else None
        ),
        # "已复制"给一个静态底色，比只换图标颜色更容易被余光捕捉到
        bgcolor=(
            ft.Colors.with_opacity(0.14, ft.Colors.GREEN) if copied else None
        ),
    )

    # ---- 折叠按钮 ----
    # `_toggle_collapse` 定义于下方"交互"分节，这里只能延迟绑定（lambda 内解析名字），
    # 与本文件 `_build_edit_body` 引用 `_exit_edit` 的方式一致。
    collapse_btn = _header_icon(
        ft.Icons.EXPAND_MORE if is_collapsed else ft.Icons.EXPAND_LESS,
        "展开" if is_collapsed else "折叠",
        c.muted,
        lambda: _toggle_collapse(),
    )

    # ---- 头部工具栏 ----
    # 显式定高：把所有子项锁在 _HEADER_H 内，后续若有人往头部塞 Material 控件，
    # 行高不会悄悄膨胀（tests/test_code_block_native.py 有用例守住每个子项的高度）。
    # 左侧刻意不放装饰性图标（原 DATA_OBJECT）：语言标签紧邻其右，语义重复，
    # 紧凑头部里多一个字形只会让起点更乱。
    header = ft.Container(
        content=ft.Row(
            controls=[
                collapse_btn,
                lang_button,
                ft.Container(expand=True),
                ft.Text(
                    value=f"{line_count} 行",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
                copy_btn,
            ],
            spacing=Spacing.SM,
            height=_HEADER_H,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        # 工具栏的左右缩进由本层承担：块容器不再留水平内边距（见下方组装的说明），
        # 否则图标会贴到块边框上。行号色带与细分隔线要贴边，工具栏不需要——
        # 这正是"内边距按内容分工"的落点。
        padding=ft.Padding.only(left=Spacing.MD, right=Spacing.MD),
    )

    # ============================ 浏览态 ============================

    def _gutter_cell(n: int, active: bool = False) -> ft.Container:
        """行号单元格：固定列宽 + 顶右对齐 + 行号底色（同列拼接成"装订线"色带）。

        height=text_h 固定为**一个视觉行**的高度，不由交叉轴拉伸决定：
        行容器在滚动 Column 内高度约束无界，若用 `CrossAxisAlignment.STRETCH`
        子控件会被赋 `tightFor(height: infinity)`，整行高度直接变成 inf
        （真机探针实测：`on_size_change` 上报 height=inf，块体不渲染、后续行被挤走）。
        折行行的行号只落在首视觉行，色带在续行处自然留白——视觉上仍是行号，
        但绝不牺牲布局正确性。

        **行号必须 `selectable=True`：这是"跨行选中代码不连带行号"的唯一开关。**
        外层 `SelectionArea` 会把子树里所有 `RenderParagraph` 都纳入选区，
        `selectable=False` **不是**"退出选区"的意思（项目里正文/公式早就在用
        `selectable=False`，它们照样能被拖选）。真机探针实测（同一 SelectionArea 里
        并列 6 种渲染方式，真实拖选后读 `on_change` 的纯文本）：

        | 行号渲染方式 | 出现在选区文本里 |
        |---|---|
        | `ft.Text(...)`（默认/`selectable=False`） | **是**（缺陷本身） |
        | `selectable=True` | 否 |
        | `selectable=True, enable_interactive_selection=False` | 否 |
        | `ft.canvas.Text`（CustomPaint） | 否 |
        | 内嵌 `ft.SelectionArea` | 否 |

        `selectable=True` 之所以能"脱选"，是因为 Flet 会把它渲染成自带选区容器的
        `SelectableText`——与外层 `SelectionArea` 是两个互不包含的选区容器，外层
        拖选因此收集不到它。再加 `enable_interactive_selection=False` 关掉它自身的
        拖选/长按能力，行号于是**既摘得出去、也点不出选区**，而文字渲染（字体/字号/
        字色/对齐）与普通 `ft.Text` 完全一致（探针截图逐项比对过）。

        行号不参与选区的直接收益：跨行复制得到的是**纯代码**，而不是把 "1 2 3"
        混进剪贴板——`parser.selection` 的"选区文本 → 文档行"匹配也因此不会被打偏。

        active=True（编辑态里光标所在逻辑行）时行号提亮成强调色：VSCode/Typora
        都有的"当前行行号高亮"，编辑时不必数行数。纯静态渲染，无额外事件。
        """
        num_color = c.link if active else ft.Colors.with_opacity(0.55, c.muted)
        return ft.Container(
            content=ft.Text(
                value=str(n),
                size=max(9, round(code_size * 0.78)),
                color=num_color,
                font_family=FONT_MONO,
                weight=ft.FontWeight.W_500 if active else ft.FontWeight.NORMAL,
                text_align=ft.TextAlign.RIGHT,
                height=text_h,
                # 见上方 docstring：行号摘出外层 SelectionArea 的唯一办法
                selectable=True,
                enable_interactive_selection=False,
            ),
            width=gutter_w,
            height=text_h,
            bgcolor=ft.Colors.with_opacity(0.26, c.link) if active else gutter_bg,
            # 装订线：行号列与代码之间一条 1px 竖线，桌面编辑器惯用的分栏提示
            border=only_border(right=ft.BorderSide(1, border_color)),
            alignment=ft.Alignment.TOP_RIGHT,
            padding=ft.Padding.only(top=Spacing.XS, right=Spacing.MD),
        )

    def _record_tap(row_idx: int, e) -> None:
        """记下本行这次按压的位置（`on_tap_down` 的 `TapEvent` 才带坐标）。

        与 `_click_row` 分成两个 handler（原因见模块 docstring「坐标从哪来」）：
        `on_tap_down` 在按下瞬间就触发，"按下后被判为拖动"（在代码块上拖动滚动文档）
        同样会触发，所以这里**只记位置、不进编辑态**；进编辑态交给 `on_click`。

        坐标换算：`local_position` 相对**本行容器**左缘（= 行号列左缘），减去行号列宽
        与行号-代码间距才落到代码文本坐标系；y 相对本行顶部，无需换算。
        """
        pos = getattr(e, "local_position", None)
        if pos is None:
            tap_pos_ref.current = None
            return
        tap_pos_ref.current = (
            row_idx,
            float(getattr(pos, "x", 0.0) or 0.0) - gutter_w - Spacing.MD,
            float(getattr(pos, "y", 0.0) or 0.0),
        )

    def _click_row(row_idx: int, e) -> None:
        """点击某一行代码 → 光标落在**点击处**，再进入编辑态。

        位置取同一手势里 `on_tap_down` 记下的快照（`tap_pos_ref`）：`on_click` 的事件
        负载是**不带坐标**的 `ControlEvent`，不能指望它给出位置。一次点击必然先经过
        本行的 `on_tap_down`，所以快照一定是本次手势的、且行号相符。

        若事件源恰好直接给了 `local_position`（测试直接调 `on_click(e)` 造事件时用得上），
        优先采用它；两条路都拿不到就退化为"只进入编辑态"，不猜位置。
        """
        recorded = tap_pos_ref.current
        tap_pos_ref.current = None
        pos = getattr(e, "local_position", None)
        if pos is not None:
            x = float(getattr(pos, "x", 0.0) or 0.0) - gutter_w - Spacing.MD
            y = float(getattr(pos, "y", 0.0) or 0.0)
        elif recorded is not None and recorded[0] == row_idx:
            _, x, y = recorded
        else:
            _enter_edit()
            return
        off = _caret_offset_in_code(code, row_idx, x, y, code_size, text_area_w)
        pending_caret_ref.current = (off, off)
        _enter_edit()

    def _caret_logical_line() -> int:
        """编辑态光标所在的**逻辑行**序号（0-based），无光标快照时返回 -1。

        文本取**文档**（`line.segments[0].text`）而不是渲染期闭包里的 `code`：
        文档是唯一真相且总是最新，用它算行号就不会出现"刚敲了一个回车、行号高亮
        还差一行"的时序问题（`on_selection_change` 与 `on_change_code` 的到达顺序
        由客户端决定）。偏移越界一律钳制——装饰性判断不该因为一次异常事件让整块渲染失败。
        """
        caret = caret_ref.current
        if not caret:
            return -1
        text = line.segments[0].text if line.segments else ""
        base = max(0, min(int(caret[0]), len(text)))
        return text.count("\n", 0, base)

    def _read_column() -> ft.Column:
        """高亮正文列：逐逻辑行高亮渲染，折行由 Flutter 按容器宽度原生完成。

        浏览态与编辑态**共用本层**：编辑态把它作为底层高亮，上面叠一个文字透明的
        原生编辑框。两层吃同一份容器约束、同一套字体度量，因此宽度、折行点、行高
        严格一致——这是"编辑态与渲染状态保持一致"的实现基础。

        每行外面再套一层点击容器（浏览态用它把光标落到点击处）；编辑态该层被上层
        编辑框遮住，收不到点击，因此两种状态下不会互相干扰。

        行号列在编辑态额外高亮光标所在逻辑行（见 `_gutter_cell`）——本层是两态
        共用的，所以"当前行"信息只能在这里算，不能在两处各写一份。
        """
        active_line = caret_line if is_editing else -1
        rows: list[ft.Control] = []
        for i, spec in enumerate(highlight_lines(code, lang)):
            spans = [
                ft.TextSpan(text=t, style=_span_style(_syn_color(c, k), code_size))
                for t, k in spec
                if t
            ]
            if not spans:
                # 空行：给一个空格 span 撑出与普通行等高的行盒，否则行高塌陷会让
                # 行号与代码逐行错位（行号单元格高度恒为 text_h，行高由代码行决定）
                spans = [
                    ft.TextSpan(
                        text=" ", style=_span_style(c.code_block_fg, code_size)
                    )
                ]
            rows.append(
                ft.Container(
                    content=ft.Row(
                        controls=[
                            _gutter_cell(i + 1, active=(i == active_line)),
                            ft.Text(
                                spans=spans,
                                style=_span_style(c.code_block_fg, code_size),
                                text_align=ft.TextAlign.LEFT,
                                # 折行模式用 expand 占满行号列右侧、由框架按宽度折行；
                                # 不折行模式必须关掉 expand（父 Row 在横向滚动容器内宽度
                                # 无界，Expanded 在无界约束下会报错），并显式 no_wrap。
                                expand=wrap,
                                no_wrap=not wrap,
                            ),
                        ],
                        spacing=Spacing.MD,
                        # 必须 START 而非 STRETCH：本行位于滚动 Column 内、交叉轴约束无界，
                        # STRETCH 会把子控件高度约束成 infinity（整行高度 inf）。
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    # ink=False：行内不要水波（配合 SelectionArea 拖选，保持"文本"观感）
                    ink=False,
                    # 代码列右缘的行内边距：块容器已不留水平内边距（行号色带要贴左边框），
                    # 故右侧留白改由本层承担——它不动左缘，行号色带依旧从块内缘开始，
                    # 点击坐标原点是本容器左缘这一点也不变（padding 只是右缘）。
                    padding=ft.Padding.only(right=Spacing.MD),
                    # 位置与动作分两个 handler：坐标只在 TapEvent（on_tap_down）上，
                    # 而 on_click 收的是不带坐标的 ControlEvent（见 _record_tap/_click_row）。
                    on_tap_down=lambda e, i=i: _record_tap(i, e),
                    on_click=lambda e, i=i: _click_row(i, e),
                )
            )
        return ft.Column(controls=rows, spacing=0, tight=True)

    def _build_read_body() -> ft.Control:
        """浏览态正文：换行开关只决定"要不要再套一层横向滚动"。"""
        column = _read_column()
        if wrap:
            return column
        # 不换行：整块横向滚动（行号随内容滚动，行为与"关闭换行"的段落一致）
        return ft.Row(
            controls=[column],
            scroll=ft.ScrollMode.AUTO,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ============================ 编辑态 ============================

    def _build_edit_body() -> ft.Control:
        """编辑态：高亮层垫底 + 文字透明的原生编辑框叠加。

        字符由底层高亮层呈现，编辑框只提供光标 / 选区 / IME / 撤销——因此编辑时
        语法高亮持续可见，且两层同宽同折行（见模块 docstring 的两条实测约束）。
        """
        # Tab 缩进后的光标位置：只能经渲染参数给客户端——渲染后的控件是冻结的，
        # 在 effect 里改 `field.selection` 会抛 "Frozen controls cannot be updated."
        # （flet 1.0 用冻结标记保证"控件状态只从渲染流入"，事后改属性不会被 diff 采纳）。
        #
        # 但**挂载当次不能下发**：新建的编辑框在本帧末尾才拿到焦点，Flutter 在取得
        # 焦点时会把光标重置到文末，挂载时带的 selection 会被它覆盖掉。真机探针实测：
        # 点击第 3 行得到 `CLICKMAP ... off=14` 且 `TF selection=base_offset=14`（确实
        # 发给了客户端），但随后客户端回报 `SEL base=38 extent=38`——即被聚焦覆盖。
        # 因此拆成两次渲染：
        #   ① 挂载（edit_focused=False）：selection 留空；
        #   ② `_on_edit_focus` 把 edit_focused 置真触发重渲染，此时编辑框已聚焦，
        #      再以**属性更新**下发 selection，客户端就会采纳。
        # 这条"更新到已聚焦编辑框"的路径是既有能力——Tab 缩进（`_apply_indent_edit`
        # → `set_tab_epoch`）本来就走它，光标列位一直保持得住的。
        # 取用即消费：一旦下发就不再显式指定（`selection=None` 在客户端是空操作，
        # 不会把光标甩回文末），此后光标由客户端自身维护。
        pending_caret = None
        if edit_focused:
            pending_caret = pending_caret_ref.current
            pending_caret_ref.current = None

        # ---- 本次要下发的 `value`：客户端自己敲的文本**绝不回灌** ----
        # 机制与真机症状见模块 docstring「第四个约束」；这里只说写法为什么是这样。
        #
        # `prev_field` 是**上一次渲染构造的那个编辑框对象**。它同时是两个角色：
        #   · flet 算补丁时的 prev 快照（差分基准）；
        #   · 客户端上报 `UPDATE_CONTROL_PROPS` 的落点（`patch_dataclass` 直接写进它的
        #     `_values`，绕过 frozen 检查）。
        # 所以 `prev_field.value` 是**客户端此刻手里的文本**（可能比文档还新——连打回车
        # 时正是如此），而不是我们上次传给它的那个字符串。
        #
        # `client_text_ref` 的语义 = **我们最后确知客户端手里有的文本**，两个来源：
        # 客户端每次回报（`_on_field_change`）、我方每次下发（本处末尾）。它天然覆盖
        # "客户端敲了字但回报还没到"的窗口——那时它仍等于上一次下发的文本。
        # 注意**只有真正下发时才更新它**：冻结分支里客户端手里那份可能已经抢跑到文档
        # 前面（这正是需要冻结的场景），若把"已知文本"跟着改成那份，下一轮渲染就会觉得
        # "文档 != 已知文本"而把旧文档推回去——等于绕一圈又回到本缺陷。
        #
        # 于是判据只有一条：**文档文本是否还等于这份已知文本**。
        #   相等 ⇒ 这段是客户端自己敲的（可能已抢跑到更前面）⇒ 沿用 `prev_field.value`，
        #          不下发：差分结果为空，客户端手里的文本与光标都不被打扰。
        #          （若改成"沿用我们上次传进去的值"，就会差分出一条把旧文本推回去的补丁、
        #          把客户端的抢跑内容覆盖掉——这正是本缺陷的成因，勿改。）
        #   不等 ⇒ 文档被我方改写（Tab 缩进 / 撤销 / 外部改写）⇒ 下发 `code`，
        #          否则客户端手里的旧文本会与文档脱节。
        # 首次渲染 `prev_field` 为空：新挂载的编辑框必须先拿到当前文本，无条件下发。
        prev_field = edit_field_ref.current
        prev_value = prev_field.value if prev_field is not None else None
        if prev_value is not None and code == client_text_ref.current:
            field_value = prev_value
        else:
            field_value = code
            client_text_ref.current = code
        field = ft.TextField(
            # key 稳定（不含 is_editing）：on_change 触发的全量重渲染按 key 复用控件，
            # 不重挂载，从而不打断 IME 组合态与原生撤销栈。
            key=f"code-edit-{line_idx}",
            # ref 必须在构造时传入：flet 的 ref 是 InitVar，只在 __init__ 里绑定
            # （BaseControl.__post_init__ 的 `ref.current = self`），事后 `field.ref = x`
            # 不会绑定，ref 会一直是 None（聚焦 / 回写光标全部失效）。
            ref=edit_field_ref,
            value=field_value,
            selection=(
                ft.TextSelection(base_offset=pending_caret[0], extent_offset=pending_caret[1])
                if pending_caret is not None
                else None
            ),
            multiline=True,
            min_lines=line_count,
            max_lines=None,
            border=ft.NoInputBorder(),
            filled=False,
            bgcolor=ft.Colors.TRANSPARENT,
            dense=True,
            text_size=code_size,
            text_style=ft.TextStyle(
                font_family=FONT_MONO,
                size=code_size,
                height=_CODE_LINE_HEIGHT,
                # 字追高亮层：字体族 / 字号 / 行高 / 字距全部同值，否则折行点与光标
                # 会相对底层可见文字漂移（见 _span_style 的说明）。
                letter_spacing=_FLET_DEFAULT_LETTER_SPACING,
                # 文字透明：字符不可见，只留光标与选区——可见字符由底层高亮层负责
                color=ft.Colors.TRANSPARENT,
            ),
            # 行盒 strut：不显式给的话 Flutter 会自造一个强制 strut，把含中文/emoji 的
            # 行压到 24px，而高亮层同一行是 25px → 光标逐行上飘（见 _edit_strut）。
            strut_style=_edit_strut(code_size),
            cursor_color=c.link,
            cursor_width=2,
            selection_color=ft.Colors.with_opacity(0.25, c.link),
            # 左内边距 = 行号列宽 + 间距，让编辑框的**文本区**与浏览态代码列严格同 x；
            # 右内边距 = 代码列右缘的行内边距，让两侧**可用宽度**也相等（浏览态由行容器
            # 的 padding 承担）。块容器已不留水平内边距（行号色带贴左边框），编辑框的
            # 外框因此与块内缘同宽，这段右内边距必须由它自己给——漏掉的话每行可用宽度
            # 比浏览态多一个 Spacing.MD，折行点会比浏览态靠后。
            # 真机探针实测：两侧文本区同为 728px 时折行点完全一致。
            content_padding=ft.Padding.only(
                left=gutter_w + Spacing.MD, right=Spacing.MD, top=0, bottom=0
            ),
            on_change=_on_field_change,
            # 取得焦点：既转发给编辑器（撤销会话分组），也是"Tab 焦点往返已结束"的信号。
            on_focus=_on_edit_focus,
            on_blur=lambda e: _exit_edit(),
            # 光标/选区跟踪：组件内留一份供 Tab 缩进定位，再写入 (value, base, extent)
            # 供代码块边界方向键跳出判定
            on_selection_change=_remember_caret,
        )
        if code_field_ref is not None:
            # 保留对外契约（历史上由编辑器持有该 ref）；聚焦不依赖它——它被文档内
            # 每个代码块依次赋值，多块共存时指向最后渲染的那个。
            code_field_ref.current = field
        # 编辑框必须拿到**紧宽度**才不会被内在宽度收窄（收缩 → 折行提前 → 行宽异常）：
        # `Row` 的 `Container(expand=True)` 是拿到紧宽度最简单的办法——Flex 子项会被
        # 赋予确定宽度，再原样传给编辑框。
        # 注意不能改用 `KeyboardListener(expand=True)` 承载：它只把自己撑开，传给
        # content 的是松约束（实测编辑框仍缩到 300px），监听器必须放到 Stack 外层。
        if wrap:
            field_layer: ft.Control = ft.Row(
                controls=[ft.Container(expand=True, content=field)],
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        else:
            # 不换行：按最长行给编辑框固定宽度（无界约束下不能用 flex），与浏览态
            # 的"单行不折 + 横向滚动"一致。
            # 宽度 = 左内边距 + 文本区 + 右内边距：右侧那段内边距现在由编辑框自己
            # 承担（见 content_padding），故末尾要**多留一个 Spacing.MD** 才能让文本区
            # 与原先一样是「最长行 + 一个 Spacing.MD」。那个余量不是凑数：`_measure_mono_width`
            # 在 HarfBuzz 不可用时退化为"字符数 × 0.5 字号"，对 CJK 偏窄（实为 1.0em），
            # 余量被吃掉后最长行会在编辑框里折行 → 透明文字折行、光标与可见字形错位。
            longest = max(code.split("\n"), key=len, default="")
            field.width = max(
                _EDIT_MIN_WIDTH + gutter_w + 2 * Spacing.MD,
                gutter_w
                + 2 * Spacing.MD
                + _measure_mono_width(longest, code_size)
                + Spacing.MD,
            )
            field_layer = ft.Row(
                controls=[field], vertical_alignment=ft.CrossAxisAlignment.START
            )

        # 高亮层与编辑层同处一个 Stack：Stack 用默认 LOOSE 按子项尺寸定型
        # （EXPAND 在滚动 Column 内交叉轴无界 → 高度会算成 inf，整块渲不出来）。
        overlay = ft.Stack(
            alignment=ft.Alignment.TOP_LEFT,
            controls=[_read_column(), field_layer],
        )
        body: ft.Control = overlay
        if not wrap:
            # 不换行：高亮层与编辑层共用同一个横向滚动容器，滚动位置天然同步。
            body = ft.Row(
                controls=[overlay],
                scroll=ft.ScrollMode.AUTO,
                vertical_alignment=ft.CrossAxisAlignment.START,
            )
        # 键盘监听器包住整个正文（而不仅编辑框）：焦点落在代码块内才触发，因此 Tab
        # 缩进既不会干扰正文光标的 Tab（段首缩进/表格跳格），也不需要把事件通道上抛。
        return ft.KeyboardListener(
            content=body,
            on_key_down=_on_edit_key_down,
            on_key_up=_on_edit_key_up,
        )

    # ============================ 交互 ============================

    def _on_field_change(e) -> None:
        """编辑框内容变化：先记下"客户端手里的文本"，再交给编辑器写回文档。

        记 `client_text_ref` 是**下发规则的判据输入**（见 `_build_edit_body`）：这次变化
        由客户端敲出，故此刻文档里的文本与它同源，重渲染时**不得**再把文本回灌给
        客户端——回灌会把客户端"抢跑"输入的内容覆盖掉、光标甩到代码块末尾。
        写回文档仍走既有链路（撤销历史 / 标脏 / 重渲染语义完全不变）。
        """
        # 文本按空白兜底：`TextField.value` 的类型是 str，None 只可能来自异常事件，
        # 而 `on_change_code` 会把它原样写进文档（`segments[0].text = None`）。
        value = e.control.value or ""
        client_text_ref.current = value
        if on_change_code is not None:
            on_change_code(line_idx, value)

    def _remember_caret(e) -> None:
        """编辑框光标/选区变化：组件内留一份选区快照（Tab 缩进定位用），再转发给编辑器。

        只记偏移、不记文本：`e.control.value` 是服务端控件的**渲染时**取值——flet 不会
        把客户端输入回写到控件属性上（文本以文档为唯一真相）。混用"旧文本 + 新偏移"
        会让缩进落错位置，真机探针实测甚至会吃掉刚输入的字符（旧文本 105 字 + 新偏移
        106 被裁剪到 105，末尾字符连同缩进一起写回文档）。
        """
        sel = getattr(e, "selection", None)
        base = int(getattr(sel, "base_offset", 0) or 0)
        extent = int(getattr(sel, "extent_offset", 0) or 0)
        caret_ref.current = (base, extent)
        # 折叠光标跨了逻辑行 → 更新"当前行"（行号高亮），这一次 set_state 同时充当
        # 重渲染的触发源；拖选（base != extent）不更新，避免拖过 N 行就重渲染 N 次。
        if base == extent:
            new_line = _caret_logical_line()
            if new_line != caret_line:
                set_caret_line(new_line)
        if on_code_selection is not None:
            on_code_selection(line_idx, e)

    def _current_caret() -> tuple[str, int, int]:
        """当前 (文本, 选区起点, 选区终点)。

        文本取文档（`on_change_code` 每次输入都写回，是唯一真相）；偏移取组件内快照，
        缺失时（进入编辑态后未收到选区事件就按 Tab）回退到编辑框控件自身的 selection。
        """
        text = line.segments[0].text if line.segments else ""
        caret = caret_ref.current
        if caret is None:
            sel = getattr(edit_field_ref.current, "selection", None)
            caret = (
                int(getattr(sel, "base_offset", 0) or 0),
                int(getattr(sel, "extent_offset", 0) or 0),
            )
        return (text, caret[0], caret[1])

    def _on_edit_key_down(e) -> None:
        """编辑态按键：Esc 退出编辑、跟踪修饰键、把 Tab / Shift+Tab 变成缩进。

        Tab 必须自己处理（原因见模块 docstring）：插入缩进后标记 tab_pending，
        把随之而来的焦点遍历失焦当作副作用处理，而不是"用户离开了代码块"。
        Ctrl+Tab 是全局标签切换快捷键，不插入缩进（与表格缩进/标签切换同一取舍）。

        Esc 是本组件处理的（而不是交给全局面键盘分发器）：Tab 能被这里收到，
        说明原生多行 TextField 并不吞掉"非文本编辑类"按键，全局面包分发器又没有
        任何动作绑定 Esc——放这里既不需要新增全局动作，也不用把组件内部状态
        （`is_editing`）暴露给外部。行为与 Typora 一致：Esc 关闭代码块的编辑态，
        回到高亮浏览态；文档光标不挪动（代码块是岛屿，光标本来也落不进去）。
        """
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            shift_ref.current = True
            return
        if key.startswith("control"):
            ctrl_ref.current = True
            return
        if key == "escape":
            if is_editing and not is_collapsed:
                _exit_edit()
            return
        if key != "tab":
            return
        if ctrl_ref.current:
            return
        if not (is_editing and not is_collapsed):
            return
        _apply_indent_edit(-1 if shift_ref.current else 1)

    def _on_edit_key_up(e) -> None:
        key = (getattr(e, "key", "") or "").lower()
        if key.startswith("shift"):
            shift_ref.current = False
        elif key.startswith("control"):
            ctrl_ref.current = False

    def _apply_indent_edit(direction: int) -> None:
        """Tab / Shift+Tab：改写代码文本并保持光标列位。

        文本改写走 `on_change_code`（入撤销历史、标脏、重渲染等与手工输入完全一致），
        缩进后的光标位置记进 pending_caret_ref，随这次重渲染下发给客户端。

        这里**不需要**维护 `client_text_ref`：下发判据是"文档文本 != 我们最后确知客户端
        手里有的文本"，而缩进是一次真正的改写（`new_value != value`），两种情况都会
        判定为"我方文本"并下发——要么已知文本还是缩进前那份（不同），要么尚未确知
        （空值，也不同）。刻意不写一行"看起来更严谨"的基线更新：变异验证证实它不可观测
        （写不写都过），留着只会让人以为它承担了判据的一部分。

        Tab 一定会让 Flutter 把焦点遍历走（原生编辑框不消费 Tab），所以无论文本是否
        变化都要走一遍"重渲染 → 收回焦点"；文本无变化时不写文档，避免产生空撤销条目。
        """
        value, base, extent = _current_caret()
        new_value, new_base, new_extent = apply_indent(value, base, extent, direction)
        tab_pending_ref.current = True
        if new_value != value:
            pending_caret_ref.current = (new_base, new_extent)
            # 乐观更新组件内光标快照：客户端的光标事件回来之前再按 Tab 也落在正确位置
            caret_ref.current = (new_base, new_extent)
            if on_change_code is not None:
                on_change_code(line_idx, new_value)
        set_tab_epoch(tab_epoch + 1)

    async def _focus_edit_field() -> None:
        """把焦点交给原生编辑框。

        进入编辑态（is_editing 变化）与 Tab 缩进后（tab_epoch 变化）都要走一次：
        - 不用 `autofocus=True`：它只在控件首次挂载时生效，重渲染路径不可靠；
        - 用 `use_effect` 在提交渲染后执行，此时 ref 已指向新建控件，聚焦稳定；
        - Tab 后必须重新聚焦：焦点遍历由客户端在按键处理时同步执行完毕，等这次重渲染
          提交后再请求聚焦，才确定"收回"发生在"被带走"之后。

        这里只调方法（`focus()`），不改控件属性——渲染后的控件是冻结的，
        赋值会抛 "Frozen controls cannot be updated."。
        """
        field = edit_field_ref.current
        if is_editing and field is not None:
            with contextlib.suppress(Exception):
                await field.focus()

    ft.use_effect(_focus_edit_field, [is_editing, tab_epoch])

    def _on_edit_focus(e) -> None:
        """编辑框取得焦点：Tab 的焦点往返结束，之后的失焦都按真实失焦处理。

        同时把 `edit_focused` 置真——它触发的那次重渲染才是**下发光标**的时机
        （挂载当次下发会被聚焦动作覆盖，见 `_build_edit_body`）。
        """
        tab_pending_ref.current = False
        if not edit_focused:
            set_edit_focused(True)
        if on_code_focus is not None:
            on_code_focus(line_idx)

    def _enter_edit() -> None:
        """点击代码块 → 进入编辑态（折叠态先展开；焦点由 use_effect 交给编辑框）。"""
        if is_collapsed:
            set_collapsed(False)
        if not is_editing:
            # 上一次会话的光标快照与修饰键状态都已失效，避免 Tab 落在过期位置
            # 或被上一次会话残留的 Shift/Ctrl 状态误判；edit_focused 复位，新挂载的
            # 编辑框要走一遍"先聚焦、后下发光标"的两段式（见 _build_edit_body）。
            caret_ref.current = None
            shift_ref.current = False
            ctrl_ref.current = False
            # 文本来源判据也随会话重置：新挂载的编辑框必须**无条件**拿到当前文本。
            # client_text 置空即可满足——下发规则是"文档文本 != 客户端回报的文本 ⇒
            # 下发"，空值必然不等于文档文本，故第一次渲染一定走下发分支
            # （`edit_field_ref` 里那份是上一会话的旧对象，不会被误当成"客户端手里的"）。
            client_text_ref.current = None
            set_edit_focused(False)
            # 清掉上一次会话的行号高亮，避免"重进编辑态时先闪一下旧行"
            set_caret_line(-1)
            set_editing(True)

    def _exit_edit() -> None:
        """编辑框失焦 → 回到高亮浏览态，并通知编辑器清理围栏聚焦态。

        Tab 引发的失焦是 Flutter 焦点遍历的副作用，不是用户要离开代码块：
        此时既不退出编辑态、也不清理围栏聚焦态（撤销会话因此不被打断），
        焦点由 `_focus_edit_field` 在本次重渲染提交后收回。
        """
        if tab_pending_ref.current:
            return
        if on_code_blur is not None:
            on_code_blur(line_idx)
        # 编辑框即将卸载，文本来源判据不再有意义（下次进入在 `_enter_edit` 里重建）
        client_text_ref.current = None
        if is_editing:
            set_editing(False)

    def _toggle_collapse() -> None:
        """折叠/展开；折叠时退出编辑态（编辑框被卸载，避免聚焦态残留）。"""
        if not is_collapsed and is_editing:
            set_editing(False)
        set_collapsed(not is_collapsed)

    # ============================ 组装 ============================

    # 折叠态摘要：首行预览 + 行数（保持与展开态同一层级结构，避免布局跳动）
    first_line = code.split("\n")[0]
    preview_text = first_line[:60] + ("…" if len(first_line) > 60 else "")
    collapsed_preview = ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(
                    value=preview_text or "(空代码块)",
                    size=12,
                    color=c.muted,
                    font_family=FONT_MONO,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
                ft.Text(
                    value=f"{line_count} 行",
                    size=11,
                    color=c.muted,
                    font_family=FONT_MONO,
                ),
            ],
            spacing=Spacing.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=Spacing.MD, vertical=Spacing.SM),
        bgcolor=ft.Colors.with_opacity(0.5, c.code_block_bg),
        border_radius=Radius.MD,
        # 折叠态**不是**代码窗格：它是一张嵌在块里的圆角卡片（自带底色 + 圆角），
        # 直接贴到块边框会与框架线叠成双层边。块容器已不留水平内边距、下边也不再留
        # 内边距（见下方组装的说明），故这里用 margin 把原先那圈留白补回来
        # （左右各 Spacing.MD；下方补一个 Spacing.SM —— 卡片容器下边已归零，
        # 不补的话圆角会压在下边框上。上方不用补：Column 的 spacing 已隔开头部）。
        margin=ft.Margin.only(left=Spacing.MD, right=Spacing.MD, bottom=Spacing.SM),
        # 左侧强调条：折叠态只剩一行摘要，靠它把自己和普通段落区分开
        # （border 画在盒子内，不改变宽度，也就不影响任何基于宽度的测量）
        border=only_border(left=ft.BorderSide(2, ft.Colors.with_opacity(0.45, c.link))),
    )

    if is_collapsed:
        body: ft.Control = collapsed_preview
    else:
        body = ft.Container(
            content=_build_edit_body() if is_editing else _build_read_body(),
            # **上下也不留内边距**：行号色带是紧贴窗格的装饰条，而窗格的上界就是这条
            # 细分隔线、下界就是块的下边框 —— 所以色带要**通高**，从分隔线一路铺到
            # 块下边框，中间不再被底色切断。原先上下各留一个 Spacing.SM，真机上就是
            # "灰色行号条上下各悬空一截"（上方被分隔线切开 8px、下方离下边框 16px，
            # 物理像素）—— 与上一轮"色带左侧悬空"同一类缺陷，只是换了根轴。
            #
            # 文本的纵向留白**不靠这一层**：行盒高度是「字号 × 1.5」，字形只占其中
            # 约 1em，上下各有约 0.25em 的 leading —— 真机实测行盒顶到字形顶 11 物理
            # px（= 5.5 逻辑 px）。去掉本层那 4px 之后，这段 leading 就是字形与分隔线
            # 之间的全部留白（5px 左右，够用且不局促）。所以去掉的是**重复留白**：
            # 与左缘"色带贴边、文字靠行内边距内缩"是同一条规则在纵轴上的落实。
            padding=ft.Padding.all(0),
            # 头部与正文之间的细分隔线：只画上边（横向 1px，不改变文本区宽度），
            # 让"工具栏 / 代码"两段在视觉上分开——桌面编辑器里这两个区域是不同层的
            # 东西，靠间距区分不够。刻意不用 ft.Divider：它会给自己加高度，
            # 而头部行高是硬锁 22px 的（子项超高会被静默裁切）。
            # 块容器不留水平内边距 → 这条线两端都与块边框对齐（左右都不缩进）；
            # 它同时是**代码窗格的上界**：本层的内边距上边已归零，行号色带从这里
            # 起铺。合起来即那条规则：**代码窗格贴边（上贴分隔线、左右贴边框、
            # 下贴下边框），工具栏与文本各自内缩**。
            border=only_border(top=ft.BorderSide(1, border_color)),
        )

    content = ft.Container(
        content=ft.Column(controls=[header, body], spacing=Spacing.XS),
        bgcolor=c.code_block_bg,
        border_radius=Radius.MD,
        # **左右不留内边距**：行号色带与头部细分隔线因此直接贴到块的左右边框，
        # 框架与行号列之间不再有一条背景色空隙。左右缩进改由各自的内容承担——
        # 工具栏在 header 上、代码列右缘在行容器（浏览态）与编辑框的 content_padding
        # （编辑态）上。原先左侧那段 Spacing.MD 不再被浪费：代码列整体左移一个
        # Spacing.MD、可用宽度也随之多出一个 Spacing.MD（两层依旧严格同 x 同宽）。
        # 上下方向同族处理：**上留一个 Spacing.XS 给工具栏**（头部图标是 22px 定高，
        # 顶到上边框会显得局促；它与 Column 的 spacing 一起构成工具栏上下各 2px 的
        # 呼吸位），**下不留** —— 代码窗格的下界就是块的下边框，行号色带要通到那条
        # 线上（见 body 的说明）。
        padding=ft.Padding.only(top=Spacing.XS),
        shadow=card_shadow(Elevation.LOW, is_dark),
        border=only_border(
            top=ft.BorderSide(1, border_color),
            bottom=ft.BorderSide(1, border_color),
            left=ft.BorderSide(1, border_color),
            right=ft.BorderSide(1, border_color),
        ),
    )

    return _block_frame.wrap_block(
        content,
        line,
        base,
        line_idx,
        on_click=lambda e: _enter_edit(),
        is_current_line=is_current_line,
        is_flash=is_flash,
        on_size_change=on_line_size_change,
        diff_mark=diff_mark,
    )
