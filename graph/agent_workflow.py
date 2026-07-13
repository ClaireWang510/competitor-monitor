"""
AgentWorkflow —— 基于 LangGraph 的竞品监控 Agent 工作流

核心流程（State Graph）：

  ┌─────────┐     ┌────────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ COLLECT │────▶│ DEDUPLICATE│────▶│ ANALYZE  │────▶│ REPORT   │────▶│ NOTIFY   │
  │ (并行)  │     │ (去重)     │     │ (LLM)    │     │ (渲染)   │     │ (推送)   │
  └─────────┘     └────────────┘     └──────────┘     └──────────┘     └──────────┘
      │                                   │                                    │
      └───────────────────────────────────┴────────────── 存储 ────────────────┘

两种运行模式：
  1. weekly_report  —— 定时生成周报并推送
  2. realtime_alert —— 检测到高优动态时立即推送简报
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from loguru import logger

from analyzer.llm_analyzer import LLMAnalyzer
from collectors.base import BaseCollector
from collectors.github_collector import GitHubCollector
from collectors.social_account_collector import SocialAccountCollector
from collectors.tikhub_client import TikHubClient
from collectors.web_scraper import WebScraper
from config.competitors import COMPETITOR_MAP, CompetitorConfig, SourceConfig
from config.settings import settings
from models.data_models import AgentState, AnalyzedItem, Priority, RawItem, WeeklyReport
from notifier.bot import CompositeNotifier
from reporter.report_generator import ReportGenerator
from storage.sqlite_storage import SQLiteStorage


def expand_social_sources(
    competitor: CompetitorConfig, max_keywords: int
) -> List[SourceConfig]:
    """Expand each TikHub platform into independent, clean keyword searches."""
    expanded: List[SourceConfig] = []
    for source in competitor.sources:
        if source.type not in {"tikhub", "tikhub_api"}:
            expanded.append(source)
            continue

        configured_keyword = str((source.tikhub_params or {}).get("keyword", "")).strip()
        candidates = [*competitor.search_keywords, configured_keyword]
        keywords: List[str] = []
        seen = set()
        for keyword in candidates:
            keyword = keyword.strip()
            normalized = keyword.casefold()
            if not keyword or normalized in seen:
                continue
            seen.add(normalized)
            keywords.append(keyword)

        for keyword in keywords[: max(1, max_keywords)]:
            params = dict(source.tikhub_params or {})
            params["keyword"] = keyword
            expanded.append(
                replace(
                    source,
                    name=f"{source.name} [{keyword}]",
                    tikhub_params=params,
                )
            )
    return expanded


def filter_items_by_publication_time(
    items: List[RawItem],
    period_start: datetime,
    period_end: datetime,
    include_unknown: bool,
) -> tuple[List[RawItem], int, int]:
    """Return in-window items plus counts of stale and unknown-date records."""
    start = period_start.astimezone(timezone.utc)
    end = period_end.astimezone(timezone.utc)
    accepted: List[RawItem] = []
    stale_count = 0
    unknown_count = 0

    for item in items:
        if item.published_at is None:
            unknown_count += 1
            if include_unknown:
                accepted.append(item)
            continue
        published = item.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        else:
            published = published.astimezone(timezone.utc)
        if start <= published <= end:
            accepted.append(item)
        else:
            stale_count += 1
    return accepted, stale_count, unknown_count


class CompetitorMonitorAgent:
    """
    竞品动态监控 Agent 核心引擎。
    整合采集 → 分析 → 报告 → 推送全链路。
    """

    def __init__(self):
        self.storage = SQLiteStorage()
        self.analyzer = LLMAnalyzer()
        self.reporter = ReportGenerator()
        self.notifier = CompositeNotifier()

        # 初始化各采集器
        tikhub_client = TikHubClient()
        self.collectors: dict[str, BaseCollector] = {
            "web": WebScraper(),
            "tikhub": tikhub_client,
            "tikhub_api": tikhub_client,
            "social_accounts": SocialAccountCollector(),
            "github": GitHubCollector(),
        }

    async def _collect_all(
        self, competitor: CompetitorConfig, since: Optional[datetime] = None
    ) -> List[RawItem]:
        """并行采集竞品的所有数据源"""
        tasks = []
        sources = expand_social_sources(
            competitor, settings.tikhub.max_keywords_per_platform
        )
        for source in sources:
            if not source.enabled:
                continue
            collector = self.collectors.get(source.type)
            if not collector:
                logger.warning(f"未知采集类型: {source.type}，跳过")
                continue
            tasks.append(
                collector.safe_collect(
                    source, competitor_name=competitor.name, since=since
                )
            )

        account_collector = self.collectors.get("social_accounts")
        if account_collector:
            tasks.append(
                account_collector.safe_collect(
                    SourceConfig(
                        name="官方账号与关键人物账号",
                        type="social_accounts",
                        tikhub_params={"max_items": 20},
                    ),
                    competitor_name=competitor.name,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: List[RawItem] = []
        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"采集任务异常: {result}")

        return all_items

    def _deduplicate(self, items: List[RawItem]) -> List[RawItem]:
        """基于 URL + source_type 去重（结合数据库历史）"""
        seen = set()
        unique = []
        for item in items:
            key = (
                (item.url, item.source_type)
                if item.url
                else (item.id, item.source_type)
            )
            if key in seen:
                continue
            if not self.storage.is_new_url(
                item.url, item.source_type, item.competitor_name
            ):
                continue
            seen.add(key)
            unique.append(item)
        return unique

    async def _analyze(self, items: List[RawItem]) -> List[AnalyzedItem]:
        """调用 LLM 逐条分析"""
        if not items:
            return []
        return await self.analyzer.analyze_batch(items)

    def _persist(self, raw_items: List[RawItem], analyzed_items: List[AnalyzedItem]):
        """持久化存储"""
        for item in raw_items:
            self.storage.save_raw_item(item)
        for item in analyzed_items:
            self.storage.save_analyzed_item(item)

    def _extract_alerts(self, analyzed: List[AnalyzedItem]) -> List[AnalyzedItem]:
        """筛选需要即时推送的高优先级条目"""
        return [i for i in analyzed if i.priority == Priority.HIGH]

    # ============================================================
    # 对外暴露的两种运行模式
    # ============================================================

    async def run_weekly_report(self, competitor_name: str) -> str:
        """
        生成并推送竞品周报。
        返回 Markdown 格式的周报内容。
        """
        competitor = COMPETITOR_MAP.get(competitor_name)
        if not competitor:
            raise ValueError(f"未知竞品: {competitor_name}")

        logger.info(f"[WeeklyReport] 开始生成 {competitor_name} 周报")

        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=7)

        # 1. 采集
        raw_items = await self._collect_all(competitor, since=period_start)
        new_items = self._deduplicate(raw_items)
        fresh_items, stale_count, unknown_count = filter_items_by_publication_time(
            new_items, period_start, period_end, include_unknown=False
        )
        logger.info(
            f"[WeeklyReport] 采集到 {len(raw_items)} 条，去重后 {len(new_items)} 条；"
            f"本周有效 {len(fresh_items)} 条，过期 {stale_count} 条，时间未知 {unknown_count} 条"
        )

        # 2. 分析
        analyzed = await self._analyze(fresh_items)

        # 3. 先落库，再合并实时监控在本周已积累的产品记忆
        self._persist(raw_items, analyzed)
        all_this_week = self.storage.get_items_published_between(
            competitor_name, period_start, period_end
        )

        # 4. 生成周报摘要（LLM）
        summary = await self.analyzer.generate_weekly_summary(
            competitor_name, all_this_week
        )

        # 5. 组装 WeeklyReport
        report = WeeklyReport(
            competitor_name=competitor_name,
            period_start=period_start,
            period_end=period_end,
            total_items=len(all_this_week),
            high_priority_count=sum(
                1 for i in all_this_week if i.priority == Priority.HIGH
            ),
            items=all_this_week,
            executive_summary=summary.get("executive_summary", ""),
            key_highlights=summary.get("key_highlights", []),
            threat_assessment=summary.get("threat_assessment", ""),
            opportunity_assessment=summary.get("opportunity_assessment", ""),
        )

        # 6. 渲染 Markdown
        md = self.reporter.generate_weekly_report_markdown(report)

        # 7. 推送
        await self.notifier.send_markdown(md)

        logger.info(f"[WeeklyReport] {competitor_name} 周报已推送")
        return md

    async def run_realtime_monitor(self, competitor_name: str) -> List[AnalyzedItem]:
        """
        实时监控模式：采集 → 分析 → 推送高优动态。
        返回本次发现的高优条目。
        """
        competitor = COMPETITOR_MAP.get(competitor_name)
        if not competitor:
            raise ValueError(f"未知竞品: {competitor_name}")

        logger.info(f"[RealtimeMonitor] 开始监控 {competitor_name}")

        period_end = datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=7)

        # 1. 采集
        raw_items = await self._collect_all(competitor, since=period_start)
        new_items = self._deduplicate(raw_items)
        recent_items, stale_count, unknown_count = filter_items_by_publication_time(
            new_items, period_start, period_end, include_unknown=True
        )
        if stale_count or unknown_count:
            logger.info(
                f"[RealtimeMonitor] {competitor_name}: 过期 {stale_count} 条，"
                f"时间未知但按本轮新发现保留 {unknown_count} 条"
            )

        if not recent_items:
            self._persist(raw_items, [])
            logger.info(f"[RealtimeMonitor] {competitor_name}: 无新内容")
            return []

        # 2. 分析
        analyzed = await self._analyze(recent_items)

        # 3. 持久化
        self._persist(raw_items, analyzed)

        # 4. 提取高优并推送
        alerts = self._extract_alerts(analyzed)
        if alerts:
            logger.info(
                f"[RealtimeMonitor] {competitor_name}: 发现 {len(alerts)} 条高优动态"
            )
            for alert in alerts:
                md = self.reporter.generate_alert_markdown(competitor_name, alert)
                await self.notifier.send_markdown(md)

        return alerts

    async def run_all_competitors(
        self, mode: Literal["weekly", "realtime"] = "realtime"
    ):
        """对所有配置的竞品执行指定模式"""
        for name in COMPETITOR_MAP:
            try:
                if mode == "weekly":
                    await self.run_weekly_report(name)
                else:
                    await self.run_realtime_monitor(name)
            except Exception as e:
                logger.error(f"处理竞品 {name} 时出错: {e}")

    async def close(self):
        """清理资源"""
        for collector in self.collectors.values():
            if hasattr(collector, "close"):
                await collector.close()
