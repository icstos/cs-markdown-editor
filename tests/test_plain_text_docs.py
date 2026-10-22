"""任意文本文件（.txt / .py / .cpp …）在编辑器里的支持测试。

背景：编辑器原先只认 Markdown，其余文件一律交系统默认程序。现在扩展为
「凡是能当文本读的文件，都按**一个代码块**渲染」（需求原话：基于 Markdown 的
代码块语法形式渲染），于是多出一条完整链路：

    扩展名 → 语言标识（utils.text_formats）
      → 装载（services.document_loader：编码探测 + 换行识别）
      → 解析（parser.plain：整篇一个 CODE 行，plain=True）
      → 渲染（views.code_block：行号 / 高亮 / 折叠与编辑态的性能分支）
      → 写回（app._file_io_ops：原扩展名 + 原换行，绝不写 ``` 围栏）

本文件按这条链路分节覆盖，重点锁三类**会被静默破坏数据**的不变量：

1. **不写围栏**：`line.raw` 就是文件正文（不是 ```lang 形式），`serialize` 也
   不加围栏。这条一破，打开 .py 再保存就会把 ``` 写进源码文件。
2. **不毁结构**：纯文本文档只有一个块，`set_block` / `change_lang` /
   空块 Backspace 删除 / 边界方向键跳出这些"Markdown 语境下合理"的操作必须
   全部让路，否则会把源码文件变成一行标题、或凭空插入空段落行。
3. **往返不掉字节**：编码（GBK / BOM）与换行（CRLF）必须原样还原，否则
   git diff 全红、中文注释变问号。

另附渲染层：折叠入口必须隐藏（整篇一个块，折叠后没有任何行可落光标），
以及大文件编辑态的单层分支（`uses_simple_edit`，性能关键路径）。
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft
import pytest

import parser
from app._file_io_ops import _save_dialog_exts, _text_for_write
from models.document import BlockType, SegType
from services.document_loader import UnsupportedFileType, load_document
from services.file_io import read_text_auto, read_text_detailed
from tests.harness import RenderHarness, walk
from utils import text_formats
from views import code_block as blk
from views._editor_helpers import parse_draft_lines
from views.editor._blocks import build_blocks
from views.editor._fence import build_fence
from views.line_view import LineView

PY_SAMPLE = "import os\n\n\ndef main():\n    print('hi')\n"


class FakeRef:
    """伪 ft.Ref：避免 flet.Ref 的 weakref 限制。"""

    def __init__(self, current=None):
        self.current = current


# ============================ 1. 扩展名 → 语言 ============================


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("a.py", "python"),
        ("a.PY", "python"),
        ("a.cpp", "cpp"),
        ("a.c", "c"),
        ("a.hpp", "cpp"),
        ("main.rs", "rust"),
        ("run.sh", "bash"),
        ("x.ps1", "powershell"),
        ("index.html", "html"),
        ("data.json", "json"),
        ("conf.yaml", "yaml"),
        ("notes.txt", ""),          # 纯文本：可打开，不做分词
        ("access.log", ""),
        ("README.md", "markdown"),
        ("README", ""),             # 无扩展名的固定文件名
        ("Makefile", "makefile"),
        ("Dockerfile", "dockerfile"),
    ],
)
def test_lang_for_path(path, lang):
    """常见扩展名 / 固定文件名映射到 Pygments 语言标识。"""
    assert text_formats.lang_for_path(path) == lang


@pytest.mark.parametrize("path", ["pic.png", "a.zip", "app.exe", "doc.docx", "", None])
def test_lang_for_path_rejects_binary(path):
    """二进制 / 未知类型返回 None（= 不该进编辑器），与"纯文本返回空串"区分开。"""
    assert text_formats.lang_for_path(path) is None
    assert text_formats.is_supported_text_path(path) is False


def test_supported_and_plain_predicates():
    """.txt 是「可打开 + 纯文本」，.md 是「可打开但非纯文本（走行结构）」。"""
    assert text_formats.is_supported_text_path("a.txt") is True
    assert text_formats.is_plain_text_path("a.txt") is True
    assert text_formats.is_markdown_path("a.txt") is False

    assert text_formats.is_supported_text_path("a.md") is True
    assert text_formats.is_plain_text_path("a.md") is False
    assert text_formats.is_markdown_path("a.md") is True


def test_language_label():
    """状态栏展示名：常见语言给惯用写法，空标识即「纯文本」。"""
    assert text_formats.language_label("cpp") == "C++"
    assert text_formats.language_label("") == "纯文本"
    assert text_formats.language_label("python") == "Python"


# ============================ 2. 编码与换行读取 ============================


def test_read_text_auto_gbk(tmp_path):
    """中文 Windows 的 GBK 文件必须能读（否则 .txt 打开即报错）。"""
    p = tmp_path / "gbk.txt"
    p.write_bytes("中文注释\r\n第二行".encode("gbk"))
    text, eol = read_text_auto(str(p))
    assert text == "中文注释\n第二行"
    assert eol == "\r\n"


def test_read_text_auto_bom(tmp_path):
    """带 BOM 的 UTF-8：正文不含 BOM 字符，编码记为 utf-8-sig（写回不丢 BOM）。"""
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbfhello\n")
    text, _eol, encoding = read_text_detailed(str(p))
    assert text == "hello\n"
    assert encoding == "utf-8-sig"


def test_read_text_auto_crlf_normalized(tmp_path):
    """CRLF 归一为 \\n，换行符单独返回（保存时还原用）。"""
    p = tmp_path / "crlf.py"
    p.write_bytes(b"a = 1\r\nb = 2\r\n")
    text, eol = read_text_auto(str(p))
    assert "\r" not in text
    assert text == "a = 1\nb = 2\n"
    assert eol == "\r\n"


def test_read_text_auto_rejects_binary(tmp_path):
    """含 NUL 字节 → ValueError（调用方提示或交系统程序，不硬解成乱码）。"""
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError):
        read_text_auto(str(p))


# ============================ 3. 解析：整篇一个代码块 ============================


def test_parse_plain_text_shape():
    """不变量：单行 CODE、plain=True、raw 即正文（不是围栏形式）。"""
    doc = parser.parse_plain_text(PY_SAMPLE, "python")
    assert doc.plain is True
    assert len(doc.lines) == 1
    line = doc.lines[0]
    assert line.block_type == BlockType.CODE
    assert line.lang == "python"
    assert line.segments[0].seg_type == SegType.CODE
    assert line.raw == PY_SAMPLE
    assert line.segments[0].text == PY_SAMPLE
    assert "```" not in line.raw


def test_parse_plain_text_normalizes_newlines():
    """CRLF / 裸 CR 一律归一为 \\n（代码块按 \\n 切行算行号）。"""
    doc = parser.parse_plain_text("a\r\nb\rc\nd")
    assert doc.lines[0].raw == "a\nb\nc\nd"


def test_serialize_plain_has_no_fence():
    """序列化 = 原文（不含围栏、不追加换行）。保存路径直接用它。"""
    doc = parser.parse_plain_text(PY_SAMPLE, "python")
    assert parser.serialize(doc) == PY_SAMPLE


def test_serialize_markdown_fence_unchanged():
    """对照组：Markdown 围栏代码块仍按围栏序列化（纯文本改造没波及它）。"""
    md = "```python\nprint(1)\n```\n"
    assert parser.serialize(parser.parse_markdown(md)) == md


# ============================ 4. 装载层 ============================


def test_load_document_python(tmp_path):
    """.py → 纯文本文档，语言由扩展名给出。"""
    p = tmp_path / "main.py"
    p.write_text(PY_SAMPLE, encoding="utf-8")
    doc = load_document(str(p))
    assert doc.plain is True
    assert doc.file_path == str(p)
    assert doc.lines[0].lang == "python"
    assert doc.lines[0].raw == PY_SAMPLE


def test_load_document_markdown_is_not_plain(tmp_path):
    """Markdown 仍走行结构解析（plain=False，多行）。"""
    p = tmp_path / "a.md"
    p.write_text("# 标题\n\n正文\n", encoding="utf-8")
    doc = load_document(str(p))
    assert doc.plain is False
    assert len(doc.lines) >= 2


def test_load_document_unsupported(tmp_path):
    """图片等二进制类型 → UnsupportedFileType（调用方转交系统程序）。"""
    p = tmp_path / "pic.png"
    p.write_bytes(b"\x89PNG")
    with pytest.raises(UnsupportedFileType):
        load_document(str(p))


def test_load_document_keeps_eol(tmp_path):
    """CRLF 源文件：装载后 eol 记录为 \\r\\n（保存时还原）。"""
    p = tmp_path / "win.txt"
    p.write_bytes(b"a\r\nb\r\n")
    doc = load_document(str(p))
    assert doc.eol == "\r\n"
    assert doc.lines[0].raw == "a\nb\n"


# ============================ 5. 写回 ============================


def test_text_for_write_restores_crlf():
    """纯文本文档按原换行写回：内容不变，换行风格不变。"""
    doc = parser.parse_plain_text("a\nb\n", "python")
    doc.eol = "\r\n"
    assert _text_for_write(doc) == "a\r\nb\r\n"


def test_text_for_write_markdown_always_lf():
    """Markdown 文档恒用 \\n（既有契约，不受纯文本改造影响）。"""
    doc = parser.parse_markdown("# t\n")
    doc.eol = "\r\n"
    assert _text_for_write(doc) == "# t\n"


def test_save_dialog_exts_plain_keeps_original():
    """.py 另存为默认仍是 py（不会变成 .md）。"""
    doc = parser.parse_plain_text("x = 1\n", "python")
    assert _save_dialog_exts(doc, "/tmp/a.py") == ["py"]
    assert _save_dialog_exts(doc, "/tmp/a.CPP") == ["cpp"]


def test_save_dialog_exts_plain_untitled_defaults_txt():
    """未命名纯文本默认 txt；Markdown 仍是 md。"""
    assert _save_dialog_exts(parser.parse_plain_text("x"), None) == ["txt"]
    assert _save_dialog_exts(parser.parse_markdown("x"), None) == ["md"]


def test_encoding_for_write_plain_uses_original():
    """纯文本文档按原编码写回；Markdown 恒 UTF-8（既有契约）。"""
    from app._file_io_ops import _encoding_for_write

    doc = parser.parse_plain_text("x", "python")
    doc.encoding = "gb18030"
    assert _encoding_for_write(doc) == "gb18030"

    md = parser.parse_markdown("x")
    md.encoding = "gb18030"  # 即使被误设，Markdown 也必须写 UTF-8
    assert _encoding_for_write(md) == "utf-8"


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        # 中文 Windows 的 GBK .py：文件里写着 coding 声明，改写编码即等于弄坏它
        ("legacy.py", "# -*- coding: gbk -*-\n姓名 = '张三'\n".encode("gbk")),
        # 带 BOM 的 UTF-8 .txt：BOM 丢掉后部分老工具会认成 ANSI
        ("bom.txt", b"\xef\xbb\xbfhello\n"),
        # CRLF 的 .cpp：换行风格变了，git diff 会整篇变红
        ("win.cpp", b"int main() {\r\n  return 0;\r\n}\r\n"),
    ],
)
def test_save_roundtrip_preserves_bytes(tmp_path, name, payload):
    """打开 → 写回：字节完全一致（编码 + BOM + 换行三者都不许变）。"""
    from services.file_io import write_text_atomic

    p = tmp_path / name
    p.write_bytes(payload)
    doc = load_document(str(p))
    write_text_atomic(str(p), _text_for_write(doc), encoding=doc.encoding)
    assert p.read_bytes() == payload


def test_write_text_atomic_encoding_roundtrip(tmp_path):
    """原子写入的编码参数生效，且完整性校验按字节比对（不是按 str 长度）。"""
    from services.file_io import write_text_atomic

    p = tmp_path / "gbk.txt"
    write_text_atomic(str(p), "中文\r\n第二行", encoding="gb18030")
    assert p.read_bytes() == "中文\r\n第二行".encode("gb18030")


def test_write_text_atomic_rejects_unencodable(tmp_path):
    """目标编码表示不了的字符必须报错，而不是静默替换成 ?。

    用 ascii 举例（gb18030 是全 Unicode 映射，连 emoji 都能编出来，不适合作反例）：
    宁可让保存失败并触发兜底备份，也不要把用户的中文写成问号。
    """
    from services.file_io import write_text_atomic

    p = tmp_path / "x.txt"
    with pytest.raises(UnicodeEncodeError):
        write_text_atomic(str(p), "中文", encoding="ascii")
    assert not p.exists()


# ============================ 6. 草稿重解析按文档类型分流 ============================


def test_parse_draft_lines_plain_keeps_code_block():
    """纯文本文档的草稿重解析不能走 Markdown（#include 会被当成标题）。"""
    doc = parser.parse_plain_text(PY_SAMPLE, "cpp")
    lines = parse_draft_lines(doc, "#include <stdio.h>\nint main(){}")
    assert len(lines) == 1
    assert lines[0].block_type == BlockType.CODE
    assert lines[0].lang == "cpp"
    assert lines[0].raw == "#include <stdio.h>\nint main(){}"


def test_parse_draft_lines_markdown_parses_blocks():
    """对照：Markdown 文档的草稿仍按 Markdown 解析（多块）。"""
    doc = parser.parse_markdown("# t\n")
    lines = parse_draft_lines(doc, "# 标题\n\n- 列表\n")
    assert len(lines) >= 2
    assert lines[0].block_type == BlockType.HEADING


# ============================ 7. 结构操作让路 ============================


def _fence_ctx(doc):
    """构造最小 mock EditorContext（on_change_code 依赖的槽）。"""
    calls: list = []
    ctx = types.SimpleNamespace(
        document=doc,
        restoring=FakeRef(False),
        code_focus_ref=FakeRef(None),
        code_caret_ref=FakeRef(None),
        code_edit_snapshot=FakeRef(None),
        code_edit_changed=FakeRef(False),
        history_ref=FakeRef(types.SimpleNamespace(push=lambda s: calls.append("push"))),
        make_snapshot=lambda: object(),
        maybe_push_history=lambda: calls.append("maybe_push"),
        push_history=lambda: calls.append("push_history"),
        undo_push_pending=FakeRef(False),
        mark_dirty=lambda: calls.append("mark_dirty"),
        suppress_blur=FakeRef(False),
        set_cursor=lambda li, off: calls.append(("set_cursor", li, off)),
        cursor_li=None,
        cursor_line=0,
    )
    return ctx, calls


def test_on_change_code_plain_writes_body_without_fence():
    """纯文本文档编辑代码块：line.raw 必须等于正文本身（不能包 ``` 围栏）。"""
    doc = parser.parse_plain_text("a = 1\n", "python")
    ctx, calls = _fence_ctx(doc)
    build_fence(ctx)["on_change_code"](0, "a = 2\n")
    line = doc.lines[0]
    assert line.raw == "a = 2\n"
    assert line.segments[0].text == "a = 2\n"
    assert "```" not in line.raw
    assert "mark_dirty" in calls


def test_on_change_code_markdown_still_wraps_fence():
    """对照：Markdown 围栏代码块编辑后仍是围栏形式（既有行为）。"""
    doc = parser.parse_markdown("```python\na = 1\n```")
    ctx, _calls = _fence_ctx(doc)
    build_fence(ctx)["on_change_code"](0, "a = 2")
    assert doc.lines[0].raw == "```python\na = 2\n```"


def test_change_lang_plain_does_not_rebuild_fence():
    """切换语言只改高亮标识，不动正文（否则保存即写入围栏）。"""
    doc = parser.parse_plain_text(PY_SAMPLE, "python")
    ctx, calls = _fence_ctx(doc)
    build_blocks(ctx)["change_lang"](0, "cpp")
    line = doc.lines[0]
    assert line.lang == "cpp"
    assert line.raw == PY_SAMPLE
    assert "mark_dirty" in calls


def test_set_block_ignored_for_plain_doc():
    """纯文本文档按 Ctrl+1 不能把整篇变成一个标题。"""
    doc = parser.parse_plain_text(PY_SAMPLE, "python")
    ctx, calls = _fence_ctx(doc)
    build_blocks(ctx)["set_block"](BlockType.HEADING, 1)
    assert doc.lines[0].block_type == BlockType.CODE
    assert doc.lines[0].raw == PY_SAMPLE
    assert calls == []  # 连历史都没推——完全无副作用


def test_code_backspace_does_not_delete_plain_doc():
    """空内容时 Backspace 不能把唯一的块替换成空段落（那等于清空文件结构）。"""
    doc = parser.parse_plain_text("", "python")
    ctx, _calls = _fence_ctx(doc)
    assert build_fence(ctx)["handle_code_backspace"](0) is False
    assert len(doc.lines) == 1
    assert doc.lines[0].block_type == BlockType.CODE


def test_code_exit_does_not_insert_paragraph():
    """边界方向键不跳出（没有相邻行可落光标，也不该往源码里插空行）。"""
    doc = parser.parse_plain_text(PY_SAMPLE, "python")
    ctx, _calls = _fence_ctx(doc)
    ctx.code_focus_ref = FakeRef(0)
    ctx.code_caret_ref = FakeRef((PY_SAMPLE, 0, 0))
    handle = build_fence(ctx)["handle_code_exit"]
    assert handle("arrowup") is False
    assert handle("arrowdown") is False
    assert len(doc.lines) == 1


# ============================ 8. 渲染 ============================


def _render_plain_block(lines: int, *, is_plain_doc: bool, active: bool = False):
    """渲染真实 LineView（整篇一个 CODE 行），返回控件树。"""
    body = "\n".join(f"line_{i} = {i}" for i in range(lines))
    doc = parser.parse_plain_text(body, "python")
    line = doc.lines[0]
    harness = RenderHarness()
    try:
        return harness.render(
            lambda: LineView(
                line,
                0,
                cursor_off=0 if active else None,
                content_width=800.0,
                is_current_line=active,
                theme_mode=ft.ThemeMode.LIGHT,
                plain_document=is_plain_doc,
            )
        )
    finally:
        harness.dispose()


def _has_tooltip(tree, text: str) -> bool:
    """树中是否有 tooltip 恰为 text 的控件。"""
    return any(getattr(node, "tooltip", None) == text for node in walk(tree))


def _block_container(tree):
    """取代码块的"卡片"容器本身（行容器 key=line-0 的 content）。"""
    root = next(node for node in walk(tree) if getattr(node, "key", None) == "line-0")
    return root, root.content


def test_plain_doc_has_no_header_row():
    """源码视图没有头部行：语言 / 行数 / 复制 / 折叠四个入口整体不存在。

    Markdown 围栏代码块照旧（对照组），否则这条用例只是在断言"删干净了"，
    而删掉的是不是别人的头部就无法区分。
    """
    plain = _render_plain_block(5, is_plain_doc=True)
    for tooltip in ("选择代码语言", "复制代码", "折叠"):
        assert _has_tooltip(plain, tooltip) is False, tooltip
    assert not any(isinstance(n, ft.PopupMenuButton) for n in walk(plain))
    values = {getattr(n, "value", None) for n in walk(plain)}
    assert "5 行" not in values  # 行数标签随头部一起消失
    assert "Python" not in values  # 语言标签同理

    md = _render_plain_block(5, is_plain_doc=False)
    assert _has_tooltip(md, "选择代码语言") is True
    assert _has_tooltip(md, "复制代码") is True
    assert "5 行" in {getattr(n, "value", None) for n in walk(md)}


def test_plain_doc_block_is_flush_with_viewport():
    """源码视图的代码区铺满编辑区：无圆角 / 无边框 / 无阴影 / 无块级上下留白。

    VSCode 打开 .py 时那一整片区域就是编辑器本身；只要留下任一条边或一层留白，
    它就会退化回"嵌在文档里的卡片"，与桌面端直觉冲突。
    """
    root, block = _block_container(_render_plain_block(5, is_plain_doc=True))
    assert block.border is None
    assert block.border_radius is None
    assert block.shadow is None
    assert block.padding is None or (
        block.padding.top == 0
        and block.padding.bottom == 0
        and block.padding.left == 0
        and block.padding.right == 0
    )
    # 块与视口之间那层上下留白（Markdown 里是段落呼吸位）也归零
    assert root.padding.top == 0
    assert root.padding.bottom == 0
    # 头部与正文之间的分隔线也随头部一起去掉（否则代码上方悬着一条横线）
    assert block.content is not None and block.content.border is None

    md_root, md_block = _block_container(_render_plain_block(5, is_plain_doc=False))
    assert md_block.border is not None
    assert md_block.border_radius is not None
    assert md_block.shadow is not None
    assert md_root.padding.top > 0
    # Markdown 保留分隔线（头部与正文是两段）
    column = md_block.content
    assert column is not None and len(column.controls) == 2
    assert column.controls[1].border is not None


def test_plain_doc_keeps_body_and_line_numbers():
    """去掉头部不等于退化成纯 TextField：行号 + 高亮层仍在（浏览态可选中复制）。"""
    tree = _render_plain_block(5, is_plain_doc=True)
    values = [getattr(n, "value", None) for n in walk(tree)]
    assert [str(i) for i in range(1, 6)] == [v for v in values if v in {"1", "2", "3", "4", "5"}]
    # 代码正文以高亮 span 形式存在（每个 token 一片），不是单块纯文本
    spans = [
        s for n in walk(tree) for s in (getattr(n, "spans", None) or [])
    ]
    assert len(spans) > 0


def test_plain_doc_suppresses_whole_block_current_highlight():
    """源码视图整篇是同一个逻辑行：光标在里面走动时不做"当前行"整块染色。

    Markdown 围栏代码块里这层高亮是对的（当前行是一行代码）；照搬到源码视图，
    `is_current_line` 恒为真，整片代码会被罩上一层选中底色 —— 语义与视觉都错。
    """
    _root, plain_block = _block_container(
        _render_plain_block(5, is_plain_doc=True, active=True)
    )
    # 行容器直接挂块容器：没有 active 底色包裹层，块本身仍是无边框无圆角的
    assert plain_block.border is None
    assert plain_block.border_radius is None
    assert plain_block.bgcolor is not None

    _md_root, md_wrap = _block_container(
        _render_plain_block(5, is_plain_doc=False, active=True)
    )
    # Markdown 有 current-line 包裹层（圆角底色），卡片是它的 content
    assert md_wrap.border_radius is not None
    inner = md_wrap.content
    assert inner is not None and getattr(inner, "bgcolor", None) is not None


def test_page_padding_zero_for_source_view():
    """页面级留白策略：源码视图归零，Markdown 照旧随 zoom 缩放。"""
    from views.editor import page_padding

    sets = {"content_padding": 36, "content_padding_top": 24}
    assert page_padding(sets, 1.0, is_plain=True) == (0, 0)
    assert page_padding(sets, 1.5, is_plain=True) == (0, 0)  # 归零与 zoom 无关
    assert page_padding(sets, 1.0, is_plain=False) == (36, 24)
    assert page_padding(sets, 1.5, is_plain=False) == (54, 36)
    assert page_padding({}, 1.0, is_plain=False) == (36, 24)  # 缺省值


def test_status_bar_counts_logical_lines_for_source_view():
    """源码视图的状态栏第三段按非空逻辑行数计（不是恒为 0 的"段"）。"""
    from views.status_bar import _compute_counts

    body = "import os\n\nprint(1)\n"
    doc = parser.parse_plain_text(body, "python")
    _words, chars, count, _mins = _compute_counts(doc)
    assert count == 2  # 两条非空行（空行不算）
    assert chars == len(body)  # 序列化不带围栏，字符数就是正文长度

    md = parser.parse_markdown("a\n\nb\n")
    assert _compute_counts(md)[2] == 2  # Markdown 不变：仍是段落数


@pytest.mark.parametrize(
    ("is_plain_doc", "line_count", "expected"),
    [
        (False, 500, False),  # Markdown 围栏：不管多长都保留高亮层
        (True, 10, False),    # 小脚本：保留"编辑时也高亮"
        (True, blk._PLAIN_SIMPLE_EDIT_LINES, False),      # 阈值本身不切换
        (True, blk._PLAIN_SIMPLE_EDIT_LINES + 1, True),   # 超一行即切换
        (True, 5000, True),
    ],
)
def test_uses_simple_edit_threshold(is_plain_doc, line_count, expected):
    """大文件编辑态切换单层可见编辑框（性能分支，见 _PLAIN_SIMPLE_EDIT_LINES）。"""
    assert blk.uses_simple_edit(is_plain_doc, line_count) is expected


def test_plain_doc_line_count_from_body():
    """编辑器按正文行数判断"是否过大"，而不是按 document.lines（恒为 1）。"""
    body = "x\n" * 1499
    doc = parser.parse_plain_text(body, "python")
    assert len(doc.lines) == 1
    assert doc.lines[0].segments[0].text.count("\n") + 1 == 1500


# ============================ 9. 侧边栏点击分流 ============================


def _row_click(tree, row_name: str):
    """在渲染树里找到显示 row_name 的那一行，返回它的 on_click（无则 None）。"""
    for node in walk(tree):
        if getattr(node, "on_click", None) is None:
            continue
        if any(getattr(n, "value", None) == row_name for n in walk(node)):
            return node.on_click
    return None


def _render_file_panel(flat, opened, external):
    """渲染文件面板（真实 _render_files_panel），返回控件树。

    `_render_files_panel` 里用到了 `ft.use_*`（搜索框 / 上下文菜单），必须在
    渲染上下文中调用，故套一层 `@ft.component` 再交给 RenderHarness。
    """
    from styles import _current_colors
    from views.sidebar import _render_files_panel

    @ft.component
    def Root():
        return _render_files_panel(
            None,                      # file_path
            [],                        # recent_files
            root_dir="/ws",            # root_dir
            root_label=None,
            is_workspace=False,
            flat=flat,
            file_filter="",
            set_file_filter=lambda v: None,
            on_open_file=opened.append,
            on_file_context_action=lambda a, p: None,
            on_close_folder=None,
            c=_current_colors(),
            on_open_external=external.append,
        )

    harness = RenderHarness()
    try:
        return harness.render(Root)
    finally:
        harness.dispose()


def test_sidebar_click_opens_plain_text_in_editor():
    """文件树里点 .py / .txt 必须进编辑器（而不是交系统默认程序）。

    这是本需求的**可达性**断言：编辑器的渲染链路做得再对，只要侧边栏把非
    Markdown 文件仍然路由到 on_open_external，用户就永远看不到那个代码块。
    """
    flat = [
        ("file", "note.md", "/ws/note.md", 0),
        ("file", "main.py", "/ws/main.py", 0),
        ("file", "readme.txt", "/ws/readme.txt", 0),
        ("file", "pic.png", "/ws/pic.png", 0),
    ]
    opened: list[str] = []
    external: list[str] = []
    tree = _render_file_panel(flat, opened, external)

    for name, path in (
        ("note.md", "/ws/note.md"),
        ("main.py", "/ws/main.py"),
        ("readme.txt", "/ws/readme.txt"),
    ):
        click = _row_click(tree, name)
        assert click is not None, f"{name} 行不可点击"
        click(None)
        assert opened[-1] == path, f"{name} 未走编辑器打开"

    # 图片仍交系统默认程序（不能把二进制塞进编辑器）
    click = _row_click(tree, "pic.png")
    assert click is not None
    click(None)
    assert external[-1] == "/ws/pic.png"
    assert len(opened) == 3
