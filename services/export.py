"""基于 pandoc 的多格式导出服务。

依赖项：
- 标准库 shutil / subprocess / os / tempfile
- parser._engine（_get_html_md 用于 pandoc 不可用时的 HTML 回退）

对外接口：
- detect_pandoc() -> str | None：检测 pandoc 可执行路径，失败返回 None
- export_to_html(md_text, path, title) -> None：导出 HTML（pandoc 优先，回退 mistune）
- export_to_docx(md_text, path) -> None：导出 Word（pandoc）
- export_to_pdf(md_text, path) -> None：导出 PDF（pandoc + wkhtmltopdf/LaTeX 引擎）
- build_standalone_html(md_text, title) -> str：构建完整 HTML 文档（含 CSS/字体）

设计要点：
- pandoc 检测惰性缓存（一次检测，多次复用），避免每次导出重复 spawn 进程
- HTML 导出在 pandoc 不可用时回退到 mistune 内置渲染器，并封装为完整文档
- PDF 导出使用 --pdf-engine=wkhtmltopdf（HTML 转 PDF，无需 LaTeX 环境，
  与中文字符兼容性最好），不可用时回退 pdflatex/xelatex
- 所有导出失败抛出带明确原因的 RuntimeError，由调用方 catch 后向用户提示
- 临时 .md 文件写入 UTF-8 BOM 头，避免 pandoc 在 Windows 上的编码误判
"""

import os
import shutil
import subprocess
import tempfile

from parser._engine import _get_html_md

# pandoc 路径缓存（None=未检测/不可用，str=可执行路径）
_pandoc_cache: list[str | None] = [None]
_pandoc_checked: list[bool] = [False]


def detect_pandoc() -> str | None:
    """检测 pandoc 可执行路径，结果缓存。

    优先用 shutil.which（PATH 查找），失败返回 None。
    检测结果在进程生命周期内缓存，避免每次导出重复 spawn 进程。
    """
    if _pandoc_checked[0]:
        return _pandoc_cache[0]
    _pandoc_checked[0] = True
    path = shutil.which("pandoc")
    if path:
        _pandoc_cache[0] = path
        return path
    # Windows 常见安装路径兜底（用户未加入 PATH 时）
    if os.name == "nt":
        for env_var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env_var)
            if not base:
                continue
            candidate = os.path.join(base, "Pandoc", "pandoc.exe")
            if os.path.isfile(candidate):
                _pandoc_cache[0] = candidate
                return candidate
    _pandoc_cache[0] = None
    return None


def reset_pandoc_cache() -> None:
    """重置 pandoc 检测缓存（供测试与「重新检测」入口使用）。"""
    _pandoc_checked[0] = False
    _pandoc_cache[0] = None


def _write_temp_md(md_text: str) -> str:
    """把 Markdown 文本写入临时 .md 文件，返回路径。

    写入 UTF-8 BOM 头，避免 pandoc 在 Windows 上把 UTF-8 误判为 ASCII
    导致中文乱码。临时文件由调用方在使用后删除。
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix="md-export-")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8-sig") as f:
            f.write(md_text)
    except Exception:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        raise
    return tmp_path


def _run_pandoc(args: list[str], tmp_md: str) -> None:
    """执行 pandoc 命令，失败抛出带 stderr 的 RuntimeError。

    args 不含输入文件路径（由 tmp_md 统一追加）。
    captures stdout/stderr，超时 120 秒（大文档足够，避免无限挂起）。
    """
    cmd = ["pandoc", tmp_md] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"pandoc 未安装或不在 PATH：{e}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("pandoc 执行超时（120 秒），文档可能过大") from e
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        raise RuntimeError(f"pandoc 转换失败：{err or '未知错误'}")


# 默认 HTML CSS：科学、清爽、桌面端阅读体验
_DEFAULT_HTML_CSS = """
html { scroll-behavior: smooth; }
body {
  font-family: "Alibaba PuHuiTi", "PingFang SC", "Microsoft YaHei",
               -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  max-width: 880px;
  margin: 40px auto;
  padding: 0 28px;
  color: #1f2328;
  background: #ffffff;
  line-height: 1.72;
  font-size: 16px;
}
h1, h2, h3, h4, h5, h6 {
  font-weight: 600;
  line-height: 1.3;
  margin-top: 1.8em;
  margin-bottom: 0.6em;
  color: #0d1117;
}
h1 { font-size: 2.0em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
h2 { font-size: 1.55em; border-bottom: 1px solid #d0d7de; padding-bottom: 0.3em; }
h3 { font-size: 1.28em; }
h4 { font-size: 1.12em; }
h5 { font-size: 1.0em; }
h6 { font-size: 0.9em; color: #57606a; }
p { margin: 0.7em 0; }
a { color: #0969da; text-decoration: none; }
a:hover { text-decoration: underline; }
code, pre {
  font-family: "JetBrains Mono", "Cascadia Code", "Consolas",
               "Source Code Pro", monospace;
  font-size: 0.92em;
}
code {
  background: #eff1f3;
  padding: 0.18em 0.4em;
  border-radius: 4px;
  color: #cf222e;
}
pre {
  background: #f6f8fa;
  padding: 14px 18px;
  border-radius: 8px;
  overflow-x: auto;
  line-height: 1.55;
  border: 1px solid #d0d7de;
}
pre code { background: transparent; padding: 0; color: #1f2328; }
blockquote {
  margin: 1em 0;
  padding: 0.4em 1.1em;
  border-left: 4px solid #d0d7de;
  color: #57606a;
  background: #f6f8fa;
  border-radius: 0 6px 6px 0;
}
table {
  border-collapse: collapse;
  margin: 1em 0;
  width: 100%;
  display: block;
  overflow-x: auto;
}
th, td {
  border: 1px solid #d0d7de;
  padding: 7px 13px;
  text-align: left;
}
th { background: #f6f8fa; font-weight: 600; }
tr:nth-child(2n) { background: #fafbfc; }
img { max-width: 100%; border-radius: 6px; }
hr {
  border: none;
  border-top: 2px solid #d0d7de;
  margin: 2em 0;
}
mark { background: #fff8c5; padding: 0.1em 0.2em; border-radius: 3px; }
ul, ol { padding-left: 1.6em; }
li { margin: 0.3em 0; }
li input[type="checkbox"] { margin-right: 0.4em; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #c9d1d9; }
  h1, h2, h3, h4, h5, h6 { color: #f0f6fc; }
  h1, h2 { border-bottom-color: #30363d; }
  h6 { color: #8b949e; }
  a { color: #58a6ff; }
  code { background: #1e2329; color: #ff7b72; }
  pre { background: #161b22; border-color: #30363d; }
  pre code { color: #c9d1d9; }
  blockquote { background: #161b22; border-left-color: #30363d; color: #8b949e; }
  th { background: #161b22; }
  tr:nth-child(2n) { background: #161b22; }
  th, td { border-color: #30363d; }
  hr { border-top-color: #30363d; }
  mark { background: #3a2e00; color: #e3b341; }
}
"""


def build_standalone_html(body_html: str, title: str = "文档") -> str:
    """把 HTML 片段封装为完整 HTML 文档（含 DOCTYPE/head/CSS）。

    pandoc 不可用时的 HTML 回退路径，以及 mistune 渲染结果的统一封装。
    CSS 内联，确保单文件导出即可在浏览器中看到完整样式（Typora 式体验）。
    """
    safe_title = (title or "文档").replace("<", "&lt;").replace(">", "&gt;")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>{_DEFAULT_HTML_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body_html}\n"
        "</body>\n"
        "</html>\n"
    )


def export_to_html(md_text: str, path: str, title: str = "文档") -> None:
    """导出为 HTML 文件。

    pandoc 可用 → pandoc -s --metadata title=... -o path（生成完整文档）
    pandoc 不可用 → 回退 mistune 渲染 + build_standalone_html 封装
    """
    pandoc = detect_pandoc()
    tmp_md = _write_temp_md(md_text)
    try:
        if pandoc:
            args = [
                "-s",
                "--metadata", f"title={title}",
                "-o", path,
            ]
            _run_pandoc(args, tmp_md)
        else:
            # 回退：mistune 渲染 + 完整 HTML 封装
            body = _get_html_md()(md_text)
            html = build_standalone_html(body, title)
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
    finally:
        try:
            os.remove(tmp_md)
        except OSError:
            pass


def export_to_docx(md_text: str, path: str) -> None:
    """导出为 Word（.docx）文件。

    必须有 pandoc。失败原因（pandoc 未安装 / 转换错误）抛 RuntimeError。
    """
    pandoc = detect_pandoc()
    if not pandoc:
        raise RuntimeError(
            "导出 Word 需要 pandoc，未在系统 PATH 中检测到 pandoc。\n"
            "请安装 pandoc：https://pandoc.org/installing.html\n"
            "Windows 可用 winget install --id JohnMacFarlane.Pandoc"
        )
    tmp_md = _write_temp_md(md_text)
    try:
        _run_pandoc(["-o", path], tmp_md)
    finally:
        try:
            os.remove(tmp_md)
        except OSError:
            pass


def export_to_pdf(md_text: str, path: str, title: str = "文档") -> None:
    """导出为 PDF 文件。

    必须有 pandoc + PDF 引擎（wkhtmltopdf / pdflatex / xelatex）。
    优先 wkhtmltopdf（HTML 转 PDF，中文兼容性最好，无需 LaTeX 环境）。
    """
    pandoc = detect_pandoc()
    if not pandoc:
        raise RuntimeError(
            "导出 PDF 需要 pandoc，未在系统 PATH 中检测到 pandoc。\n"
            "请安装 pandoc：https://pandoc.org/installing.html"
        )
    # 检测可用 PDF 引擎（wkhtmltopdf 优先，中文兼容性最好）
    pdf_engine = _detect_pdf_engine()
    if not pdf_engine:
        raise RuntimeError(
            "导出 PDF 需要额外的 PDF 引擎（wkhtmltopdf 或 LaTeX）。\n"
            "推荐安装 wkhtmltopdf：https://wkhtmltopdf.org/downloads.html\n"
            "或安装 LaTeX 发行版（如 MiKTeX / TeX Live）"
        )
    tmp_md = _write_temp_md(md_text)
    try:
        args = [
            "-s",
            "--metadata", f"title={title}",
            "--pdf-engine", pdf_engine,
            "-o", path,
        ]
        _run_pandoc(args, tmp_md)
    finally:
        try:
            os.remove(tmp_md)
        except OSError:
            pass


def _detect_pdf_engine() -> str | None:
    """检测可用的 PDF 引擎，优先级：wkhtmltopdf > xelatex > pdflatex。

    - wkhtmltopdf：基于 WebKit，HTML 转 PDF，中文兼容性最好，无需 LaTeX
    - xelatex：支持 Unicode 与中文字体
    - pdflatex：最通用，但中文需额外宏包（ctex）
    """
    for engine in ("wkhtmltopdf", "xelatex", "pdflatex"):
        if shutil.which(engine):
            return engine
    return None
