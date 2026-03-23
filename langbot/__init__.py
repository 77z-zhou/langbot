"""
LangBot - LangChain-based personal AI assistant framework.

A lightweight alternative to nanobot using LangChain DeepAgents.
"""

__version__ = "0.1.0"
__logo__ = "🐽"

from langbot.config import settings
from langbot.config.schema import Config

__all__ = ["Config", "settings"]
