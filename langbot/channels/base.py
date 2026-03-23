"""Base channel interface for chat platforms."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from langbot.bus.events import InboundMessage, OutboundMessage
from langbot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (CLI, QQ, Telegram, etc.) should implement this interface
    to integrate with the langbot message bus.

    Example:
        ```python
        from langbot.channels.base import BaseChannel

        class MyChannel(BaseChannel):
            name = "my"
            display_name = "My Platform"

            async def start(self) -> None:
                # Connect to platform and listen for messages
                while self._running:
                    message = await self.platform.recv()
                    await self._handle_message(
                        sender_id=message.sender,
                        chat_id=message.chat,
                        content=message.text
                    )

            async def stop(self) -> None:
                self._running = False
                await self.platform.close()

            async def send(self, msg: OutboundMessage) -> None:
                await self.platform.send(msg.chat_id, msg.content)
        ```
    """

    name: str = "base"
    display_name: str = "Base"
    transcription_api_key: str = ""

    def __init__(self, config: Any, bus: MessageBus):
        """
        初始化channel

        Args:
            config: channel 配置.
            bus: 消息总线(用于通信).
        """
        self.config = config
        self.bus = bus
        self._running = False

    async def transcribe_audio(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file (optional feature).

        Returns empty string if transcription is not configured.

        Args:
            file_path: Path to audio file

        Returns:
            Transcribed text or empty string on failure
        """
        if not self.transcription_api_key:
            return ""
        try:
            # TODO: Implement transcription (e.g., using Groq Whisper)
            # from langbot.providers.transcription import GroqTranscriptionProvider
            # provider = GroqTranscriptionProvider(api_key=self.transcription_api_key)
            # return await provider.transcribe(file_path)
            logger.warning("{}: audio transcription not implemented", self.name)
            return ""
        except Exception as e:
            logger.warning("{}: audio transcription failed: {}", self.name, e)
            return ""

    @abstractmethod
    async def start(self) -> None:
        """
        启动channel并监听消息.

        这是一个长期运行的异步任务:
        1. 连接聊天平台
        2. 监听消息
        3. 发送消息到总线中处理
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止channel并清理资源."""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """通过channel发送消息"""
        pass

    def is_allowed(self, sender_id: str) -> bool:
        """
        检查sender_id是否有权限访问此channel.

        Empty allow_list → deny all
        "*" in allow_list → allow all
        """
        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty — all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        处理来自聊天平台的传入消息
        该方法检查权限并转发到消息总线.

        Args:
            sender_id: The sender's identifier.
            chat_id: The chat/channel identifier.
            content: 消息内容 msg content.
            --- 可选项 ---
            media: Optional list of media URLs.
            metadata: Optional channel-specific metadata.
            session_key: Optional session key override.
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id,
                self.name,
            )
            return

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """
        返回一个默认配置.
        """
        return {"enabled": False}

    @property
    def is_running(self) -> bool:
        """检查channel状态"""
        return self._running
