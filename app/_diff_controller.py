"""对比标签控制器（从 main.py 闭包抽取）。

闭包组：get_text_for_compare / select_for_compare / compare_with_selected /
on_diff_dirty_change

跨组依赖（通过 ctx 装配槽，调用时读取）：
- file_dialogs 组：show_snack（错误/状态提示）
- tab_management 组：update_active（复用空白标签时不可变更新）
- 共享：is_blank_untitled / tab_paths（纯函数，直接导入）

设计要点：
- 对比以 type=="diff" 标签形式管理，可与普通编辑标签并存、切换、关闭。
- get_text_for_compare 优先用已打开标签的内存内容（含未保存修改），与
  VSCode 行为一致——比较未保存草稿也能反映最新编辑结果。
- compare_with_selected 复用当前空白未命名标签（完全替换，避免残留 editor
  字段），否则追加新标签。
- on_diff_dirty_change 仅当状态真正变化时更新标签，避免高频回调触发重渲染；
  同步写 tabs_ref.current，使 autosave 等异步读取者立即拿到最新脏状态。

依赖项：
- os / parser（解析/序列化）
- services.file_io.read_text
- app._tab_helpers（is_blank_untitled / tab_paths）
"""

import os

import parser
from app._tab_helpers import is_blank_untitled, tab_paths
from services.file_io import read_text


def build_diff_controller(ctx):
    """构造对比标签控制器闭包组。

    返回 dict[str, Callable]：
    get_text_for_compare / select_for_compare / compare_with_selected /
    on_diff_dirty_change
    """

    def get_text_for_compare(path: str) -> str:
        """获取用于比较的文本：优先用已打开标签的内存内容（含未保存修改），否则读磁盘。

        这样比较未保存的草稿也能反映最新编辑结果，与 VSCode 行为一致。
        """
        for t in ctx.tabs_ref.current:
            if path in tab_paths(t):
                # diff 标签：返回对应侧文档内容；editor 标签：返回 document
                if t.get("type") == "diff":
                    if t.get("left_path") == path:
                        return parser.serialize(t["left_doc"])
                    return parser.serialize(t["right_doc"])
                return parser.serialize(t["document"])
        try:
            return read_text(path)
        except Exception as e:
            ctx.show_snack(f"读取失败：{e}")
            return ""

    def select_for_compare(path: str):
        """记录比较源文件路径，供后续「与已选项目进行比较」使用。"""
        ctx.set_compare_source(path)
        ctx.show_snack(f"已选择以进行比较：{os.path.basename(path)}")

    def compare_with_selected(right_path: str):
        """用已选源（左）与 right_path（右）创建对比标签。

        两侧均加载为可编辑 Document，实时计算行级 diff 标记和间隙对齐。
        对比以 type=="diff" 标签形式存在，可与普通编辑标签并存、切换、关闭。
        """
        src = ctx.compare_source
        if not src:
            ctx.show_snack("请先「选择以进行比较」一个文件")
            return
        if os.path.abspath(src) == os.path.abspath(right_path):
            ctx.show_snack("不能与同一个文件进行比较")
            return
        left_text = get_text_for_compare(src)
        right_text = get_text_for_compare(right_path)
        left_doc = parser.parse_markdown(left_text)
        left_doc.file_path = src
        right_doc = parser.parse_markdown(right_text)
        right_doc.file_path = right_path
        new_tab = {
            "type": "diff",
            "left_path": src,
            "right_path": right_path,
            "left_doc": left_doc,
            "right_doc": right_doc,
            "left_dirty": False,
            "right_dirty": False,
        }
        # 复用当前空白未命名标签（完全替换，避免残留 editor 字段），否则追加新标签
        if is_blank_untitled(ctx.cur_tab):
            new_tabs = list(ctx.tabs)
            new_tabs[ctx.active_index] = new_tab
            new_idx = ctx.active_index
        else:
            new_tabs = list(ctx.tabs)
            new_tabs.append(new_tab)
            new_idx = len(new_tabs) - 1
        ctx.set_tabs(new_tabs)
        ctx.tabs_ref.current = new_tabs
        ctx.set_active_index(new_idx)
        ctx.active_index_ref.current = new_idx
        ctx.set_diff_active_pane(0)
        ctx.diff_active_pane_ref.current = 0
        ctx.set_session(ctx.session + 1)

    def on_diff_dirty_change(side: int, dirty: bool):
        """对比标签侧文档脏状态变化回调。

        side: 0=左, 1=右。仅当状态真正变化时更新标签，避免高频回调触发重渲染。
        同步写 tabs_ref.current，使 autosave 等异步读取者立即拿到最新脏状态。
        """
        ts = list(ctx.tabs_ref.current)
        ai = ctx.active_index_ref.current
        if not (0 <= ai < len(ts)) or ts[ai].get("type") != "diff":
            return
        tab = ts[ai]
        key = "left_dirty" if side == 0 else "right_dirty"
        if tab.get(key) == dirty:
            return
        ts[ai] = {**tab, key: dirty}
        ctx.set_tabs(ts)
        ctx.tabs_ref.current = ts

    return {
        "get_text_for_compare": get_text_for_compare,
        "select_for_compare": select_for_compare,
        "compare_with_selected": compare_with_selected,
        "on_diff_dirty_change": on_diff_dirty_change,
    }
