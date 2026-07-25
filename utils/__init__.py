"""通用工具层：无项目内依赖的纯工具函数与常量。

子模块：
- file_helpers：文件名派生等文件工具
- segment_helpers：段类型常量、显示文本、包裹语法、段拆分
- table_helpers：表格行解析与拼接、对齐正则
- text_layout：文本像素测量与图片尺寸

对外接口：通过子模块访问，例如 utils.file_helpers.file_name(path)。
"""

from utils import (  # noqa: F401
    file_helpers,
    segment_helpers,
    table_helpers,
    text_layout,
)
