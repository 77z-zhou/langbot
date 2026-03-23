"""QQ channel implementation using botpy SDK."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from langbot.bus.events import InboundMessage, OutboundMessage
from langbot.bus.queue import MessageBus
from langbot.channels.base import BaseChannel

try:
    import botpy
    from botpy.message import C2CMessage, GroupMessage

    QQ_AVAILABLE = True
except ImportError:
    QQ_AVAILABLE = False
    botpy = None
    C2CMessage = None
    GroupMessage = None

if TYPE_CHECKING:
    from botpy.message import C2CMessage, GroupMessage


class QQConfig(BaseModel):
    """QQ channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""
    secret: str = ""
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    msg_format: Literal["plain", "markdown"] = "plain"


def _make_bot_class(channel: "QQChannel") -> "type[botpy.Client] | None":
    """动态创建一个botpy Client子类, 绑定给指定的频道实例"""
    if not QQ_AVAILABLE:
        return None

    intents = botpy.Intents(public_messages=True, direct_message=True)

    class _Bot(botpy.Client):
        def __init__(self):
            super().__init__(intents=intents)

        async def on_ready(self):
            logger.info("QQ bot ready: {}", self.robot.name)
        # 私聊消息(用户直连机器人)
        async def on_c2c_message_create(self, message: "C2CMessage"):
            logger.debug("[QQ C2C] Received private message: author={}, content={}",
                        getattr(message.author, 'id', 'unknown'), message.content[:50])
            asyncio.create_task(channel._on_message(message, is_group=False))

        # 群组消息(@机器人 发送的消息)
        async def on_group_at_message_create(self, message: "GroupMessage"):
            logger.debug("[QQ GROUP] Received group @message: group={}, author={}, content={}",
                        message.group_openid, getattr(message.author, 'member_openid', 'unknown'),
                        message.content[:50])
            asyncio.create_task(channel._on_message(message, is_group=True))

        # 直连消息,某些特殊场景的私聊(在某场景同等于C2C)
        async def on_direct_message_create(self, message):
            logger.debug("[QQ DIRECT] Received direct message: author={}, content={}",
                        getattr(message.author, 'id', 'unknown'), message.content[:50])
            asyncio.create_task(channel._on_message(message, is_group=False))

    return _Bot


class QQChannel(BaseChannel):
    """
    QQ channel using botpy SDK with WebSocket connection.
    Supports both C2C (private) messages and group messages.
    """

    name = "qq"
    display_name = "QQ"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = QQConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: QQConfig = config

        self._client: "botpy.Client | None" = None       # QQ SDK 实例
        self._processed_ids: deque = deque(maxlen=1000)  # 消息去重   最近 1000 条消息 ID，用于去重
        self._msg_seq: int = 1                           # QQ API 序列号
        self._chat_type_cache: dict[str, str] = {}       # 聊天类型缓存

        # HITL 状态管理（按 chat_id 隔离）
        self._pending_hitl: dict[str, dict] = {}         # chat_id -> HITL 数据
        self._hitl_index: dict[str, int] = {}            # chat_id -> 当前确认索引

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """获取QQ channel 默认配置（使用 snake_case 格式）"""
        return QQConfig().model_dump(by_alias=False, exclude_none=True)

    async def start(self) -> None:
        """启动QQ channel."""

        # 1. 前置检查
        if not QQ_AVAILABLE:
            logger.error("QQ SDK not installed. Run: pip install qq-botpy")
            return

        if not self.config.app_id or not self.config.secret:
            logger.error("QQ app_id and secret not configured")
            return

        # 2. 运行标志设置
        self._running = True
        
        # 3. 创建 bot 实例
        BotClass = _make_bot_class(self)  # 闭包 动态创建 botpy Client subClass
        if BotClass is None:
            logger.error("Failed to create QQ bot class")
            return

        self._client = BotClass()
        logger.info("QQ bot started (C2C & Group supported)")
        
        # 4. 启动连接（阻塞直到停止）
        await self._run_bot()

    async def _run_bot(self) -> None:
        """以重连机制 运行bot."""
        while self._running:
            try:
                await self._client.start(appid=self.config.app_id, secret=self.config.secret)
            except Exception as e:
                logger.warning("QQ bot error: {}", e)
            if self._running:
                logger.info("Reconnecting QQ bot in 5 seconds...")
                await asyncio.sleep(5)  # 5 秒后重连

    async def stop(self) -> None:
        """停止 QQ bot."""
        self._running = False
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
        logger.info("QQ bot stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """
        发送消息(核心逻辑).
        处理 ChannelManager 的dispatcher监听到agent返回的消息, 将该发送给QQ
        """
        if not self._client:
            logger.warning("QQ client not initialized")
            return

        try:
            # 处理 HITL 请求
            if msg.metadata.get("_hitl_request"):
                hitl_data = msg.metadata.get("_hitl_data", {})
                action_requests = hitl_data.get("action_requests", [])

                # 保存 HITL 数据
                self._pending_hitl[msg.chat_id] = hitl_data
                self._hitl_index[msg.chat_id] = 0

                # 发送工具列表和第一个确认提示
                await self._send_hitl_prompt(msg.chat_id, action_requests, 0)
                return

            # 1. 构造payload
            msg_id = msg.metadata.get("message_id")
            self._msg_seq += 1
            use_markdown = self.config.msg_format == "markdown"
            payload: dict[str, Any] = {
                "msg_type": 2 if use_markdown else 0,  # 0: plain, 2: markdown
                "msg_id": msg_id,                      # 回复的消息ID
                "msg_seq": self._msg_seq,              # 序列号,防止去重
            }

            # 2. 根据消息格式设置消息内容
            if use_markdown:
                payload["markdown"] = {"content": msg.content}
            else:
                payload["content"] = msg.content

            # 3. 根据聊天类型 选择发送API  (该聊天类型在接收消息时会缓存)
            chat_type = self._chat_type_cache.get(msg.chat_id, "c2c")
            if chat_type == "group":  # 群聊
                await self._client.api.post_group_message(
                    group_openid=msg.chat_id,
                    **payload,
                )
            else:  # 单聊
                await self._client.api.post_c2c_message(
                    openid=msg.chat_id,
                    **payload,
                )
        except Exception as e:
            logger.error("Error sending QQ message: {}", e)

    async def _send_hitl_prompt(self, chat_id: str, action_requests: list, index: int) -> None:
        """发送 HITL 确认提示."""
        total = len(action_requests)

        # 构建消息内容
        if index == 0:
            # 第一次发送，显示所有工具列表
            lines = ["🔔 需要批准以下工具调用:\n"]
            for i, ar in enumerate(action_requests, 1):
                lines.append(f"{i}. {ar.get('name', 'unknown')}")
                lines.append(f"   {ar.get('description', '')}")
            lines.append("\n─── 即将逐个确认 ───")
            content = "\n".join(lines)
        else:
            content = ""

        # 添加当前确认提示
        ar = action_requests[index]
        content += f"\n\n{index + 1}/{total} {ar.get('name', 'unknown')}\n{ar.get('description', '')}\n\n回复 y/yes 批准，n/no 拒绝，或输入拒绝理由"

        # 发送消息
        await self._send_plain_message(chat_id, content)

    async def _send_plain_message(self, chat_id: str, content: str) -> None:
        """发送纯文本消息."""
        if not self._client:
            return

        self._msg_seq += 1
        payload: dict[str, Any] = {
            "msg_type": 0,  # plain text
            "msg_seq": self._msg_seq,
            "content": content,
        }

        chat_type = self._chat_type_cache.get(chat_id, "c2c")
        if chat_type == "group":
            await self._client.api.post_group_message(
                group_openid=chat_id,
                **payload,
            )
        else:
            await self._client.api.post_c2c_message(
                openid=chat_id,
                **payload,
            )

    async def _on_message(self, data: "C2CMessage | GroupMessage", is_group: bool = False) -> None:
        """处理来自QQ的消息."""
        try:
            # 1. 消息去重
            if hasattr(data, 'id') and data.id in self._processed_ids:
                return
            if hasattr(data, 'id'):
                self._processed_ids.append(data.id)  # 将msg id添加到去重队列

            # 2. 提取消息内容
            content = (getattr(data, 'content', "") or "").strip()
            if not content:
                return

            # 3. 提取用户和聊天ID
            if is_group:
                chat_id = data.group_openid
                user_id = data.author.member_openid
                self._chat_type_cache[chat_id] = "group"
            else:
                # C2C消息处理
                user_id = str(getattr(data.author, 'id', None) or
                            getattr(data.author, 'user_openid', None) or
                            getattr(data, 'author', None) or "unknown")
                chat_id = user_id
                self._chat_type_cache[chat_id] = "c2c"

            # 4. 检查是否有待处理的 HITL
            if chat_id in self._pending_hitl:
                # 处理 HITL 响应
                result = await self._handle_hitl_response(chat_id, content)
                if result == "continue":
                    return  # 继续等待下一个 HITL 响应
                elif result == "done":
                    # HITL 完成，清理状态
                    self._pending_hitl.pop(chat_id, None)
                    self._hitl_index.pop(chat_id, None)
                    return

            # 正常消息处理
            await self._handle_message(
                sender_id=user_id,
                chat_id=chat_id,
                content=content,
                metadata={"message_id": getattr(data, 'id', None)},
            )
        except Exception:
            logger.exception("Error handling QQ message")

    async def _handle_hitl_response(self, chat_id: str, content: str) -> str:
        """
        处理 HITL 响应。

        Returns:
            "continue" - 继续等待下一个 HITL 响应
            "done" - HITL 完成
            "normal" - 不是 HITL 响应，按正常消息处理
        """
        hitl_data = self._pending_hitl.get(chat_id)
        if not hitl_data:
            return "normal"

        action_requests = hitl_data.get("action_requests", [])
        index = self._hitl_index.get(chat_id, 0)
        total = len(action_requests)

        # 解析用户输入
        content_lower = content.lower().strip()

        if content_lower in ("y", "yes"):
            # 批准
            action = "approve"
            reason = None
        elif content_lower in ("n", "no"):
            # 默认拒绝
            action = "reject"
            reason = "用户拒绝"
        else:
            # 其他文字作为拒绝理由
            action = "reject"
            reason = content.strip() or "用户拒绝"

        # 保存当前决策
        if "_decisions" not in hitl_data:
            hitl_data["_decisions"] = []

        from langchain.agents.middleware.human_in_the_loop import ApproveDecision, RejectDecision
        if action == "approve":
            hitl_data["_decisions"].append(ApproveDecision(type="approve"))
        else:
            hitl_data["_decisions"].append(RejectDecision(type="reject", message=reason))

        # 检查是否还有更多工具需要确认
        if index + 1 < total:
            # 继续下一个
            self._hitl_index[chat_id] = index + 1
            await self._send_hitl_prompt(chat_id, action_requests, index + 1)
            return "continue"
        else:
            # 所有工具都已确认，发送 HITL 响应
            from langchain.agents.middleware.human_in_the_loop import HITLResponse
            hitl_response = HITLResponse(decisions=hitl_data["_decisions"])

            await self.bus.publish_inbound(
                InboundMessage(
                    channel=self.name,
                    sender_id="user",
                    chat_id=chat_id,
                    content="",
                    metadata={
                        "_hitl_response": True,
                        "_hitl_data": hitl_response,
                    },
                )
            )
            return "done"

