"""Chat channels for langbot.

This package provides channel implementations for various chat platforms.
Channels are discovered automatically via pkgutil (built-in) and entry_points (plugins).

Built-in channels:
- qq: QQ bot using botpy SDK

External channels can be added via entry_points in pyproject.toml:
    [project.entry-points."langbot.channels"]
    telegram = "my_package:TelegramChannel"

Example:
    ```python
    from langbot.channels.manager import ChannelManager

    manager = ChannelManager(config, bus)
    await manager.start_all()
    ```
"""

from langbot.channels.base import BaseChannel
from langbot.channels.manager import ChannelManager
from langbot.channels.qq import QQChannel, QQConfig
from langbot.channels.registry import discover_all, discover_channel_names, discover_plugins

__all__ = [
    "BaseChannel",
    "ChannelManager",
    "QQChannel",
    "QQConfig",
    "discover_all",
    "discover_channel_names",
    "discover_plugins",
]
