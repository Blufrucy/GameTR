"""插件框架（路线图 2.1）：扫描/加载/契约测试/API 版本守卫。

对外只暴露 PluginManager。加载失败的插件不阻断服务，标记 disabled + 原因
（plugins.list 可见），调用时按 ENGINE_NOT_SUPPORTED 报错。
"""

from gt_core.plugin.manager import (
    PLUGIN_API_VERSION,
    LoadedPlugin,
    PluginManager,
)

__all__ = ["PluginManager", "LoadedPlugin", "PLUGIN_API_VERSION"]
