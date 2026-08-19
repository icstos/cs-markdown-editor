"""「打开文件夹」工作区功能测试。

覆盖：
- views.sidebar._resolve_files_root：文件面板根目录优先级
  （workspace_folder > 当前文件所在目录 > None）
- app._file_io_ops.open_folder：选择目录后写入 workspace_folder、
  自动展开侧边栏并切到文件面板、弹提示；取消选择时不改设置

不依赖 UI 渲染，用 SimpleNamespace / MagicMock 注入依赖，async 协程用
asyncio.run 直接驱动（与 test_autosave 一致，不引入 pytest-asyncio）。
"""

import asyncio
import json
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app._file_io_ops
from app._file_io_ops import build_file_io_ops
from views.sidebar import _resolve_files_root


@pytest.fixture(autouse=True)
def _no_real_settings_write(monkeypatch, tmp_path):
    """隔离 save_settings：防止测试把最小 settings 写回真实 settings.json。

    回归守护：open_folder 内直接调用 config.settings.save_settings（模块级导入），
    测试 ctx 的 settings 只有最小键——若不拦截会把用户配置文件覆盖成残缺内容。
    改为写入 pytest 临时目录，保留“持久化”语义供未来断言。
    """
    out = tmp_path / "settings_out.json"

    def _fake_save(settings):
        out.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    monkeypatch.setattr(app._file_io_ops, "save_settings", _fake_save)

# ---- _resolve_files_root：根目录优先级 ----


def test_resolve_root_workspace_priority_over_file_path(tmp_path):
    """workspace_folder 存在时优先于 file_path 所在目录。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    file_dir = tmp_path / "other"
    file_dir.mkdir()
    file_path = str(file_dir / "note.md")

    root, label, is_ws = _resolve_files_root(str(ws), file_path)
    assert root == str(ws)
    assert label == "workspace"
    assert is_ws is True


def test_resolve_root_falls_back_to_file_dir(tmp_path):
    """workspace_folder 为 None 时回退到当前文件所在目录。"""
    file_dir = tmp_path / "docs"
    file_dir.mkdir()
    file_path = str(file_dir / "note.md")

    root, label, is_ws = _resolve_files_root(None, file_path)
    assert root == str(file_dir)
    assert label is None
    assert is_ws is False


def test_resolve_root_none_when_nothing_available():
    """workspace 与 file_path 均无 → 返回 None（最近文件列表分支）。"""
    root, label, is_ws = _resolve_files_root(None, None)
    assert root is None
    assert label is None
    assert is_ws is False


def test_resolve_root_workspace_must_exist(tmp_path):
    """workspace_folder 指向不存在的目录时降级到 file_path。"""
    missing = tmp_path / "missing"
    file_dir = tmp_path / "docs"
    file_dir.mkdir()
    file_path = str(file_dir / "note.md")

    root, _label, is_ws = _resolve_files_root(str(missing), file_path)
    assert root == str(file_dir)
    assert is_ws is False


def test_resolve_root_workspace_basename(tmp_path):
    """workspace 模式下 label 为文件夹名。"""
    ws = tmp_path / "项目笔记"
    ws.mkdir()
    _root, label, is_ws = _resolve_files_root(str(ws), None)
    assert label == "项目笔记"
    assert is_ws is True


# ---- open_folder 控制器 ----


def _make_ctx(picked_path):
    """构造最小 ctx 供 build_file_io_ops，注入 fake FilePicker。

    set_settings 就地把 settings 更新为新值，便于调用后断言最终状态
    （open_folder 单次原子合并写入，故最终 settings 应包含全部三个键）。
    """
    settings = {"sidebar_open": False, "sidebar_panel": "outline"}

    def set_settings(new):
        settings.clear()
        settings.update(new)

    snacks: list[str] = []

    def show_snack(msg):
        snacks.append(msg)

    fake_picker = MagicMock()
    fake_picker.get_directory_path = AsyncMock(return_value=picked_path)

    ctx = types.SimpleNamespace(
        picker_holder=types.SimpleNamespace(current=fake_picker),
        settings=settings,
        set_settings=set_settings,
        apply_content_layout=lambda: None,
        show_snack=show_snack,
    )
    return ctx, snacks


def test_open_folder_sets_workspace_and_opens_sidebar(tmp_path):
    """选择目录后：原子写入 workspace_folder + 展开侧边栏 + 切文件面板 + 弹提示。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    ctx, snacks = _make_ctx(str(ws))

    cbs = build_file_io_ops(ctx)
    asyncio.run(cbs["open_folder"]())

    assert ctx.settings["workspace_folder"] == str(ws)
    assert ctx.settings["sidebar_open"] is True
    assert ctx.settings["sidebar_panel"] == "files"
    assert any("workspace" in m for m in snacks)


def test_open_folder_atomic_merge_preserves_workspace_when_sidebar_open(tmp_path):
    """侧边栏已展开时仍正确写入 workspace_folder（原子合并不丢键）。

    回归守护：若误用多次 update_setting 分写，Flet 批量提交会使末次值
    覆盖前序 workspace_folder 写入。原子合并应保证三键同时生效。
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    ctx, _snacks = _make_ctx(str(ws))
    ctx.settings["sidebar_open"] = True
    ctx.settings["sidebar_panel"] = "files"

    cbs = build_file_io_ops(ctx)
    asyncio.run(cbs["open_folder"]())

    assert ctx.settings["workspace_folder"] == str(ws)
    assert ctx.settings["sidebar_open"] is True
    assert ctx.settings["sidebar_panel"] == "files"


def test_open_folder_cancel_does_nothing(tmp_path):
    """用户取消目录选择时不改任何设置、不弹提示。"""
    ctx, snacks = _make_ctx(None)
    before = dict(ctx.settings)

    cbs = build_file_io_ops(ctx)
    asyncio.run(cbs["open_folder"]())

    assert ctx.settings == before
    assert snacks == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
