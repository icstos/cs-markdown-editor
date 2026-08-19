"""文档首部元数据（YAML frontmatter）交互支持测试。

覆盖：
- _parse_yaml_pairs：扁平 key: value 解析（含空行/注释/无冒号行/值含冒号）
- _paste_row_from_clipboard：剪贴板 "key: value" 解析插入（无冒号回退、
  空剪贴板 no-op）
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from views.line_view import _parse_yaml_pairs, _paste_row_from_clipboard

# ---------------- _parse_yaml_pairs ----------------

def test_parse_basic_pairs():
    """基本 key: value 解析。"""
    assert _parse_yaml_pairs("title: 我的文档\ntags: note") == [
        ("title", "我的文档"),
        ("tags", "note"),
    ]


def test_parse_skips_blank_and_comments():
    """跳过空行与 # 注释行。"""
    assert _parse_yaml_pairs("\n# 注释\nkey: value\n\n") == [("key", "value")]


def test_parse_line_without_colon_skipped():
    """无冒号的行（如多行值/嵌套结构）原样忽略。"""
    assert _parse_yaml_pairs("key: value\njust-text-line") == [("key", "value")]


def test_parse_value_contains_colon():
    """值内含冒号：仅按第一个冒号切分。"""
    assert _parse_yaml_pairs("url: https://example.com/a:b") == [
        ("url", "https://example.com/a:b"),
    ]


def test_parse_empty_and_whitespace_key():
    """空内容返回空列表；带缩进的行键两侧空白剥离后仍解析。"""
    assert _parse_yaml_pairs("") == []
    assert _parse_yaml_pairs("  indented: x") == [("indented", "x")]


# ---------------- _paste_row_from_clipboard ----------------

class _FakeClipboard:
    """记录 set/get 的伪剪贴板。"""

    def __init__(self, text=None):
        self._text = text
        self.set_calls = []

    async def get(self):
        return self._text

    async def set(self, text):
        self._text = text
        self.set_calls.append(text)


def _run(coro):
    import asyncio
    asyncio.run(coro)


def test_paste_parses_key_value():
    """剪贴板 \"key: value\" → 解析插入。"""
    cb = _FakeClipboard("author: 张三")
    inserted = []
    _run(_paste_row_from_clipboard(types.SimpleNamespace(current=cb),
                                   lambda k, v: inserted.append((k, v))))
    assert inserted == [("author", "张三")]


def test_paste_value_with_colon():
    """值内含冒号：第一个冒号切分，其余保留在值里。"""
    cb = _FakeClipboard("url: https://a.com/b:c")
    inserted = []
    _run(_paste_row_from_clipboard(types.SimpleNamespace(current=cb),
                                   lambda k, v: inserted.append((k, v))))
    assert inserted == [("url", "https://a.com/b:c")]


def test_paste_without_colon_falls_back_to_key():
    """无冒号文本：整段作为值、键为空（用户补键名）。"""
    cb = _FakeClipboard("一段纯文本")
    inserted = []
    _run(_paste_row_from_clipboard(types.SimpleNamespace(current=cb),
                                   lambda k, v: inserted.append((k, v))))
    assert inserted == [("", "一段纯文本")]


def test_paste_empty_clipboard_noop():
    """空剪贴板：不插入。"""
    cb = _FakeClipboard("")
    inserted = []
    _run(_paste_row_from_clipboard(types.SimpleNamespace(current=cb),
                                   lambda k, v: inserted.append((k, v))))
    assert inserted == []


def test_paste_missing_clipboard_noop():
    """剪贴板 ref 为 None：不插入（防御）。"""
    inserted = []
    _run(_paste_row_from_clipboard(None, lambda k, v: inserted.append((k, v))))
    assert inserted == []


def test_paste_strips_whitespace():
    """键/值两侧空白剥离。"""
    cb = _FakeClipboard("  title :  我的文档  ")
    inserted = []
    _run(_paste_row_from_clipboard(types.SimpleNamespace(current=cb),
                                   lambda k, v: inserted.append((k, v))))
    assert inserted == [("title", "我的文档")]
