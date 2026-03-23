"""Async message queue for decoupled channel-agent communication."""

import asyncio

from langbot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    异步消息总线, 将Channel与Agent解耦  (生产者-消费者模式)
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent."""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

        # Notify subscribers
        for queue in self._subscribers.get(msg.channel, []):
            await queue.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    def subscribe(self, channel: str) -> asyncio.Queue:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        queue: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._subscribers[channel].append(queue)
        return queue

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
