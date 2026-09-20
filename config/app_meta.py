"""应用元信息：名称、版本、许可与对外链接的**唯一来源**。

设置面板「关于」页与全局「帮助」菜单都从这里取数据。集中一处是为了避免
「官网链接改了、但某个入口忘了改」这类静默漂移——外链一旦分散，没有任何
测试能发现两处不一致。

⚠️ 带 `# TODO` 的常量目前是**占位值**，替换为正式内容后「关于」页与帮助菜单
   会同时生效，无需改动其它文件。
"""

import platform
import sys
from importlib.metadata import PackageNotFoundError, version

# ---------------------------------------------------------------------------
# 应用身份
# ---------------------------------------------------------------------------

APP_NAME = "CS Markdown Editor"
APP_TAGLINE = "Typora 风格的段级所见即所得 Markdown 编辑器"
APP_AUTHOR = "CSTOS"
APP_LICENSE = "MIT"

# 发行版名（[project].name，importlib.metadata 按它查版本）
DIST_NAME = "cs-markdown-editor"

# 源码直跑（未 `pip install -e .`）时的回退版本，须与 pyproject.toml
# 的 [project].version 保持一致；正常安装态下不会被用到。
_FALLBACK_VERSION = "0.1.0"


def app_version() -> str:
    """返回应用版本，优先取安装元数据（与 pyproject.toml 同源）。"""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return _FALLBACK_VERSION


def runtime_summary() -> str:
    """一行运行环境摘要（「关于」页展示，便于用户报障时直接照抄）。"""
    try:
        flet_ver = version("flet")
    except PackageNotFoundError:
        flet_ver = "未知"
    return (
        f"{platform.system()} {platform.release()} · "
        f"Python {sys.version.split()[0]} · Flet {flet_ver}"
    )


# ---------------------------------------------------------------------------
# 对外链接（「关于」页 / 帮助菜单共用）
# ---------------------------------------------------------------------------

URL_OFFICIAL = "https://example.com"              # TODO: 官方网站
URL_CHANGELOG = "https://example.com/changelog"   # TODO: 更新日志
URL_PRIVACY = "https://example.com/privacy"       # TODO: 隐私条款
URL_FEEDBACK = "https://example.com/feedback"     # TODO: 问题反馈
URL_CREDITS = "https://example.com/credits"       # TODO: 鸣谢

SUPPORT_EMAIL = "support@example.com"             # TODO: 联系邮箱
COMMUNITY_URL = "https://example.com/community"   # TODO: 官方社群
USER_MANUAL_URL = "https://example.com/manual"    # TODO: 用户手册


def mailto(address: str = "") -> str:
    """把邮箱转成 mailto 链接（空地址返回空串，调用方据此禁用按钮）。"""
    address = (address or "").strip()
    return f"mailto:{address}" if address else ""


# 「关于」页底部相关链接：(图标名, 标题, 地址)
EXTRA_LINKS: tuple[tuple[str, str, str], ...] = (
    ("LANGUAGE", "官方网站", URL_OFFICIAL),
    ("HISTORY_TOGGLE_OFF", "更新日志", URL_CHANGELOG),
    ("PRIVACY_TIP_OUTLINED", "隐私条款", URL_PRIVACY),
    ("BUG_REPORT_OUTLINED", "问题反馈", URL_FEEDBACK),
    ("VOLUNTEER_ACTIVISM", "鸣谢", URL_CREDITS),
)
