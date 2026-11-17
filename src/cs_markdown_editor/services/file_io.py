"""文件读写工具。

依赖项：标准库（os / tempfile / hashlib）。
对外接口：
- read_text(path: str) -> str：UTF-8 读取文本
- read_text_auto(path: str) -> tuple[str, str]：编码探测读取（任意用户文本文件），
  返回 (正文, 换行符)；正文的换行已归一为 "\n"
- read_text_detailed(path: str) -> tuple[str, str, str]：同上，另返回实际使用的编码
  （跨文件替换要按原编码写回，否则 GBK 文件会被写成 UTF-8）
- detect_eol(text: str) -> str：识别换行风格（保存时还原用）
- write_text(path: str, text: str, encoding="utf-8") -> None：直接写入（兼容旧调用方）
- write_text_atomic(path: str, text: str, encoding="utf-8") -> None：
  原子写入（临时文件 → 校验 → 替换）；encoding 供纯文本文档按原编码写回

设计要点：
- write_text_atomic 采用「临时文件写入 → fsync → 完整性校验 → os.replace 替换原文件」
  方案，避免写入中断（崩溃 / 磁盘满 / 断电）导致原文件损坏。
- 临时文件与目标文件位于同一目录同一文件系统，保证 os.replace 是原子操作。
- 完整性校验：写入后立即重新读取临时文件并比对内容长度与 SHA256，确保落盘数据
  与内存一致（防止磁盘错误 / 写缓存未刷盘导致内容缺失）。
- 失败兜底：任一步骤异常时立即删除临时文件，原文件保持不变（用户数据安全优先）。
- 大文件优化：单次 write+read 即可，避免分段写入；fsync 仅对临时文件调用一次。
"""


import hashlib
import os
import tempfile


def read_text(path: str) -> str:
    """UTF-8 读取文本文件。失败抛出异常由调用方处理。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


# 编码回退顺序：BOM 由 read_text_auto 先行识别，这里只列"无 BOM"的候选。
# gb18030 是 GBK/GB2312 的超集：中文 Windows 上大量 .txt / .py 仍是这种编码，
# 用 utf-8 硬读会 UnicodeDecodeError（用户看到的提示是"打开失败"，但文件没坏）。
_ENCODINGS = ("utf-8", "gb18030")


def detect_eol(text: str) -> str:
    """文本的换行风格（"\\r\\n" 或 "\\n"）。

    只用于**保存时还原**：编辑器内部统一用 "\\n"（代码块按 "\\n" 切行），
    写回 CRLF 文件时不还原就会把整个文件的换行改掉（git diff 全红）。
    """
    return "\r\n" if "\r\n" in text else "\n"


def read_text_auto(path: str) -> tuple[str, str]:
    """按编码探测读取文本，返回 (正文, 换行符)。

    与 `read_text` 的区别：`read_text` 是"已知 UTF-8"场景下的严格读取（Markdown
    文档、备份等），本函数面向**任意用户文本文件**，必须容错：

    1. BOM 优先（utf-8-sig / utf-16）：BOM 是作者明确声明，直接照它解；
    2. 依次尝试 utf-8 → gb18030；任一成功即用（gb18030 覆盖中文 Windows 的
       历史编码）；
    3. 全部失败才用 utf-8 + replace 兜底 —— 宁可显示几个替换字符，也不要把
       "能读出来的文件"报成打不开；
    4. 正文统一成 "\\n"（换行符原样返回，供保存时还原）。

    二进制文件（含 NUL 字节且不是带 BOM 的 UTF-16）抛 ValueError：这类文件
    就算硬解也是一屏乱码，交给系统默认程序更合适。
    """
    text, eol, _encoding = read_text_detailed(path)
    return text, eol


def read_text_detailed(path: str) -> tuple[str, str, str]:
    """同 `read_text_auto`，额外返回实际使用的编码（读改写场景要按原编码写回）。

    编码选择与 read_text_auto 完全一致（本函数就是它的实现主体）。
    """
    with open(path, "rb") as f:
        raw = f.read()

    # ---- BOM 优先 ----
    if raw.startswith(b"\xff\xfe\x00\x00") or raw.startswith(b"\x00\x00\xfe\xff"):
        # UTF-32：极少见，但 BOM 明确时照解（否则下面的 NUL 判定会误杀）
        encoding = "utf-32"
        text = raw.decode(encoding, errors="replace")
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
        text = raw.decode(encoding, errors="replace")
    elif raw.startswith(b"\xef\xbb\xbf"):
        # 写回时用带 BOM 的编码名，BOM 才不会在往返中丢掉
        encoding = "utf-8-sig"
        text = raw.decode(encoding, errors="replace")
    else:
        if b"\x00" in raw[:8192]:
            raise ValueError("二进制文件（含 NUL 字节），不是文本格式")
        text = ""
        encoding = "utf-8"
        for candidate in _ENCODINGS:
            try:
                text = raw.decode(candidate)
                encoding = candidate
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")

    eol = detect_eol(text)
    # 统一内部换行：\r\n 与孤立 \r 都归一成 \n（否则代码块会把 \r 当成可见字符）
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, eol, encoding



def write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """直接写入文本（保留供不要求原子性的场景使用）。

    保存主路径请改用 write_text_atomic 以获得崩溃安全保障。
    `encoding` 用于纯文本文档「按原编码写回」（GBK 的 .py 不能被改成 UTF-8，
    否则文件里的 `# -*- coding: gbk -*-` 会让 Python 判语法错误）。
    """
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(text)


def write_text_atomic(path: str, text: str, encoding: str = "utf-8") -> None:
    """原子写入：临时文件 → fsync → 完整性校验 → os.replace 替换原文件。

    流程：
    1. 在目标文件同目录创建临时文件（ NamedTemporaryFile 保留扩展名便于排查）。
    2. 写入文本并 flush+fsync，确保数据刷盘（系统断电后仍可恢复）。
    3. 关闭临时文件后立即重新读取并校验 SHA256 与原文一致。
    4. 校验通过后用 os.replace 原子替换原文件（POSIX/Windows 均为原子操作）。
    5. 任一步骤失败：立即删除临时文件，原文件保持不变，向上抛出原始异常。

    编码：先 `text.encode(encoding)` 成字节，再全程按**字节**写入与校验。
    这样既不会被平台换行转换污染（内存文本里的 \\r 原样落盘），也天然覆盖
    非 UTF-8 文件（gb18030 / utf-8-sig / utf-16 的 BOM 都由编码器自己带）。

    异常策略：与 write_text 一致，失败时抛出异常由调用方处理（保存失败时上层
    会触发兜底备份，确保数据不丢失）。
    """
    target_dir = os.path.dirname(os.path.abspath(path))
    if not target_dir:
        target_dir = os.getcwd()
    # 确保目录存在（保存到不存在的目录时给出明确错误）
    if not os.path.isdir(target_dir):
        raise FileNotFoundError(f"目标目录不存在：{target_dir}")

    data = text.encode(encoding)
    src_digest = hashlib.sha256(data).hexdigest()

    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".md-tmp-",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        # 二进制写入：不做任何换行转换，字节级 round-trip 才可能与 src_digest 相等
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(data)
            f.flush()
            # fsync 确保数据写入物理磁盘（系统断电后仍可恢复）
            os.fsync(f.fileno())

        # 完整性校验：重读临时文件并比对 SHA256，防止写缓存异常。
        with open(tmp_path, "rb") as f:
            verified = f.read()
        if hashlib.sha256(verified).hexdigest() != src_digest:
            raise OSError("写入完整性校验失败：临时文件内容与原文不匹配")

        # 原子替换：os.replace 在同文件系统下是原子操作（POSIX rename / Win MoveFileEx）
        os.replace(tmp_path, path)
    except Exception:
        # 失败兜底：清理临时文件，原文件保持不变
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise
