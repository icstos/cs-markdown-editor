"""数据结构层：文档三级状态模型（文档 / 行 / 文本段）。

从原 models.py 迁移为 models/ 包，__init__.py 重新导出所有公共符号，
保持 `from models import Document` 等原有引用完全兼容。

对外接口：
- SegType / BlockType：段类型与块类型枚举
- Segment / Line / Document：三级数据模型（均 @ft.observable）
"""

from models.document import (
    BlockType,
    Document,
    Line,
    SegType,
    Segment,
)

__all__ = ["BlockType", "Document", "Line", "SegType", "Segment"]
