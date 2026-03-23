"""CLI commands for langbot."""

import asyncio
import os
import select
import signal
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer    #  CLI 框架
from prompt_toolkit import PromptSession  #  高级终端交互（历史、多行、粘贴）
from prompt_toolkit.formatted_text import ANSI, HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit import print_formatted_text
from rich.console import Console  #  美化输出（Markdown、颜色、表格）
from rich.markdown import Markdown
from rich.table import Table

from langbot import __logo__, __version__
from langbot.agent.factory import LangBotAgent
from langbot.bus.events import InboundMessage, OutboundMessage
from langbot.bus import MessageBus
from langbot.config.schema import Config
from langbot.config.settings import get_config_path, load_config
from langbot.store import ensure_skills_directories, ensure_workspace_templates

# CLI 应用入口
app = typer.Typer(
    name="langbot",
    help=f"{__logo__} langbot - Personal AI Assistant",
    no_args_is_help=True,
)

# Rich 控制台
console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"} # 退出命令集合

# 全局输入会话
_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # 终端原始状态（用于恢复）


def _flush_pending_tty_input() -> None:
    """丢弃用户在等待期间输入的字符."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """退出时恢复终端原始状态(echo、缓冲)"""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """初始化 prompt_toolkit 会话，保存会话历史到 ~/.langbot/cli_history.txt."""

    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # 保存终端状态以便退出时恢复
    try:
        import termios  
        # 保存当前终端（stdin）的原始输入配置
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".langbot" / "cli_history.txt"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # 是否默认启用多行输入模式。
    )


def _make_console() -> Console:
    """创建新的 Rich 控制台实例"""
    return Console(file=sys.stdout)


def _render_interactive_ansi(render_fn) -> str:
    """将 Rich 输出转为 ANSI,与 prompt_toolkit 兼容."""
    ansi_console = Console(
        force_terminal=True,
        color_system=console.color_system or "standard",
        width=console.width,
    )
    with ansi_console.capture() as capture:
        render_fn(ansi_console)
    return capture.get()


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Rich 打印 agent 最终响应(带 logo、Markdown)."""
    c = _make_console()
    c.print()
    c.print(f"[cyan]{__logo__} langbot[/cyan]")
    content = Markdown(response) if render_markdown else response
    c.print(content)
    c.print()


async def _print_interactive_line(text: str) -> None:
    """在交互模式下打印进度行(异步安全)."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: c.print(f"  [dim]↳ {text}[/dim]")
        )
        print_formatted_text(ANSI(ansi), end="")

    from prompt_toolkit.application import run_in_terminal
    await run_in_terminal(_write)


async def _print_interactive_response(response: str, render_markdown: bool) -> None:
    """在交互模式下打印完整响应."""
    def _write() -> None:
        ansi = _render_interactive_ansi(
            lambda c: (
                c.print(),
                c.print(f"[cyan]{__logo__} langbot[/cyan]"),
                c.print(Markdown(response) if render_markdown else response),
                c.print(),
            )
        )
        print_formatted_text(ANSI(ansi), end="")

    from prompt_toolkit.application import run_in_terminal
    await run_in_terminal(_write)


def _hitl_approve_simple(
    tool_name: str,
    tool_description: str,
    current: int,
    total: int
) -> tuple[str, str | None]:
    """
    使用 typer.confirm 进行 HITL 批准选择。

    Args:
        tool_name: 工具名称
        tool_description: 工具描述
        current: 当前索引（1-based）
        total: 总数

    Returns:
        (action, reason) 元组
        - action: "approve" 或 "reject"
        - reason: 拒绝原因（仅当 action="reject" 时有值）
    """
    console.print(f"\n🔔 需要批准工具调用 ({current}/{total}):")
    console.print(f"  [cyan bold]{tool_name}[/cyan bold]")
    console.print(f"  {tool_description}")
    console.print()

    # 使用 typer.confirm 进行确认
    if typer.confirm("是否批准？", default=True):
        return ("approve", None)
    else:
        # 拒绝，询问原因
        console.print()
        reason = typer.prompt("请输入拒绝原因", default="")
        return ("reject", reason if reason else "用户拒绝")



class _ThinkingSpinner:
    """Spinner 上下文管理器，支持暂停."""

    def __init__(self, enabled: bool):
        self._spinner = console.status(
            "[dim]langbot is thinking...[/dim]", spinner="dots"
        ) if enabled else None
        self._active = False

    def __enter__(self):
        if self._spinner:
            self._spinner.start()
        self._active = True
        return self

    def __exit__(self, *exc):
        self._active = False
        if self._spinner:
            self._spinner.stop()
        return False

    @contextmanager
    def pause(self):
        """打印进度时暂时停止旋转器."""
        if self._spinner and self._active:
            self._spinner.stop()
        try:
            yield
        finally:
            if self._spinner and self._active:
                self._spinner.start()


async def _print_interactive_progress_line(text: str, thinking: _ThinkingSpinner | None) -> None:
    """打印交互式进度行."""

    # 打印进度时暂停 spinner
    with thinking.pause() if thinking else nullcontext():
        await _print_interactive_line(text)


def _print_cli_progress_line(text: str, thinking: _ThinkingSpinner | None = None) -> None:
    """单消息模式下打印进度."""
    with thinking.pause() if thinking else nullcontext():
        console.print(f"  [dim]↳ {text}[/dim]")


def _is_exit_command(command: str) -> bool:
    """判断是否为退出命令."""
    return command.lower() in EXIT_COMMANDS


def _parse_session(session: str) -> tuple[str, str]:
    """
    统一解析 session 参数为 (channel, chat_id)。

    支持:
    - "cli:direct" → ("cli", "direct")
    - "telegram:user123" → ("telegram", "user123")
    - "direct" → ("cli", "direct")

    Args:
        session: Session 字符串

    Returns:
        (channel, chat_id) 元组
    """
    if ":" in session:
        channel, chat_id = session.split(":", 1)
        return channel, chat_id
    return "cli", session


async def _read_interactive_input_async() -> str:
    """异步读取用户输入(支持 Ctrl+D → KeyboardInterrupt)."""
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} langbot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """langbot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


"""
    onboard 初始化配置
    langbot onboard -w /path/to//ws      指定工作区域
    langbot onboard -c /path/to/config   指定配置文件
"""
@app.command()
def onboard(
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """初始化工作区(workspace)和配置文件(config file)"""
    from langbot.config.schema import Config as ConfigClass

    config_path = get_config_path()  # 获取配置文件路径

    if config:  # 用户指定了配置文件, 则使用用户的配置
        config_path = Path(config).expanduser().resolve()
        console.print(f"[dim]Using config: {config_path}[/dim]")

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if typer.confirm("Overwrite with defaults?"):  # 是否要用默认配置覆盖当前配置
            cfg = ConfigClass()
            if workspace:
                cfg.agents.defaults.workspace = workspace
            _save_config(cfg, config_path)
            console.print(f"[green]✓[/green] Config reset to defaults")
        else:
            console.print("Existing config preserved.")
            raise typer.Exit(0)
    else:
        cfg = ConfigClass()
        if workspace:
            cfg.agents.defaults.workspace = workspace
        _save_config(cfg, config_path)
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # 确保工作区目录存在
    ws = Path(workspace or cfg.agents.defaults.workspace or Path.home() / ".langbot")
    ws.mkdir(parents=True, exist_ok=True)
    ensure_workspace_templates(ws)  # 确保工作目录下的模板文件存在 xxx.md
    console.print(f"[green]✓[/green] Workspace ready at {ws}")

    # 确保技能目录存在
    ensure_skills_directories(cfg)
    console.print(f"[green]✓[/green] Skills directories ready")

    # Show next steps
    console.print(f"\n{__logo__} langbot is ready!")
    console.print("\nNext steps:")
    console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
    console.print(f"  2. Chat: [cyan]langbot agent[/cyan]")


def _save_config(config: Config, path: Path) -> None:
    """保存配置到文件中.

    Args:
        config: 配置对象
        path: 保存路径
        add_channels: 是否添加所有 channel 默认配置（默认 True）
    """
    import json
    from langbot.channels.registry import discover_all

    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(by_alias=True, exclude_none=True)

    # 添加所有已发现 channel 的默认配置
    for name, channel_cls in discover_all().items():
        if name not in data["channels"]:
            data["channels"][name] = channel_cls.default_config()

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ============================================================================
# Agent Commands
# ============================================================================


"""
    agent -m  单条消息模式 - 直接调用，无需总线
    agent 

"""
@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show runtime logs during chat"),
):
    """直接和LangBot进行互动"""
    from loguru import logger

    from langbot.agent.factory import LangBotAgent
    from langbot.bus.queue import MessageBus

    config: Config = _load_runtime_config(config, workspace)

    if logs:
        logger.enable("langbot")
    else:
        logger.disable("langbot")

    bus = MessageBus()
    agent = LangBotAgent(config)

    if message:
        # 单条消息模式 - 直接调用，无需总线
        async def run_once():
            _thinking = _ThinkingSpinner(enabled=not logs)

            # 进度回调：显示工具执行和文本输出
            async def on_progress(content: str, tool_hint: bool) -> None:
                # 工具提示受 send_tool_hints 控制，文本进度受 send_progress 控制
                if tool_hint and not config.channels.send_tool_hints:
                    return
                if not tool_hint and not config.channels.send_progress:
                    return
                _print_cli_progress_line(content, _thinking)

            with _thinking:
                response = await agent.ainvoke(message, thread_id=session, on_progress=on_progress)
            _print_agent_response(response, render_markdown=markdown)

        asyncio.run(run_once())
    else:
        # 交互模式 - 像其他通道一样通过总线路由
        _init_prompt_session()  # 初始化PromptSession
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        # 统一解析 session
        cli_channel, cli_chat_id = _parse_session(session)

        # Signal handlers for clean exit
        def _handle_signal(signum, frame):
            # 将数字信号编号转换为枚举类型 signal.Signals，然后获取其名称
            # signum=2→ signal.Signals(2)→ SIGINT
            sig_name = signal.Signals(signum).name 
            _restore_terminal()  # 退出时恢复终端
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        # 将 SIGINT和 SIGTERM信号绑定到我们定义的 _handle_signal函数。
        # SIGINT：通常由 Ctrl+C触发，表示用户想中断程序。
        # SIGTERM：通常由 kill命令（不带 -9）触发，表示请求程序终止。
        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        
        # =========== windows 没有这几个 --------------
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # 忽略 SIGPIPE（防止静默崩溃）
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        # 运行交互式模式
        async def run_interactive():
            # 创建持续运行的代理任务
            agent_task = asyncio.create_task(_run_agent_dispatcher(agent, bus, config))
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []
            _thinking: _ThinkingSpinner | None = None

            async def _consume_outbound():
                """消费来自bus的 outbound msg."""
                from loguru import logger
                nonlocal _thinking
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        logger.debug(f"[DEBUG] _consume_outbound: received msg, content: {msg.content[:100] if msg.content else '(empty)'}, metadata: {msg.metadata}")
                        logger.debug(f"[DEBUG] turn_done.is_set(): {turn_done.is_set()}")

                        # 处理 HITL 请求（直接在后台处理确认）
                        if msg.metadata.get("_hitl_request"):
                            hitl_data = msg.metadata.get("_hitl_data", {})
                            action_requests = hitl_data.get("action_requests", [])

                            # 逐个确认每个工具
                            decisions = []
                            # 暂停 thinking spinner
                            with _thinking.pause() if _thinking else nullcontext():
                                for i, ar in enumerate(action_requests):
                                    action, reason = _hitl_approve_simple(
                                        tool_name=ar.get("name", "unknown"),
                                        tool_description=ar.get("description", ""),
                                        current=i + 1,
                                        total=len(action_requests)
                                    )

                                    # 构建 Decision 对象
                                    if action == "approve":
                                        from langchain.agents.middleware.human_in_the_loop import ApproveDecision
                                        decisions.append(ApproveDecision(type="approve"))
                                    else:  # reject
                                        from langchain.agents.middleware.human_in_the_loop import RejectDecision
                                        decisions.append(RejectDecision(type="reject", message=reason))

                            # 构建 HITLResponse
                            from langchain.agents.middleware.human_in_the_loop import HITLResponse
                            hitl_response = HITLResponse(decisions=decisions)

                            # 通过 bus 发送 HITL 响应
                            await bus.publish_inbound(
                                InboundMessage(
                                    channel=cli_channel,
                                    sender_id="user",
                                    chat_id=cli_chat_id,
                                    content="",
                                    metadata={
                                        "_hitl_response": True,
                                        "_hitl_data": hitl_response,
                                    },
                                )
                            )
                            continue

                        # 处理进度消息（受配置控制）
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            # 工具提示受 send_tool_hints 控制，普通进度受 send_progress 控制
                            if is_tool_hint and not config.channels.send_tool_hints:
                                logger.debug(f"[DEBUG] Skipping tool hint (send_tool_hints=False)")
                                continue
                            if not is_tool_hint and not config.channels.send_progress:
                                logger.debug(f"[DEBUG] Skipping progress (send_progress=False)")
                                continue
                            logger.debug(f"[DEBUG] Printing progress line")
                            await _print_interactive_progress_line(msg.content, _thinking)

                        # 将AI msg最终的输出写入到 trun_response中
                        elif not turn_done.is_set():
                            logger.debug(f"[DEBUG] Collecting final response, content: {msg.content[:100] if msg.content else '(empty)'}")
                            if msg.content:
                                turn_response.append(msg.content)
                            logger.debug(f"[DEBUG] Setting turn_done!")
                            turn_done.set()

                        # Handle spontaneous messages (not part of current turn)
                        elif msg.content:
                            logger.debug(f"[DEBUG] Spontaneous message, printing directly")
                            await _print_interactive_response(msg.content, render_markdown=markdown)

                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()  # 丢弃用户在等待期间输入的字符
                        user_input = await _read_interactive_input_async() # 异步读取用户输入
                        command = user_input.strip()

                        if not command:
                            continue

                        # 退出命令
                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        # Reset turn state
                        turn_done.clear()
                        turn_response.clear()

                        # 发送 inbound msg
                        await bus.publish_inbound(
                            InboundMessage(
                                channel=cli_channel,
                                sender_id="user",
                                chat_id=cli_chat_id,
                                content=user_input,
                            )
                        )

                        # turn_done夯住，等待
                        _thinking = _ThinkingSpinner(enabled=not logs)
                        with _thinking:
                            await turn_done.wait()
                        _thinking = None

                        # 打印AI msg
                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)

                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                # Cancel background tasks
                agent_task.cancel()
                outbound_task.cancel()
                await asyncio.gather(agent_task, outbound_task, return_exceptions=True)

        asyncio.run(run_interactive())


def _load_runtime_config(config: str | None = None, workspace: str | None = None) -> Config:
    """加载配置并可选地覆盖工作区."""
    config_path = get_config_path()

    if config:
        config_path = Path(config).expanduser().resolve()
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_path}[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Using config: {config_path}[/dim]")

    loaded = load_config(config_path)
    if workspace:
        loaded.agents.defaults.workspace = workspace
    return loaded


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(8000, "--port", "-p", help="Gateway port"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
):
    """启动langbot gateway服务."""
    from langbot.channels.manager import ChannelManager

    # 1. 加载配置
    config = _load_runtime_config(config, workspace)
    console.print(f"{__logo__} Starting langbot gateway on port {port}...")

    # 2. 创建消息总线、代理、频道管理器
    bus = MessageBus()
    agent = LangBotAgent(config)
    channels = ChannelManager(config, bus)
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    async def run():
        try:
            # 生产者-消费者模式
            await asyncio.gather(
                #  启动Agent 循环监听inbound消息
                _run_agent_dispatcher(agent, bus, config),

                # 启动频道监听outbound消息
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await channels.stop_all()

    asyncio.run(run())


async def _run_agent_dispatcher(agent: LangBotAgent, bus: MessageBus, config: Config):
    """将 inbound msg 分发给 agent处理,并发布进度消息到bus"""
    from loguru import logger

    while True:
        try:
            msg = await bus.consume_inbound()  # 阻塞等待inbound msg
            logger.info("Received from {}: {}", msg.channel, msg.content)

            # 构建 session_key 作为 thread_id
            thread_id = f"{msg.channel}:{msg.chat_id}"

            # 保存原消息的 metadata (用于回复原消息)
            original_metadata = msg.metadata.copy()

            # 处理 HITL 响应
            resume_data = None
            if msg.metadata.get("_hitl_response"):
                resume_data = msg.metadata.get("_hitl_data")

            # 进度回调：将进度发布到 bus
            async def on_progress(content: str | dict, tool_hint: bool) -> None:
                """将工具和文本进度转换为 bus 消息"""
                # 工具提示受 send_tool_hints 控制，文本进度受 send_progress 控制
                if tool_hint and not config.channels.send_tool_hints:
                    return
                if not tool_hint and not config.channels.send_progress:
                    return

                # 处理HITL
                if isinstance(content, dict):
                    # HITL 请求
                    if content.get("type") == "hitl_request":
                        # 将 HITL 请求包装成 OutboundMessage 发送
                        await bus.publish_outbound(
                            OutboundMessage(
                                channel=msg.channel,
                                chat_id=msg.chat_id,
                                content="",
                                metadata={
                                    "_hitl_request": True,
                                    "_hitl_data": content,
                                },
                            )
                        )
                        return  # HITL 请求已发送，等待用户响应

                # 普通进度消息
                if isinstance(content, str):
                    await bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=content,
                            metadata={"_progress": True, "_tool_hint": tool_hint},
                        )
                    )

            # agent 处理消息
            response = await agent.ainvoke(
                msg.content,
                thread_id=thread_id,
                resume=resume_data,
                on_progress=on_progress
            )
            if response == "__HITL_INTERRUPT__":
                continue

            # 发送最终响应到 bus (带上原消息 metadata 以支持回复功能)
            await bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=response,
                    metadata=original_metadata,  # 传递原消息 metadata (如 message_id)
                )
            )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in dispatcher: {}", e)


# ============================================================================
# Status
# ============================================================================


@app.command()
def status():
    """Show langbot status."""
    config_path = get_config_path()
    config = load_config(config_path)

    console.print(f"{__logo__} langbot Status\n")
    console.print(f"Config: {config_path}")
    console.print(f"Workspace: {config.agents.defaults.workspace}")
    console.print(f"Model: {config.agents.defaults.model}")


if __name__ == "__main__":
    app()
