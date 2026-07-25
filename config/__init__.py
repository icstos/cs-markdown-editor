"""配置层：应用级常量、默认设置、示例文档。

依赖项：services.shortcuts.DEFAULT_SHORTCUTS（仅用于 shortcuts 默认值深合并）。
对外接口：config.settings.DEFAULT_SETTINGS / load_settings / save_settings；
         config.sample.SAMPLE_MD。
"""

from config import sample, settings  # noqa: F401  (re-export)
