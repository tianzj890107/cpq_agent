"""外部转换服务适配器扩展点（Spec §2 目录约定）。

本批**不实现**：向第三方服务上传图纸是数据出境行为，必须先过合规评审与选型拍板
（Spec §6）。这里只保留接口形状，让编排层与缓存键不必因为将来接入而改口径。

只有在启用本适配器时才允许出现网络访问；探测不到配置时永远返回「未安装」。
"""
from __future__ import annotations


class RemoteAdapterNotConfigured(RuntimeError):
    """本批没有可用的外部转换服务。"""


def build(requested: str = ""):
    """永远返回 None：未配置即未安装，绝不用别的适配器顶上。"""
    return None
