"""Storage layer for session persistence.

This module provides factory functions for creating LangGraph storage
instances that work with DeepAgents' built-in middleware.

Note: DeepAgents uses backends (FilesystemBackend, StateBackend, StoreBackend)
for file operations. LangGraph Store is only needed when using StoreBackend.
"""

from langbot.store.checkpoint import (
    create_checkpointer,
    ensure_skills_directories,
    ensure_workspace_templates,
)

__all__ = [
    "create_checkpointer",
    "ensure_skills_directories",
    "ensure_workspace_templates",
]
