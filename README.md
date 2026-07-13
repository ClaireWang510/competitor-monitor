# 竞品动态监控 Agent

一个面向产品、战略、销售和运营团队的竞品情报自动化工具。系统会定期从官网、文档、社交平台和 GitHub 等渠道采集竞品动态，通过大模型进行结构化分析，并生成周报或高优先级即时提醒。

当前版本重点解决的问题是：减少人工巡检多个渠道的成本，把分散信息整理成可读的竞品信号，并通过 Markdown 报告或群机器人推送给团队。

## 功能概览

- 多竞品监控：通过配置文件维护竞品、关键词和数据源。
- 多渠道采集：支持网页、TikHub REST 社交搜索、GitHub 仓库动态。
- 官方账号追踪：从账号表读取竞品官方账号和关键人物账号，采集其近期动态。
- 自动去重：基于 URL 和来源类型去重，并写入 SQLite 历史库。
- LLM 分析：为每条动态生成类型、优先级、摘要、影响判断和建议行动。
- 周报生成：按竞品生成 Markdown 周报，适合每周同步。
- 实时提醒：筛选高优先级动态，推送即时简报。
- 群通知：支持企业微信、飞书、钉钉机器人。
- 定时调度：支持每周周报和周期性实时监控。

更偏产品视角的说明见 [PRODUCT_OVERVIEW.md](PRODUCT_OVERVIEW.md)。

## 当前监控对象

当前配置文件中已包含以下竞品：

- 飞书 Aily
- Coze
- WorkBuddy
- Manus AI
- Notion AI
- Microsoft 365 Copilot
- Skywork
- Claude
- Codex
- Zapier Central
- Make
- n8n

竞品配置位于 [config/competitors.py](config/competitors.py)。新增或调整竞品时，主要修改这里的 `COMPETITORS` 列表。

## 数据源能力

### 网页采集

用于采集官网、产品页、文档、博客、更新日志和新闻页面。

实现文件：[collectors/web_scraper.py](collectors/web_scraper.py)

当前能力：

- 使用 HTTP 请求获取页面内容。
- 用 BeautifulSoup 提取标题、正文片段、链接和发布时间。
- 优先解析 `article`、`main`、`section` 等主体区域。
- 自动移除导航、页脚、脚本等噪声元素。
- 对网络异常进行重试。

### 社交平台采集

用于通过关键词搜索社交平台和社区内容。

实现文件：[collectors/tikhub_client.py](collectors/tikhub_client.py)

当前通过 TikHub REST API 接入，支持的平台包括：

- 微博
- 小红书
- 知乎
- B 站
- 抖音
- X/Twitter
- YouTube
- Reddit
- Instagram
- Threads

每个社交源在 [config/competitors.py](config/competitors.py) 中配置平台和关键词。采集结果会统一转成系统内部的 `RawItem`，便于后续去重和分析。

### 官方账号和关键人物账号采集

用于追踪竞品在各平台上的官方账号，以及关键人物账号的近期动态。

实现文件：[collectors/social_account_collector.py](collectors/social_account_collector.py)

账号表默认路径：

```text
files/social_media_accounts.xlsx
```

账号表当前包含两个主要 sheet：

- `竞品官方账号总览`：按竞品列出各平台官方账号。
- `关键人物账号`：按人物列出关联产品、平台、账号、URL 和备注。

采集器会在每轮竞品采集时自动读取账号表，并按当前竞品名匹配对应账号。它会优先使用 TikHub 的账号/用户动态接口，例如 X 用户推文、Instagram 用户帖子、Reddit 用户或 subreddit、YouTube 频道视频等；如果账号表只提供了显示名而不是 UID、频道 ID 或精确 URL，则回退到该平台的搜索接口。

`files/` 目录默认不提交到 Git，账号表可以作为本地配置维护。

### GitHub 采集

用于跟踪竞品的开源项目和开发者生态。

实现文件：[collectors/github_collector.py](collectors/github_collector.py)

当前能力：

- 支持组织或用户级别采集，例如 `openai`、`anthropics`。
- 支持单仓库采集，例如 `coze-dev/coze-studio`。
- 采集 Release、Commit、Issue、PR。
- 支持 GitHub 关键词搜索，用于发现社区项目。
- 支持通过 `GITHUB_TOKEN` 提升 API 速率限制和稳定性。

## 工作流程

整体流程如下：

```text
配置竞品和数据源
        |
并行采集网页 / 社交平台 / GitHub
        |
读取账号表并采集官方账号 / 关键人物账号
        |
基于 URL 和来源去重
        |
调用 LLM 做结构化分析
        |
生成周报或筛选高优提醒
        |
写入 SQLite 并推送到通知渠道
```

单个数据源失败不会中断整轮任务，失败会记录到日志中，其余数据源继续执行。

## 安装

建议使用 Python 虚拟环境。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

安装依赖：

```bash
pip install -r requirements.txt
```

## 环境变量

项目会自动读取根目录下的 `.env` 文件。`.env` 已加入 `.gitignore`，不要提交真实密钥。

最小可用配置：

```env
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
```

社交平台采集需要：

```env
TIKHUB_API_TOKEN=your_tikhub_token
TIKHUB_BASE_URL=https://api.tikhub.io
TIKHUB_MAX_RESULTS=20
```

GitHub 采集建议配置：

```env
GITHUB_TOKEN=your_github_token
```

通知渠道可按需配置一个或多个：

```env
WECHAT_WORK_WEBHOOK_URL=
FEISHU_WEBHOOK_URL=
DINGTALK_WEBHOOK_URL=
DINGTALK_SECRET=
```

调度和存储相关配置：

```env
SQLITE_DB_PATH=data/competitor_monitor.db
WEEKLY_REPORT_CRON=0 9 * * 1
MONITOR_INTERVAL_HOURS=2
```

说明：

- `WEEKLY_REPORT_CRON` 默认表示每周一 09:00 生成周报。
- `MONITOR_INTERVAL_HOURS` 默认每 2 小时执行一次实时监控。
- 调度时区为 `Asia/Shanghai`。

## 命令行用法

### 生成指定竞品周报

```bash
python main.py weekly Coze
```

会采集 Coze 的全部数据源，生成 Markdown 周报，并在控制台打印。如果配置了通知机器人，也会推送到对应渠道。

### 生成全部竞品周报

```bash
python main.py weekly
```

会按配置逐个处理全部竞品。该模式适合定时任务或手动全量巡检。

### 执行指定竞品实时监控

```bash
python main.py monitor Coze
```

会采集并分析 Coze 的最新动态，包括配置的数据源、官方账号和关键人物账号，然后输出发现的高优先级动态数量。若存在高优内容且配置了通知渠道，会推送即时提醒。

### 执行全部竞品实时监控

```bash
python main.py monitor
```

会逐个处理全部竞品。该模式主要依赖日志和通知渠道查看结果。

### 启动定时服务

```bash
python main.py serve
```

启动后会注册两个任务：

- 周报任务：按 `WEEKLY_REPORT_CRON` 运行。
- 实时监控任务：按 `MONITOR_INTERVAL_HOURS` 运行。

## 输出内容

### 周报

周报由 [reporter/report_generator.py](reporter/report_generator.py) 生成，包含：

- 报告周期和生成时间。
- 新动态总数。
- 高、中、低优先级数量。
- 一句话结论。
- 重点事项。
- 高优动态详情。
- 其他值得关注的中低优动态。
- 威胁评估。
- 机会评估。

### 即时提醒

实时监控会把 LLM 判断为 `high` 的动态生成简报，包含：

- 摘要。
- 来源和链接。
- 内容类型。
- 关键竞品信号。
- 潜在影响。
- 建议行动。

如果未配置任何通知渠道，系统会把内容打印到控制台，便于本地调试。

## 数据存储

系统使用 SQLite 做本地持久化。

实现文件：[storage/sqlite_storage.py](storage/sqlite_storage.py)

默认数据库路径：

```text
data/competitor_monitor.db
```

主要用途：

- 保存原始采集结果。
- 保存 LLM 分析结果。
- 基于历史 URL 判断内容是否为新增。

## 日志

日志会输出到控制台，并按天写入 `logs/monitor_YYYY-MM-DD.log`。

日志中可以查看：

- 每个 source 采集到的条数。
- 单个数据源失败原因。
- GitHub rate limit 或网络错误。
- LLM 分析失败和降级情况。
- 通知发送结果。

## 新增竞品

在 [config/competitors.py](config/competitors.py) 中增加一个 `CompetitorConfig`。

示例：

```python
CompetitorConfig(
    name="Example",
    description="示例竞品描述",
    search_keywords=["Example", "Example AI"],
    github_orgs=["example-org"],
    sources=[
        web_source("Example 官网", "https://example.com/"),
        social_source("twitter", "Example AI", "X-Example"),
        github_repo_source("example-org/example-repo", "GitHub-Example"),
        github_search_source('"Example AI" example-org', "GitHub社区-Example"),
    ],
)
```

常用 helper：

- `web_source(name, url)`：网页源。
- `social_source(platform, keyword, name)`：社交平台关键词源。
- `github_repo_source(repo, name)`：GitHub 组织或仓库源。
- `github_search_source(query, name)`：GitHub 搜索源。

官方账号和关键人物账号不需要写进 `CompetitorConfig.sources`，只要在 `files/social_media_accounts.xlsx` 里按竞品名维护即可。采集流程会自动为每个竞品追加账号动态采集任务。

## 当前限制

当前版本仍是 MVP，适合内部试用和半自动化竞品巡检。使用时需要注意：

- 周报中的“本周”并不总是严格等于发布时间属于本周；部分平台搜索结果可能按相关性返回。
- 一些网页缺少标准发布时间，系统只能做有限解析。
- 社交平台关键词搜索会带来噪声，需要 LLM 和人工共同筛选。
- 官方账号表中如果只填写显示名，部分平台只能回退到搜索接口；填写 UID、频道 URL、用户 URL 或标准 handle 会更稳定。
- 当前 Prompt 偏基础，能做摘要和初步优先级判断，但横向对比和深度分析还不够。
- 单个数据源失败不会中断整体流程，但需要通过日志排查失败原因。
- 高优先级判断仍需人工复核，关键事实应以原文链接为准。

## 后续计划

优先改进方向：

- 强化发布时间解析和时间可信度标记。
- 在周报中区分“本周发布”和“本周发现”。
- 升级 Prompt，从单条摘要升级到按主题聚类和业务影响分析。
- 增加多竞品横向对比报告。
- 建立竞品信号分类体系，例如产品能力、商业化、生态、客户、开源社区等。
- 增加数据源健康监控，展示最近成功时间、失败原因和连续失败次数。
- 引入人工反馈，用于优化关键词、过滤规则和优先级判断。
- 提供 Web Dashboard，降低非技术用户使用门槛。

## 目录说明

```text
competitor-monitor/
├── main.py                     # CLI 入口
├── config/
│   ├── settings.py             # 环境变量和全局配置
│   └── competitors.py          # 竞品和数据源配置
├── collectors/
│   ├── base.py                 # 采集器基类
│   ├── web_scraper.py          # 网页采集
│   ├── tikhub_client.py        # TikHub REST 社交平台采集
│   ├── social_account_collector.py # 官方账号和关键人物账号采集
│   └── github_collector.py     # GitHub 采集
├── analyzer/
│   └── llm_analyzer.py         # LLM 结构化分析
├── reporter/
│   └── report_generator.py     # Markdown 周报和提醒生成
├── notifier/
│   └── bot.py                  # 企业微信 / 飞书 / 钉钉通知
├── storage/
│   └── sqlite_storage.py       # SQLite 存储和去重
├── graph/
│   ├── agent_workflow.py       # 监控流程编排
│   └── scheduler.py            # 定时任务
├── models/
│   └── data_models.py          # 数据模型
├── data/                       # SQLite 数据库
└── logs/                       # 运行日志
```
