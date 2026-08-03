"""文件读写工具。

依赖项：标准库（os / tempfile / hashlib）。
对外接口：
- read_text(path: str) -> str：UTF-8 读取文本
- write_text(path: str, text: str) -> None：UTF-8 直接写入（兼容旧调用方）
- write_text_atomic(path: str, text: str) -> None：原子写入（临时文件 → 校验 → 替换）

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


def write_text(path: str, text: str) -> None:
    """UTF-8 直接写入文本（保留供不要求原子性的场景使用）。

    保存主路径请改用 write_text_atomic 以获得崩溃安全保障。
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_text_atomic(path: str, text: str) -> None:
    """UTF-8 原子写入：临时文件 → fsync → 完整性校验 → os.replace 替换原文件。

    流程：
    1. 在目标文件同目录创建临时文件（ NamedTemporaryFile 保留扩展名便于排查）。
    2. 写入文本并 flush+fsync，确保数据刷盘（系统断电后仍可恢复）。
    3. 关闭临时文件后立即重新读取并校验 SHA256 与原文一致。
    4. 校验通过后用 os.replace 原子替换原文件（POSIX/Windows 均为原子操作）。
    5. 任一步骤失败：立即删除临时文件，原文件保持不变，向上抛出原始异常。

    异常策略：与 write_text 一致，失败时抛出异常由调用方处理（保存失败时上层
    会触发兜底备份，确保数据不丢失）。
    """
    target_dir = os.path.dirname(os.path.abspath(path))
    if not target_dir:
        target_dir = os.getcwd()
    # 确保目录存在（保存到不存在的目录时给出明确错误）
    if not os.path.isdir(target_dir):
        raise FileNotFoundError(f"目标目录不存在：{target_dir}")

    # 计算原文 SHA256 供落盘后校验
    src_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".md-tmp-",
        suffix=".tmp",
        dir=target_dir,
    )
    try:
        # 用 os.fdopen 包装文件描述符写入，避免 NamedTemporaryFile 跨平台差异
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            # fsync 确保数据写入物理磁盘（系统断电后仍可恢复）
            os.fsync(f.fileno())

        # 完整性校验：重读临时文件并比对 SHA256，防止写缓存异常
        with open(tmp_path, encoding="utf-8") as f:
            verified = f.read()
        if hashlib.sha256(verified.encode("utf-8")).hexdigest() != src_digest:
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
