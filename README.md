# LangBot

<div align="center">

**基于 LangChain 的个人 AI 助手框架**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![LangChain](https://img.shields.io/badge/LangChain-1.2+-orange.svg)](https://github.com/langchain-ai/langchain)

基于 LangChain DeepAgents 和 LangGraph 构建的轻量级、模块化 AI 助手框架。

</div>

## 特性

- **多提供商支持** - 兼容 Anthropic、OpenAI、DeepSeek、Gemini、Groq、智谱等
- **多渠道接入** - CLI、QQ 机器人，可扩展的渠道系统
- **MCP 集成** - 支持模型上下文协议工具
- **人工干预控制** - 可配置的工具调用人工审批
- **技能系统** - 组织和管理 Agent 能力
- **定时任务** - Cron 定时任务支持
- **网页工具** - 内置网页搜索和抓取功能
- **工作区管理** - 隔离文件访问，可配置安全级别

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/your-username/langbot.git
cd langbot

# 使用 uv 安装（推荐）
uv sync

# 或使用 pip
pip install -e .
```

### 配置

初始化工作区和配置文件：

```bash
langbot onboard
```

这将创建：
- `~/.langbot/config.json` - 配置文件
- `~/.langbot/workspace/` - 工作区目录（包含模板）
- `~/.langbot/skills/` - 技能目录

编辑 `~/.langbot/config.json` 添加你的 API Key：

```json
{
  "agents": {
    "defaults": {
      "model": "deepseek-chat",
      "provider": "deepseek"
    }
  },
  "providers": {
    "deepseek": {
      "apiKey": "your-api-key-here"
    }
  }
}
```

### 使用

启动交互式聊天：

```bash
langbot agent
```

发送单条消息：

```bash
langbot agent -m "你好，请帮我写个排序算法"
```

启动网关服务（用于机器人）：

```bash
langbot gateway
```

## 配置说明

### 工作区模板

工作区目录包含重要的配置文件：

| 文件 | 说明 |
|------|------|
| `SOUL.md` | Agent 性格和行为设定 |
| `USER.md` | 自定义用户指令 |
| `MEMORY.md` | 持久学习数据（Agent 可编辑） |

### 提供商配置

支持的提供商：

```json
{
  "providers": {
    "anthropic": { "apiKey": "sk-ant-..." },
    "openai": { "apiKey": "sk-..." },
    "deepseek": { "apiKey": "sk-..." },
    "gemini": { "apiKey": "..." },
    "groq": { "apiKey": "gsk_..." },
    "zhipu": { "apiKey": "..." },
    "ollama": { "apiBase": "http://localhost:11434" }
  }
}
```

### HITL（人工干预）

配置工具调用审批行为：

```json
{
  "agents": {
    "defaults": {
      "hitl": {
        "mode": "custom",
        "tools": {
          "execute": true,
          "write_file": true,
          "read_file": false
        },
        "exclude": ["ls", "glob"]
      }
    }
  }
}
```

模式说明：
- `all` - 所有工具都需要审批
- `none` - 无需审批
- `custom` - 按工具单独配置

### 渠道配置

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": true,
    "qq": {
      "enabled": true,
      "token": "your-qq-bot-token"
    }
  }
}
```

## 命令列表

| 命令 | 说明 |
|------|------|
| `langbot onboard` | 初始化工作区和配置 |
| `langbot agent` | 启动交互式聊天 |
| `langbot agent -m "消息"` | 发送单条消息 |
| `langbot gateway` | 启动网关服务 |
| `langbot status` | 显示配置状态 |

## 项目结构

```
langbot/
├── agent/           # Agent 工厂和工具
├── bus/             # 渠道通信消息总线
├── channels/        # 聊天渠道实现
├── cli/             # 命令行界面
├── config/          # 配置模式和加载
├── cron/            # 定时任务服务
├── providers/       # LLM 提供商注册
├── skills/          # 技能管理系统
├── store/           # 检查点和存储
└── utils/           # 工具函数
```

## 开发

### 运行测试

```bash
pytest
```

### 代码质量

```bash
# 格式化代码
ruff format .

# 代码检查
ruff check .
```

### 添加渠道

渠道通过入口点发现。创建一个继承 `BaseChannel` 的类：

```python
from langbot.channels.base import BaseChannel

class MyChannel(BaseChannel):
    def default_config(self) -> dict:
        return {"enabled": False}
```

在 `pyproject.toml` 中注册：

```toml
[project.entry-points."langbot.channels"]
mychannel = "myapp.mychannel:MyChannel"
```

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 致谢

- 基于 [LangChain](https://github.com/langchain-ai/langchain) 构建
- 由 [LangGraph](https://github.com/langchain-ai/langgraph) 驱动
- 灵感来自 [nanobot](https://github.com/nanobot-xyz/nanobot)
