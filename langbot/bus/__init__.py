"""Message bus for decoupled channel-agent communication."""

from langbot.bus.events import InboundMessage, OutboundMessage
from langbot.bus.queue import MessageBus

__all__ = ["InboundMessage", "OutboundMessage", "MessageBus"]
