"""MCP (Model Context Protocol) client integration using langchain-mcp-adapters.

This module provides integration with MCP servers, using the official
langchain-mcp-adapters package to expose MCP tools as LangChain tools.

Installation:
    pip install langchain-mcp-adapters
"""

from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel, Field

from loguru import logger


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str = Field(..., description="Unique name for this server")
    transport: str = Field(default="stdio", description="Transport type: stdio, sse, or http")
    # stdio transport
    command: str | None = Field(default=None, description="Command to run for stdio transport")
    args: list[str] = Field(default_factory=list, description="Arguments for stdio command")
    env: dict[str, str] | None = Field(default=None, description="Environment variables for stdio")
    # http/sse transport
    url: str | None = Field(default=None, description="URL for http/sse transport")
    headers: dict[str, str] | None = Field(default=None, description="HTTP headers")
    # Tool filtering
    enabled_tools: list[str] | None = Field(default=None, description="List of enabled tool names, or [] for all")


def normalize_server_configs(servers: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    """
    Normalize server configurations to the format expected by MultiServerMCPClient.

    Args:
        servers: Either a dict of server_name -> config, or a list of config dicts

    Returns:
        Dict mapping server names to their transport configurations

    Example:
        ```python
        # Input as list
        servers = [
            {"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
        ]

        # Input as dict
        servers = {
            "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
        }

        # Both produce the same normalized output
        ```
    """
    normalized = {}

    if isinstance(servers, list):
        for server in servers:
            name = server.pop("name", server.get("server_name", ""))
            if not name:
                continue
            normalized[name] = server
    else:
        for name, config in servers.items():
            if not config.get("enabled", True):
                continue
            normalized[name] = config

    return normalized


async def load_mcp_tools(servers: dict[str, Any] | list[dict[str, Any]]) -> list[Any]:
    """
    Load tools from MCP servers using langchain-mcp-adapters.

    Args:
        servers: Server configurations (dict or list format)

    Returns:
        List of LangChain BaseTool instances from all MCP servers

    Example:
        ```python
        from langbot.agent.middleware.mcp import load_mcp_tools

        servers = {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
                "env": {"PATH": "/usr/bin"}
            },
            "github": {
                "url": "https://api.github.com/mcp",
                "headers": {"Authorization": "Bearer token"}
            }
        }

        tools = await load_mcp_tools(servers)

        # Use with DeepAgents
        agent = create_deep_agent(model=model, tools=tools, ...)
        ```

    Server Configuration Format:

        **stdio transport** (local processes):
        ```python
        {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
            "env": {"VAR": "value"}  # optional
        }
        ```

        **sse transport** (server-sent events):
        ```python
        {
            "url": "https://example.com/mcp/sse",
            "headers": {"Authorization": "Bearer token"}  # optional
        }
        ```

        **http transport** (streaming HTTP):
        ```python
        {
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer token"}  # optional
        }
        ```

    Note:
        - Transport type is auto-detected from config (command=stdio, url=sse/http)
        - URLs ending with /sse use SSE transport, others use HTTP
        - Tools are automatically prefixed with server name (e.g., "mcp_filesystem_read_file")
    """
    normalized = normalize_server_configs(servers)

    if not normalized:
        logger.info("No MCP servers configured")
        return []

    try:
        # Create the MCP client
        client = MultiServerMCPClient(normalized)

        # Get all tools from all servers
        tools = await client.get_tools()

        logger.info("Loaded {} tools from {} MCP servers", len(tools), len(normalized))
        for tool in tools:
            logger.debug("  - {} ({})", tool.name, tool.__class__.__name__)

        return tools

    except Exception as e:
        logger.error("Failed to load MCP tools: {}", e)
        logger.debug("MCP error details", exc_info=True)
        return []


class MCPClientManager:
    """
    Manager for MCP client connections with proper lifecycle management.

    This class handles the connection lifecycle and provides cleanup
    for MCP server connections.

    Example:
        ```python
        from langbot.agent.middleware.mcp import MCPClientManager

        async with MCPClientManager() as manager:
            # Add servers
            await manager.add_servers({
                "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]}
            })

            # Get tools
            tools = await manager.get_tools()

            # Use with agent
            ...
        # Connections are automatically closed on exit
        ```
    """

    def __init__(self):
        """Initialize the MCP client manager."""
        self._client: MultiServerMCPClient | None = None
        self._servers: dict[str, Any] = {}

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any):
        """Async context manager exit - cleanup connections."""
        await self.close()

    async def add_servers(self, servers: dict[str, Any] | list[dict[str, Any]]) -> None:
        """
        Add MCP servers to the manager.

        Args:
            servers: Server configurations
        """
        normalized = normalize_server_configs(servers)
        self._servers.update(normalized)

    async def get_tools(self) -> list[Any]:
        """
        Get all tools from configured servers.

        Returns:
            List of LangChain BaseTool instances
        """
        if not self._servers:
            return []

        if self._client is None:
            self._client = MultiServerMCPClient(self._servers)

        return await self._client.get_tools()

    async def close(self) -> None:
        """Close all MCP connections."""
        self._client = None
        self._servers.clear()
