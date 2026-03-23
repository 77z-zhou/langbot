"""Agent factory using LangChain DeepAgents.

This module provides the LangBotAgent class for creating and managing agents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.backends.local_shell import LocalShellBackend
from langchain.chat_models import init_chat_model
from langchain.agents.middleware.human_in_the_loop import HITLRequest, InterruptOnConfig
from langchain_core.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from loguru import logger

from langbot.agent.tools.cron import CronTool
from langbot.agent.tools.web import create_web_fetch_tool, create_web_search_tool
from langbot.agent.mcp import load_mcp_tools
from langbot.config.schema import Config
from langbot.store import create_checkpointer, ensure_workspace_templates


class LangBotAgent:
    """LangBot Agent."""

    def __init__(
        self,
        config: Config,
        checkpointer: BaseCheckpointSaver | None = None,
        backend: SandboxBackendProtocol | None = None,
        tools: list[BaseTool] | None = None,
        cron_service: Any = None,  # 定时服务
    ) -> None:
        self.config: Config = config
        self._external_tools = tools or []
        self._cron_service = cron_service 

        # 初始化 checkpointer
        self._checkpointer = checkpointer or create_checkpointer()

        # 初始化LocalShellBackend
        self._backend = backend or LocalShellBackend(
            root_dir=config.workspace_path,
            virtual_mode=config.agents.defaults.restrict_to_workspace,  # True=限制在workspace, False=完全访问
            inherit_env=True, # True=继承父进程环境变量
        )

        # 确保工作区模板存在（如果缺失，从langbot/templates复制）
        ensure_workspace_templates(config.workspace_path)

        self._agent: CompiledStateGraph | None = None
        self._model: BaseChatModel | None = None

    @property
    def agent(self) -> CompiledStateGraph:
        if self._agent is None:
            self._agent = self._create_agent()
        return self._agent

    def _create_custom_tools(self) -> list[BaseTool]:
        """创建自定义工具."""
        tools = []

        # 1. Web 工具 (如果有配置 Tavily API key)
        search_config = self.config.tools.web.search
        if search_config.api_key:
            tools.append(create_web_search_tool(
                api_key=search_config.api_key,
                max_results=search_config.max_results
            ))

        # 2. web_fetch 工具总是可用(不需要 API key)
        tools.append(create_web_fetch_tool())

        # 2. Cron 工具 (如果有 cron_service)
        if self._cron_service:
            tools.append(CronTool(cron_service=self._cron_service))

        # 3. SendMessage 工具 (需要动态设置 context，暂不添加到默认工具)
        # 这个工具需要在每次调用时动态设置 context，所以不在初始化时添加

        # 4. 外部传入的工具
        tools.extend(self._external_tools)

        logger.info(f"Created {len(tools)} custom tools")
        for tool in tools:
            logger.debug(f"  - {tool.name}")

        return tools

    @classmethod
    async def create_with_mcp(
        cls,
        config: Config,
        checkpointer: BaseCheckpointSaver | None = None,
        backend: FilesystemBackend | None = None,
        tools: list[BaseTool] | None = None,
        cron_service: Any = None,
    ) -> "LangBotAgent":
        """异步创建包含 MCP 工具的 Agent 实例."""
        # 创建基础实例
        agent = cls(
            config=config,
            checkpointer=checkpointer,
            backend=backend,
            tools=tools,
            cron_service=cron_service,
        )

        # 加载 MCP 工具
        mcp_servers = config.tools.mcp_servers
        if mcp_servers:
            try:
                from langchain_core.tools import BaseTool

                # 使用异步导入，避免阻塞
                mcp_tools = await load_mcp_tools(mcp_servers)

                # 将 MCP 工具添加到 _external_tools
                if mcp_tools:
                    agent._external_tools.extend(mcp_tools)
                    logger.info(f"Added {len(mcp_tools)} MCP tools to agent")
            except Exception as e:
                logger.error(f"Failed to load MCP tools: {e}")

        return agent

    @property
    def model(self) -> BaseChatModel:
        if self._model is None:
            self._model = self._init_model()
        return self._model

    def _create_agent(self) -> CompiledStateGraph:
        # 1. Load system prompt (SOUL.md, USER.md)
        system_prompt = self._load_system_prompt()

        # 2. Get all tools: custom + external (需要在 interrupt_on 之前获取)
        all_tools = self._create_custom_tools()

        # 3. Configure interrupt settings (传递工具列表以支持动态 HITL 配置)
        interrupt_on = self._get_interrupt_config(all_tools)

        # Get skills configuration
        skills = self._get_skills_config()

        # Memory file for persistent learning (editable by agent)
        memory_file = str(self.config.workspace_path / "MEMORY.md")

        # Create the agent with DeepAgents
        # FilesystemBackend provides filesystem access, no separate store needed
        agent = create_deep_agent(
            model=self.model,
            system_prompt=system_prompt,
            tools=all_tools if all_tools else None,
            skills=skills,  # Skills middleware
            memory=[memory_file],  # DeepAgents MemoryMiddleware (MEMORY.md)
            backend=self._backend,  # FilesystemBackend for file operations
            checkpointer=self._checkpointer,
            interrupt_on=interrupt_on if interrupt_on else None,
        )

        return agent

    def _init_model(self) -> BaseChatModel:
        params = self.config.get_model_init_params()
        model_kwargs = {
            "model": params.pop("model"),
        }
        if "api_key" in params:
            model_kwargs["api_key"] = params.pop("api_key")
        if "base_url" in params:
            model_kwargs["base_url"] = params.pop("base_url")
        if "temperature" in params:
            model_kwargs["temperature"] = params.pop("temperature")
        if "max_tokens" in params:
            model_kwargs["max_tokens"] = params.pop("max_tokens")

        return init_chat_model(**model_kwargs)

    def _load_system_prompt(self) -> str:
        """加载系统提示词"""
        workspace = self.config.workspace_path
        parts = []

        # Load SOUL.md - agent personality
        soul_path = workspace / "SOUL.md"
        if soul_path.exists():
            with open(soul_path, encoding="utf-8") as f:
                parts.append(f.read())
        else:
            parts.append(self._get_default_soul())

        # Load USER.md - user instructions
        user_path = workspace / "USER.md"
        if user_path.exists():
            with open(user_path, encoding="utf-8") as f:
                parts.append(f"\n{f.read()}")

        return "\n\n".join(parts)

    def _get_default_soul(self) -> str:
        """获取默认的系统提示词."""
        return """# You are LangBot

You are a helpful AI assistant. You are designed to help users with a wide variety of tasks including:

- Writing and editing code
- Answering questions and providing information
- Analyzing data and documents
- Planning and breaking down complex tasks
- Executing shell commands (with user approval)
- Searching the web for current information

## Core Principles

1. **Be helpful and accurate**: Provide clear, correct, and useful information.
2. **Think step-by-step**: Break down complex tasks into manageable steps.
3. **Use tools wisely**: Only use tools when they add value to the response.
4. **Stay focused**: Address the user's request directly and concisely.
5. **Ask for clarification**: When uncertain, ask clarifying questions.

## Working with Files

You can read, write, and edit files in the workspace directory. Always explain what you're doing before modifying files.

## Running Commands

You can execute shell commands, but sensitive operations may require user approval. Always explain what a command will do before running it.

## Memory

You can remember information across sessions. Store important context, preferences, and learned patterns for future reference.
"""

    def _get_skills_config(self) -> list[str]:
        """
        获取 Skills 配置的源路径列表.

        所有技能必须位于 workspace 下的 skills/ 目录，防止路径逃逸。
        支持用户在配置中指定子目录路径。

        Returns:
            技能源路径列表（POSIX 格式，使用 /）
        """
        skills_paths = []

        # 默认技能目录：{workspace}/skills/
        if (self.config.workspace_path / "skills").exists():
            skills_paths.append("/skills/")

        # 用户配置的自定义技能路径（相对于 workspace）
        configured_skills = self.config.agents.defaults.skills or []
        for skill_path in configured_skills:
            # 确保路径以 / 开头且不以 / 结尾
            normalized = skill_path.rstrip("/")
            if not normalized.startswith("/"):
                normalized = "/" + normalized
            skills_paths.append(normalized)

        return skills_paths

    def _get_interrupt_config(self, all_tools: list[BaseTool] | None = None) -> dict[str, InterruptOnConfig] | None:
        """
        获取 HITL 配置。

        根据 agents.defaults.hitl 配置决定哪些工具需要人工确认:
        - mode="all": 所有工具都需要确认（除了 exclude 列表中的）
        - mode="none": 所有工具都不需要确认
        - mode="custom": 根据 tools 配置决定，未配置的工具默认需要确认（安全优先）

        Args:
            all_tools: 所有可用工具的列表（用于动态生成 HITL 配置）

        Returns:
            HITL 配置字典（InterruptOnConfig），或 None（不需要 HITL）
        """
        hitl = self.config.agents.defaults.hitl

        # none 模式：不启用任何 HITL
        if hitl.mode == "none":
            return None

        # DeepAgents 内置工具列表
        builtin_tools = {
            "execute", "write_file", "edit_file", "ls", "read_file",
            "glob", "grep", "task", "write_todos",

        }

        # 创建只包含 approve 和 reject 的 InterruptOnConfig
        hitl_enabled_config = InterruptOnConfig(allowed_decisions=["approve", "reject"])

        # all 模式：所有工具都需要 HITL（排除 exclude 列表）
        if hitl.mode == "all":
            config = {}
            tool_names = set(builtin_tools)

            # 添加自定义工具名称
            if all_tools:
                for tool in all_tools:
                    tool_names.add(tool.name)

            # 应用排除列表
            for excluded in hitl.exclude:
                tool_names.discard(excluded)

            return {name: hitl_enabled_config for name in tool_names}

        # custom 模式：根据 tools 配置决定，未配置的工具默认需要 HITL
        if hitl.mode == "custom":
            config = {}

            # 1. DeepAgents 内置工具：按配置决定，未配置默认需要 HITL
            for tool_name in builtin_tools:
                needs_hitl = hitl.tools.get(tool_name, True)
                if needs_hitl:
                    config[tool_name] = hitl_enabled_config

            # 2. 自定义工具：按配置决定，未配置默认需要 HITL
            if all_tools:
                for tool in all_tools:
                    tool_name = tool.name
                    if tool_name not in config:  # 避免覆盖内置工具配置
                        needs_hitl = hitl.tools.get(tool_name, True)
                        if needs_hitl:
                            config[tool_name] = hitl_enabled_config

            # 3. 应用排除列表（排除列表中的工具不需要 HITL）
            for excluded in hitl.exclude:
                config.pop(excluded, None)

            # 如果所有工具都被排除，返回 None
            if not config:
                return None

            return config

        return None

    async def ainvoke(
        self,
        message: str,
        thread_id: str | None = None,
        on_progress: callable | None = None,
        resume: dict | None = None,
        **kwargs
    ) -> str:
        # 保留 deepagents 的默认 recursion_limit=1000，避免被覆盖变成默认值 25
        config = {"recursion_limit": 1000}
        if thread_id:
            config["configurable"] = {"thread_id": thread_id}

        input_state = {"messages": [message]}
        if resume:
            input_state = Command(resume=resume)

        # 如果提供进度回调，使用 astream_events 监听工具执行和中间输出
        if on_progress:
            final_output = None
            event_count = 0
            self.agent.astream
            async for event in self.agent.astream_events(input_state,config=config,version="v2"):
                event_count += 1
                event_type = event.get("event", "")
                event_name = event.get("name", "")


                # HITL 中断处理
                if event_type == "on_chain_stream":
                    chunk = event["data"].get("chunk")
                    if isinstance(chunk, dict) and "__interrupt__" in chunk:
                        from langgraph.types import Interrupt

                        # chunk['__interrupt__'] 是 Interrupt 对象的元组
                        interrupt_obj: Interrupt = chunk['__interrupt__'][0]
                        hitl_request: HITLRequest = interrupt_obj.value

                        action_requests = hitl_request.get("action_requests", [])
                        review_configs = hitl_request.get("review_configs", [])

                        # 构建 HITL 信息传递给外层
                        hitl_info = {
                            "type": "hitl_request",
                            "action_requests": action_requests,
                            "review_configs": review_configs,
                            "interrupt_id": interrupt_obj.id,
                        }
                        # 通过 on_progress 传递 HITL 请求（使用特殊标记）
                        await on_progress(hitl_info, tool_hint=False)

                        # 返回特殊标记，告诉外层需要 HITL
                        return "__HITL_INTERRUPT__"
                    
                # 模型输出事件
                if event_type == "on_chat_model_end":
                    output = event["data"].get("output")

                    # 检查是否有工具调用
                    if hasattr(output, "tool_calls") and output.tool_calls:
                        # 有工具调用 = 中间输出，通过 send_progress 控制
                        text_content = self._extract_text_from_message(output)
                        if text_content and text_content.strip():
                            await on_progress(text_content, tool_hint=False)
                        final_output = None  # 有工具调用，不是最终输出
                    else:
                        # 最终输出不打印
                        # 非工具调用前输出,保留一轮, 如果还在则证明不是最终输出, 打印
                        if final_output:
                            await on_progress(self._extract_text_from_message(final_output), tool_hint=False)
                        text_content = self._extract_text_from_message(output)
                        if text_content and text_content.strip():
                            final_output = output

                # 工具事件（受 send_tool_hints 控制）
                elif event_type == "on_tool_start" and event_name:
                    await on_progress(f"使用工具【{event_name}】", tool_hint=True)
                elif event_type == "on_tool_end" and event_name:
                    tool_output = event['data'].get('output')
                    tool_content = self._extract_text_from_message(tool_output)
                    if tool_content and tool_content.strip():
                        await on_progress(f"工具【{event_name}调用结果:\n{tool_content}", tool_hint=True)
                elif event_type == "on_tool_error" and event_name:
                    await on_progress(f"工具【{event_name}】调用出错!!", tool_hint=True)

            # 返回最终输出（优先使用 final_output，否则使用最后一次模型输出）
            from loguru import logger
            logger.debug(f"[DEBUG] Total events processed: {event_count}")
            logger.debug(f"[DEBUG] final_output type: {type(final_output)}, value: {final_output}")

            output_to_return = final_output
            result = self._extract_response_content({"messages": [output_to_return]})
            logger.debug(f"[DEBUG] Returning response: {result[:200] if result else '(empty)'}")
            return result

        # 无进度回调时直接调用
        state = await self.agent.ainvoke(input_state, config=config, **kwargs)
        return self._extract_response_content(state)

    def _extract_text_from_message(self, message) -> str:
        if hasattr(message, "text"):
            return message.text
        return str(message)

    def _extract_response_content(self, state: dict) -> str:
        messages = state.get("messages", [])
        if not messages:
            return ""

        last_message = messages[-1]
        if hasattr(last_message, "text"):
            return last_message.text
        return str(last_message)

    def reload(self) -> None:
        self._agent = None
        self._model = None
