"""非 Markdown 文档在「与已选项目进行比较」里的表现（对齐 VSCode 的源码对比）。

背景：对比标签原先把两侧一律 `parse_markdown`。于是同一个 .py 文件在编辑标签里是
带语法高亮的代码块，在对比标签里却变成"`# 注释` 是一级标题、`- x` 是列表"的散文；
差异高亮又只挂在**文档行**上，而纯文本文档整篇只有一个文档行 —— 整块被染成一种
颜色，"哪一行不同"完全看不出来。

本文件按三层锁住修复，每层都是"改掉就被破坏"的不变量：

1. **解析一致**（services.document_loader.parse_document_text）：按扩展名分派，
   同一个文件在两条链路里必须是同一种渲染形态；编码 / 换行随文档一起带下去，
   否则对比标签保存会把 GBK 的 .py 写成 UTF-8（源码里 `# -*- coding: gbk -*-`
   直接失效）。
2. **对比装的是纯文本文档**（app._diff_controller）：diff 标签两侧 `plain=True`。
3. **差异高亮下沉到代码行**（views/code_block + 编辑器分流）：逐行底色 + 行号列
   标记条 + 占位行对齐；块级染色在纯文本下必须为空（否则整片代码被罩一层色）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft
import pytest

import parser
from app._diff_controller import build_diff_controller
from services.document_loader import load_document, parse_document_text
from services.file_io import read_text_detailed
from styles import get_colors
from tests.harness import RenderHarness, walk
from views import code_block as blk
from views.code_block import diff_line_palette
from views.diff_view import compute_changed_ranges, compute_diff_for_editors
from views.editor import MarkdownEditor

LEFT = "import os\n\ndef main():\n    print('hi')\n    return 0\n"
RIGHT = "import os\nimport sys\n\ndef main():\n    print('hello')\n    return 0\n"


class _Ref:
    def __init__(self, current=None):
        self.current = current


# ============================ 1. 解析按类型分派 ============================


def test_parse_document_text_routes_by_extension():
    """扩展名决定文档形态：源码/文本 → 纯文本单代码块；Markdown → 行结构。"""
    py = parse_document_text("a.py", LEFT)
    assert py.plain is True
    assert py.lines[0].lang == "python"
    assert py.file_path == "a.py"
    # 序列化不得带 ``` 围栏（否则保存会把围栏写进 .py）
    assert parser.serialize(py) == LEFT

    txt = parse_document_text("notes.txt", "第一行\n第二行\n")
    assert txt.plain is True
    assert txt.lines[0].lang == ""  # 纯文本：可打开但不需要分词

    md = parse_document_text("a.md", "# 标题\n\n正文\n")
    assert md.plain is False
    assert len(md.lines) > 1


def test_parse_document_text_keeps_eol_and_encoding():
    """编码 / 换行必须随文档落下去：对比标签两侧也可编辑保存，按原样写回是前提。"""
    doc = parse_document_text("a.py", LEFT, eol="\r\n", encoding="gb18030")
    assert doc.eol == "\r\n"
    assert doc.encoding == "gb18030"

    # 缺省值兜底（不能把 None 写进 Document 让保存链路炸掉）
    blank = parse_document_text("a.py", LEFT, eol="", encoding="")
    assert blank.eol == "\n"
    assert blank.encoding == "utf-8"


def test_parse_document_text_falls_back_to_markdown():
    """未知扩展名不抛错（兜底按 Markdown 解析）：对话框理论上已拦住，但对比链路
    不能因为一个意外的扩展名整条失败。"""
    doc = parse_document_text("weird.bin-ish", "# x\n")
    assert doc.plain is False
    assert doc.file_path == "weird.bin-ish"


def test_load_document_and_parse_document_text_agree(tmp_path):
    """读盘入口与"内存文本"入口对同一文件给出同一形态。

    这两条链路必须同源：对比标签走的是后者，普通编辑走的是前者，一旦分叉，
    同一个 .py 在两个标签里长得不一样（正是本需求的缺陷形状）。
    """
    p = tmp_path / "gbk.py"
    p.write_bytes("# -*- coding: gbk -*-\r\nprint('中文')\r\n".encode("gbk"))

    loaded = load_document(str(p))
    text, eol, encoding = read_text_detailed(str(p))
    parsed = parse_document_text(str(p), text, eol=eol, encoding=encoding)

    assert loaded.plain is parsed.plain is True
    assert loaded.lines[0].lang == parsed.lines[0].lang == "python"
    assert parser.serialize(loaded) == parser.serialize(parsed)
    # GBK 必须原样带出（否则保存会把带 coding 声明的源码改成 UTF-8）
    assert loaded.encoding == parsed.encoding
    assert loaded.encoding.lower().replace("-", "") in ("gb18030", "gbk")


# ============================ 2. 对比标签装的是什么 ============================


class _DiffCtx:
    """build_diff_controller 需要的最小装配（DiffEnv 的 12 个字段）。"""

    def __init__(self, tabs, compare_source):
        self.tabs_ref = _Ref(list(tabs))
        self.active_index_ref = _Ref(0)
        self.active_index_left_ref = _Ref(0)
        self.diff_active_pane_ref = _Ref(0)
        self.compare_source = compare_source
        self.snacks: list[str] = []
        self.appended: list[dict] = []

    def set_compare_source(self, path):
        self.compare_source = path

    def show_snack(self, msg):
        self.snacks.append(msg)

    def set_tabs(self, tabs):
        self.tabs_ref.current = list(tabs)

    def set_diff_active_pane(self, pane):
        self.diff_active_pane_ref.current = pane

    def activate_index(self, idx):
        self.active_index_ref.current = idx

    def bump_tab_session(self, idx):
        pass

    def append_and_activate(self, tab):
        self.appended.append(tab)
        self.tabs_ref.current = [*self.tabs_ref.current, tab]


def test_compare_builds_plain_documents_for_source_files(tmp_path):
    """两个 .py 做对比：两侧都必须是纯文本文档（而不是 Markdown 行结构）。"""
    left = tmp_path / "a.py"
    right = tmp_path / "b.py"
    left.write_text(LEFT, encoding="utf-8")
    right.write_text(RIGHT, encoding="utf-8")

    ctx = _DiffCtx([], str(left))
    ctl = build_diff_controller(ctx)
    ctl["compare_with_selected"](str(right))

    assert ctx.appended, "未创建对比标签"
    tab = ctx.appended[0]
    assert tab["type"] == "diff"
    assert tab["left_doc"].plain is True
    assert tab["right_doc"].plain is True
    assert tab["left_doc"].lines[0].lang == "python"
    assert tab["right_doc"].lines[0].lang == "python"
    # file_path 必须落到各自一侧（保存 / 状态栏 / 语言标签都依赖它）
    assert os.path.abspath(tab["left_doc"].file_path) == os.path.abspath(str(left))
    assert os.path.abspath(tab["right_doc"].file_path) == os.path.abspath(str(right))


def test_compare_prefers_open_tab_content_over_disk(tmp_path):
    """已打开的标签有未保存修改时，对比用的是内存内容（VSCode 同款语义）。

    顺带守住"内存内容也要按类型解析"：从标签里拿到的是 serialize 结果，
    对它再 parse_markdown 就会把 .py 变成散文。
    """
    p = tmp_path / "a.py"
    p.write_text(LEFT, encoding="utf-8")
    edited = LEFT.replace("print('hi')", "print('edited')")
    open_tab = {"type": "editor", "file_path": str(p), "document": parse_document_text(str(p), edited)}

    ctx = _DiffCtx([open_tab], str(p))
    ctl = build_diff_controller(ctx)
    assert ctl["get_text_for_compare"](str(p)) == edited

    right = tmp_path / "b.py"
    right.write_text(RIGHT, encoding="utf-8")
    ctl["compare_with_selected"](str(right))
    tab = ctx.appended[0]
    assert "print('edited')" in parser.serialize(tab["left_doc"])
    assert tab["left_doc"].plain is True


def test_compare_keeps_gbk_encoding_for_save(tmp_path):
    """GBK 的两个 .py 对比后，两侧文档仍记录原编码（保存不得静默转码）。"""
    left = tmp_path / "a.py"
    right = tmp_path / "b.py"
    left.write_bytes("# -*- coding: gbk -*-\r\nprint('甲')\r\n".encode("gbk"))
    right.write_bytes("# -*- coding: gbk -*-\r\nprint('乙')\r\n".encode("gbk"))

    ctx = _DiffCtx([], str(left))
    build_diff_controller(ctx)["compare_with_selected"](str(right))
    tab = ctx.appended[0]
    for side in ("left_doc", "right_doc"):
        enc = (tab[side].encoding or "").lower().replace("-", "")
        assert enc in ("gb18030", "gbk"), tab[side].encoding
        assert tab[side].eol == "\r\n"


# ============================ 3. 行级配色语义 ============================


def test_palette_side_switches_modified_color():
    """替换行：左边红（被改掉的旧内容）、右边绿（改出来的新内容）。

    两侧同色的话，"哪边是新内容"无从判断 —— 这正是替换行最需要表达的信息。
    """
    c = get_colors(ft.ThemeMode.LIGHT)
    left = diff_line_palette(c, 0)
    right = diff_line_palette(c, 1)

    assert left["modified"][0] == c.diff_del_bg
    assert right["modified"][0] == c.diff_add_bg
    # 纯增 / 纯删与侧别无关
    assert left["added"][0] == right["added"][0] == c.diff_add_bg
    assert left["removed"][0] == right["removed"][0] == c.diff_del_bg


def test_palette_gap_color_is_the_faint_counterpart():
    """占位行取"对侧发生了增 / 删"的浅色：左侧空白=对侧新增，右侧空白=对侧删除。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    assert diff_line_palette(c, 0)["gap"][0] == c.diff_gap_add_bg
    assert diff_line_palette(c, 1)["gap"][0] == c.diff_gap_del_bg


def test_palette_bar_colors_follow_git_semantics():
    """标记条颜色取 Git 语义色（新增绿 / 删除红），与侧边栏 / 状态栏同一套语言。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    assert diff_line_palette(c, 0)["removed"][1] == c.git["deleted"]
    assert diff_line_palette(c, 1)["added"][1] == c.git["added"]
    assert diff_line_palette(c, 1)["modified"][1] == c.git["added"]


# ============================ 4. 渲染：差异落在代码行上 ============================


def _render_editor(doc, marks=None, gaps=None, side=0, ranges=None):
    harness = RenderHarness()
    try:
        harness.render(
            MarkdownEditor,
            document=doc,
            settings={"word_wrap": True},
            show_toolbar=False,
            diff_marks=marks,
            diff_gaps=gaps,
            diff_ranges=ranges,
            diff_side=side,
        )
        return harness, harness.tree
    except Exception:
        harness.dispose()
        raise


def _bgcount(tree, color) -> int:
    return len([n for n in walk(tree) if getattr(n, "bgcolor", None) == color])


def _block_root(tree):
    """代码块的块级容器（wrap_block 的 key=line-0）。"""
    for n in walk(tree):
        if isinstance(n, ft.Container) and getattr(n, "key", None) == "line-0":
            return n
    raise AssertionError("未找到块级容器")


def test_plain_diff_colors_changed_lines_only():
    """纯文本文档：只有变了的代码行有底色，其余行与普通源码视图完全一样。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parse_document_text("a.py", LEFT)
    marks_l, marks_r, gaps_l, gaps_r = compute_diff_for_editors(LEFT, RIGHT)

    h, tree = _render_editor(doc, marks_l, gaps_l, side=0)
    try:
        assert _bgcount(tree, c.diff_del_bg) == 1, "被替换的那一行应当只有一行红底"
        assert _bgcount(tree, c.diff_add_bg) == 0
    finally:
        h.dispose()

    doc_r = parse_document_text("b.py", RIGHT)
    h, tree = _render_editor(doc_r, marks_r, gaps_r, side=1)
    try:
        # 右侧：1 行新增 + 1 行替换 = 2 行绿底
        assert _bgcount(tree, c.diff_add_bg) == 2
        assert _bgcount(tree, c.diff_del_bg) == 0
    finally:
        h.dispose()


def test_plain_diff_does_not_tint_whole_block():
    """块级染色必须为空：整篇一个文档行，块级着色会把整个文件罩成一片色。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parse_document_text("a.py", LEFT)
    marks_l, _marks_r, gaps_l, _gaps_r = compute_diff_for_editors(LEFT, RIGHT)

    h, tree = _render_editor(doc, marks_l, gaps_l, side=0)
    try:
        root = _block_root(tree)
        # 块容器的直接子层是"代码区"（底色 = 代码块底），不是 diff 色
        assert root.content.bgcolor == c.code_block_bg
        # 整块范围内只有一行带 diff 底色（不是一层包住整块的色块）
        assert _bgcount(tree, c.diff_del_bg) == 1
    finally:
        h.dispose()


def test_markdown_diff_still_tints_at_block_level():
    """对照组：Markdown 文档的差异依旧按文档行整块着色（本次改动不能顺手改掉）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parser.parse_markdown("```python\nx = 1\n```\n")
    h, tree = _render_editor(doc, {0: "added"}, None, side=1)
    try:
        root = _block_root(tree)
        assert root.content.bgcolor == c.diff_add_bg
    finally:
        h.dispose()


def test_plain_diff_inserts_gap_rows_for_alignment():
    """对侧多出来的行在本侧留等高占位，两侧同一处差异才会落在同一水平线上。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parse_document_text("a.py", LEFT)
    marks_l, _marks_r, gaps_l, _gaps_r = compute_diff_for_editors(LEFT, RIGHT)
    assert sum(len(v) for v in gaps_l.values()) == 1, "用例前提：左侧缺一行"

    h, tree = _render_editor(doc, marks_l, gaps_l, side=0)
    try:
        assert _bgcount(tree, c.diff_gap_add_bg) == 1
        # 占位行不带行号：行号列里仍是 1..6，没有多出一个数字
        numbers = [
            getattr(n, "value", None)
            for n in walk(tree)
            if getattr(n, "value", None) and str(getattr(n, "value", None)).isdigit()
        ]
        assert numbers == ["1", "2", "3", "4", "5", "6"]
    finally:
        h.dispose()


def test_plain_diff_gutter_marker_only_on_changed_lines():
    """行号列左缘的 3px 标记条只出现在变更行上（VSCode 的 gutter indicator）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parse_document_text("a.py", LEFT)
    marks_l, _mr, gaps_l, _gr = compute_diff_for_editors(LEFT, RIGHT)

    h, tree = _render_editor(doc, marks_l, gaps_l, side=0)
    try:
        bars = []
        for n in walk(tree):
            border = getattr(n, "border", None)
            left = getattr(border, "left", None) if border is not None else None
            if left is not None and getattr(left, "width", 0) >= 3:
                bars.append(getattr(left, "color", None))
        assert bars == [c.git["deleted"]]
    finally:
        h.dispose()


def test_plain_diff_drops_gap_rows_while_editing():
    """编辑态不插占位行：原生编辑框的文本是连续的，插了底色就会与字形错位。

    退出编辑后必须恢复（对齐是浏览态的读图体验，不能一进编辑就永久丢掉）。
    """
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parse_document_text("a.py", LEFT)
    marks_l, _mr, gaps_l, _gr = compute_diff_for_editors(LEFT, RIGHT)

    h = RenderHarness()
    try:
        h.render(
            MarkdownEditor,
            document=doc,
            settings={"word_wrap": True},
            show_toolbar=False,
            diff_marks=marks_l,
            diff_gaps=gaps_l,
            diff_side=0,
        )
        assert _bgcount(h.tree, c.diff_gap_add_bg) == 1

        entry = h.find(
            lambda n: getattr(n, "on_click", None) is not None
            and getattr(n, "key", None) == "line-0"
        )
        assert entry, "未找到块级容器的点击入口"
        h.interact(entry[0].on_click, None)
        assert h.find(lambda n: isinstance(n, ft.TextField)), "未进入编辑态"
        assert _bgcount(h.tree, c.diff_gap_add_bg) == 0, "编辑态仍然插入了占位行"

        # 失焦 → 回到浏览态 → 占位行回来
        field = h.find(lambda n: isinstance(n, ft.TextField))[0]
        h.interact(field.on_blur, None)
        assert _bgcount(h.tree, c.diff_gap_add_bg) == 1
    finally:
        h.dispose()


def test_plain_diff_without_marks_renders_like_normal_source_view():
    """没有差异数据时逐行不留任何底色（普通打开 .py 的观感不能被对比改掉）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    doc = parse_document_text("a.py", LEFT)
    h, tree = _render_editor(doc)
    try:
        for color in (c.diff_add_bg, c.diff_del_bg, c.diff_gap_add_bg, c.diff_gap_del_bg):
            assert _bgcount(tree, color) == 0
    finally:
        h.dispose()


@pytest.mark.parametrize("side", [0, 1])
def test_only_containers_carry_diff_backgrounds(side):
    """diff 底色只挂在行容器上：不能出现别的控件被顺带染色（例如头部 / 边框）。"""
    c = get_colors(ft.ThemeMode.LIGHT)
    text = LEFT if side == 0 else RIGHT
    doc = parse_document_text("x.py", text)
    marks_l, marks_r, gaps_l, gaps_r = compute_diff_for_editors(LEFT, RIGHT)
    marks = marks_l if side == 0 else marks_r
    gaps = gaps_l if side == 0 else gaps_r

    h, tree = _render_editor(doc, marks, gaps, side=side)
    try:
        palette = {c.diff_add_bg, c.diff_del_bg, c.diff_gap_add_bg, c.diff_gap_del_bg}
        tinted = [n for n in walk(tree) if getattr(n, "bgcolor", None) in palette]
        assert tinted, "没有任何差异底色"
        for n in tinted:
            # 只允许"行容器"：内容是一个 Row（行号列 + 代码列）
            assert isinstance(getattr(n, "content", None), ft.Row), type(n).__name__
    finally:
        h.dispose()


# ============================ 5. 行内字符级强调 ============================


def test_changed_ranges_only_cover_modified_lines():
    """字符区间只来自"改行"：纯增 / 纯删行整行就是变更，equal 行没有区间。"""
    ranges_l, ranges_r = compute_changed_ranges(LEFT, RIGHT)
    # LEFT/RIGHT：右侧多一行 import sys（insert），print 那行是改行
    assert 1 not in ranges_l, "纯新增行不该在左侧产生字符区间"
    assert 1 not in ranges_r, "纯新增行整行就是变更，不需要字符区间"
    assert ranges_l[3] == [(12, 13)]
    assert ranges_r[4] == [(12, 16)]


def test_changed_ranges_empty_when_identical():
    """内容相同时返回空 dict（调用方据此完全不叠加字符层）。"""
    assert compute_changed_ranges(LEFT, LEFT) == ({}, {})


def test_emphasize_spec_keeps_text_intact():
    """切片不得吞字：拼接结果必须与分词原文逐字相同。

    这条是"重排行号 / 丢字符"这类缺陷的唯一护栏——多切一刀少切一刀，
    用户看到的就是代码缺字，而且只在对比视图里出现，最难被发现。
    """
    from services.code_highlight import highlight_lines

    code = "    print('hello')  # 注释 emoji: X"
    spec = highlight_lines(code, "python")[0]
    for ranges in ([], [(0, 4)], [(4, 9)], [(0, len(code))], [(2, 5), (10, 14)]):
        out = blk._emphasize_spec(spec, ranges)
        assert "".join(t for t, _k, _e in out) == code
        # 强调态必须与区间一致：拼出被强调的文本
        emph_text = "".join(t for t, _k, e in out if e)
        want = "".join(code[i] for s, e2 in ranges for i in range(s, e2))
        assert emph_text == want


def test_emphasize_spec_clamps_out_of_range():
    """越界区间一律钳制（diff 尚未重算时区间可能比现行更长）。"""
    from services.code_highlight import highlight_lines

    spec = highlight_lines("abc", "python")[0]
    out = blk._emphasize_spec(spec, [(1, 99)])
    assert "".join(t for t, _k, _e in out) == "abc"


def test_plain_diff_emphasizes_changed_chars_inline():
    """改行在行底色之上再叠一层字符强调，且只强调真正改动的字符。"""
    marks_l, marks_r, gaps_l, gaps_r = compute_diff_for_editors(LEFT, RIGHT)
    ranges_l, ranges_r = compute_changed_ranges(LEFT, RIGHT)

    for doc_path, text, marks, gaps, ranges, side, want in (
        ("a.py", LEFT, marks_l, gaps_l, ranges_l, 0, ["i"]),
        ("b.py", RIGHT, marks_r, gaps_r, ranges_r, 1, ["ello"]),
    ):
        doc = parse_document_text(doc_path, text)
        h, tree = _render_editor(doc, marks, gaps, side=side, ranges=ranges)
        try:
            emph = [
                s.text
                for n in walk(tree)
                for s in (getattr(n, "spans", None) or [])
                if getattr(getattr(s, "style", None), "bgcolor", None)
            ]
            assert emph == want, emph
        finally:
            h.dispose()


def test_no_inline_emphasis_without_ranges():
    """没有字符区间时 span 不带任何底纹（普通文档 / 纯增删行的观感不变）。"""
    doc = parse_document_text("a.py", LEFT)
    marks_l, _mr, gaps_l, _gr = compute_diff_for_editors(LEFT, RIGHT)
    h, tree = _render_editor(doc, marks_l, gaps_l, side=0)
    try:
        styled = [
            s
            for n in walk(tree)
            for s in (getattr(n, "spans", None) or [])
            if getattr(getattr(s, "style", None), "bgcolor", None)
        ]
        assert styled == []
    finally:
        h.dispose()


def test_changed_ranges_skipped_for_huge_documents():
    """超大规模时放弃字符区间（行级底色照旧）：每次击键都会重算，必须设上限。"""
    from views.diff_view import _MAX_CHAR_DIFF_LINES

    n = _MAX_CHAR_DIFF_LINES  # 左右各一半 → 合计刚好触顶
    left = "\n".join(f"line_{i} = {i}" for i in range(n))
    right = left.replace("line_0 = 0", "line_0 = 1")
    assert compute_changed_ranges(left, right) == ({}, {})

    small = "\n".join(f"line_{i} = {i}" for i in range(10))
    assert compute_changed_ranges(small, small.replace("line_0 = 0", "line_0 = 1"))[0]


# ==================== 4. 恢复草稿与编辑标签同形（按类型解析） ====================


def _write_backup_file(tmp_path, name: str | None, content: str) -> str:
    """按真实备份流程落一份备份（含 original_path 元数据），返回备份路径。"""
    from services.backup import write_backup

    settings = {"backup_dir": str(tmp_path / "bk"), "backup_enabled": True}
    path = write_backup(settings, {"file_path": str(tmp_path / name) if name else None}, content)
    assert path, "备份未写入（write_backup 返回 None）"
    return path


def test_restore_backup_parses_py_as_plain_document(tmp_path):
    """恢复 .py 草稿必须还原成代码块，而不是被当 Markdown 拆成散文行。"""
    from services.recovery import load_backup_document

    backup = _write_backup_file(tmp_path, "a.py", LEFT)
    restored = load_backup_document(backup)
    assert restored is not None
    doc, cursor, _scroll = restored
    assert doc.plain is True
    assert doc.lines[0].lang == "python"
    assert len(doc.lines) == 1  # 整篇一个代码块
    assert parser.serialize(doc) == LEFT  # 不得凭空多出 ``` 围栏
    assert cursor == (1, 1)


def test_restore_backup_keeps_markdown_as_markdown(tmp_path):
    """Markdown 备份恢复后仍是行结构（历史行为不能被顺手改掉）。"""
    from services.recovery import load_backup_document

    backup = _write_backup_file(tmp_path, "a.md", "# 标题\n\n正文\n")
    restored = load_backup_document(backup)
    assert restored is not None
    doc, _cursor, _scroll = restored
    assert doc.plain is False
    assert len(doc.lines) > 1


def test_restore_untitled_backup_falls_back_to_markdown(tmp_path):
    """未命名草稿的备份没有 original_path，无从判断类型 → 按 Markdown（兜底）。"""
    from services.recovery import load_backup_document

    backup = _write_backup_file(tmp_path, None, "# 草稿\n\n正文\n")
    restored = load_backup_document(backup)
    assert restored is not None
    doc, _cursor, _scroll = restored
    assert doc.plain is False


def test_restore_missing_backup_returns_none(tmp_path):
    """备份不存在 / 损坏 → None（由调用方提示，不得抛异常打断恢复面板）。"""
    from services.recovery import load_backup_document

    assert load_backup_document(str(tmp_path / "nope.md.autosave")) is None


def test_open_backup_in_new_tab_loads_plain_document(tmp_path):
    """控制器恢复入口：新标签里的文档与「Ctrl+O 打开同一个 .py」形态一致。"""
    from app._backup_controller import build_backup_controller

    backup = _write_backup_file(tmp_path, "a.py", LEFT)
    appended: list[dict] = []
    snacks: list[str] = []
    recovery_flags: list[bool] = []
    ctx = types.SimpleNamespace(
        split_editor=False,
        active_pane_ref=types.SimpleNamespace(current=0),
        active_index_ref=types.SimpleNamespace(current=0),
        append_and_activate=lambda tab: appended.append(tab),
        set_recovery_open=lambda v: recovery_flags.append(v),
        show_snack=lambda *a, **k: snacks.append(a[0] if a else ""),
        settings={"backup_dir": str(tmp_path / "bk")},
        settings_ref=None,
        tabs_ref=types.SimpleNamespace(current=[]),
        recovery_list=[],
        set_recovery_list=lambda v: None,
        set_status_message=lambda *a, **k: None,
        save_doc=None,
        save_doc_sync=None,
        page_ref=types.SimpleNamespace(current=None),
        file_dialog=None,
        set_file_dialog=lambda v: None,
    )
    ctrl = build_backup_controller(ctx)
    ctrl["open_backup_in_new_tab"](backup)

    assert appended, "未新建标签页"
    tab = appended[0]
    assert tab["file_path"] is None  # 仍是未命名草稿（不覆盖原文件）
    assert tab["dirty"] is True
    assert tab["document"].plain is True
    assert tab["document"].lines[0].lang == "python"
    assert recovery_flags == [False]  # 恢复后关闭面板
    assert "5 行" in snacks[0]  # LEFT 正文共 5 行
