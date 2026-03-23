"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from langbot.bus.queue import MessageBus
from langbot.channels.base import BaseChannel
from langbot.channels.registry import discover_all
from langbot.config.schema import Config


class ChannelManager:
    """Channel管理器"""
    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None

        # 初始化所有channel
        self._init_channels()

    def _init_channels(self) -> None:
        """初始化通过 pkgutil + entry_points 插件发现的通道."""
        # Get transcription API key for audio features
        # groq_key = self.config.providers.groq.api_key if hasattr(self.config, 'providers') else None

        # 遍历所有channel, 内建channel + 外部channel
        for name, cls in discover_all().items():
            # 获取该channel的配置
            section = getattr(self.config.channels, name, None)
            if section is None:
                continue

            enabled = (
                section.get("enabled", False)
                if isinstance(section, dict)
                else getattr(section, "enabled", False)
            )
            if not enabled:
                continue

            try:
                channel = cls(section, self.bus)
                self.channels[name] = channel
                logger.info("{} channel enabled", cls.display_name)
            except Exception as e:
                logger.warning("{} channel not available: {}", name, e)

        self._validate_allow_from()

    def _validate_allow_from(self) -> None:
        """验证所有通道是否配置了正确的allow_from参数."""
        for name, ch in self.channels.items():
            allow_from = getattr(ch.config, "allow_from", None)
            if allow_from == []:
                raise SystemExit(
                    f'Error: "{name}" has empty allowFrom (denies all). '
                    f'Set ["*"] to allow everyone, or add specific user IDs.'
                )

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """启动channel."""
        try:
            await channel.start()
        except Exception as e:
            logger.error("Failed to start channel {}: {}", name, e)

    async def start_all(self) -> None:
        """启动所有channel, 并启动 outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # 1. 启动 outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        # 2. 启动channels
        tasks = []
        for name, channel in self.channels.items():
            logger.info("Starting {} channel...", name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        # 3. 等待所有channel处理完成(但是它们运行是 forever的)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """停止所有channel和outbound dispatcher."""
        logger.info("Stopping all channels...")

        # 1. 停止outbound dispatcher
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # 2. 停止所有channel
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception as e:
                logger.error("Error stopping {}: {}", name, e)

    async def _dispatch_outbound(self) -> None:
        """将outbound消息分发到对应的channel."""
        logger.info("Outbound dispatcher started")
        while True:
            try:
                # 1. 监听outbound消息
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)

                # 2. 检查该消息是否为 进度消息
                if msg.metadata.get("_progress"):  # 关于进度消息/工具消息的 处理(根据配置来控制是否输出)
                    send_progress = self.config.channels.send_progress if hasattr(self.config, 'channels') else True
                    send_tool_hints = self.config.channels.send_tool_hints if hasattr(self.config, 'channels') else True
                    if msg.metadata.get("_tool_hint") and not send_tool_hints:
                        continue
                    if not msg.metadata.get("_tool_hint") and not send_progress:
                        continue
                
                # 3. 获取msg所属的channel, 并向该channel发送当前msg
                channel = self.channels.get(msg.channel)
                if channel:
                    try:
                        await channel.send(msg)
                    except Exception as e:
                        logger.error("Error sending to {}: {}", msg.channel, e)
                else:
                    logger.warning("Unknown channel: {}", msg.channel)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        return {
            name: {
                "enabled": True,
                "running": channel.is_running,
            }
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        return list(self.channels.keys())
