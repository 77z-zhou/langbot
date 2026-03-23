"""Configuration loading utilities."""

import json
from pathlib import Path

from langbot.channels.registry import discover_all
from langbot.config.schema import Config


# 全局变量，用于存储当前配置路径（支持多实例）
_current_config_path: Path | None = None


def set_config_path(path: Path) -> None:
    """Set the current config path (used to derive data directory)."""
    global _current_config_path
    _current_config_path = path


def get_config_path() -> Path:
    """获取配置文件路径."""
    if _current_config_path:
        return _current_config_path
    return Path.home() / ".langbot" / "config.json"


def load_config(config_path: Path | None = None) -> Config:
    """加载配置文件或者创建默认配置."""
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return Config.model_validate(data)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    首次保存时自动添加所有已发现 channel 的默认配置。

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)

    # 首次保存时，自动添加所有已发现 channel 的默认配置
    if not path.exists() and "channels" in data:
        for name, channel_cls in discover_all().items():
            if name not in data["channels"]:
                data["channels"][name] = channel_cls.default_config()

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
