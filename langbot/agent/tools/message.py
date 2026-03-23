"""Message tool for sending messages across channels."""

from typing import Any, Awaitable, Callable, Optional, Type

from langchain_core.callbacks import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from langbot.bus.events import OutboundMessage


class SendMessageInput(BaseModel):
    """Input schema for SendMessageTool."""

    content: str = Field(description="The message content to send")
    channel: str | None = Field(
        default=None,
        description="Optional: target channel (cli, qq, telegram, etc.). Uses current channel if not specified."
    )
    chat_id: str | None = Field(
        default=None,
        description="Optional: target chat/user ID. Uses current chat if not specified."
    )


class SendMessageTool(BaseTool):
    """
    Tool for sending messages to users across different channels.

    This tool maintains context about the current message (channel, chat_id)
    and allows sending responses back to users.

    Example:
        ```python
        from langbot.agent.tools.message import SendMessageTool

        tool = SendMessageTool(
            send_callback=handle_outbound,
            default_channel="cli",
            default_chat_id="default"
        )

        # Set context for current message
        tool.set_context("telegram", "123456")

        # Use directly as a LangChain tool
        result = await tool._arun("Hello!", channel="telegram", chat_id="123456")
        ```
    """

    name: str = "send_message"
    description: str = (
        "Send a message to a user on a chat channel. "
        "Use this to communicate with users across different channels "
        "(cli, qq, etc.). If no target is specified, "
        "sends to the current channel/chat."
    )
    args_schema: Type[BaseModel] = SendMessageInput

    # Internal state (not part of BaseTool interface)
    _send_callback: Callable[[OutboundMessage], Awaitable[None]] | None
    _default_channel: str
    _default_chat_id: str
    _sent_in_current_turn: bool

    class Config:
        """Pydantic config for SendMessageTool."""

        arbitrary_types_allowed = True

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        **kwargs: Any,
    ):
        """
        Initialize the message tool.

        Args:
            send_callback: Async callback for sending outbound messages
            default_channel: Default channel to send to
            default_chat_id: Default chat ID to send to
            **kwargs: Additional arguments passed to BaseTool
        """
        super().__init__(**kwargs)
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._sent_in_current_turn = False

    def set_context(
        self,
        channel: str,
        chat_id: str,
    ) -> None:
        """
        Set the current message context.

        This should be called at the start of each message turn
        to establish where to send responses.

        Args:
            channel: The current channel name
            chat_id: The current chat ID
        """
        self._default_channel = channel
        self._default_chat_id = chat_id

    def set_send_callback(
        self,
        callback: Callable[[OutboundMessage], Awaitable[None]],
    ) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking. Call at start of each agent turn."""
        self._sent_in_current_turn = False

    def did_send_in_turn(self) -> bool:
        """Check if a message was sent to the original channel this turn."""
        return self._sent_in_current_turn

    def _run(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        """
        Synchronous implementation - not supported.

        This tool requires async execution. Use _arun() instead.
        """
        raise NotImplementedError(
            "SendMessageTool only supports async execution. "
            "Use await tool._arun(...) or invoke through an async agent."
        )

    async def _arun(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> str:
        """
        Async implementation - sends the message.

        Args:
            content: Message content to send
            channel: Target channel (uses default if None)
            chat_id: Target chat ID (uses default if None)
            run_manager: Optional async callback manager

        Returns:
            Status message indicating success or failure
        """
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=[],
        )

        try:
            await self._send_callback(msg)
            if channel == self._default_channel and chat_id == self._default_chat:
                self._sent_in_current_turn = True
            return f"Message sent to {channel}:{chat_id}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
