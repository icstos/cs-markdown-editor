"""原生代码块（views/code_block.py）测试：软换行开关 / 高亮分词 / 编辑态切换。

背景：代码块原用 flet-code-editor，但上游 `CodeField.wrap` 是未实现的空属性，
"开启换行时代码块内也折行"无法达成，故改为 Flet 原生双态实现。本文件锁定三条
不变量，防止后续再退化：

1. **分词不变式**：`highlight_lines` 的行数恒等于原文行数、逐行拼接恒等于原文
   （行号、逻辑行坐标、边界方向键跳出全部依赖它）；
2. **换行开关生效**：`word_wrap` 真正改变浏览态布局（折行 vs 横向滚动），
   这正是本次改造的原始诉求；
3. **契约未变**：点击进入编辑态后是原生多行 `TextField`，四个围栏回调
   （on_change / on_focus / on_blur / on_selection_change）的参数形态不变，
   因此 views/editor/_fence.py 与 key_bindings 的路由无需改动；
4. **编辑态与浏览态同宽同折行**：编辑态用 `Stack` 把浏览态那个高亮层垫在下面、
   上面叠文字透明的编辑框，两层共用容器约束与字体度量（含显式 letter_spacing），
   故折行点与光标逐字对齐，且编辑时语法高亮持续可见。锁定这条是因为真机上出现
   过"编辑态行宽异常"：`KeyboardListener` 只把自己撑开、传给内容的是松约束，
   多行 TextField 因此缩到内在宽度（实测 300px vs 应有的 728px）。
5. **头部行高紧凑**：头部行高恒为 `_HEADER_H`，故头部内不得出现 Material 固有
   尺寸控件（IconButton 40 / Dropdown 48）——它们是"顶部行过高"的唯一成因，
   比内边距的影响大一个量级。锁定时同时守住"每个子项都显式声明了高度"，
   因为头部高度是硬锁的，超高子项会被静默裁切。

第 6 节「点击定位」另锁两条真机时序（都是先有真机探针实测、再写进测试的）：
- **坐标只在 `on_tap_down` 上**：`Container.on_click` 的声明是 `ControlEventHandler`，
  收到的是不带 `local_position` 的 `ControlEvent`（真机实测 `local=None`）。
  故位置与动作分两个 handler：`on_tap_down` → 记位置快照，`on_click` → 用快照定位。
- **光标要在聚焦之后下发**：挂载当次带的 `selection` 会被 Flutter 的聚焦动作重置到
  文末（真机实测：下发 `14`、客户端回报 `38`）。故 `_settle_edit_focus()` 复现
  "聚焦触发第二次渲染、光标在那一次补发"的时序。
"""

import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import flet as ft

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType  # noqa: E402
from parser import parse_markdown  # noqa: E402
from services.code_highlight import (  # noqa: E402
    DEFAULT,
    clear_highlight_cache,
    highlight_lines,
)
from styles import FONT_MONO, Spacing, get_colors  # noqa: E402
from tests.harness import RenderHarness, walk  # noqa: E402
from views import code_block as blk  # noqa: E402
from views import line_view as lv  # noqa: E402

# 含空行、缩进、字符串，覆盖"空行行盒高度"与多类语义色
RAW_CODE = "import os\n\ndef main():\n    print('hi')\n"
RAW_LINES = RAW_CODE.split("\n")  # 服务层原文行数（含末尾空行）
FENCE = f"```python\n{RAW_CODE}```"
# 围栏正文由解析器剥掉末尾换行（与 line.segments[0].text 完全一致），
# 浏览态 / 编辑态的行数以解析结果为准
PARSED_CODE = parse_markdown(FENCE).lines[0].segments[0].text
LOGICAL_LINES = PARSED_CODE.split("\n")

ROOT = Path(__file__).resolve().parent.parent


def _ref(value=None):
    ref = ft.Ref()
    ref.current = value
    return ref


def _noop(*_a, **_k):
    return None


class _Recorder:
    """记录围栏回调的调用参数（验证契约未变）。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)


def _code_line(raw: str = FENCE):
    """用真实解析器产出一行 CODE 围栏（保证 segments / lang 与线上一致）。"""
    doc = parse_markdown(raw)
    assert doc.lines, f"解析结果为空：{raw!r}"
    line = doc.lines[0]
    assert line.block_type == BlockType.CODE, f"未解析为代码块：{line.block_type}"
    return line


@contextmanager
def _rendered(
    *,
    word_wrap: bool = True,
    active: bool = False,
    change=None,
    focus=None,
    blur=None,
    selection=None,
    change_lang=None,
    raw: str = FENCE,
    enter_seq: int = 0,
    enter_off: int | None = None,
    consumed=None,
):
    """在组件渲染上下文内渲染代码块（`ft.use_state` / `use_effect` 需要宿主）。"""
    line = _code_line(raw)

    @ft.component
    def _Probe():
        return ft.Container(
            content=lv._render_code_block(
                line,
                0,
                16,
                800.0,
                _ref(None),
                change or _noop,
                focus or _noop,
                blur or _noop,
                change_lang or _noop,
                selection or _noop,
                _ref(None),
                active,
                False,
                None,
                diff_mark=None,
                word_wrap=word_wrap,
                enter_seq=enter_seq,
                enter_off=enter_off,
                on_enter_consumed=consumed,
            ),
            key="probe",
        )

    harness = RenderHarness()
    try:
        harness.render(_Probe)
        yield harness
    finally:
        harness.dispose()


def _code_text(h: RenderHarness) -> ft.Text | None:
    """浏览态代码文本控件（第一个带 spans 的 Text；行号 / 计数 Text 无 spans）。"""
    for node in h.find(lambda n: isinstance(n, ft.Text)):
        if getattr(node, "spans", None):
            return node
    return None


def _gutter_numbers(h: RenderHarness) -> list[str]:
    """行号列文本（纯数字内容）。"""
    out = []
    for node in h.find(lambda n: isinstance(n, ft.Text)):
        value = getattr(node, "value", None)
        if isinstance(value, str) and value.isdigit():
            out.append(value)
    return out


def _edit_fields(h: RenderHarness) -> list[ft.TextField]:
    return h.find(lambda n: isinstance(n, ft.TextField))


def _enter_edit(h: RenderHarness) -> ft.TextField:
    """点击块级容器（wrap_block 的 on_click）进入编辑态，返回原生编辑框。

    真机上"进入编辑态"必然紧跟一次编辑框取得焦点（`_focus_edit_field` effect），
    这里一并复现：`_on_edit_focus` 会触发第二次渲染，**待下发的光标在那一次才补上**
    （挂载当次会被 Flutter 的聚焦动作覆盖，见 `_settle_edit_focus`）。因此返回的是
    重渲染后的编辑框对象。
    """
    entry = [
        n
        for n in h.find(
            lambda n: getattr(n, "on_click", None) is not None
            and getattr(n, "key", None) == "line-0"
        )
    ]
    assert entry, "未找到块级容器点击入口（block frame on_click）"
    h.interact(entry[0].on_click, None)
    fields = _edit_fields(h)
    assert fields, "进入编辑态后未生成原生 TextField"
    _settle_edit_focus(h)
    return _edit_fields(h)[0]


# ==================== 1. 分词不变式 ====================


def test_highlight_row_count_and_text_invariant():
    """行数恒等于原文行数，且逐行拼接恒等于原文（行号 / 行坐标依赖此不变式）。"""
    for lang in ("python", "javascript", "text", "no-such-lang", "", "md"):
        rows = highlight_lines(RAW_CODE, lang)
        assert len(rows) == len(RAW_LINES), f"{lang}: 行数不符"
        assert [
            "".join(t for t, _ in row) for row in rows
        ] == RAW_LINES, f"{lang}: 文本被改写"


def test_highlight_empty_code_is_single_empty_row():
    """空代码块恰有一行（与 `"".split("\\n")` 一致）。"""
    assert highlight_lines("", "python") == ((),)
    assert len(highlight_lines("", "")) == 1


def test_highlight_unknown_lang_falls_back_to_plain():
    """未知语言回退纯文本：只有默认类别，但行数与内容不变。"""
    rows = highlight_lines(RAW_CODE, "definitely-not-a-language")
    assert all(kind == DEFAULT for row in rows for _, kind in row)
    assert len(rows) == len(RAW_LINES)


def test_highlight_plain_langs_skip_tokenizing():
    """text/txt/plaintext 等视为纯文本，不做分词（无需 Pygments）。"""
    clear_highlight_cache()
    rows = highlight_lines(RAW_CODE, "plaintext")
    assert all(kind == DEFAULT for row in rows for _, kind in row)


def test_highlight_cache_returns_same_object():
    """同一 (语言, 代码) 命中缓存：文档反复重渲染不重复分词。"""
    clear_highlight_cache()
    first = highlight_lines(RAW_CODE, "python")
    assert highlight_lines(RAW_CODE, "python") is first


# ==================== 2. 软换行开关 ====================


def test_word_wrap_on_uses_expanding_text_and_no_hscroll():
    """开启换行：代码文本 expand 占满行号右侧、允许折行，且无横向滚动容器。"""
    with _rendered(word_wrap=True) as h:
        text = _code_text(h)
        assert text is not None, "浏览态未渲染代码文本"
        assert text.expand is True, "开启换行时文本应 expand 撑满可用宽度"
        assert not text.no_wrap, "开启换行时不应设置 no_wrap"
        scroll_rows = [
            n
            for n in h.find(lambda n: isinstance(n, ft.Row))
            if getattr(n, "scroll", None) == ft.ScrollMode.AUTO
        ]
        assert not scroll_rows, "开启换行时不应出现横向滚动容器"


def test_word_wrap_off_uses_no_wrap_and_hscroll():
    """关闭换行：文本 no_wrap 单行不折，外层横向滚动。"""
    with _rendered(word_wrap=False) as h:
        text = _code_text(h)
        assert text is not None, "浏览态未渲染代码文本"
        assert text.no_wrap is True, "关闭换行时应设 no_wrap"
        assert not text.expand, "关闭换行时不应 expand（父级宽度无界）"
        scroll_rows = [
            n
            for n in h.find(lambda n: isinstance(n, ft.Row))
            if getattr(n, "scroll", None) == ft.ScrollMode.AUTO
        ]
        assert scroll_rows, "关闭换行时应出现横向滚动容器"


def test_gutter_numbers_cover_every_logical_line():
    """行号列覆盖全部逻辑行（含空行），编号从 1 连续递增。"""
    with _rendered() as h:
        assert _gutter_numbers(h) == [str(i) for i in range(1, len(LOGICAL_LINES) + 1)]


def test_empty_line_keeps_line_box():
    """空行也必须有文字片段（空格占位），否则行盒塌陷导致行号逐行错位。"""
    with _rendered() as h:
        texts = [
            n
            for n in h.find(lambda n: isinstance(n, ft.Text))
            if getattr(n, "spans", None)
        ]
        assert len(texts) == len(LOGICAL_LINES), "每个逻辑行都应有独立文本控件"
        # 第 2 个逻辑行为空
        empty_row_text = texts[1]
        assert "".join(s.text for s in empty_row_text.spans) == " "


def test_syntax_spans_use_theme_colors():
    """语法高亮确实上色：关键字 / 字符串命中主题 code_syntax 配色。"""
    light = get_colors(ft.ThemeMode.LIGHT)
    with _rendered() as h:
        # 每个逻辑行是独立 Text，需聚合全部行才覆盖到字符串所在的那一行
        colors = [
            span.style.color
            for node in h.find(lambda n: isinstance(n, ft.Text))
            for span in (getattr(node, "spans", None) or [])
            if span.style is not None
        ]
        assert light.code_syntax["kw"] in colors, "关键字未按主题色上色"
        assert light.code_syntax["str"] in colors, "字符串未按主题色上色"
        assert len(set(colors)) >= 3, "语义色种类过少，疑似整体回退为单色"


# ==================== 3. 编辑态与回调契约 ====================


def test_click_enters_edit_mode_with_native_multiline_field():
    """点击代码块 → 生成原生多行 TextField（替代 CodeEditor），并保留高亮层。"""
    with _rendered() as h:
        assert _code_text(h) is not None, "初始应为浏览态"
        field = _enter_edit(h)
        assert field.multiline is True
        assert field.value == PARSED_CODE
        assert field.min_lines == len(LOGICAL_LINES)
        # 编辑态的高亮层仍应在：字符由它呈现，编辑框只留光标与选区
        assert _code_text(h) is not None, "编辑态丢失了底层高亮层"


def test_edit_change_forwards_to_on_change_code():
    """编辑框输入 → on_change_code(line_idx, value) 契约不变。"""
    rec = _Recorder()
    with _rendered(change=rec) as h:
        field = _enter_edit(h)
        h.interact(
            field.on_change,
            types.SimpleNamespace(control=types.SimpleNamespace(value="x = 1")),
        )
    assert rec.calls == [(0, "x = 1")]


def test_focus_and_blur_forward_to_on_code_focus_blur():
    """聚焦 / 失焦回调形态不变，失焦后回到浏览态。"""
    focus_rec, blur_rec = _Recorder(), _Recorder()
    with _rendered(focus=focus_rec, blur=blur_rec) as h:
        field = _enter_edit(h)
        # `_enter_edit` 已复现"进入编辑态 → 编辑框取得焦点"的真实时序
        assert focus_rec.calls == [(0,)], "进入编辑态应转发一次 on_code_focus"
        h.interact(field.on_blur, None)
        assert blur_rec.calls == [(0,)]
        assert _code_text(h) is not None, "失焦后应回到高亮浏览态"
        assert not _edit_fields(h), "失焦后编辑框应被卸载"


def test_selection_change_forwards_event_to_on_code_selection():
    """选区变化回调把原始事件透传给 on_code_selection（边界跳出依赖它）。"""
    rec = _Recorder()
    event = types.SimpleNamespace(
        control=types.SimpleNamespace(value=RAW_CODE),
        selection=types.SimpleNamespace(base_offset=3, extent_offset=3),
    )
    with _rendered(selection=rec) as h:
        field = _enter_edit(h)
        h.interact(field.on_selection_change, event)
    assert rec.calls == [(0, event)]


def test_edit_field_ref_is_assigned():
    """code_field_ref 契约保留（历史上有外部持有者，聚焦另有组件内 ref）。"""
    shared = _ref(None)
    line = _code_line()

    @ft.component
    def _Probe():
        return ft.Container(
            content=lv._render_code_block(
                line, 0, 16, 800.0, _ref(None),
                _noop, _noop, _noop, _noop, _noop, shared,
                False, False, None,
                diff_mark=None, word_wrap=True,
            ),
            key="probe",
        )

    harness = RenderHarness()
    try:
        harness.render(_Probe)
        entry = [
            n
            for n in harness.find(
                lambda n: getattr(n, "on_click", None) is not None
                and getattr(n, "key", None) == "line-0"
            )
        ]
        harness.interact(entry[0].on_click, None)
        assert shared.current is not None, "code_field_ref 未被赋值为编辑框"
        assert isinstance(shared.current, ft.TextField)
    finally:
        harness.dispose()


# ==================== 4. 依赖清理 ====================


def test_flet_code_editor_dependency_removed():
    """flet-code-editor 已彻底移除，改由 pygments 提供分词。"""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "flet-code-editor" not in pyproject, "依赖清单仍残留 flet-code-editor"
    assert "pygments" in pyproject, "依赖清单缺少 pygments"

    for rel in ("views/code_block.py", "views/line_view.py", "views/editor/_render.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "import flet_code_editor" not in src, f"{rel} 仍导入 flet_code_editor"


# ==================== 5. Tab / Shift+Tab 缩进 ====================
#
# 原生多行 TextField 不会插入制表符：Flutter 把 Tab 当焦点遍历键，Flet 1.0 没有
# Focus / Shortcuts 控件、TextField 也没有 on_key_down。真机探针实测：编辑级
# KeyboardListener 能收到 Tab，但回调返回 True 拦不住遍历（KEYDOWN Tab 与 BLUR 同帧）。
# 故缩进由组件自行写入，并把这随后的失焦当遍历副作用处理——本组测试锁定该行为。


def _key_event(key: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(key=key)


def _keyboard_listeners(h: RenderHarness) -> list[ft.KeyboardListener]:
    return h.find(lambda n: isinstance(n, ft.KeyboardListener))


def _edit_listener(h: RenderHarness) -> ft.KeyboardListener:
    """编辑态的按键监听器（包住整个正文，焦点在代码块内才触发）。

    注意它包的是**整个叠加层**而不是编辑框：`KeyboardListener` 会把内容撑开但不
    向内容传递紧宽度，直接包编辑框会让多行 TextField 缩到内在宽度（行宽异常根因）。
    """
    listeners = _keyboard_listeners(h)
    assert listeners, "编辑态未挂载 KeyboardListener，Tab 收不到"
    return listeners[0]


def _overlay(h: RenderHarness) -> ft.Stack:
    """编辑态叠加容器：底层高亮层 + 顶层透明编辑层。"""
    stacks = h.find(lambda n: isinstance(n, ft.Stack))
    assert stacks, "编辑态未使用 Stack 叠加高亮层与编辑层"
    return stacks[0]


def _gutter_cell_w(h: RenderHarness) -> float:
    """行号列宽（从渲染出的行号单元格反推，避免在测试里复制宽度公式）。"""
    cells = [
        n
        for n in h.find(lambda n: isinstance(n, ft.Container))
        if isinstance(n.content, ft.Text)
        and str(getattr(n.content, "value", "")).isdigit()
    ]
    assert cells, "未找到行号单元格"
    return cells[0].width


def _caret(h: RenderHarness, field: ft.TextField, base: int, extent: int) -> None:
    """驱动一次选区变化（真实使用时由点击 / 方向键触发）。"""
    h.interact(
        field.on_selection_change,
        types.SimpleNamespace(
            control=types.SimpleNamespace(value=field.value),
            selection=types.SimpleNamespace(base_offset=base, extent_offset=extent),
        ),
    )


def _press(h: RenderHarness, listener: ft.KeyboardListener, key: str) -> None:
    h.interact(lambda: listener.on_key_down(_key_event(key)))


def test_edit_body_overlays_field_on_highlight_layer():
    """编辑态 = Stack[高亮层, 编辑层]，且监听器包住整个正文而非编辑框。

    `KeyboardListener` 只把自己撑开、不向内容传递紧宽度：若直接包住编辑框，
    多行 TextField 会缩到内在宽度（真机实测 300px vs 应有的 728px）→ 折行提前、
    块高多出两行，即"编辑态行宽异常"。故它必须在最外层。
    """
    with _rendered() as h:
        field = _enter_edit(h)
        stack = _overlay(h)
        assert isinstance(stack.controls[0], ft.Column), "Stack 底层应是高亮正文列"
        assert field not in stack.controls, "编辑框应在 Stack 的第二层里"
        listener = _edit_listener(h)
        assert listener.content is stack, "监听器应包住整个叠加层"
        assert listener.content is not field, "监听器不能直接包住编辑框（会收窄宽度）"


def test_edit_field_receives_tight_width_from_flex_parent():
    """换行模式下编辑框由 flex 容器赋予**紧宽度**，不得缩到内在宽度。

    这条直接对应"编辑态行宽异常"：宽度塌缩时编辑框只有 300px、浏览态代码列有 728px，
    折行点因此完全对不上。修复手段是让编辑框挂在 `Container(expand=True)` 下
    （无界约束下才允许退化为固定宽度）。
    """
    with _rendered(word_wrap=True) as h:
        field = _enter_edit(h)
        assert field.width is None, "换行模式不应给编辑框固定宽度，应由 flex 撑满"
        holders = [
            n
            for n in h.find(lambda n: isinstance(n, ft.Container))
            if n.expand and n.content is field
        ]
        assert holders, "编辑框未被 expand 容器承载，宽度会塌缩到内在宽度"


def test_edit_field_is_transparent_and_matches_highlight_metrics():
    """编辑框文字透明，且字体度量与高亮层逐项一致（字距尤甚）。

    任一项不一致都会让光标相对底层可见文字漂移；字距差异按 0.25px/字形线性累积，
    长行尤其明显（两层都必须**显式**指定，因为 `TextStyle.letter_spacing` 默认 None
    走继承，继承链不确定）。
    """
    with _rendered() as h:
        field = _enter_edit(h)
        style = field.text_style
        assert style.color == ft.Colors.TRANSPARENT, "编辑框文字应透明"
        assert style.letter_spacing is not None, "字距必须显式指定"

        ref = _code_text(h).spans[0].style
        assert style.font_family == ref.font_family == FONT_MONO
        assert style.size == ref.size, "字号不一致 → 行高与折行点漂移"
        assert style.height == ref.height, "行高倍数不一致 → 光标纵向漂移"
        assert style.letter_spacing == ref.letter_spacing, "字距不一致 → 光标横向漂移"


def test_edit_field_declares_non_forcing_strut():
    """编辑框必须显式带"不强制行高"的 strut —— 这是光标纵向漂移的根因修复。

    `TextField` 不指定 strut 时，Flutter 会自造一个 `force_strut_height=True` 的 strut，
    把每行高度钉死为 `size × height`（16×1.5=24px）；而浏览层 `ft.Text` 无 strut，
    行盒取"该行所有 run 的自然行高最大值"。两者仅在纯 ASCII 下相等——
    行内一旦出现需要字体回退的字形（中文注释、emoji），`ft.Text` 行盒变成 25px、
    编辑框仍是 24px，于是**每经过一个中文/emoji 行，可见文字相对光标下移 1px**，
    越往下越明显，即用户报的"越往后面的行光标越往上偏"。

    `force_strut_height=False` 让 strut 只保证下限，编辑框行盒退化为与 `ft.Text`
    相同的自然最大值，两层逐行严格相等。此测试锁死这一约束，防止被"顺手简化"掉。
    """
    with _rendered() as h:
        field = _enter_edit(h)
        strut = field.strut_style
        assert strut is not None, "编辑框缺 strut_style → Flutter 自造强制 strut，中文/emoji 行光标上飘"
        assert strut.force_strut_height is False, "strut 必须不强制行高，否则回退字形的行盒被压扁"
        assert strut.font_family == FONT_MONO, "strut 字体族应与正文一致"
        assert strut.height == blk._CODE_LINE_HEIGHT, "strut 行高倍数应与正文一致"


def test_edit_strut_helper_shape():
    """`_edit_strut` 是编辑框 strut 的唯一来源，字段须与正文样式同源同值。"""
    strut = blk._edit_strut(16)
    assert isinstance(strut, ft.StrutStyle)
    assert strut.font_family == FONT_MONO
    assert strut.size == 16
    assert strut.height == blk._CODE_LINE_HEIGHT
    assert strut.force_strut_height is False

    ref = blk._span_style(ft.Colors.WHITE, 16)
    assert strut.font_family == ref.font_family
    assert strut.size == ref.size
    assert strut.height == ref.height


def test_edit_field_text_area_aligns_with_highlight_column():
    """编辑框文本区左缘与高亮层代码列同 x，右侧不再额外留白。

    右侧若也留一个间距，编辑框每行可用宽度就比浏览态少一个 `Spacing.MD`，折行点
    会比浏览态提前——真机探针实测两侧文本区同为 728px 时折行点才完全一致。
    """
    with _rendered() as h:
        field = _enter_edit(h)
        pad = field.content_padding
        assert pad.left == _gutter_cell_w(h) + Spacing.MD, "左内边距应等于行号列宽 + 间距"
        assert pad.right == 0, "右侧留白会让可用宽度比浏览态少一个间距"


def test_word_wrap_off_edit_shares_scroll_with_highlight():
    """关闭换行：高亮层与编辑层共用同一个横向滚动容器（滚动位置天然同步）。"""
    with _rendered(word_wrap=False) as h:
        field = _enter_edit(h)
        stack = _overlay(h)
        scroll_rows = [
            n
            for n in h.find(lambda n: isinstance(n, ft.Row))
            if getattr(n, "scroll", None) == ft.ScrollMode.AUTO
        ]
        assert scroll_rows, "关闭换行时编辑态应有横向滚动容器"
        assert any(stack in (r.controls or []) for r in scroll_rows), (
            "叠加层应直接放进滚动容器，两层才能同步滚动"
        )
        assert field.width is not None, "无界约束下必须给编辑框固定宽度（flex 不可用）"


def test_tab_inserts_indent_and_forwards_to_on_change_code():
    """Tab 在光标处插入 4 空格，并经 on_change_code 回写文档（与手打输入同一路径）。

    编辑框文本以文档为唯一真相：缩进经 on_change_code 写入文档 → 重渲染用文档文本
    重建编辑框（key 稳定，不重挂载）。这里断言的是这条链路的前半段。
    """
    rec = _Recorder()
    with _rendered(change=rec) as h:
        field = _enter_edit(h)
        pos = PARSED_CODE.index("def main()")
        _caret(h, field, pos, pos)
        _press(h, _edit_listener(h), "Tab")

        expected = PARSED_CODE[:pos] + "    " + PARSED_CODE[pos:]
        assert rec.calls == [(0, expected)], "缩进未回写文档"
        assert _edit_fields(h), "缩进后不应掉出编辑态"


def test_tab_keeps_caret_after_inserted_indent():
    """缩进后的光标经渲染参数下发到编辑框（否则 Flutter 会把它甩到文末）。

    光标不能在 effect 里回写：渲染后的控件是冻结的，改属性会抛
    "Frozen controls cannot be updated."，故必须作为构造参数给出。
    """
    with _rendered() as h:
        field = _enter_edit(h)
        pos = PARSED_CODE.index("print")
        _caret(h, field, pos, pos)
        _press(h, _edit_listener(h), "Tab")

        updated = _edit_fields(h)[0]
        assert updated.selection is not None, "缩进后未下发光标位置"
        assert updated.selection.base_offset == pos + 4
        assert updated.selection.extent_offset == pos + 4


def test_repeated_tab_indents_progressively():
    """连按 Tab 逐级插入缩进：光标快照随缩进推进（不会被上一次的位置钉住）。

    顺带锁定"待下发光标只用一次"：若第一次的光标一直挂在渲染参数上，第二次 Tab
    会重新插入到同一列而不是往后推进。
    """
    with _rendered() as h:
        field = _enter_edit(h)
        pos = PARSED_CODE.index("print")
        _caret(h, field, pos, pos)
        _press(h, _edit_listener(h), "Tab")
        assert _edit_fields(h)[0].selection.base_offset == pos + 4
        _press(h, _edit_listener(h), "Tab")
        assert _edit_fields(h)[0].selection.base_offset == pos + 8


def test_shift_tab_outdents_current_line():
    """Shift+Tab 删掉当前行的缩进。"""
    rec = _Recorder()
    with _rendered(change=rec) as h:
        field = _enter_edit(h)
        # "    print('hi')" 行内光标在 print 之后
        pos = PARSED_CODE.index("print")
        _caret(h, field, pos, pos)
        # Shift 自身按键先到达（KeyDownEvent 不带修饰键，只能靠跟踪）
        _press(h, _edit_listener(h), "Shift")
        _press(h, _edit_listener(h), "Tab")

        expected = PARSED_CODE[: pos - 4] + PARSED_CODE[pos:]
        assert rec.calls == [(0, expected)]
        assert _edit_fields(h)[0].selection.base_offset == pos - 4


def test_shift_tab_without_indent_makes_no_change():
    """行首无缩进时 Shift+Tab 是空变换：不回写文档（否则产生空撤销条目），
    但仍要保持编辑态（Tab 的焦点遍历照样发生，必须把焦点收回）。"""
    rec = _Recorder()
    blur_rec = _Recorder()
    with _rendered(change=rec, blur=blur_rec) as h:
        field = _enter_edit(h)
        _caret(h, field, 0, 0)
        _press(h, _edit_listener(h), "Shift")
        _press(h, _edit_listener(h), "Tab")
        assert rec.calls == []
        assert _edit_fields(h), "空变换的 Shift+Tab 不该把用户踢出代码块"
    assert blur_rec.calls == []


def test_ctrl_tab_does_not_indent():
    """Ctrl+Tab 是全局标签切换，不能在代码块内插入缩进。"""
    rec = _Recorder()
    with _rendered(change=rec) as h:
        field = _enter_edit(h)
        _caret(h, field, 3, 3)
        _press(h, _edit_listener(h), "Control")
        _press(h, _edit_listener(h), "Tab")
    assert rec.calls == [], "Ctrl+Tab 被误当缩进"


def test_tab_induced_blur_keeps_edit_mode():
    """Tab 引发的失焦是 Flutter 焦点遍历的副作用：不掉出编辑态、不清理围栏聚焦态。

    真机上失焦与按键同帧到达（在重渲染 / 焦点回收之前），故这里把两个回调放在
    同一次交互里顺序触发，复现该时序。
    """
    blur_rec = _Recorder()
    with _rendered(blur=blur_rec) as h:
        field = _enter_edit(h)
        listener = _edit_listener(h)
        pos = PARSED_CODE.index("def main()")
        _caret(h, field, pos, pos)

        def _tab_then_blur() -> None:
            listener.on_key_down(_key_event("Tab"))
            edit_field.on_blur(None)

        edit_field = _edit_fields(h)[0]
        h.interact(_tab_then_blur)

        assert blur_rec.calls == [], "把遍历副作用当成了真实失焦（会清掉撤销会话）"
        assert _edit_fields(h), "Tab 后被踢出了编辑态"
        assert _code_text(h) is not None, "编辑态应保留底层高亮层"


def test_real_blur_still_exits_edit_mode():
    """非 Tab 引起的失焦仍正常退出编辑态（抑制只作用于紧接着 Tab 的那一次）。"""
    blur_rec = _Recorder()
    with _rendered(blur=blur_rec) as h:
        field = _enter_edit(h)
        h.interact(field.on_blur, None)
    assert blur_rec.calls == [(0,)]
    assert _code_text(h) is not None
    assert not _edit_fields(h)


# ==================== 6. 头部工具栏紧凑性 ====================


def _subtree(root) -> Iterator:
    """头部子树遍历：在 harness.walk 之上补 `items`。

    `harness.walk` 只顺着 controls / content / actions / title 下钻，而语言菜单的
    `PopupMenuItem` 只存在于 `PopupMenuButton.items` 里，不补这一步就遍历不到。
    """
    for node in walk(root):
        yield node
        items = getattr(node, "items", None)
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, ft.BaseControl):
                    yield from _subtree(item)


def _header(h: RenderHarness) -> ft.Row:
    """头部工具栏：按高度常量定位（不猜控件顺序）。"""
    rows = [
        n
        for n in h.find(lambda n: isinstance(n, ft.Row))
        if getattr(n, "height", None) == blk._HEADER_H
    ]
    assert rows, f"未找到定高为 {blk._HEADER_H} 的头部工具栏"
    return rows[0]


def _lang_button(h: RenderHarness) -> ft.PopupMenuButton:
    btns = h.find(lambda n: isinstance(n, ft.PopupMenuButton))
    assert btns, "头部未挂载语言选择器"
    return btns[0]


def _header_icon_btns(h: RenderHarness) -> list[ft.Container]:
    """头部紧凑图标按钮：定见方 + ink 水波 + 单个 Icon。"""
    return [
        n
        for n in _subtree(_header(h))
        if isinstance(n, ft.Container)
        and n.ink
        and n.width == blk._HEADER_H
        and n.height == blk._HEADER_H
    ]


def test_header_row_height_is_locked_to_compact_token():
    """头部行高被显式锁定。

    行高由**最高子项**决定，不锁就会被 Material 的固有尺寸顶回去——这正是
    "顶部行过高"的成因，跟内边距无关（真机探针：压内边距后行高纹丝不动）。
    """
    with _rendered() as h:
        assert _header(h).height == blk._HEADER_H, "头部行高未锁定在紧凑高度"


def test_header_has_no_material_min_tap_target_controls():
    """头部不得出现 IconButton / Dropdown（含菜单项内容里的）。

    真机实测高度：IconButton 40（`visual_density=COMPACT` 也只降到 32）、
    Dropdown **恒为 48** —— 即使 `dense=True` / `text_size=12` / 内边距归零。
    且 Dropdown 用 `height=` 强压会**裁切其文字**（压到 24 时文字溢出到下一行标签上），
    所以它只能整体换掉，不能压。这条守住"换掉"的结论。
    """
    with _rendered() as h:
        for node in _subtree(_header(h)):
            assert not isinstance(
                node, (ft.IconButton, ft.Dropdown)
            ), f"头部出现 {type(node).__name__}：其固有高度会顶大行高"


def test_header_children_declare_explicit_height_within_cap():
    """每个定高子项都不超过 `_HEADER_H`。

    头部行高是**硬锁**的，超过该值的子项会被静默裁切（视觉损坏、无报错）。
    这条守住"硬锁不会切到东西"这一前提，也就守住了上一条的替换是安全的。
    """
    with _rendered() as h:
        sized = [
            n
            for n in _subtree(_header(h))
            if isinstance(n, (ft.Container, ft.PopupMenuButton))
            and getattr(n, "height", None) is not None
        ]
        assert len(sized) >= 3, f"头部定高子项过少（{len(sized)}），结构可能已变"
        for node in sized:
            assert node.height <= blk._HEADER_H, (
                f"{type(node).__name__} 高 {node.height} > {blk._HEADER_H}，"
                "会被头部硬锁裁切"
            )


def test_header_icon_buttons_are_compact_and_clickable():
    """折叠 / 复制用固定尺寸 Container（项目紧凑按钮惯例），不是 IconButton。"""
    with _rendered() as h:
        btns = _header_icon_btns(h)
        tooltips = [n.tooltip for n in btns]
        assert tooltips == ["折叠", "复制代码"], f"头部图标按钮不符：{tooltips}"
        for node in btns:
            assert node.on_click is not None, "图标按钮没有单击回调"
        assert len(h.find(lambda n: isinstance(n, ft.IconButton))) == 0


def test_header_shows_line_count_and_no_decoration_icon():
    """头部信息构成：折叠 / 语言 / 行数 / 复制；无装饰性冗余图标。"""
    with _rendered() as h:
        header = _header(h)
        texts = [
            getattr(n, "value", None)
            for n in _subtree(header)
            if isinstance(n, ft.Text)
        ]
        assert f"{len(LOGICAL_LINES)} 行" in texts, f"缺少行数标签：{texts}"
        assert any(isinstance(n, ft.PopupMenuButton) for n in _subtree(header))
        assert not any(
            isinstance(n, ft.Icon) and n.icon == ft.Icons.DATA_OBJECT
            for n in _subtree(header)
        ), "装饰性图标应已移除以保持头部紧凑"


def test_language_trigger_shows_display_name_not_fence_key():
    """语言标签显示展示名（Python），而不是围栏里的标识（python）。"""
    with _rendered() as h:
        texts = [
            getattr(n, "value", None)
            for n in _subtree(_lang_button(h).content)
            if isinstance(n, ft.Text)
        ]
        assert "Python" in texts, f"语言标签未显示展示名：{texts}"
        assert "python" not in texts, "语言标签显示了围栏标识而非展示名"


def test_language_menu_lists_common_langs_and_checks_current():
    """菜单覆盖常用语言、项高已压缩、且恰好勾选当前语言。"""
    with _rendered() as h:
        items = _lang_button(h).items
        assert len(items) == len(blk._COMMON_LANGS), "语言菜单项数与常用清单不符"
        checked = [i for i in items if i.checked]
        assert len(checked) == 1, "应当只有当前语言被勾选"
        assert getattr(checked[0].content, "value", None) == "Python"
        assert all(i.height < 48 for i in items), "菜单项未压缩（默认 48，26 项会很长）"


def test_language_menu_click_forwards_key_to_on_change_lang():
    """选中菜单项 → on_change_lang(line_idx, 标识)，契约与旧 Dropdown 一致。"""
    rec = _Recorder()
    with _rendered(change_lang=rec) as h:
        items = _lang_button(h).items
        go = [i for i in items if getattr(i.content, "value", None) == "Go"]
        assert go, "语言菜单里没有 Go 选项"
        h.interact(go[0].on_click, None)
    assert rec.calls == [(0, "go")], f"选语言回传不符契约：{rec.calls}"


def test_language_menu_appends_unknown_current_lang():
    """围栏写了清单外的标识（如 py3）时：末位追加并勾选，用户能看到当前状态。"""
    with _rendered(raw="```py3\n" + RAW_CODE + "```") as h:
        items = _lang_button(h).items
        assert len(items) == len(blk._COMMON_LANGS) + 1, "未追加清单外的当前语言"
        last = items[-1]
        assert last.checked is True, "清单外的当前语言未被勾选"
        assert getattr(last.content, "value", None) == "py3"


def test_lang_display_maps_known_and_passes_unknown_through():
    """展示名映射：空标识 → Plain text，未知标识原样回显（别名不等于无语言）。"""
    assert blk._lang_display("") == "Plain text"
    assert blk._lang_display("python") == "Python"
    assert blk._lang_display("cpp") == "C++"
    assert blk._lang_display("py3") == "py3"


def test_lang_entries_appends_unknown_current_only():
    """`_lang_entries`：常用清单打底，仅在必要时追加当前语言，不改动原清单。"""
    assert blk._lang_entries("python") == blk._COMMON_LANGS
    assert blk._lang_entries("") == blk._COMMON_LANGS
    entries = blk._lang_entries("py3")
    assert entries[:-1] == blk._COMMON_LANGS
    assert entries[-1] == ("py3", "py3")


def test_collapse_button_switches_to_preview():
    """折叠按钮切到首行预览：正文（高亮层 / 编辑框）卸载。"""
    with _rendered() as h:
        collapse = [n for n in _header_icon_btns(h) if n.tooltip == "折叠"]
        assert collapse, "未找到折叠按钮"
        h.interact(collapse[0].on_click, None)
        assert _code_text(h) is None, "折叠后仍渲染了正文高亮层"
        assert not _edit_fields(h), "折叠后不应有编辑框"


# ==================== 6. 点击定位（光标落在点击处，而非代码块末尾） ====================
#
# 缺陷背景：点击任意位置进入编辑态时，编辑框以 `selection=None` 新建，Flutter 给
# 新建且将获焦的 TextField 的默认选区是**文末** —— 表现为"点哪都跳到代码块最后
# 一行"。修复方式是把点击坐标映射成字符偏移，随渲染参数下发为 `selection`。
#
# 映射分两级：行（由每行的点击容器精确给出）→ 行内（y 定视觉行、x 定列）。


def _row_click_containers(h: RenderHarness) -> list[ft.Container]:
    """逻辑行级点击容器：`Container` 且 content 是 `Row`。

    浏览态每个逻辑行外面套一层（承载点击定位）；块级容器（wrap_block 的 on_click）
    的 content 是 Column，因此不会被这个判据误收。
    """
    return [
        n
        for n in h.find(lambda n: isinstance(n, ft.Container))
        if getattr(n, "on_click", None) is not None
        and isinstance(getattr(n, "content", None), ft.Row)
    ]


def _row_start_offsets() -> list[int]:
    """每个逻辑行在代码全文中的起始偏移（逐行累加 len+1 个换行符）。"""
    out, acc = [], 0
    for text in LOGICAL_LINES:
        out.append(acc)
        acc += len(text) + 1
    return out


def _tap_row(h: RenderHarness, row_idx: int, x: float, y: float) -> None:
    """在指定逻辑行上模拟一次**真实点击**（x 从**行左缘**起算，含行号列）。

    真机的事件序列是「`on_tap_down`（`TapEvent`，**带坐标**）→ `on_click`
    （`ControlEvent`，**不带坐标**）」，这里逐字复现：坐标只喂给 tap_down，
    on_click 只给一个无坐标事件。这正是原缺陷的形状——`ft.Container.on_click`
    的声明是 `ControlEventHandler`，真机实测 `local=None`；位置若只能从 on_click
    取，映射永远拿不到输入，光标就被 Flutter 甩到文末。
    """
    rows = _row_click_containers(h)
    assert len(rows) == len(LOGICAL_LINES), (
        f"行级点击容器数应为逻辑行数 {len(LOGICAL_LINES)}，实际 {len(rows)}"
    )
    row = rows[row_idx]
    tap_down = getattr(row, "on_tap_down", None)
    assert tap_down is not None, "行容器必须绑定 on_tap_down —— 坐标只在该事件上"
    h.interact(tap_down, types.SimpleNamespace(local_position=ft.Offset(x, y)))
    # ControlEvent 的等价物：只有 name/control，没有 local_position
    h.interact(row.on_click, types.SimpleNamespace(name="click"))


def _settle_edit_focus(h: RenderHarness) -> None:
    """模拟编辑框取得焦点，促成**第二次**渲染（光标在那一次才被下发）。

    挂载当次不能下发 selection：新建的编辑框在本帧末尾才拿到焦点，Flutter 取得焦点
    时会把光标重置到文末，挂载时带的 selection 会被覆盖（真机实测：TTF 收到
    `selection=14`，客户端却回报 `base=38`）。故真实时序是
    「挂载（selection=None）→ on_focus → 重渲染（带 selection）」，这里复现第二步。
    """
    fields = _edit_fields(h)
    assert fields, "未进入编辑态"
    handler = getattr(fields[0], "on_focus", None)
    assert handler is not None, "编辑框未绑定 on_focus"
    h.interact(handler, types.SimpleNamespace(control=fields[0]))


def _caret_offset_after_tap(h: RenderHarness, row_idx: int, x: float, y: float) -> int:
    """点击某行、等编辑框聚焦后，编辑框收到的光标偏移。"""
    _tap_row(h, row_idx, x, y)
    _settle_edit_focus(h)
    fields = _edit_fields(h)
    assert fields, "点击代码行后未进入编辑态"
    sel = fields[0].selection
    assert sel is not None, (
        "点击后未下发光标位置 —— 会退化为 Flutter 默认的文末（原缺陷）"
    )
    return int(sel.base_offset)


def _text_x(h: RenderHarness, text_px: float) -> float:
    """代码文本内的 x 换算成行容器坐标（加上行号列宽与 Row 间距）。"""
    return _gutter_cell_w(h) + Spacing.MD + text_px


def test_click_places_caret_on_clicked_row_not_block_end():
    """回归守护：点击中间行 → 光标落在**该行**，而不是代码块末尾。

    这是本次修复的核心：原实现在任何位置点击都把光标甩到文末（点第 3 行也跳到
    最后一行），用户无法用鼠标定位到想改的那一行。
    """
    starts = _row_start_offsets()
    with _rendered() as h:
        off = _caret_offset_after_tap(h, 2, _text_x(h, 0.0), 0.0)
    assert off == starts[2], f"光标未落在第 3 行起点：{off} != {starts[2]}"
    assert off != len(PARSED_CODE), "光标落到了代码块末尾（原缺陷未修复）"


def test_click_on_every_row_start_lands_on_that_row():
    """逐行点击行首：每一行都精确命中（含空行）。"""
    starts = _row_start_offsets()
    for row_idx, expected in enumerate(starts):
        with _rendered() as h:
            off = _caret_offset_after_tap(h, row_idx, _text_x(h, 0.0), 0.0)
        assert off == expected, f"第 {row_idx + 1} 行行首应得 {expected}，实得 {off}"


def test_click_on_gutter_falls_to_row_start():
    """点行号列（x 在代码文本左缘之外）→ 落在该行行首。"""
    starts = _row_start_offsets()
    with _rendered() as h:
        off = _caret_offset_after_tap(h, 3, 0.0, 0.0)
    assert off == starts[3], f"点行号列应落在行首 {starts[3]}，实得 {off}"


def test_click_x_uses_midpoint_snap():
    """列命中用中点吸附：点在字符左半 → 落在它之前，右半 → 落在它之后。

    字宽 = 0.5em + 字距补偿 0.25（FONT_MONO 的拉丁 advance 实测恒为 0.5em）。
    """
    adv = 16 * 0.5 + 0.25
    with _rendered() as h:
        left_half = _caret_offset_after_tap(h, 0, _text_x(h, adv * 2 + adv * 0.25), 0.0)
    with _rendered() as h:
        right_half = _caret_offset_after_tap(h, 0, _text_x(h, adv * 2 + adv * 0.75), 0.0)
    assert left_half == 2, f"点在 3 号字左半应落在其前（偏移 2），实得 {left_half}"
    assert right_half == 3, f"点在 3 号字右半应落在其后（偏移 3），实得 {right_half}"


def test_click_past_line_end_clamps_to_that_line_end():
    """x 超出该行右端 → 落在**该行**末尾，不是代码块末尾（关键区分）。"""
    with _rendered() as h:
        off = _caret_offset_after_tap(h, 0, _text_x(h, 9999.0), 0.0)
    assert off == len(LOGICAL_LINES[0]), f"应落在第 1 行末尾，实得 {off}"


def test_click_without_local_position_degrades_to_plain_enter_edit():
    """事件缺 `local_position`（非真机来源）→ 只进入编辑态，不猜位置。

    这条同时守住既有调用约定：块级容器与测试都用 `on_click(None)` 进入编辑态。
    """
    with _rendered() as h:
        _tap_row_eventless(h)
        assert _edit_fields(h), "无坐标事件也应能进入编辑态"


def _tap_row_eventless(h: RenderHarness) -> None:
    """以 `None` 事件触发行级点击（模拟无坐标来源）。"""
    rows = _row_click_containers(h)
    h.interact(rows[0].on_click, None)


# ---------- 纯函数：_caret_offset_in_code ----------


def test_caret_offset_pure_function_maps_row_and_column():
    """纯函数映射：行起点 + 行内列，逐行累加换行符。"""
    code = "abc\ndef\n\nxyz"
    # 行首：x 在文本左缘之外一律落到该行首
    assert blk._caret_offset_in_code(code, 0, -5.0, 0.0, 16, float("inf")) == 0
    assert blk._caret_offset_in_code(code, 1, -5.0, 0.0, 16, float("inf")) == 4
    assert blk._caret_offset_in_code(code, 2, -5.0, 0.0, 16, float("inf")) == 8
    assert blk._caret_offset_in_code(code, 3, -5.0, 0.0, 16, float("inf")) == 9
    # 空行只有偏移 0（浏览态用空格 span 撑行盒，没有可落字的列）
    assert blk._caret_offset_in_code(code, 2, 500.0, 0.0, 16, float("inf")) == 8


def test_caret_offset_pure_function_out_of_range_row_is_clamped():
    """行号越界钳制到首/末行（点击事件与重渲染之间文档可能已变短）。"""
    code = "abc\ndef"
    assert blk._caret_offset_in_code(code, -3, -5.0, 0.0, 16, float("inf")) == 0
    assert blk._caret_offset_in_code(code, 99, -5.0, 0.0, 16, float("inf")) == 4


def test_caret_offset_never_exceeds_code_length():
    """任何坐标下返回的偏移都在 [0, len(code)] 内（可直接用于 TextSelection）。"""
    code = "abc\ndefgh\nij"
    for row in range(3):
        for x in (-50.0, 0.0, 5.0, 500.0):
            for y in (-10.0, 0.0, 24.0, 500.0):
                off = blk._caret_offset_in_code(code, row, x, y, 16, float("inf"))
                assert 0 <= off <= len(code), f"越界：row={row} x={x} y={y} -> {off}"


def test_caret_offset_wrapped_row_second_visual_line_is_later_in_row():
    """折行的行：点第 2 个视觉行应落在该行内更靠后的位置（同一 x 下）。

    折行行不套用"每行一个视觉行"，故 y 必须参与映射；这里只断言"更靠后"这一
    不依赖具体断点位置的相对性质，避免把折行算法的实现细节写进测试。
    """
    row_text = "word " * 60
    code = row_text.rstrip()
    line_h = blk._code_line_h(16)
    # 可用宽度取小值，确保该行折行
    first = blk._caret_offset_in_code(code, 0, 0.0, 0.0, 16, 200.0)
    second = blk._caret_offset_in_code(code, 0, 0.0, line_h + 1.0, 16, 200.0)
    assert first == 0
    assert second > first, "第 2 个视觉行应落在该行内更靠后处"
    assert second < len(code), "不应越过该行末尾"


def test_caret_offset_wrapped_row_last_visual_line_reaches_row_end():
    """折行的行：点最后一个视觉行，x 超出右端 → 落在该行末尾。"""
    row_text = "word " * 60
    code = row_text.rstrip()
    line_h = blk._code_line_h(16)
    last_y = line_h * 20  # 远超该行视觉行数 → 钳制到最后一行的 y 区间
    off = blk._caret_offset_in_code(code, 0, 9999.0, last_y, 16, 200.0)
    assert off == len(code), f"应钳到该行末尾，实得 {off}"


def test_caret_offset_line_height_matches_render_contract():
    """行高换算 = round(字号 × 1.5)，与浏览态行盒 / 编辑态 strut 同源。"""
    assert blk._code_line_h(16) == round(16 * blk._CODE_LINE_HEIGHT) == 24
    assert blk._code_line_h(12) == 18


def test_click_row_containers_cover_every_logical_line():
    """每个逻辑行都有独立的点击容器 —— 行定位靠它精确给出，不能少也不能多。"""
    with _rendered() as h:
        assert len(_row_click_containers(h)) == len(LOGICAL_LINES)


def test_click_position_survives_word_wrap_off():
    """关闭换行：每行仍是单视觉行，列映射不依赖行高。"""
    with _rendered(word_wrap=False) as h:
        off = _caret_offset_after_tap(h, 2, _text_x(h, 0.0), 0.0)
    assert off == _row_start_offsets()[2]


# ---------- 两段式：位置取 on_tap_down、动作取 on_click、光标在聚焦后下发 ----------


def test_click_carries_position_through_tap_down_then_click():
    """整条真机链路：tap_down 记位置 → on_click 用位置 → 聚焦后下发光标。

    回归守护本次缺陷的**根因链**（任一环断掉都会退化成"点哪都到文末"）：
    1. `Container.on_click` 收到的是无坐标的 `ControlEvent`（真机实测 `local=None`），
       位置只能由 `on_tap_down`（`TapEvent`）提供；
    2. 挂载当次下发的 selection 会被 Flutter 的聚焦动作覆盖，必须等聚焦后重发一次。
    """
    starts = _row_start_offsets()
    assert starts[1] != len(PARSED_CODE), "取样行不该是代码块末行，否则断不出缺陷"
    with _rendered() as h:
        rows = _row_click_containers(h)
        # 只喂坐标给 tap_down（真实事件就是这么来的）
        h.interact(
            rows[1].on_tap_down,
            types.SimpleNamespace(local_position=ft.Offset(_text_x(h, 0.0), 3.0)),
        )
        h.interact(rows[1].on_click, types.SimpleNamespace(name="click"))
        # ① 挂载当次：不下发光标（下发也会被聚焦覆盖）
        assert _edit_fields(h)[0].selection is None, "挂载当次不应下发 selection"
        # ② 聚焦后：光标补发到点击处（第 2 行行首）
        _settle_edit_focus(h)
        sel = _edit_fields(h)[0].selection
    assert sel is not None, "聚焦后仍未下发光标"
    assert int(sel.base_offset) == starts[1], f"应落第 2 行行首 {starts[1]}，实得 {sel}"


def test_tap_down_alone_does_not_enter_edit():
    """只按下（`on_tap_down`）不进编辑态：在代码块上拖动滚动文档不应误入编辑。

    这是"位置与动作分成两个 handler"的直接收益——`on_tap_down` 在按下瞬间就触发，
    被判定为拖动时也会触发；若在那里直接进编辑态，拖动滚动就会误入编辑。
    进编辑态必须交给 `on_click`（抬起且未被取消）。
    """
    with _rendered() as h:
        rows = _row_click_containers(h)
        tap_down = rows[0].on_tap_down
        h.interact(tap_down, types.SimpleNamespace(local_position=ft.Offset(40.0, 5.0)))
        assert not _edit_fields(h), "仅 tap_down 就进了编辑态（拖动会误入编辑）"


def test_caret_is_dispatched_only_after_focus():
    """两段式下发：挂载时 `selection is None`，聚焦重渲染后才等于映射结果。"""
    starts = _row_start_offsets()
    with _rendered() as h:
        _tap_row(h, 1, _text_x(h, 0.0), 0.0)
        assert _edit_fields(h)[0].selection is None, "挂载当次不应下发 selection"
        _settle_edit_focus(h)
        sel = _edit_fields(h)[0].selection
    assert sel is not None, "聚焦后仍未下发光标（缺陷未修复）"
    assert int(sel.base_offset) == starts[1], f"应落第 2 行行首 {starts[1]}，实得 {sel}"


def test_stale_tap_position_is_not_reused_by_another_row():
    """快照只对**同一行**生效：行号不符时退化为"只进编辑态"，绝不猜位置。

    这样即使某次 `on_tap_down`（例如按下后转为拖动、tap 被取消）留下的快照过期，
    也不会被后续另一行的 `on_click` 误用成"光标跳到别处"。
    """
    with _rendered() as h:
        rows = _row_click_containers(h)
        # 先在第 3 行留下一个快照（模拟"按下后拖动、tap 被取消"）
        h.interact(rows[2].on_tap_down,
                   types.SimpleNamespace(local_position=ft.Offset(40.0, 1.0)))
        # 随后第 1 行的 on_click 到来（没有自己的 tap_down）→ 不得复用第 3 行的位置
        h.interact(rows[0].on_click, types.SimpleNamespace(name="click"))
        assert _edit_fields(h), "无可用坐标也应能进入编辑态"
        assert _edit_fields(h)[0].selection is None, "过期快照被误用为光标位置"



# ==================== 7. 桌面编辑器交互强化 ====================
#
# 本节锁三件"只有真机能问出答案"的事（都先有探针实测，再写成测试）：
#
# a. **行号不参与选区**：行号是 `ft.Text`，外层 `SelectionArea` 会把子树里所有
#    `RenderParagraph` 纳入选区，`selectable=False` 并不能退出选区（项目里正文/公式
#    早就在用 `selectable=False`，它们照样能被拖选）。真机探针把 6 种渲染方式并列
#    放进同一个 SelectionArea 做真实拖选、再读 `on_change` 的纯文本，只有
#    `selectable=True` 系列与 canvas 文本不出现在选区文本里。
#
# b. **Esc 退出编辑态**：原生多行 `TextField` 不吞非编辑类按键（Tab 能被组件内嵌的
#    `KeyboardListener` 收到即为证），Esc 因此可以在组件内处理，不必新增全局动作，
#    也不必把 `is_editing` 暴露给外部。
#
# c. **外部进入请求**（方向键从代码块外进入）：`enter_seq` 是"只增序号"，组件按
#    "变了才消费"判定，消费后无需回写清理，也不会被父层无关的重渲染重复触发。


def _gutter_texts(h: RenderHarness) -> list[ft.Text]:
    """行号列文本控件（纯数字内容的 Text，按渲染顺序）。"""
    return [
        n
        for n in h.find(lambda n: isinstance(n, ft.Text))
        if isinstance(n.value, str) and n.value.isdigit()
    ]


def test_gutter_numbers_opt_out_of_outer_selection_area():
    """行号必须 `selectable=True`，否则跨行拖选会连带把行号复制走。

    锁的是"唯一开关"本身：一旦有人把行号改回普通 `ft.Text`（或改成
    `selectable=False` 这种看着像"不可选"、实际仍会被选区带走的写法），
    跨行复制代码就会重新混入 1/2/3。
    """
    with _rendered() as h:
        nums = _gutter_texts(h)
    assert nums, "未找到行号文本"
    for t in nums:
        assert t.selectable is True, (
            f"行号 {t.value!r} 未启用 selectable —— 外层 SelectionArea 会把它一起选走"
        )
        assert t.enable_interactive_selection is False, (
            f"行号 {t.value!r} 仍允许交互选择（应只「摘出去」，不该自己产生选区）"
        )


def test_gutter_column_has_separator_border():
    """行号列与代码之间有一条竖分隔线（装订线）。"""
    with _rendered() as h:
        borders = [
            n.border
            for n in h.find(lambda n: isinstance(n, ft.Container))
            if getattr(n, "border", None) is not None
        ]
    assert any(getattr(b, "right", None) is not None for b in borders), (
        "未找到行号列的右分隔线"
    )


def test_escape_key_exits_edit_mode():
    """编辑态按 Esc → 回到高亮浏览态，并转发 on_code_blur（撤销会话收束）。"""
    blur_rec = _Recorder()
    with _rendered(blur=blur_rec) as h:
        _enter_edit(h)
        assert _edit_fields(h), "前置条件：应已进入编辑态"
        _press(h, _edit_listener(h), "escape")
        assert not _edit_fields(h), "Esc 未退出编辑态"
        assert _code_text(h) is not None, "Esc 后应回到高亮浏览态"
    assert blur_rec.calls == [(0,)], f"Esc 退出应转发 on_code_blur，实得 {blur_rec.calls}"


def test_escape_key_does_not_touch_document():
    """Esc 只切状态、不改文档（不得顺带触发 on_change_code）。"""
    change_rec = _Recorder()
    with _rendered(change=change_rec) as h:
        _enter_edit(h)
        _press(h, _edit_listener(h), "escape")
    assert change_rec.calls == [], f"Esc 不应改写文档，实得 {change_rec.calls}"


def test_enter_request_enters_edit_mode():
    """外部进入请求（方向键跨界）→ 组件切到编辑态。"""
    with _rendered(enter_seq=1, enter_off=0) as h:
        assert _edit_fields(h), "enter_seq 请求未进入编辑态"


def test_enter_request_without_seq_does_not_enter_edit():
    """`enter_seq=0`（无请求）绝不进编辑态——否则文档一渲染代码块就自己被打开。"""
    with _rendered(enter_seq=0, enter_off=0) as h:
        assert not _edit_fields(h), "无请求却进入了编辑态"


def test_enter_request_places_caret_at_requested_offset():
    """请求携带的初始偏移经"聚焦后补发"落到编辑框（↓=0、↑=len(code)）。"""
    with _rendered(enter_seq=1, enter_off=5) as h:
        assert _edit_fields(h), "前置条件：应已进入编辑态"
        _settle_edit_focus(h)
        sel = _edit_fields(h)[0].selection
    assert sel is not None, "进入编辑态后未下发光标"
    assert int(sel.base_offset) == 5, f"光标应落 5，实得 {sel.base_offset}"


def test_enter_request_reports_consumption_to_owner():
    """兑现请求后必须回报请求方一次（父层据此作废那条一次性待办）。

    这是"请求只被消费一次"的真正机制：effect 只在 `enter_seq` 变化时重跑，所以
    "同序号重渲染"根本不会二次消费——**会重复消费的是重建后重新挂载**，而组件
    重建时自己的任何标记都一并重生，只有持有请求的父层能拦住它。因此组件唯一的
    责任就是"兑现即回报"。
    """
    consumed = _Recorder()
    with _rendered(enter_seq=1, enter_off=0, consumed=consumed) as h:
        assert _edit_fields(h), "前置条件：应已进入编辑态"
    assert consumed.calls == [(0,)], f"兑现后应回报一次 (行号)，实得 {consumed.calls}"


def test_enter_request_without_seq_reports_nothing():
    """无请求（enter_seq=0）→ 不得回报，否则父层会白白清掉别人的待办。"""
    consumed = _Recorder()
    with _rendered(enter_seq=0, enter_off=0, consumed=consumed):
        pass
    assert consumed.calls == [], f"无请求却回报了 {consumed.calls}"


def test_rebuilt_row_without_pending_request_stays_in_browse_mode():
    """**幽灵进入态**回归测试：请求被父层作废后，本行被重建不得再进编辑态。

    缺陷路径（真机可达）：编辑器按 `window` 只物化视口附近的行 → 请求若不作废，
    目标行永久带非 0 序号 → 该行滚出窗口被卸载、再滚回来重建时，挂载期 effect 会
    拿同一个旧序号再消费一次，代码块自己跳进编辑态并抢走焦点。

    这里分两步复刻：① 请求被兑现并回报（父层于是把状态清成 None）；
    ② 该行组件**重新挂载**（新的 state/ref，等价于滚回窗口重建），此时父层下发
    `enter_seq=0` → 必须停在浏览态。
    """
    consumed = _Recorder()
    with _rendered(enter_seq=1, enter_off=0, consumed=consumed) as h:
        assert _edit_fields(h), "前置条件：应已进入编辑态"
        assert consumed.calls, "前置条件：应已回报兑现"
        _press(h, _edit_listener(h), "escape")

    # ② 重建（等价于滚回渲染窗口）：父层已作废请求 → 下发 0
    with _rendered(enter_seq=0, enter_off=None) as h2:
        assert not _edit_fields(h2), "重建后自己跳进了编辑态（幽灵进入）"


def test_enter_request_is_not_reconsumed_by_unrelated_rerenders():
    """请求已兑现后，与本请求无关的重渲染（折叠 → 展开）不得再拽进编辑态。

    注意这条**不足以**证明"只消费一次"——它成立是因为 effect 的依赖（`enter_seq`）
    没变就不会重跑，而不是因为组件做了去重。真正守护"只消费一次"的机制见
    `test_enter_request_reports_consumption_to_owner` 与
    `test_rebuilt_row_without_pending_request_stays_in_browse_mode`。保留它是因为
    它锁住的是**用户可见行为**：Esc 退出后再点折叠/展开，不该被弹回编辑态。
    """
    with _rendered(enter_seq=1, enter_off=0) as h:
        assert _edit_fields(h), "前置条件：应已进入编辑态"
        _press(h, _edit_listener(h), "escape")
        assert not _edit_fields(h), "前置条件：Esc 应已退出编辑态"
        # 两次与请求无关的重渲染（折叠 → 展开）：序号未变 → 不得重新进入
        h.interact(
            next(n for n in _header_icon_btns(h) if n.tooltip == "折叠").on_click, None
        )
        h.interact(
            next(n for n in _header_icon_btns(h) if n.tooltip == "展开").on_click, None
        )
        assert not _edit_fields(h), "无关重渲染把用户重新拽进了编辑态"


def test_gutter_highlights_caret_line_in_edit_mode():
    """编辑态里光标所在逻辑行的行号提亮成强调色，其余行号保持暗色。"""
    light = get_colors(ft.ThemeMode.LIGHT)
    target = 2
    with _rendered() as h:
        field = _enter_edit(h)
        off = _row_start_offsets()[target]
        _caret(h, field, off, off)
        nums = _gutter_texts(h)
        assert len(nums) == len(LOGICAL_LINES)
        assert nums[target].color == light.link, "光标所在行号未提亮"
        others = [n for i, n in enumerate(nums) if i != target]
        assert all(n.color != light.link for n in others), "非当前行的行号不该提亮"


def test_gutter_highlight_absent_in_browse_mode():
    """浏览态没有"当前行"概念，所有行号都是暗色。"""
    light = get_colors(ft.ThemeMode.LIGHT)
    with _rendered() as h:
        nums = _gutter_texts(h)
    assert all(n.color != light.link for n in nums), "浏览态不应有提亮的行号"
