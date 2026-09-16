"""pytest 收集阶段的忽略规则。

`collect_ignore_glob`：跳过受限沙箱下意外遗留、且无法被本进程读写/删除的目录
（`tests/probe-*` 等）。这类目录由 tempfile.mkdtemp 创建，带进程无法突破的
访问控制；pytest 对其 os.scandir 会抛 PermissionError 并中断整个收集
（环境问题，非项目代码缺陷）。删除该目录后此文件即可移除。
"""

collect_ignore_glob = ["probe-*"]
