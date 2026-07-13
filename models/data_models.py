"""
核心数据模型 —— 在 Agent 各节点间流转的统一数据结构
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    FEATURE_RELEASE = "feature_release"  # 新功能发布
    BLOG_POST = "blog_post"  # 博客文章
    COMMUNITY_POST = "community_post"  # 社区/社媒帖子
    GITHUB_ACTIVITY = "github_activity"  # GitHub 活动
    NEWS = "news"  # 新闻报道
    DOCUMENTATION = "documentation"  # 文档更新
    OTHER = "other"


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RawItem(BaseModel):
    """从某个数据源采集到的原始条目"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    competitor_name: str
    source_name: str
    source_type: str  # web | tikhub | github | rss
    url: str = ""
    title: str = ""
    content_snippet: str = ""  # 正文摘要（前 500 字）
    author: str = ""
    published_at: Optional[datetime] = None
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    raw_metadata: Dict = Field(default_factory=dict)


class AnalyzedItem(BaseModel):
    """经 LLM 分析后的结构化条目"""

    id: str
    competitor_name: str
    source_name: str
    content_type: ContentType = ContentType.OTHER
    priority: Priority = Priority.MEDIUM
    summary: str = ""  # 一句话总结
    detailed_analysis: str = ""  # 详细分析
    key_signals: List[str] = Field(default_factory=list)  # 关键信号点
    potential_impact: str = ""  # 对我们产品的潜在影响
    recommended_actions: List[str] = Field(default_factory=list)
    url: str = ""
    published_at: Optional[datetime] = None


class WeeklyReport(BaseModel):
    """竞品周报数据模型"""

    competitor_name: str
    report_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    period_start: datetime
    period_end: datetime
    total_items: int = 0
    high_priority_count: int = 0
    items: List[AnalyzedItem] = Field(default_factory=list)
    executive_summary: str = ""  # 高管摘要
    key_highlights: List[str] = Field(default_factory=list)
    threat_assessment: str = ""  # 威胁评估
    opportunity_assessment: str = ""  # 机会评估


class AgentState(BaseModel):
    """LangGraph Agent 的全局状态"""

    competitor_name: str
    raw_items: List[RawItem] = Field(default_factory=list)
    analyzed_items: List[AnalyzedItem] = Field(default_factory=list)
    weekly_report: Optional[WeeklyReport] = None
    alerts: List[AnalyzedItem] = Field(default_factory=list)  # 需要即时推送的高优条目
    errors: List[str] = Field(default_factory=list)
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
