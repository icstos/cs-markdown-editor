"""进程内声明式渲染夹具：不依赖 Flutter 前端即可渲染组件树并驱动状态变更。

用途：验证 ``@ft.component`` 组件的渲染树、按钮回调、effect 与状态联动——
这是重构期间唯一能覆盖「App/编辑器装配 + 交互回调」的自动化手段
（``flet.testing`` 需要 scikit-image + Flutter 集成测试宿主，本环境不可用）。

原理：Flet 0.86 的 ``Session`` 把「组件重渲染」与「effect 执行」都投递到
``__pending_updates`` / ``__pending_effects`` 队列，由后台调度任务消费。
夹具构造一个**无传输**的 ``Session``：出站消息被吞掉，队列被取出后同步 drain，
从而在纯 Python 中复现 ``page.session`` 的调度与挂载语义。
"""

from __future__ import annotations

import asyncio
import threading
import types
from collections.abc import Iterator
from typing import Any

import flet as ft
from flet.components.component import Component, Renderer
from flet.controls.context import _context_page
from flet.messaging.session import Session

_F = "_Session__"


class _StubConn:
    """Session 构造所需的最小连接桩：属性占位 + 后台事件循环（``page.run_task``）。"""

    def __init__(self) -> None:
        self.pubsubhub = types.SimpleNamespace()
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)


class _HarnessSession(Session):
    """无传输会话：保留挂载语义与调度队列，丢弃所有出站消息。"""

    def _Session__send_message(self, message: Any) -> None:
        """吞掉出站消息：夹具没有前端可发。"""


class RenderHarness:
    """在进程内渲染组件并驱动其更新 / 副作用 / 交互回调。"""

    def __init__(self) -> None:
        self.conn = _StubConn()
        self.session = _HarnessSession(self.conn)  # type: ignore[arg-type]
        self.page = self.session.page
        self._max_rounds = 50
        self._effects: list[tuple[Any, bool]] = []
        self._updates: set[Any] = set()
        setattr(self.session, _F + "pending_effects", self._effects)
        setattr(self.session, _F + "pending_updates", self._updates)

    def dispose(self) -> None:
        """关闭后台事件循环（测试收尾时调用）。"""
        self.conn.close()

    # ---- 渲染 ----

    def render(self, component, *args, **kwargs) -> Any:
        """渲染根组件、执行挂载期 effect，返回最终渲染树。

        Flet 运行时的时序是「挂载 → 执行组件体 → 执行 effect」，而 ``update()``
        又要求组件已挂载，两者互相依赖。因此这里手工展开：
        1. ``did_mount()`` 标记挂载（子树的挂载由 ``patch_control`` 递归负责）；
        2. ``update()`` 执行组件体，填充 hook 表并生成渲染体；
        3. 补登记 ``deps=[]`` 的挂载期 effect——它们只在 ``did_mount`` 时刻被登记，
           而那时 hook 表还是空的（首次渲染才产生 hook），故需在组件体执行后补一次。
        """
        token = _context_page.set(self.page)
        try:
            root = Renderer().render(component, *args, **kwargs)
            self.page.views[0].controls = root
            node = root[0] if isinstance(root, (list, tuple)) and root else root
            node.did_mount()
            node.update()
            self._schedule_mount_effects()
            self.pump()
            return self.tree
        finally:
            _context_page.reset(token)

    def _schedule_mount_effects(self) -> None:
        """补登记渲染树中所有 ``deps=[]`` 的 effect（首次渲染的挂载语义）。"""
        from flet.components.hooks.use_effect import EffectHook

        for control in walk(self.page.views[0].controls):
            if not isinstance(control, Component):
                continue
            for hook in control._state.hooks:
                if (
                    isinstance(hook, EffectHook)
                    and hook.deps == []
                    and not getattr(hook, "_harness_queued", False)
                ):
                    hook._harness_queued = True  # type: ignore[attr-defined]
                    self._effects.append((hook, False))

    @property
    def tree(self) -> Any:
        """当前渲染树根（组件体，随每次重渲染更新）。"""
        root = self.page.views[0].controls
        node = root[0] if isinstance(root, (list, tuple)) and root else root
        if isinstance(node, Component):
            return node._b
        return node

    def _mount(self, root: Any) -> None:
        """对渲染树中的组件发出挂载通知（触发挂载期 effect）。"""
        for node in walk(root):
            if isinstance(node, Component):
                node.did_mount()

    # ---- 调度 ----

    def interact(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """在页面上下文内执行一次交互并排空调度队列。

        组件回调里的 ``use_state`` setter 会在调用瞬间读取 ``ft.context.page``，
        因此回调本身也必须包在页面上下文内（仅包裹 drain 不够）。
        """
        token = _context_page.set(self.page)
        try:
            result = fn(*args, **kwargs)
            self._drain(self._max_rounds)
            return result
        finally:
            _context_page.reset(token)

    def pump(self, max_rounds: int = 50) -> int:
        """排空调度队列（effect + 重渲染）。返回执行轮数。"""
        token = _context_page.set(self.page)
        try:
            return self._drain(max_rounds)
        finally:
            _context_page.reset(token)

    def _drain(self, max_rounds: int) -> int:
        rounds = 0
        while rounds < max_rounds:
            effects = list(self._effects)
            self._effects.clear()
            updates = set(self._updates)
            self._updates.clear()
            if not effects and not updates:
                break
            rounds += 1
            for hook, is_cleanup in effects:
                self._run_effect(hook, is_cleanup)
            for control in updates:
                control.update()
        return rounds

    def _run_effect(self, hook: Any, is_cleanup: bool) -> None:
        fn = hook.cleanup if is_cleanup else hook.setup
        if fn is None:
            return
        if getattr(fn, "_background_loop", False):
            # 长期驻留的后台循环（定时备份 / 自动保存 / 文件监测）在夹具中跳过：
            # 它们不会自行结束，会在测试收尾阶段留下 pending task。
            return
        result = fn()
        if asyncio.iscoroutine(result):
            asyncio.new_event_loop().run_until_complete(result)

    # ---- 树查询 ----

    def render_tree(self) -> Any:
        """重新读取当前渲染树。"""
        return self.tree

    def find(self, pred) -> list[Any]:
        return [n for n in walk(self.tree) if pred(n)]

    def buttons(self) -> list[Any]:
        """所有可点击控件（on_click 已绑定）。"""
        return self.find(lambda n: getattr(n, "on_click", None) is not None)

    def click(self, control: Any) -> None:
        """触发控件的 on_click 并排空调度队列（模拟一次用户点击）。"""
        self.interact(invoke, control.on_click)


def invoke(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """调用事件回调：按签名自适应传参（多数项目内 lambda 不接收事件对象）。"""
    import inspect

    if fn is None:
        return None
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {"_": None}
    if not params:
        return fn()
    return fn(*args, **kwargs)


def walk(root: Any) -> Iterator[Any]:
    """深度优先遍历控件树，产出全部控件（正文以列表形式传入）。"""
    stack: list[Any] = list(root) if isinstance(root, (list, tuple)) else [root]
    seen: set[int] = set()
    while stack:
        cur = stack.pop(0)
        if not isinstance(cur, ft.BaseControl) or id(cur) in seen:
            continue
        seen.add(id(cur))
        yield cur
        body = getattr(cur, "_b", None)
        if body is not None:
            stack.extend(body if isinstance(body, (list, tuple)) else [body])
        for attr in ("controls", "content", "actions", "title"):
            value = getattr(cur, attr, None)
            if isinstance(value, (list, tuple)):
                stack.extend(value)
            elif isinstance(value, ft.BaseControl):
                stack.append(value)
