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
        # 收尾要同时避开两条噪声（噪声里很容易藏住真正的回归信号）：
        # 1. **不** close → loop 被 GC 时抛 `ResourceWarning: unclosed event loop`；
        # 2. 直接 close → 仍挂着的任务让 asyncio 逐个打 "Task was destroyed but it is
        #    pending!"。
        # 故先把未完成任务**取消并等它们真正结束**（`cancel()` 只是打标记，必须让
        # 循环再跑一拍才会落地），再停循环、再 close。
        async def _drain() -> None:
            me = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not me]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_drain(), self.loop).result(timeout=2)
        except Exception:  # noqa: BLE001 - 收尾阶段尽力而为：超时/循环已停都直接继续
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)
        if not self.loop.is_running():
            self.loop.close()


class _HarnessSession(Session):
    """无传输会话：保留挂载语义与调度队列，丢弃所有出站消息（但留档）。

    `sent` 里按发送顺序留下每条出站消息。夹具本身不需要它，但**协议层的不变量只能
    在这里验证**——例如"编辑框的 `value` 有没有被回灌"：组件内部拿到的是自己算的
    渲染参数，而真正决定客户端行为的是 flet 差分出来的补丁（属性相同就不发补丁，
    发了才会让客户端重设文本、光标跳走）。留档让这类断言不必去猜。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.sent: list[Any] = []

    def _Session__send_message(self, message: Any) -> None:
        """吞掉出站消息（夹具没有前端可发），同时记进 `sent` 供协议层断言。"""
        self.sent.append(message)

    async def invoke_method(
        self,
        control_id: int,
        method_name: str,
        args: Any,
        timeout: float | None = None,
    ) -> Any:
        """控件方法调用视为立即生效。

        真实客户端会回执 ``invokeMethod``，而 ``Session.invoke_method`` 在无回执时会
        ``await`` 到超时（``timeout=None`` 即永久等待）。夹具里没有前端，不接管这一步
        会把任何 ``await control.focus()`` 之类的调用挂死——连 effect 都跑不完。
        这里直接返回：方法在真机上的效果（聚焦 / 滚动等）由真机探针验证。
        """
        return None


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
        3. 补登记**全部**挂载期 effect——它们只在 ``did_mount`` 时刻被登记，
           而那时 hook 表还是空的（首次渲染才产生 hook），故需在组件体执行后补一次。
           详见 ``_schedule_mount_effects()`` 对真实时序的说明。
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
        """补登记挂在首次渲染上的全部 effect（对齐 ``Component._run_mount_effects``）。

        真实运行时的时序是「**先跑组件体，再挂载**」——补丁遍历
        （``controls/object_patch.py`` → ``_before_update_safe()``）里执行组件体，
        此时 ``_state.mounted`` 仍为 ``False``，``_run_render_effects()`` 会在第一行
        直接返回；遍历结束后 ``patch_control`` 才调 ``did_mount()``，把 ``mounted``
        置 True 并调 ``_run_mount_effects()``，其源码注释原话是
        "all effects are running on mount"——**不区分 deps**。也就是说
        ``use_effect(fn, [x])`` 在挂载那一次同样会执行（这正是 React 的
        ``useEffect`` 语义），只有**后续**重渲染才按 deps 比较决定是否重跑。

        夹具为了绕开「``update()`` 要求组件已挂载」的死锁，顺序被改成了
        ``did_mount()`` → ``update()``：组件体执行时 ``mounted`` 已经是 True，
        于是 ``deps=None`` 的 effect 会被 ``_run_render_effects`` 抢跑一次
        （真实运行时不会，那时 mounted 还是 False）。所以这里按 **hook 身份去重**，
        只补登记尚未进队列的那些，避免 ``deps=None`` 的 effect 首渲染跑两遍。
        """
        from flet.components.hooks.use_effect import EffectHook

        for control in walk(self.page.views[0].controls):
            if not isinstance(control, Component):
                continue
            for hook in control._state.hooks:
                if not isinstance(hook, EffectHook):
                    continue
                if getattr(hook, "_harness_queued", False):
                    continue
                if any(queued is hook for queued, _ in self._effects):
                    continue
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
            # 与 flet `Session.__updates_scheduler` 严格同序：先跑控件更新（组件重渲染），
            # 再跑 effect。顺序反了会让 effect 读到重建前的旧控件——ref 尚未重新绑定，
            # 「渲染提交后操作控件」这类 effect（聚焦、回写光标）会打到已废弃的对象上，
            # 夹具里看似失败、真机上却是对的（或反之）。
            for control in updates:
                control.update()
            for hook, is_cleanup in effects:
                self._run_effect(hook, is_cleanup)
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
