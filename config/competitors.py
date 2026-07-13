"""
竞品监控目标配置

数据源覆盖三类信号：
1. 官方渠道：官网、新闻/博客、文档、更新日志
2. 社交媒体：通过 TikHub 按关键词检索
3. 开源社区：GitHub 官方组织/仓库，或相关社区关键词检索
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SourceConfig:
    """单个数据源配置"""

    name: str  # 数据源名称，如 "官网"、"X/Twitter"
    type: str  # 采集类型：web | tikhub | github | social_accounts | rss
    url: str = ""  # 目标 URL
    # TikHub 专用字段
    tikhub_endpoint: str = ""  # TikHub API 端点路径
    tikhub_params: dict = field(default_factory=dict)  # 额外请求参数
    tikhub_platform: str = ""  # TikHub REST 平台，如 weibo / xiaohongshu / twitter
    tikhub_tool: str = ""  # 兼容旧配置字段，REST 采集当前不使用
    # GitHub 专用字段
    github_repo: str = ""  # 格式：owner 或 owner/repo
    github_query: str = ""  # GitHub 搜索语句，用于社区项目发现
    # 采集频率（小时），None 表示跟随全局默认
    interval_hours: Optional[int] = None
    enabled: bool = True


@dataclass
class CompetitorConfig:
    """单个竞品的完整监控配置"""

    name: str
    description: str
    sources: List[SourceConfig] = field(default_factory=list)
    # 用于社交媒体搜索的关键词
    search_keywords: List[str] = field(default_factory=list)
    # 相关 GitHub 组织 / 用户 / 仓库
    github_orgs: List[str] = field(default_factory=list)
    enabled: bool = True


TIKHUB_ENDPOINTS = {
    "weibo": "/api/v1/weibo/web_v2/fetch_realtime_search",
    "xiaohongshu": "/api/v1/xiaohongshu/app_v2/search_notes",
    "zhihu": "/api/v1/zhihu/web/fetch_article_search_v3",
    "bilibili": "/api/v1/bilibili/web/fetch_general_search",
    "douyin": "/api/v1/douyin/search/fetch_general_search_v2",
    "twitter": "/api/v1/twitter/web/fetch_search_timeline",
    "youtube": "/api/v1/youtube/web_v2/get_general_search_v2",
    "reddit": "/api/v1/reddit/app/fetch_dynamic_search",
    "instagram": "/api/v1/instagram/v2/general_search",
    "threads": "/api/v1/threads/web/search_top",
}


def web_source(name: str, url: str) -> SourceConfig:
    return SourceConfig(name=name, type="web", url=url)


def social_source(platform: str, keyword: str, name: str | None = None) -> SourceConfig:
    return SourceConfig(
        name=name or f"{platform}-{keyword}",
        type="tikhub",
        tikhub_endpoint=TIKHUB_ENDPOINTS[platform],
        tikhub_params={"keyword": keyword},
        tikhub_platform=platform,
    )


def github_repo_source(repo: str, name: str | None = None) -> SourceConfig:
    return SourceConfig(
        name=name or f"GitHub-{repo}",
        type="github",
        github_repo=repo,
    )


def github_search_source(query: str, name: str | None = None) -> SourceConfig:
    return SourceConfig(
        name=name or f"GitHub搜索-{query}",
        type="github",
        github_query=query,
    )


def china_social_sources(keyword: str, prefix: str) -> List[SourceConfig]:
    return [
        social_source("weibo", keyword, f"微博-{prefix}"),
        social_source("xiaohongshu", keyword, f"小红书-{prefix}"),
        social_source("zhihu", keyword, f"知乎-{prefix}"),
        social_source("bilibili", keyword, f"B站-{prefix}"),
        social_source("douyin", keyword, f"抖音-{prefix}"),
    ]


def global_social_sources(keyword: str, prefix: str) -> List[SourceConfig]:
    return [
        social_source("twitter", keyword, f"X-{prefix}"),
        social_source("youtube", keyword, f"YouTube-{prefix}"),
        social_source("reddit", keyword, f"Reddit-{prefix}"),
        social_source("instagram", keyword, f"Instagram-{prefix}"),
        social_source("threads", keyword, f"Threads-{prefix}"),
    ]


COMPETITORS: List[CompetitorConfig] = [
    CompetitorConfig(
        name="飞书 Aily",
        description="飞书旗下企业级智能体平台与官方 AI 智能伙伴，深度集成飞书工作流。",
        search_keywords=["Aily", "飞书 Aily", "飞书 aily", "飞书智能伙伴", "Feishu Aily", "飞书"],
        github_orgs=["larksuite"],
        sources=[
            web_source("飞书 Aily 官网", "https://aily.feishu.cn/"),
            web_source("飞书 Aily 产品页", "https://www.feishu.cn/landing/feishu_aily_2026"),
            web_source("飞书 Aily 功能手册", "https://aily.feishu.cn/hc"),
            web_source("飞书开放平台 MCP/CLI", "https://open.feishu.cn/document/mcp_open_tools/feishu-cli-let-ai-actually-do-your-work-in-feishu"),
            *china_social_sources("飞书 Aily", "Aily"),
            github_repo_source("larksuite/lark-openapi-mcp", "GitHub-飞书 MCP"),
            github_repo_source("larksuite/lark-base-mcp", "GitHub-飞书多维表格 MCP"),
            github_search_source('"飞书 Aily" Feishu', "GitHub社区-飞书 Aily"),
        ],
    ),
    CompetitorConfig(
        name="Coze",
        description="字节跳动旗下 AI Agent 智能办公与一站式 AI 应用开发平台。",
        search_keywords=["Coze", "扣子 Coze", "Coze Studio", "扣子空间"],
        github_orgs=["coze-dev"],
        sources=[
            web_source("Coze 国际官网", "https://www.coze.com/"),
            web_source("扣子国内官网", "https://www.coze.cn/overview"),
            web_source("Coze 文档", "https://docs.coze.com/"),
            web_source("Coze 模型发布说明", "https://docs.coze.com/guides/model_release_note"),
            *china_social_sources("扣子 Coze", "Coze"),
            *global_social_sources("Coze AI agent", "Coze"),
            github_repo_source("coze-dev/coze-studio", "GitHub-Coze Studio"),
            github_repo_source("coze-dev/coze-py", "GitHub-Coze Python SDK"),
            github_search_source('"Coze Studio" coze-dev', "GitHub社区-Coze"),
        ],
    ),
    CompetitorConfig(
        name="WorkBuddy",
        description="腾讯云代码助手推出的全场景 AI Agent 办公工作台。",
        search_keywords=["WorkBuddy", "腾讯 WorkBuddy", "腾讯云 WorkBuddy", "Tencent WorkBuddy"],
        github_orgs=["Tencent"],
        sources=[
            web_source("WorkBuddy 中文官网", "https://copilot.tencent.com/work/"),
            web_source("WorkBuddy 国际官网", "https://www.tencentcloud.com/act/pro/workbuddy"),
            web_source("WorkBuddy 文档", "https://www.workbuddy.ai/docs/workbuddy/Overview"),
            web_source("腾讯云 WorkBuddy 活动页", "https://cloud.tencent.com/act/pro/workbuddy"),
            *china_social_sources("腾讯 WorkBuddy", "WorkBuddy"),
            *global_social_sources("Tencent WorkBuddy", "WorkBuddy"),
            github_search_source('"WorkBuddy" "Tencent"', "GitHub社区-WorkBuddy"),
        ],
    ),
    CompetitorConfig(
        name="Manus AI",
        description="通用 AI Agent 产品，支持沙箱环境、任务自动执行和 Manus API 集成。",
        search_keywords=["Manus", "Manus agent", "Manus.im", "OpenManus"],
        github_orgs=["FoundationAgents"],
        sources=[
            web_source("Manus 官网", "https://manus.im/"),
            web_source("Manus 博客", "https://manus.im/blog"),
            web_source("Manus 产品文档", "https://manus.im/docs/introduction/welcome"),
            web_source("Manus API 文档", "https://manus.im/docs/integrations/manus-api"),
            *china_social_sources("Manus AI", "Manus"),
            *global_social_sources("Manus AI agent", "Manus"),
            github_repo_source("FoundationAgents/OpenManus", "GitHub-OpenManus社区"),
            github_search_source('"Manus AI" OpenManus', "GitHub社区-Manus"),
        ],
    ),
    CompetitorConfig(
        name="Notion AI",
        description="Notion 内置 AI、Notion Agents、AI Meeting Notes 与协作工作区智能能力。",
        search_keywords=["Notion AI", "Notion Agents", "Notion MCP", "Notion AI Meeting Notes"],
        github_orgs=["makenotion"],
        sources=[
            web_source("Notion AI 产品页", "https://www.notion.com/product/ai"),
            web_source("Notion 更新日志", "https://www.notion.com/releases"),
            web_source("Notion API Changelog", "https://developers.notion.com/page/changelog"),
            web_source("Notion 帮助中心 AI", "https://www.notion.com/help/category/notion-ai"),
            *china_social_sources("Notion AI", "Notion AI"),
            *global_social_sources("Notion AI Notion Agents", "Notion AI"),
            github_repo_source("makenotion/notion-sdk-js", "GitHub-Notion SDK"),
            github_search_source('"Notion AI" MCP', "GitHub社区-Notion AI"),
        ],
    ),
    CompetitorConfig(
        name="Microsoft 365 Copilot",
        description="微软面向 Microsoft 365 的企业级 Copilot 与 Agent 生态。",
        search_keywords=["Microsoft 365 Copilot", "微软 Copilot", "M365 Copilot", "Copilot Studio", "Microsoft Copilot agents"],
        github_orgs=["microsoft"],
        sources=[
            web_source("Microsoft 365 Copilot 官网", "https://www.microsoft.com/en-us/microsoft-365/copilot"),
            web_source("Microsoft 365 Copilot 博客", "https://techcommunity.microsoft.com/category/microsoft365copilot/blog/microsoft365copilotblog"),
            web_source("Microsoft 365 Roadmap", "https://www.microsoft.com/en-us/microsoft-365/roadmap"),
            web_source("Microsoft Learn Copilot", "https://learn.microsoft.com/en-us/copilot/microsoft-365/"),
            *china_social_sources("微软 Copilot", "M365 Copilot"),
            *global_social_sources("Microsoft 365 Copilot", "M365 Copilot"),
            github_repo_source("microsoft/semantic-kernel", "GitHub-Semantic Kernel"),
            github_search_source('"Microsoft 365 Copilot"', "GitHub社区-M365 Copilot"),
        ],
    ),
    CompetitorConfig(
        name="Skywork",
        description="Skywork AI Workspace 与 SkyworkAI 开源模型/多模态生成生态。",
        search_keywords=["Skywork AI", "Skywork agent", "Skywork AI workspace", "Skywork"],
        github_orgs=["SkyworkAI"],
        sources=[
            web_source("Skywork 官网", "https://skywork.ai/"),
            web_source("Skywork 帮助/更新", "https://skywork.ai/help"),
            web_source("SkyClaw 模型页", "https://skyworkai.github.io/skyclaw/"),
            *china_social_sources("Skywork AI", "Skywork"),
            *global_social_sources("Skywork AI", "Skywork"),
            github_repo_source("SkyworkAI", "GitHub-SkyworkAI"),
            github_search_source('"Skywork AI" SkyworkAI', "GitHub社区-Skywork"),
        ],
    ),
    CompetitorConfig(
        name="Claude",
        description="Anthropic 推出的大语言模型产品，以安全性、长文本和工具调用能力著称。",
        search_keywords=["Claude", "Anthropic Claude", "Claude Code", "Claude Cowork"],
        github_orgs=["anthropics"],
        sources=[
            web_source("Claude 官网", "https://claude.ai"),
            web_source("Anthropic 官网", "https://www.anthropic.com"),
            web_source("Anthropic News", "https://www.anthropic.com/news"),
            web_source("Anthropic 文档", "https://docs.anthropic.com"),
            web_source("Claude Code 文档", "https://docs.anthropic.com/en/docs/claude-code"),
            *china_social_sources("Claude", "Claude"),
            *global_social_sources("Anthropic Claude", "Claude"),
            github_repo_source("anthropics", "GitHub-Anthropic"),
            github_search_source('"Claude Code" Anthropic', "GitHub社区-Claude"),
        ],
    ),
    CompetitorConfig(
        name="Codex",
        description="OpenAI 面向软件工程的 Codex 编码智能体与开源 CLI 生态。",
        search_keywords=["OpenAI Codex", "Codex CLI", "ChatGPT", "Codex 编程"],
        github_orgs=["openai"],
        sources=[
            web_source("OpenAI Codex 官网", "https://openai.com/codex/"),
            web_source("Codex Getting Started", "https://chatgpt.com/codex/get-started/"),
            web_source("OpenAI Codex 历史发布", "https://openai.com/index/openai-codex/"),
            *china_social_sources("OpenAI Codex", "Codex"),
            *global_social_sources("OpenAI Codex Codex CLI", "Codex"),
            github_repo_source("openai/codex", "GitHub-OpenAI Codex"),
            github_search_source('"OpenAI Codex" "Codex CLI"', "GitHub社区-Codex"),
        ],
    ),
    CompetitorConfig(
        name="Zapier Central",
        description="Zapier 面向 AI bots、AI automation、MCP 和跨应用自动化的智能代理能力。",
        search_keywords=["Zapier Central", "Zapier AI bots", "Zapier MCP", "Zapier Agents"],
        github_orgs=["zapier"],
        sources=[
            web_source("Zapier Central 发布页", "https://zapier.com/blog/introducing-zapier-central-ai-bots/"),
            web_source("Zapier Blog", "https://zapier.com/blog/"),
            web_source("Zapier AI 页面", "https://zapier.com/ai"),
            web_source("Zapier Platform Docs", "https://zapier.github.io/zapier-platform-cli/"),
            *china_social_sources("Zapier AI", "Zapier Central"),
            *global_social_sources("Zapier Central Zapier AI bots", "Zapier Central"),
            github_repo_source("zapier", "GitHub-Zapier"),
            github_repo_source("zapier/zapier-mcp", "GitHub-Zapier MCP"),
            github_search_source('"Zapier MCP"', "GitHub社区-Zapier"),
        ],
    ),
    CompetitorConfig(
        name="Make",
        description="Make 的可视化自动化平台与 Make AI Agents 能力。",
        search_keywords=["Make AI Agents", "Make.com automation", "Make agentic automation", "Make 自动化"],
        github_orgs=["MakeHQ"],
        sources=[
            web_source("Make 官网", "https://www.make.com/en"),
            web_source("Make Blog", "https://www.make.com/en/blog"),
            web_source("Make AI Agents 发布", "https://www.make.com/en/blog/make-ai-agents"),
            web_source("Make AI Agents 新一代", "https://www.make.com/en/blog/announcing-next-generation-make-ai-agents"),
            web_source("Make TypeScript SDK", "https://integromat.github.io/make-typescript-sdk/"),
            *china_social_sources("Make AI Agents", "Make"),
            *global_social_sources("Make AI Agents", "Make"),
            github_repo_source("MakeHQ", "GitHub-MakeHQ"),
            github_search_source('"Make AI Agents" makehq', "GitHub社区-Make"),
        ],
    ),
    CompetitorConfig(
        name="Kimi Work",
        description="月之暗面 Kimi 面向办公交付、文档、表格、PPT 与多任务 Agent 协作的工作能力。",
        search_keywords=["Kimi Work", "Kimi Agent", "Kimi Office", "Kimi PPT", "Kimi 文档"],
        github_orgs=["MoonshotAI"],
        sources=[
            web_source("Kimi 官网", "https://www.kimi.com/zh/"),
            web_source("Moonshot AI 官网", "https://www.moonshot.cn/"),
            web_source("Kimi API 开放平台", "https://platform.kimi.com/"),
            *china_social_sources("Kimi Work", "Kimi Work"),
            *global_social_sources("Kimi Agent Office", "Kimi Work"),
            github_repo_source("MoonshotAI/kimi-agent-sdk", "GitHub-Kimi Agent SDK"),
            github_repo_source("MoonshotAI/kimi-help-center", "GitHub-Kimi Help Center"),
            github_search_source('"Kimi Agent" MoonshotAI', "GitHub社区-Kimi Work"),
        ],
    ),
    CompetitorConfig(
        name="TRAE Work",
        description="TRAE 从 AI 编程扩展到日常办公的多端协作 Agent，覆盖 Work 与 Code 双模式。",
        search_keywords=["TRAE Work", "TRAE SOLO", "TRAE AI", "TRAE Work 办公"],
        github_orgs=["Trae-AI", "bytedance"],
        sources=[
            web_source("TRAE Work 中文产品页", "https://www.trae.cn/sem-work"),
            web_source("TRAE Work 中文工作台", "https://work.trae.cn/"),
            web_source("TRAE Work 国际产品页", "https://www.trae.ai/work"),
            web_source("TRAE Work 文档", "https://docs.trae.ai/solo/what-is-trae-solo?_lang=zh"),
            *china_social_sources("TRAE Work", "TRAE Work"),
            *global_social_sources("TRAE Work", "TRAE Work"),
            github_repo_source("Trae-AI/TRAE", "GitHub-TRAE"),
            github_repo_source("bytedance/trae-agent", "GitHub-TRAE Agent"),
            github_search_source('"TRAE Work" OR "TRAE SOLO"', "GitHub社区-TRAE Work"),
        ],
    ),
    CompetitorConfig(
        name="MiniMax Code",
        description="MiniMax 面向复杂软件工程任务的多 Agent 编程产品，与 MiniMax M3 等代码模型协同。",
        search_keywords=["MiniMax Code", "MiniMax coding agent", "MiniMax M3", "MiniMax Agent"],
        github_orgs=["MiniMax-AI"],
        sources=[
            web_source("MiniMax Code 下载页", "https://agent.minimaxi.com/download"),
            web_source("MiniMax 中文官网", "https://www.minimaxi.com/"),
            web_source("MiniMax 国际官网", "https://www.minimax.io/"),
            web_source("MiniMax M3 模型页", "https://www.minimaxi.com/models/text/m3"),
            *china_social_sources("MiniMax Code", "MiniMax Code"),
            *global_social_sources("MiniMax Code", "MiniMax Code"),
            github_repo_source("MiniMax-AI/minimax-code", "GitHub-MiniMax Code"),
            github_repo_source("MiniMax-AI/MiniMax-M3", "GitHub-MiniMax M3"),
            github_repo_source("MiniMax-AI/MiniMax-Coding-Plan-MCP", "GitHub-MiniMax Coding MCP"),
            github_search_source('"MiniMax Code"', "GitHub社区-MiniMax Code"),
        ],
    ),
    CompetitorConfig(
        name="Marvis",
        description="腾讯应用宝团队推出的操作系统层级个人 AI 助手，支持文件理解、应用操控与跨端协作。",
        search_keywords=["Marvis", "Marvis 马维斯", "腾讯 Marvis", "马维斯 AI 助手"],
        github_orgs=["Tencent"],
        sources=[
            web_source("Marvis 官网", "https://marvis.qq.com/"),
            web_source("腾讯软件中心 Marvis", "https://pc.qq.com/detail/6/detail_57746.html"),
            web_source("腾讯云 Marvis 介绍", "https://cloud.tencent.com/developer/techpedia/2612"),
            *china_social_sources("腾讯 Marvis", "Marvis"),
            *global_social_sources("Tencent Marvis AI", "Marvis"),
            github_search_source('"Marvis" "Tencent" AI assistant', "GitHub社区-Marvis"),
        ],
    ),
    CompetitorConfig(
        name="QClaw",
        description="腾讯电脑管家基于 OpenClaw 生态打造的本地 AI Agent，支持微信远程办公和技能扩展。",
        search_keywords=["QClaw", "腾讯 QClaw", "QClaw OpenClaw", "QClaw 微信办公"],
        github_orgs=["Tencent"],
        sources=[
            web_source("QClaw 官网", "https://qclaw.qq.com/"),
            web_source("QClaw 产品文档", "https://qclaw.qq.com/docs/205441750814556160"),
            web_source("QClaw 国际官网", "https://qclawsg.qq.com/"),
            *china_social_sources("腾讯 QClaw", "QClaw"),
            *global_social_sources("Tencent QClaw", "QClaw"),
            github_repo_source("Tencent/openclaw-weixin", "GitHub-OpenClaw 微信"),
            github_search_source('"QClaw" "Tencent"', "GitHub社区-QClaw"),
        ],
    ),
    CompetitorConfig(
        name="阶跃 AI",
        description="阶跃星辰（StepFun）的模型与 Agent 产品体系，覆盖深度研究、多模态理解和办公工具调用。",
        search_keywords=["阶跃 AI", "阶跃星辰", "StepFun", "Step DeepResearch", "Step Agent"],
        github_orgs=["stepfun-ai"],
        sources=[
            web_source("阶跃星辰官网", "https://www.stepfun.com/"),
            web_source("阶跃星辰开放平台", "https://platform.stepfun.com/"),
            web_source("StepFun Open Platform", "https://platform.stepfun.ai/"),
            *china_social_sources("阶跃星辰 AI", "阶跃 AI"),
            *global_social_sources("StepFun AI Agent", "阶跃 AI"),
            github_repo_source("stepfun-ai/StepDeepResearch", "GitHub-Step DeepResearch"),
            github_repo_source("stepfun-ai/Step-3.7-Flash", "GitHub-Step 3.7 Flash"),
            github_repo_source("stepfun-ai", "GitHub-StepFun"),
            github_search_source('"StepFun" agent', "GitHub社区-阶跃 AI"),
        ],
    ),
]


# 便捷索引
COMPETITOR_MAP = {c.name: c for c in COMPETITORS}
