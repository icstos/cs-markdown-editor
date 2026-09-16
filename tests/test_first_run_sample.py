"""首次启动示例文档策略测试（启动提速）。

背景：App 原先每次启动都打开内置示例（SAMPLE_MD，约 298 行）——首次渲染约
225ms / 2400+ 控件。改为「仅首次启动展示示例，之后打开空白文档」后，普通启动
首次渲染约 63ms / 558 控件（客户端需构建的控件树同步缩小）。

本测试锁定该契约：
1. 无 settings.json（首次启动）→ 初始文档 == SAMPLE_MD，并立即落盘
   sample_shown=True；
2. 同一 settings.json 再启动（sample_shown=True）→ 初始文档为空。

设置文件隔离到本目录沙箱（monkeypatch config.settings.SETTINGS_PATH），与
test_boot_smoke / test_shell_search_focus 同模式。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config.settings as cs
import parser
from app import App
from config.sample import SAMPLE_MD
from tests.harness import RenderHarness

_SANDBOX = Path(__file__).resolve().parent / ".firstrun-sandbox"
_SANDBOX.mkdir(parents=True, exist_ok=True)
_SETTINGS_FILE = _SANDBOX / "settings.json"


def _app_ctx(h: RenderHarness):
    """从 page.on_keyboard_event 处理器闭包取出 AppContext（同 shell_search_focus）。"""
    handler = h.page.on_keyboard_event
    return next(
        c.cell_contents for c in handler.__closure__
        if type(c.cell_contents).__name__ == "AppContext"
    )


def _initial_doc_text(h: RenderHarness) -> str:
    ctx = _app_ctx(h)
    return parser.serialize(ctx.tabs[0]["document"])


def test_first_run_shows_sample_and_marks_flag(monkeypatch):
    """首次启动（无设置文件）：展示示例文档，并把 sample_shown 落盘。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))
    _SETTINGS_FILE.unlink(missing_ok=True)

    h = RenderHarness()
    try:
        h.render(App)
        assert _initial_doc_text(h) == SAMPLE_MD, "首次启动应展示内置示例文档"
    finally:
        h.dispose()

    assert cs.load_settings().get("sample_shown") is True, (
        "首次启动后必须落盘 sample_shown=True，否则每次启动都会重复渲染示例"
    )


def test_later_run_opens_empty_document(monkeypatch):
    """再次启动（sample_shown=True）：初始文档为空，不再渲染示例。"""
    monkeypatch.setattr(cs, "SETTINGS_PATH", str(_SETTINGS_FILE))
    _SETTINGS_FILE.unlink(missing_ok=True)

    first = RenderHarness()
    first.render(App)  # 首次启动：写入 sample_shown
    first.dispose()

    second = RenderHarness()
    try:
        second.render(App)
        assert _initial_doc_text(second).strip() == "", "非首次启动应打开空白文档"
    finally:
        second.dispose()


def test_sample_flag_defaults_to_false():
    """默认设置必须带 sample_shown=False（老 settings.json 由深合并补齐）。"""
    assert cs.DEFAULT_SETTINGS["sample_shown"] is False


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
