"""Auto-discovery for built-in channel modules and external plugins."""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from langbot.channels.base import BaseChannel

_INTERNAL = frozenset({"base", "manager", "registry"})


def discover_channel_names() -> list[str]:
    """
    返回内置的channel名称 e.g [qq, wechat]
    """
    import langbot.channels as pkg

    return [
        name
        for _, name, ispkg in pkgutil.iter_modules(pkg.__path__)
        if name not in _INTERNAL and not ispkg
    ]


def load_channel_class(module_name: str) -> type[BaseChannel]:
    """
    找到BaseChannel子类, 并返回该类
    """
    from langbot.channels.base import BaseChannel as _Base

    mod = importlib.import_module(f"langbot.channels.{module_name}")
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if isinstance(obj, type) and issubclass(obj, _Base) and obj is not _Base:
            return obj
    raise ImportError(f"No BaseChannel subclass in langbot.channels.{module_name}")


def discover_plugins() -> dict[str, type[BaseChannel]]:
    """
    通过entry_points加载插件的方式 来加载外部channel
    """
    from importlib.metadata import entry_points

    plugins: dict[str, type[BaseChannel]] = {}
    try:
        for ep in entry_points(group="langbot.channels"):
            try:
                cls = ep.load()
                plugins[ep.name] = cls
                logger.debug("Loaded channel plugin: {}", ep.name)
            except Exception as e:
                logger.warning("Failed to load channel plugin '{}': {}", ep.name, e)
    except Exception as e:
        logger.debug("No channel plugins found: {}", e)

    return plugins


def discover_all() -> dict[str, type[BaseChannel]]:
    """
    返回所有通道:内置(pkgutil)与外部(entry_points)合并。

    内置通道具有优先级——外部插件不能覆盖内置名称
    Returns:
        Dict mapping channel names to BaseChannel classes
    """
    builtin: dict[str, type[BaseChannel]] = {}
    for modname in discover_channel_names():
        try:
            builtin[modname] = load_channel_class(modname)
        except ImportError as e:
            logger.debug("Skipping built-in channel '{}': {}", modname, e)

    external = discover_plugins()
    shadowed = set(external) & set(builtin)
    if shadowed:
        logger.warning("Plugin(s) shadowed by built-in channels (ignored): {}", shadowed)

    return {**external, **builtin}
