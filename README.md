# 竞品动态监控 Agent

> 基于 LangGraph 工作流引擎，集成多源数据采集、LLM 智能分析、自动化报告生成与群机器人推送的端到端竞品监控系统。

---

## 目录

- [项目简介](#项目简介)
- [系统架构](#系统架构)
- [技术选型](#技术选型)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [MVP 监控目标](#mvp-监控目标)
- [数据采集层](#数据采集层)
  - [WebScraper — 网页爬取](#webscraper--网页爬取)
  - [TikHubMCPCollector — 社交媒体 MCP 工具](#tikhubmcpcollector--社交媒体-mcp-工具)
  - [GitHubCollector — 仓库监控](#githubcollector--仓库监控)
- [LLM 分析层](#llm-分析层)
- [报告与推送](#报告与推送)
- [定时调度](#定时调度)
- [命令行用法](#命令行用法)
- [扩展计划](#扩展计划)

---

## 项目简介

本项目的核心目标是**自动化竞品情报收集**：定期爬取竞品在官网、社交媒体、GitHub 等渠道的最新动态，利用大模型进行结构化分析，生成可读性强的周报或即时简报，并通过企业微信 / 飞书 / 钉钉等群机器人推送到工作群，帮助产品团队高效进行竞品分析和决策。

**两种运行模式：**

| 模式 | 触发方式 | 行为 |
|------|---------|------|
| `weekly_report` | 定时（默认每周一 09:00） | 汇总一周内所有竞品动态，生成完整 Markdown 周报并推送 |
| `realtime_alert` | 定时轮询（默认每 2 小时） | 检测到新内容后立即分析，高优先级动态即时推送简报 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Data Collectors（并行采集）                      │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────┐               │
│  │WebScraper│  │TikHub MCP    │  │GitHubCollector │               │
│  │官网/博客  │  │微博/小红书/X │  │Release/Issue   │               │
│  └────┬─────┘  └──────┬───────┘  └───────┬────────┘               │
└───────┼───────────────┼──────────────────┼─────────────────────────┘
        │               │                  │
        └───────────────▼──────────────────┘
                        │  RawItem[]
                        ▼
              ┌──────────────────┐
              │   DEDUPLICATE    │  URL + SQLite 历史去重
              └────────┬─────────┘
                       │  new items only
                       ▼
              ┌──────────────────┐
              │   ANALYZE (LLM)  │  ChatOpenAI → 结构化 JSON
              └────────┬─────────┘
                       │
          ┌────────────┼────────────┐
          │ HIGH 优先级 │  ALL items  │
          ▼            ▼            ▼
  ┌──────────────┐ ┌────────────────┐ ┌──────────────┐
  │ 即时简报推送  │ │ Markdown 周报  │ │ SQLite 持久化 │
  │ (REALTIME)   │ │ (WEEKLY)       │ │ (STORE)      │
  └──────┬───────┘ └───────┬────────┘ └──────────────┘
         └─────────────────▼──────────────────────────┐
                    ┌──────────────┐                   │
                    │ 群机器人推送  │◀──────────────────┘
                    │ 企微/飞书/钉钉│
                    └──────────────┘
```

整个工作流基于 LangGraph 的 **StateGraph** 设计，通过 `AgentState` 在节点间传递数据，支持条件分支和并行执行。

---

## 技术选型

| 层级 | 技术 | 选型理由 |
|------|------|---------|
| **工作流引擎** | LangGraph | 基于有向图的显式工作流定义，支持条件分支、状态持久化、检查点；比纯 Chain 更灵活，比多 Agent 框架（CrewAI/AutoGen）更轻量 |
| **LLM 调用** | LangChain + langchain-openai | 通过 `ChatOpenAI` 统一调用任意 OpenAI 兼容 API，修改 `base_url` 即可切换模型提供商 |
| **网页爬取** | httpx + BeautifulSoup | 异步 HTTP 客户端 + HTML 解析，轻量可靠 |
| **社交媒体** | TikHub MCP + mcp-remote | 通过 MCP 工具发现和工具调用接入 TikHub，避免维护每个平台不同的 REST endpoint |
| **GitHub** | PyGithub | 官方维护的 Python SDK，功能完善 |
| **定时调度** | APScheduler | 成熟的 Python 任务调度库，支持 Cron 和 Interval 两种触发器 |
| **数据存储** | SQLite | 轻量嵌入式数据库，MVP 阶段无需额外部署；自带唯一索引实现 URL 去重 |
| **模板渲染** | Jinja2 | Markdown 周报模板引擎，方便自定义格式 |
| **配置管理** | Pydantic + python-dotenv | 类型安全的配置加载，从 `.env` 文件读取环境变量 |
| **日志** | loguru | 简洁强大的日志库，支持文件轮转 |
| **重试** | tenacity | 声明式重试装饰器，支持指数退避 |

---

## 目录结构

```
competitor-monitor/
├── main.py                        # CLI 主入口
├── .env.example                   # 环境变量配置模板
├── requirements.txt               # Python 依赖
│
├── config/
│   ├── settings.py                # 全局配置（Pydantic BaseModel）
│   └── competitors.py             # 竞品监控目标定义
│
├── collectors/
│   ├── base.py                    # 采集器抽象基类（safe_collect 异常保护）
│   ├── web_scraper.py             # 通用网页爬取器
│   ├── mcp_stdio.py               # 最小 MCP stdio JSON-RPC 客户端
│   ├── tikhub_mcp_collector.py    # TikHub MCP 工具型采集器
│   ├── tikhub_client.py           # 旧 TikHub REST API 客户端（可作为 tikhub_api 回退）
│   └── github_collector.py        # GitHub 仓库动态监控
│
├── analyzer/
│   └── llm_analyzer.py            # LLM 结构化分析 + 周报摘要生成
│
├── reporter/
│   └── report_generator.py        # Markdown 周报/即时简报模板渲染
│
├── notifier/
│   └── bot.py                     # 群机器人通知（企微 / 飞书 / 钉钉）
│
├── storage/
│   └── sqlite_storage.py          # SQLite 持久化（去重 + 历史查询）
│
├── models/
│   └── data_models.py             # 数据模型（RawItem, AnalyzedItem, WeeklyReport, AgentState）
│
├── graph/
│   ├── agent_workflow.py          # CompetitorMonitorAgent 核心引擎
│   └── scheduler.py               # APScheduler 定时任务调度器
│
├── logs/                          # 运行日志（按天轮转，保留 30 天）
└── data/                          # SQLite 数据库文件
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入所需配置（详见[配置说明](#配置说明)）。

### 3. 运行

```bash
# 手动为所有竞品生成周报
python main.py weekly

# 手动为指定竞品生成周报
python main.py weekly 腾讯WorkBuddy

# 手动执行一次实时监控（所有竞品）
python main.py monitor

# 手动执行一次实时监控（指定竞品）
python main.py monitor Claude

# 启动后台定时调度服务
python main.py serve
```

---

## 配置说明

所有配置通过 `.env` 文件管理，参考 `.env.example`：

### 必填配置

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_API_KEY` | 大模型 API 密钥 | `sk-xxxxxxxx` |
| `LLM_BASE_URL` | 大模型 API 地址（兼容 OpenAI 格式） | `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名称 | `gpt-4o` |
| `TIKHUB_API_TOKEN` | TikHub 平台 API Token（从 tikhub.io 获取） | `eyJhbGc...` |

TikHub MCP 通过本机 Node.js 调用 `npx -y mcp-remote https://mcp.tikhub.io/<platform>/mcp --header "Authorization: Bearer <TOKEN>"`。需要先安装 Node.js，并确保当前网络允许访问 `mcp.tikhub.io`。如果公司代理/安全网关导致 Node 报 `SELF_SIGNED_CERT_IN_CHAIN`，可临时设置 `TIKHUB_MCP_INSECURE_TLS=true`；这会只对 MCP 子进程设置 `NODE_TLS_REJECT_UNAUTHORIZED=0`，安全性较低，应优先使用正确的企业 CA 证书。

### 推送渠道（至少配置一个）

| 变量 | 说明 |
|------|------|
| `WECHAT_WORK_WEBHOOK_URL` | 企业微信群机器人 Webhook 地址 |
| `FEISHU_WEBHOOK_URL` | 飞书群机器人 Webhook 地址 |
| `DINGTALK_WEBHOOK_URL` | 钉钉群机器人 Webhook 地址 |
| `DINGTALK_SECRET` | 钉钉机器人加签密钥（钉钉使用加签模式时必填） |

### 可选配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `GITHUB_TOKEN` | 空 | GitHub Personal Access Token（提高 API 速率限制：匿名 60次/h → 认证 5000次/h） |
| `SQLITE_DB_PATH` | `data/competitor_monitor.db` | SQLite 数据库文件路径 |
| `WEEKLY_REPORT_CRON` | `0 9 * * 1` | 周报生成的 Cron 表达式（默认每周一 09:00） |
| `MONITOR_INTERVAL_HOURS` | `2` | 实时监控轮询间隔（小时） |
| `TIKHUB_MCP_BASE_URL` | `https://mcp.tikhub.io` | TikHub MCP 服务地址 |
| `TIKHUB_MCP_TIMEOUT` | `120` | MCP 初始化和工具调用超时秒数 |
| `TIKHUB_MCP_MAX_RESULTS` | `20` | 每个社媒源最多保留的结果数 |
| `TIKHUB_MCP_INSECURE_TLS` | 空 | 仅在本机证书链问题时设为 `true` |

---

## MVP 监控目标

当前 MVP 阶段选取以下两个竞品：

### 腾讯 WorkBuddy

腾讯推出的企业级 AI 工作助手，集成 IM、文档、日程等办公场景。

| 数据源 | 类型 | 说明 |
|--------|------|------|
| workbuddy.qq.com | Web 爬取 | 产品官网 |
| cloud.tencent.com/product/hunyuan | Web 爬取 | 腾讯云 AI 动态 |
| 微博 | TikHub MCP | 关键词搜索：腾讯WorkBuddy |
| 小红书 | TikHub MCP | 关键词搜索：WorkBuddy |
| 知乎 | TikHub MCP | 关键词搜索：腾讯WorkBuddy |
| B站 | TikHub MCP | 关键词搜索：WorkBuddy 腾讯 |
| GitHub (Tencent) | PyGithub | 组织下近期 Release 和热门 Issue |

### Claude（Anthropic）

Anthropic 推出的大语言模型产品，以安全性和长文本能力著称。

| 数据源 | 类型 | 说明 |
|--------|------|------|
| claude.ai | Web 爬取 | 产品官网 |
| anthropic.com | Web 爬取 | 公司官网 |
| anthropic.com/news | Web 爬取 | 官方博客 |
| docs.anthropic.com | Web 爬取 | 技术文档 |
| X / Twitter | TikHub MCP | 关键词搜索：Anthropic Claude |
| YouTube | TikHub MCP | 关键词搜索：Claude AI Anthropic |
| Reddit | TikHub MCP | 关键词搜索 + 子版块：r/ClaudeAI |
| Instagram | TikHub MCP | 关键词搜索：Anthropic Claude |
| GitHub (anthropics) | PyGithub | 组织下近期 Release 和热门 Issue |

### 添加新竞品

在 `config/competitors.py` 中向 `COMPETITORS` 列表追加一个新的 `CompetitorConfig` 即可：

```python
CompetitorConfig(
    name="新竞品名称",
    description="竞品描述",
    search_keywords=["关键词1", "关键词2"],
    github_orgs=["org_name"],
    sources=[
        SourceConfig(name="官网", type="web", url="https://..."),
        SourceConfig(name="微博", type="tikhub",
                     tikhub_platform="weibo",
                     tikhub_params={"keyword": "关键词"}),
        # ... 更多数据源
    ],
)
```

---

## 数据采集层

采集器采用统一的抽象基类 `BaseCollector`，所有采集器实现 `collect()` 方法并返回 `List[RawItem]`。`safe_collect()` 提供异常保护，避免单个数据源失败导致整体中断。

### WebScraper — 网页爬取

**文件：** `collectors/web_scraper.py`

**适用场景：** 官网、博客、新闻网站、文档站点等无强反爬机制的页面。

**工作原理：**
1. 使用 `httpx.AsyncClient` 发起异步 GET 请求（带浏览器 User-Agent）
2. 使用 `BeautifulSoup` 解析 HTML，优先提取 `<article>` / `<main>` 区域
3. 自动移除导航栏、页脚、脚本等干扰元素
4. 按 `<article>` 或 `<section>` 分块，提取标题、正文段落、链接和发布时间
5. 内置 `tenacity` 重试机制（最多 3 次，指数退避）

**关键特性：**
- 异步并发，支持同时爬取多个页面
- 自动识别 `<time datetime>` 标签或文本中的日期模式
- 可配置的超时时间和链接跟踪深度

### TikHubMCPCollector — 社交媒体 MCP 工具

**文件：** `collectors/tikhub_mcp_collector.py`，底层 stdio 客户端为 `collectors/mcp_stdio.py`

**适用场景：** 微博、小红书、B站、知乎、X/Twitter、YouTube、Reddit、Instagram 等有反爬机制的社交平台。

**工作原理：**
1. 根据 `SourceConfig.tikhub_platform` 选择对应 TikHub MCP server，例如 `weibo` 对应 `https://mcp.tikhub.io/weibo/mcp`
2. 通过 stdio 启动 `npx -y mcp-remote ...`
3. 调用 `tools/list` 动态发现该平台可用工具
4. 按工具名、描述和 input schema 自动选择搜索类工具；必要时可用 `tikhub_tool` 精确指定工具
5. 调用 `tools/call` 后把 `structuredContent` 或文本 JSON 结果统一映射为 `RawItem`

旧 REST API 客户端仍保留在 `collectors/tikhub_client.py`，如果需要临时回退，可把某个 source 的 `type` 改成 `tikhub_api`。

### GitHubCollector — 仓库监控

**文件：** `collectors/github_collector.py`

**适用场景：** 跟踪竞品在 GitHub 上的开源项目动态。

**工作原理：**
- 通过 PyGithub 库获取指定组织/仓库的最新 Release（默认近 7 天）
- 通过 GitHub Search API 获取近期热门 Issue（按评论数排序）
- 支持组织级别（遍历所有仓库）和单仓库级别（`owner/repo` 格式）

**未配置 Token 时** 以匿名方式访问（速率限制 60 次/小时）；配置 `GITHUB_TOKEN` 后提升至 5000 次/小时。

---

## LLM 分析层

**文件：** `analyzer/llm_analyzer.py`

通过 LangChain 的 `ChatOpenAI` 调用大模型，对采集到的原始数据进行结构化分析。

### 单条分析

**System Prompt** 将 LLM 定位为专业竞品分析师，要求返回以下 JSON 结构：

```json
{
  "content_type": "feature_release | blog_post | community_post | github_activity | news | documentation | other",
  "priority": "high | medium | low",
  "summary": "一句话总结（30字以内）",
  "detailed_analysis": "详细分析（100-300字）",
  "key_signals": ["关键信号1", "关键信号2"],
  "potential_impact": "对我们产品的潜在影响",
  "recommended_actions": ["建议行动1", "建议行动2"]
}
```

**User Prompt** 注入竞品名称、来源渠道、标题和内容摘要作为分析上下文。

### 周报摘要生成

汇总所有分析结果后，再次调用 LLM 生成周报级别的高管摘要、关键亮点、威胁评估和机会评估。

### 降级策略

当 LLM 调用失败（API 异常、超时、JSON 解析失败等）时，自动切换为 `fallback_analysis`：提取基础信息，标记为低优先级待人工审核，确保流水线不中断。

### 接入自定义模型

只需在 `.env` 中修改以下三个变量即可对接任意 OpenAI 兼容 API：

```
LLM_API_KEY=your_key
LLM_BASE_URL=https://your-api-provider.com/v1
LLM_MODEL=your-model-name
```

---

## 报告与推送

### Markdown 周报

**文件：** `reporter/report_generator.py`

使用 Jinja2 模板渲染，包含以下板块：

- 高管摘要（LLM 生成）
- 关键亮点（3 条高优事项）
- 详细动态（按优先级分组：高/中/低）
- 威胁评估
- 机会评估

### 即时简报

针对单条高优先级动态生成简报，包含一句话总结、关键信号、影响评估和建议行动。

### 推送渠道

**文件：** `notifier/bot.py`

| 渠道 | 协议 | 特性 |
|------|------|------|
| 企业微信 | Webhook POST | Markdown 格式，4096 字自动截断 |
| 飞书 | Webhook POST | Interactive Card 富文本卡片 |
| 钉钉 | Webhook POST + HMAC-SHA256 加签 | 安全签名验证 |

`CompositeNotifier` 自动检测 `.env` 中已配置的渠道，同时向所有启用渠道推送。如果未配置任何渠道，内容将输出到控制台（便于本地调试）。

---

## 定时调度

**文件：** `graph/scheduler.py`

基于 APScheduler 的 `AsyncIOScheduler`，时区为 `Asia/Shanghai`：

| 任务 | 触发器 | 默认频率 | 行为 |
|------|--------|---------|------|
| `weekly_report` | CronTrigger | 每周一 09:00 | 采集→分析→生成周报→推送 |
| `realtime_monitor` | IntervalTrigger | 每 2 小时 | 采集→分析→高优即时推送 |

频率可在 `.env` 中通过 `WEEKLY_REPORT_CRON` 和 `MONITOR_INTERVAL_HOURS` 调整。

---

## 命令行用法

```
python main.py <command> [competitor_name]

Commands:
  weekly [name]      生成竞品周报（不指定 name 则处理所有竞品）
  monitor [name]     执行一次实时监控（不指定 name 则处理所有竞品）
  serve              启动后台定时调度服务

Examples:
  python main.py weekly                  # 所有竞品周报
  python main.py weekly 腾讯WorkBuddy    # 指定竞品周报
  python main.py monitor Claude          # 指定竞品实时监控
  python main.py serve                   # 启动定时服务
```

---

## 扩展计划

以下功能计划在 v2 版本中实现：

- [ ] **RSS 采集器** — 支持订阅竞品博客/新闻的 RSS 源
- [ ] **Discord 采集器** — 监控竞品官方 Discord 频道的公告和讨论
- [ ] **向量数据库语义去重** — 引入 embedding 实现内容相似度的语义级去重
- [ ] **LangGraph 检查点持久化** — 使用 LangGraph 内置的 persistence 机制实现断点续跑
- [ ] **Web Dashboard** — 基于 FastAPI + React 的可视化监控面板
- [ ] **多竞品对比分析** — 在同一份报告中横向对比多个竞品的动态趋势
- [ ] **邮件 / Slack 通知** — 扩展推送渠道
- [ ] **TikHub 用户级监控** — 除关键词搜索外，直接跟踪竞品官方账号的发布动态
- [ ] **数据导出** — 支持导出为 PDF / HTML 报告
