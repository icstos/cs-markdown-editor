"""文件系统操作工具（新建/重命名/副本/删除/打开位置）。

为右键菜单提供纯文件系统操作，不含 Flet 依赖：
- create_file / create_folder：在指定目录创建新文件或文件夹
- rename_path：重命名文件或文件夹（保持同目录）
- duplicate_file：创建文件副本（自动 _copy 后缀去重）
- delete_path：删除文件
- reveal_in_explorer：在系统文件管理器中打开包含目录
- sanitize_name / name_exists：名称校验辅助

异常策略：操作失败抛出异常，由调用方（main.py）捕获后用 SnackBar 提示用户。
"""

import os
import platform
import shutil
import subprocess

# Windows 文件名非法字符（<>:"/\|?*）及控制字符
_INVALID_CHARS = set('<>:"/\\|?*\x00')


def sanitize_name(name: str) -> str:
    """去除首尾空白和非法字符。返回清理后的名称（可能为空字符串）。"""
    name = name.strip()
    # 去除 Windows 非法字符
    name = "".join(c for c in name if c not in _INVALID_CHARS)
    # 去除尾部的点和空格（Windows 不允许）
    name = name.rstrip(". ")
    return name


def name_exists(dir_path: str, name: str) -> bool:
    """检查 dir_path 下是否已存在同名文件或文件夹。"""
    return os.path.exists(os.path.join(dir_path, name))


def ensure_md_extension(name: str) -> str:
    """确保文件名以 .md 结尾（若无扩展名或非 .md/.markdown 则追加 .md）。"""
    lower = name.lower()
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return name
    if "." in os.path.basename(name):
        # 有其他扩展名，替换为 .md
        base = os.path.splitext(name)[0]
        return base + ".md"
    return name + ".md"


def create_file(dir_path: str, name: str) -> str:
    """在 dir_path 下创建新 Markdown 文件（空内容），返回完整路径。

    自动确保 .md 扩展名；名称冲突时抛出 FileExistsError。
    """
    name = sanitize_name(name)
    if not name:
        raise ValueError("文件名不能为空")
    name = ensure_md_extension(name)
    full_path = os.path.join(dir_path, name)
    if os.path.exists(full_path):
        raise FileExistsError(f"已存在同名文件：{name}")
    # 创建空文件（UTF-8 BOM-less）
    with open(full_path, "w", encoding="utf-8") as f:
        f.write("")
    return full_path


def create_folder(dir_path: str, name: str) -> str:
    """在 dir_path 下创建新文件夹，返回完整路径。名称冲突时抛出 FileExistsError。"""
    name = sanitize_name(name)
    if not name:
        raise ValueError("文件夹名不能为空")
    full_path = os.path.join(dir_path, name)
    if os.path.exists(full_path):
        raise FileExistsError(f"已存在同名文件夹：{name}")
    os.makedirs(full_path, exist_ok=False)
    return full_path


def rename_path(old_path: str, new_name: str) -> str:
    """重命名文件或文件夹（保持在同一父目录），返回新路径。

    new_name 仅含文件名（不含路径）；名称冲突时抛出 FileExistsError。
    """
    new_name = sanitize_name(new_name)
    if not new_name:
        raise ValueError("名称不能为空")
    # 如果是文件且原名有 .md 扩展名，确保新名也有
    if os.path.isfile(old_path) and old_path.lower().endswith((".md", ".markdown")):
        new_name = ensure_md_extension(new_name)
    dir_path = os.path.dirname(old_path)
    new_path = os.path.join(dir_path, new_name)
    if os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(old_path):
        raise FileExistsError(f"已存在同名项：{new_name}")
    os.rename(old_path, new_path)
    return new_path


def duplicate_file(src_path: str) -> str:
    """创建文件副本，自动生成不冲突的副本名（filename_copy.md, filename_copy_2.md ...）。

    返回副本的完整路径。源文件不存在时抛出 FileNotFoundError。
    """
    if not os.path.isfile(src_path):
        raise FileNotFoundError(f"文件不存在：{src_path}")
    dir_path = os.path.dirname(src_path)
    base, ext = os.path.splitext(os.path.basename(src_path))
    # 尝试 filename_copy.ext, filename_copy_2.ext, ...
    candidate = f"{base}_copy{ext}"
    counter = 2
    while os.path.exists(os.path.join(dir_path, candidate)):
        candidate = f"{base}_copy_{counter}{ext}"
        counter += 1
    dest_path = os.path.join(dir_path, candidate)
    shutil.copy2(src_path, dest_path)
    return dest_path


def move_path(src_path: str, dst_dir: str) -> str:
    """将文件/文件夹移动到 dst_dir 下（侧边栏拖拽），返回新路径。

    合法性校验（非法抛 ValueError）：
    - 源不存在 / 目标不是存在的文件夹
    - 源已在目标文件夹中（原地移动）
    - 源是文件夹时，目标不能是源自身或其子孙（循环移动）

    目标下同名冲突时自动重命名为 "name (1)" / "name (1).ext"（资源管理器直觉，
    无阻断）。
    """
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"源不存在：{src_path}")
    if not os.path.isdir(dst_dir):
        raise ValueError("目标不是文件夹")
    src_abs = os.path.abspath(src_path)
    dst_abs = os.path.abspath(dst_dir)
    if os.path.dirname(src_abs) == dst_abs:
        raise ValueError("已在目标文件夹中")
    if os.path.isdir(src_abs):
        src_nc = os.path.normcase(src_abs)
        dst_nc = os.path.normcase(dst_abs)
        if dst_nc == src_nc or dst_nc.startswith(src_nc + os.sep):
            raise ValueError("不能移动到自身或其子文件夹")
    name = os.path.basename(src_abs)
    dest = os.path.join(dst_abs, name)
    if os.path.exists(dest):
        base, ext = os.path.splitext(name)
        counter = 1
        while os.path.exists(os.path.join(dst_abs, f"{base} ({counter}){ext}")):
            counter += 1
        dest = os.path.join(dst_abs, f"{base} ({counter}){ext}")
    shutil.move(src_abs, dest)
    return dest


def delete_path(path: str) -> None:
    """删除文件。文件不存在时抛出 FileNotFoundError。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    if os.path.isfile(path):
        os.remove(path)
    else:
        shutil.rmtree(path)


def reveal_in_explorer(path: str) -> None:
    """在系统文件管理器中打开 path 的包含目录（并选中文件）。

    - Windows: explorer.exe /select,"path"
    - macOS: open -R "path"
    - Linux: xdg-open "dir"（无法选中文件）
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"路径不存在：{path}")

    system = platform.system()
    if system == "Windows":
        # /select 在路径含空格时需要引号，subprocess.list2cmdline 会自动处理
        subprocess.Popen(["explorer.exe", "/select,", path])
    elif system == "Darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        # Linux: 打开包含目录（xdg-open 不支持选中文件）
        dir_path = path if os.path.isdir(path) else os.path.dirname(path)
        subprocess.Popen(["xdg-open", dir_path])


def open_external(path: str) -> None:
    """用系统默认程序打开文件（资源管理器双击直觉）。

    供侧边栏文件树点击非 md 文件时调用：.png 用图片查看器、.pdf 用阅读器、
    .py 用编辑器等，与在系统资源管理器中双击文件的行为一致。
    文件不存在时抛 FileNotFoundError（调用方捕获后 SnackBar 提示）。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在：{path}")
    system = platform.system()
    if system == "Windows":
        os.startfile(path)
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
