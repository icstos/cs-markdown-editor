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
"""

import sys
import types
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
from tests.harness import RenderHarness  # noqa: E402
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
):
    """在组件渲染上下文内渲染代码块（`ft.use_state` / `use_effect` 需要宿主）。"""
    line = _code_line()

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
                _noop,
                selection or _noop,
                _ref(None),
                active,
                False,
                None,
                diff_mark=None,
                word_wrap=word_wrap,
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
    """点击块级容器（wrap_block 的 on_click）进入编辑态，返回原生编辑框。"""
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
    return fields[0]


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
        h.interact(field.on_focus, None)
        assert focus_rec.calls == [(0,)]
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

