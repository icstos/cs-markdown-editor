"""diff 对比模式双向滚动同步（VSCode 风格）。

把 main.py 的 4-ref + 60ms 异步追赶状态机封装为 DiffScrollSync 类。
原 4 个 ft.use_ref 改为普通实例属性，逻辑完全等价。

依赖项：
- 标准库 asyncio
- flet（ft.Ref 类型注解）

对外接口：
- DiffScrollSync(page_ref, diff_nav_left, diff_nav_right)
- sync_to(target_nav, offset, direction)
- on_left_scroll(offset, max_scroll, viewport_h)
- on_right_scroll(offset, max_scroll, viewport_h)

设计要点：
- 像素同步：一侧滚动时另一侧跟随相同像素偏移（diff_gaps 已让差异行视觉对齐）
- syncing 标记：程序触发滚动期间为 True，被动侧 on_scroll 据此跳过反向同步
- direction 标记："lr"=左主动同步右 / "rl"=右主动同步左 / None
  syncing 期间仅主动侧的 on_scroll 累积 pending，被动侧忽略，避免短文档侧
  clamp 后反向拉回长文档侧（VSCode 行为：一侧到底时另一侧可继续独立滚动）
- pending 追赶：syncing 期间主动侧累积的最新请求，标记清除后追赶，避免连续
  滚轮滚动时中间帧被丢弃导致跟随滞后
"""

import asyncio

import flet as ft


class DiffScrollSync:
    """diff 双向滚动同步状态机。

    构造参数：
      page_ref: ft.Ref，指向 page 对象（用于 run_task 调度异步追赶）
      diff_nav_left: ft.Ref，左侧编辑器导航接口（需有 scroll_to_offset 方法）
      diff_nav_right: ft.Ref，右侧编辑器导航接口

    用法：
      sync = DiffScrollSync(page_ref, diff_nav_left, diff_nav_right)
      # 左侧滚动事件 → sync.on_left_scroll(offset, max_scroll, viewport_h)
      # 右侧滚动事件 → sync.on_right_scroll(offset, max_scroll, viewport_h)
    """

    __slots__ = (
        "_direction",
        "_nav_left",
        "_nav_right",
        "_page_ref",
        "_pending_offset",
        "_pending_target",
        "_syncing",
    )

    def __init__(
        self,
        page_ref: ft.Ref,
        diff_nav_left: ft.Ref,
        diff_nav_right: ft.Ref,
    ) -> None:
        self._page_ref = page_ref
        self._nav_left = diff_nav_left
        self._nav_right = diff_nav_right
        self._syncing: bool = False
        self._direction: str | None = None
        self._pending_target: ft.Ref | None = None
        self._pending_offset: float = 0.0

    def sync_to(self, target_nav: ft.Ref, offset: float, direction: str) -> None:
        """将 target_nav 侧滚动到 offset（像素同步）。

        direction: "lr"=左主动同步右 / "rl"=右主动同步左。标记主动侧，使 syncing
        期间仅主动侧 on_scroll 累积 pending，被动侧忽略，避免 clamp 反向拉回。

        流程：置 syncing+direction 标记 → 调用目标侧 scroll_to_offset(duration=0)
        → 异步等待 Flutter 执行 + 触发目标侧 on_scroll（被动侧被标记拦截）
        → 清除标记 → 追赶 syncing 期间主动侧累积的最新请求。
        """
        target = target_nav.current if target_nav is not None else None
        if target is None or target.scroll_to_offset is None:
            return
        self._syncing = True
        self._direction = direction
        self._pending_target = None
        self._pending_offset = 0.0
        try:
            target.scroll_to_offset(offset)
        except Exception:
            self._syncing = False
            self._direction = None
            return
        page = self._page_ref.current
        if page is None:
            self._syncing = False
            self._direction = None
            return
        page.run_task(self._after_sync)

    async def _after_sync(self) -> None:
        """等待目标侧滚动完成 + on_scroll 触发后，清除同步标记并追赶累积请求。"""
        # duration=0 的 scroll_to 仍需一次 Flutter 帧往返执行 + 触发 on_scroll
        await asyncio.sleep(0.06)
        direction = self._direction
        self._syncing = False
        self._direction = None
        # 追赶：syncing 期间主动侧继续滚动累积的最新 offset
        pending_nav = self._pending_target
        pending_off = self._pending_offset
        if pending_nav is not None:
            self._pending_target = None
            self.sync_to(pending_nav, pending_off, direction or "lr")

    def on_left_scroll(self, offset: float, max_scroll: float, viewport_h: float) -> None:
        """左侧滚动 → 同步右侧。

        syncing 期间：仅当左侧是主动侧（direction=lr）才累积 pending 追赶；
        若左侧是被动侧（direction=rl，被右侧同步触发），忽略，避免反向拉回。
        """
        if self._syncing:
            if self._direction == "lr":
                self._pending_target = self._nav_right
                self._pending_offset = offset
            return
        self.sync_to(self._nav_right, offset, "lr")

    def on_right_scroll(self, offset: float, max_scroll: float, viewport_h: float) -> None:
        """右侧滚动 → 同步左侧。

        syncing 期间：仅当右侧是主动侧（direction=rl）才累积 pending 追赶；
        若右侧是被动侧（direction=lr，被左侧同步触发），忽略，避免反向拉回。
        """
        if self._syncing:
            if self._direction == "rl":
                self._pending_target = self._nav_left
                self._pending_offset = offset
            return
        self.sync_to(self._nav_left, offset, "rl")
