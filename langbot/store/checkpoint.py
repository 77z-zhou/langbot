"""LangGraph storage factory functions.

DeepAgents handles storage through backends:
- FilesystemBackend: Real filesystem access
- StateBackend: Ephemeral storage in agent state
- Checkpointing: Handled by LangGraph checkpointer for state persistence

This module provides simple factory functions for creating storage instances.
"""

from pathlib import Path
from shutil import copy2
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver
from loguru import logger

# Default templates location
_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def ensure_skills_directories(config) -> None:
    """
    确保技能目录存在.

    创建工作区技能目录（如果不存在）。
    所有文件操作限制在 workspace 内，防止路径逃逸。

    Args:
        config: LangBot 配置对象
    """
    # 工作区技能目录
    workspace = config.workspace_path
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Skills directory: {skills_dir}")


def create_checkpointer(
    checkpoint_dir: Path | None = None,
) -> BaseCheckpointSaver:
    """
    创建langgraph的checkpointer, 这里默认使用 SqliteSaver
    """
    if checkpoint_dir is None:
        # Use in-memory checkpointer (default for DeepAgents)
        return MemorySaver()

    # For persistent storage, use SqliteSaver
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver

        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        db_path = checkpoint_dir / "checkpoints.db"
        return SqliteSaver(str(db_path))
    except ImportError:
        logger.warning("SqliteSaver not available, falling back to MemorySaver")
        return MemorySaver()


def ensure_workspace_templates(workspace: Path) -> None:
    """
    确保工作去目录下的模板文件存在
    若不存在, 拷贝 SOUL.md, USER.md, MEMORY.md到工作目录下
    """
    workspace.mkdir(parents=True, exist_ok=True)

    templates = {
        "SOUL.md": "Agent personality and behavior guidelines",
        "USER.md": "User-specific preferences and instructions",
        "MEMORY.md": "Persistent memory for learned user information",
    }

    for template_name, description in templates.items():
        target_path = workspace / template_name
        source_path = _TEMPLATE_DIR / template_name

        if not target_path.exists():
            if source_path.exists():
                copy2(source_path, target_path)
                logger.info(f"Created {template_name} from template: {description}")
            else:
                logger.warning(f"Template not found: {source_path}")
