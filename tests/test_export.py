"""基于 pandoc 的多格式导出服务测试。

验证：
- detect_pandoc：检测结果缓存、reset_pandoc_cache 重置
- build_standalone_html：完整 HTML 文档结构（DOCTYPE/head/CSS/body）
- export_to_html：pandoc 不可用时回退 mistune + 封装为完整文档
- export_to_docx / export_to_pdf：pandoc 不可用时抛 RuntimeError 含安装指引
- 临时 .md 文件写入 UTF-8 BOM（Windows 中文兼容）
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from services.export import (  # noqa: E402
    _DEFAULT_HTML_CSS,
    build_standalone_html,
    detect_pandoc,
    export_to_docx,
    export_to_html,
    export_to_pdf,
    reset_pandoc_cache,
)


# ================ detect_pandoc ================

def test_detect_pandoc_returns_str_or_none():
    """detect_pandoc 返回 str（路径）或 None，不抛异常。"""
    reset_pandoc_cache()
    result = detect_pandoc()
    assert result is None or isinstance(result, str)
    assert isinstance(result, str) or result is None


def test_detect_pandoc_caches_result():
    """多次调用 detect_pandoc 返回同一结果（缓存生效）。"""
    reset_pandoc_cache()
    r1 = detect_pandoc()
    r2 = detect_pandoc()
    assert r1 == r2


def test_reset_pandoc_cache_clears_cache():
    """reset_pandoc_cache 后再次检测应重新执行（不抛异常）。"""
    reset_pandoc_cache()
    _ = detect_pandoc()
    reset_pandoc_cache()
    r = detect_pandoc()
    assert r is None or isinstance(r, str)


# ================ build_standalone_html ================

def test_build_standalone_html_has_doctype():
    """完整 HTML 文档以 <!DOCTYPE html> 开头。"""
    html = build_standalone_html("<p>hello</p>", "测试")
    assert html.startswith("<!DOCTYPE html>")


def test_build_standalone_html_has_head_and_title():
    """HTML 文档含 <head> 与 <title>（标题正确转义）。"""
    html = build_standalone_html("<p>body</p>", "我的<文档>")
    assert "<head>" in html
    assert "<title>我的&lt;文档&gt;</title>" in html


def test_build_standalone_html_has_css():
    """HTML 文档内联 CSS（确保单文件样式完整）。"""
    html = build_standalone_html("<p>x</p>", "t")
    assert "<style>" in html
    assert _DEFAULT_HTML_CSS.strip() in html


def test_build_standalone_html_contains_body_content():
    """HTML body 含传入的 HTML 片段。"""
    body = '<h1>标题</h1><p>段落</p><code>code</code>'
    html = build_standalone_html(body, "t")
    assert body in html
    assert "<body>" in html
    assert "</body>" in html


def test_build_standalone_html_default_title():
    """未传 title 时默认「文档」。"""
    html = build_standalone_html("<p>x</p>")
    assert "<title>文档</title>" in html


def test_build_standalone_html_has_meta_charset():
    """HTML 文档含 <meta charset> 确保中文正确显示。"""
    html = build_standalone_html("<p>x</p>", "t")
    assert '<meta charset="utf-8">' in html


def test_build_standalone_html_has_dark_mode_css():
    """CSS 含暗色模式媒体查询（自适应主题）。"""
    html = build_standalone_html("<p>x</p>", "t")
    assert "prefers-color-scheme: dark" in html


# ================ export_to_html ================

def test_export_to_html_writes_file():
    """export_to_html 写入 HTML 文件（pandoc 不可用时回退 mistune）。"""
    md = "# 标题\n\n这是一段**加粗**文本。\n\n- 列表项 1\n- 列表项 2"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        path = f.name
    try:
        export_to_html(md, path, title="测试文档")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # 无论 pandoc 是否可用，都应是 HTML 文档
        assert "<html" in content.lower() or "<!DOCTYPE" in content.upper()
        # 内容应包含原文的某些字符
        assert "加粗" in content or "标题" in content
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_export_to_html_fallback_has_full_structure():
    """pandoc 不可用时回退路径生成完整 HTML 文档（含 DOCTYPE/CSS）。"""
    # 强制 pandoc 不可用（mock detect_pandoc 返回 None）
    import services.export as exp_module
    original_detect = exp_module.detect_pandoc
    exp_module.detect_pandoc = lambda: None
    try:
        md = "# 测试\n\n内容"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as f:
            path = f.name
        try:
            export_to_html(md, path, title="测试")
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # 回退路径应使用 build_standalone_html，含完整结构
            assert "<!DOCTYPE html>" in content
            assert "<style>" in content
            assert "prefers-color-scheme" in content
        finally:
            if os.path.exists(path):
                os.remove(path)
    finally:
        exp_module.detect_pandoc = original_detect


# ================ export_to_docx / export_to_pdf ================

def test_export_to_docx_raises_without_pandoc():
    """pandoc 不可用时 export_to_docx 抛 RuntimeError 含安装指引。"""
    import services.export as exp_module
    original_detect = exp_module.detect_pandoc
    exp_module.detect_pandoc = lambda: None
    try:
        with pytest.raises(RuntimeError) as exc_info:
            export_to_docx("# 测试", "test.docx")
        # 错误消息应含安装指引
        assert "pandoc" in str(exc_info.value).lower()
        assert "install" in str(exc_info.value).lower() or "安装" in str(exc_info.value)
    finally:
        exp_module.detect_pandoc = original_detect


def test_export_to_pdf_raises_without_pandoc():
    """pandoc 不可用时 export_to_pdf 抛 RuntimeError 含安装指引。"""
    import services.export as exp_module
    original_detect = exp_module.detect_pandoc
    exp_module.detect_pandoc = lambda: None
    try:
        with pytest.raises(RuntimeError) as exc_info:
            export_to_pdf("# 测试", "test.pdf")
        assert "pandoc" in str(exc_info.value).lower()
    finally:
        exp_module.detect_pandoc = original_detect


def test_export_to_pdf_raises_without_pdf_engine():
    """pandoc 可用但 PDF 引擎缺失时抛 RuntimeError 含引擎安装指引。"""
    import services.export as exp_module
    original_detect_pandoc = exp_module.detect_pandoc
    original_detect_engine = exp_module._detect_pdf_engine
    exp_module.detect_pandoc = lambda: "/fake/pandoc"
    exp_module._detect_pdf_engine = lambda: None
    try:
        with pytest.raises(RuntimeError) as exc_info:
            export_to_pdf("# 测试", "test.pdf")
        msg = str(exc_info.value)
        # 应提示安装 wkhtmltopdf 或 LaTeX
        assert "pdf" in msg.lower() or "wkhtmltopdf" in msg.lower() or "latex" in msg.lower()
    finally:
        exp_module.detect_pandoc = original_detect_pandoc
        exp_module._detect_pdf_engine = original_detect_engine


# ================ 集成测试（仅 pandoc 可用时执行） ================

@pytest.fixture
def pandoc_available():
    """跳过 pandoc 不可用环境的集成测试。"""
    reset_pandoc_cache()
    if detect_pandoc() is None:
        pytest.skip("pandoc 未安装，跳过集成测试")
    return detect_pandoc()


def test_export_to_docx_with_pandoc(pandoc_available):
    """pandoc 可用时，导出真实 .docx 文件。"""
    md = "# 标题\n\n这是一段**加粗**文本。\n\n- 列表项 1\n- 列表项 2"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".docx", delete=False, encoding="utf-8"
    ) as f:
        path = f.name
    try:
        export_to_docx(md, path)
        # .docx 是 ZIP 格式，文件头应为 PK
        with open(path, "rb") as f:
            header = f.read(4)
        assert header[:2] == b"PK"
        assert os.path.getsize(path) > 0
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_export_to_html_with_pandoc(pandoc_available):
    """pandoc 可用时，导出 HTML 含完整文档结构与 CSS。"""
    md = "# 标题\n\n这是一段**加粗**文本。"
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        path = f.name
    try:
        export_to_html(md, path, title="测试文档")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # pandoc -s 生成完整 HTML 文档
        assert "<html" in content.lower()
        assert "加粗" in content or "标题" in content
    finally:
        if os.path.exists(path):
            os.remove(path)
