"""非编辑器原生输入框的焦点跟踪（快捷键「焦点域」门控）。

KeyDispatcher 依据「当前键盘焦点属于谁」决定快捷键作用域：
- 编辑器自己的输入面（光标 TextField / 原文 TextField / 代码块 / 表格等岛屿）
  → 编辑器域，文档编辑 / 选区 / 导航 / 剪贴板快捷键正常生效；
- 其余任何原生输入框（侧边栏搜索 / 替换 / 过滤框、文件操作对话框输入、Git 面板
  提交框与过滤框等）→ 外部输入域，文档层快捷键一律不消费、交原生输入框自己处理。
  典型场景：焦点在搜索框时按 Ctrl+A 应只全选搜索框文本，不再误全选 / 误改编辑器
  文档。

实现：每个外部输入框创建时调用 :func:`native_focus_hooks` 生成一对
on_focus / on_blur 闭包（每个输入框独立 token）：
- on_focus：shared_ref.current = token（进入外部输入域）；
- on_blur：仅当 shared_ref.current 是本输入框的 token 时才清空——防止
  A→B 焦点切换时「B.on_focus 先于 A.on_blur」的竞态把 B 误清空。

**具名域（domain）**：传 ``name`` 时 token 用该字符串而不是匿名 ``object()``，
让 KeyDispatcher 能进一步分辨「焦点在哪个输入框里」。目前唯一的用途是 Git 提交框
（:data:`DOMAIN_GIT_COMMIT`）：VSCode 的 Ctrl+Enter 只在提交框内表示「提交」，
而同一组合键在编辑器里表示「切换原文模式」——不区分焦点域就只能二选一。
匿名 token 仍是 ``object()``，历史调用点行为不变。
"""

from collections.abc import Callable

#: Git 面板提交框的焦点域名（KeyDispatcher 据此放行 Ctrl+Enter 提交）
DOMAIN_GIT_COMMIT = "git_commit"


def native_focus_hooks(
    ref, name: str | None = None
) -> tuple[Callable | None, Callable | None]:
    """为单个原生输入框生成 (on_focus, on_blur) 焦点跟踪闭包。

    ref 为 None（该输入框未接入焦点域跟踪）时返回 (None, None)。
    token 每输入框独立：on_blur 仅当 ref.current 仍指向本输入框 token 才清空。
    name 非空时 token 为具名字符串（供 KeyDispatcher 分辨具体输入框）。
    """
    if ref is None:
        return None, None

    token: object = name if name else object()

    def _on_focus(_e=None):
        ref.current = token

    def _on_blur(_e=None):
        if ref.current is token:
            ref.current = None

    return _on_focus, _on_blur
