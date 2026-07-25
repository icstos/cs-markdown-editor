"""文件读写工具。

依赖项：标准库。
对外接口：
- read_text(path: str) -> str：UTF-8 读取文本
- write_text(path: str, text: str) -> None：UTF-8 写入文本

消除重复：原先 main.py 中 _read_file / _write_file 局部函数，多处内联 open()，
此处统一为单一来源，并集中异常处理策略（交由调用方决定如何提示用户）。
"""


def read_text(path: str) -> str:
    """UTF-8 读取文本文件。失败抛出异常由调用方处理。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_text(path: str, text: str) -> None:
    """UTF-8 写入文本文件。失败抛出异常由调用方处理。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
