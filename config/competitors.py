"""
竞品监控目标配置
MVP 阶段：腾讯 WorkBuddy + Claude（Anthropic）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SourceConfig:
    """单个数据源配置"""

    name: str  # 数据源名称，如 "官网"、"X/Twitter"
    type: str  # 采集类型：web | tikhub | github | rss
    url: str = ""  # 目标 URL
    # TikHub 专用字段
    tikhub_endpoint: str = ""  # TikHub API 端点路径
    tikhub_params: dict = field(default_factory=dict)  # 额外请求参数
    # GitHub 专用字段
    github_repo: str = ""  # 格式：owner/repo
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
    # 相关 GitHub 组织 / 用户
    github_orgs: List[str] = field(default_factory=list)
    enabled: bool = True


# ============================================================
# MVP 竞品配置
# ============================================================

COMPETITORS: List[CompetitorConfig] = [
    # ---------------- 腾讯 WorkBuddy ----------------
    CompetitorConfig(
        name="腾讯WorkBuddy",
        description="腾讯推出的企业级 AI 工作助手，集成 IM、文档、日程等办公场景",
        search_keywords=["腾讯WorkBuddy", "WorkBuddy", "腾讯AI办公"],
        github_orgs=["Tencent"],
        sources=[
            # --- 官方渠道 ---
            SourceConfig(
                name="WorkBuddy官网",
                type="web",
                url="https://workbuddy.qq.com",
            ),
            SourceConfig(
                name="腾讯云AI动态",
                type="web",
                url="https://cloud.tencent.com/product/hunyuan",
            ),
            # --- 社交媒体（通过 TikHub）---
            SourceConfig(
                name="微博-WorkBuddy",
                type="tikhub",
                tikhub_endpoint="/api/v1/weibo/search_by_keyword",
                tikhub_params={"keyword": "腾讯WorkBuddy"},
            ),
            SourceConfig(
                name="小红书-WorkBuddy",
                type="tikhub",
                tikhub_endpoint="/api/v1/xiaohongshu/app_v2/search_by_keyword",
                tikhub_params={"keyword": "WorkBuddy"},
            ),
            SourceConfig(
                name="知乎-WorkBuddy",
                type="tikhub",
                tikhub_endpoint="/api/v1/zhihu/search_by_keyword",
                tikhub_params={"keyword": "腾讯WorkBuddy"},
            ),
            SourceConfig(
                name="B站-WorkBuddy",
                type="tikhub",
                tikhub_endpoint="/api/v1/bilibili/web/search_by_keyword",
                tikhub_params={"keyword": "WorkBuddy 腾讯"},
            ),
            # --- GitHub ---
            SourceConfig(
                name="GitHub-Tencent",
                type="github",
                github_repo="Tencent",
            ),
        ],
    ),
    # ---------------- Claude（Anthropic）----------------
    CompetitorConfig(
        name="Claude",
        description="Anthropic 推出的大语言模型产品，以安全性和长文本能力著称",
        search_keywords=["Claude AI", "Anthropic Claude", "Claude 3", "Claude 4"],
        github_orgs=["anthropics"],
        sources=[
            # --- 官方渠道 ---
            SourceConfig(
                name="Claude官网",
                type="web",
                url="https://claude.ai",
            ),
            SourceConfig(
                name="Anthropic官网",
                type="web",
                url="https://www.anthropic.com",
            ),
            SourceConfig(
                name="Anthropic博客",
                type="web",
                url="https://www.anthropic.com/news",
            ),
            SourceConfig(
                name="Anthropic文档",
                type="web",
                url="https://docs.anthropic.com",
            ),
            # --- 海外社交媒体（通过 TikHub）---
            SourceConfig(
                name="X-AnthropicAI",
                type="tikhub",
                tikhub_endpoint="/api/v1/twitter/web/search_by_keyword",
                tikhub_params={"keyword": "Anthropic Claude"},
            ),
            SourceConfig(
                name="YouTube-Anthropic",
                type="tikhub",
                tikhub_endpoint="/api/v1/youtube/web/search_by_keyword",
                tikhub_params={"keyword": "Claude AI Anthropic"},
            ),
            SourceConfig(
                name="Reddit-ClaudeAI",
                type="tikhub",
                tikhub_endpoint="/api/v1/reddit/web/search_by_keyword",
                tikhub_params={
                    "keyword": "Claude AI Anthropic",
                    "subreddit": "ClaudeAI",
                },
            ),
            SourceConfig(
                name="Instagram-anthropic",
                type="tikhub",
                tikhub_endpoint="/api/v1/instagram/web/search_by_keyword",
                tikhub_params={"keyword": "Anthropic Claude"},
            ),
            # --- GitHub ---
            SourceConfig(
                name="GitHub-Anthropics",
                type="github",
                github_repo="anthropics",
            ),
        ],
    ),
]

# 便捷索引
COMPETITOR_MAP = {c.name: c for c in COMPETITORS}
