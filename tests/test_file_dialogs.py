"""文件对话框控制器测试（app._file_dialogs）。

覆盖：
- open_input_dialog：每次弹窗 instance 递增（作为 FileActionDialog 的 key
  触发重挂载，重置输入框 state）；新建文件/文件夹 input_value 为空，
  重命名保留当前文件名
- open_delete_dialog：confirm 模式携带递增 instance
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app._file_dialogs import build_file_dialogs


def _make_ctx():
    """最小 ctx：open_input_dialog / open_delete_dialog 仅写 file_dialog state。"""
    return SimpleNamespace(set_file_dialog=MagicMock())


def test_open_input_dialog_instance_increases_and_value_reset():
    """每次弹窗 instance 递增；新建文件/文件夹 input_value 为空（不保留上次输入）。"""
    ctx = _make_ctx()
    cbs = build_file_dialogs(ctx)

    cbs["open_input_dialog"](
        "new_file", "新建文件", "icon", "文件名", "输入文件名", "",
        "在 D:/x 创建", "创建", "D:/x",
    )
    first = ctx.set_file_dialog.call_args[0][0]
    assert first["mode"] == "input"
    assert first["action"] == "new_file"
    assert first["input_value"] == ""
    assert first["instance"] >= 1

    # 连续再次打开新建对话框：instance 递增 → 组件 key 变化 → 输入框重挂载重置
    cbs["open_input_dialog"](
        "new_folder", "新建文件夹", "icon", "文件夹名", "输入文件夹名", "",
        "在 D:/x 创建", "创建", "D:/x",
    )
    second = ctx.set_file_dialog.call_args[0][0]
    assert second["instance"] == first["instance"] + 1
    assert second["input_value"] == ""


def test_open_input_dialog_rename_keeps_current_name():
    """重命名保留当前文件名作为默认值（仅新建场景重置为空）。"""
    ctx = _make_ctx()
    cbs = build_file_dialogs(ctx)
    cbs["open_input_dialog"](
        "rename", "重命名", "icon", "新名称", "输入新名称", "old.md",
        "位置：D:/x", "重命名", "D:/x/old.md",
    )
    state = ctx.set_file_dialog.call_args[0][0]
    assert state["action"] == "rename"
    assert state["input_value"] == "old.md"
    assert state["instance"] >= 1


def test_open_delete_dialog_has_instance():
    """删除确认对话框同样携带递增 instance（key 重挂载，无输入残留问题）。"""
    ctx = _make_ctx()
    cbs = build_file_dialogs(ctx)
    cbs["open_delete_dialog"]("D:/x/a.md", False)
    state = ctx.set_file_dialog.call_args[0][0]
    assert state["mode"] == "confirm"
    assert state["action"] == "delete"
    assert state["instance"] >= 1
