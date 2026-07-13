"""将竞品动态渲染成简短、分类式 Markdown。"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List
from zoneinfo import ZoneInfo

from jinja2 import Template

from models.data_models import AnalyzedItem, Priority, ReportSection, WeeklyReport


WEEKLY_REPORT_TEMPLATE = Template("""# 竞品动态周报｜{{ competitor_name }}

> {{ period_start }} ~ {{ period_end }} · 本周有效信号 {{ total_items }} 条

## 本周概述

{{ executive_summary or "本周期未发现新的有效竞品动态。" }}
{% for section in sections %}
## {{ section.title }}

{{ section.summary }}
{% for item in section["items"] %}
- [{{ item.summary or item.source_name }}]({{ item.url }}) · {{ item.source_name }}{% if item.published_at %} · {{ item.published_at.strftime('%m-%d') }}{% endif %}
{% endfor %}
{% endfor %}
---
*自动汇总，仅呈现代表性动态；事实以原文链接为准。*
""")

DIGEST_TEMPLATE = Template("""🚨 **竞品动态｜{{ competitor_name }}**

{{ overview }}
{% for section in sections %}
**{{ section.title }}**

{{ section.summary }}
{% for item in section["items"] %}
- [{{ item.summary or item.source_name }}]({{ item.url }}) · {{ item.source_name }}
{% endfor %}
{% endfor %}
*本次仅推送最相关动态，事实以原文为准。*""")


class ReportGenerator:
    SECTION_CONFIG = {
        ReportSection.PRODUCT: ("产品与业务动态", "product_summary", 4),
        ReportSection.MARKET: ("市场与外部报道", "market_summary", 3),
        ReportSection.SOCIAL: ("社交媒体舆论", "social_trend", 3),
        ReportSection.OPEN_SOURCE: ("开源社区", "open_source_summary", 4),
    }

    @staticmethod
    def _priority(item: AnalyzedItem) -> int:
        return {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}[item.priority]

    @classmethod
    def _representative_items(
        cls, items: List[AnalyzedItem], section: ReportSection, limit: int
    ) -> List[AnalyzedItem]:
        candidates = [item for item in items if item.report_section == section and item.url]
        candidates.sort(
            key=lambda item: (
                cls._priority(item),
                -(item.published_at.timestamp() if item.published_at else 0),
            )
        )
        result: List[AnalyzedItem] = []
        seen = set()
        for item in candidates:
            if item.url in seen:
                continue
            seen.add(item.url)
            result.append(item)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _sections(cls, items: List[AnalyzedItem], summaries: Dict, compact: bool = False) -> List[Dict]:
        result = []
        for section, (title, summary_key, limit) in cls.SECTION_CONFIG.items():
            selected = cls._representative_items(items, section, min(limit, 2) if compact else limit)
            summary = summaries.get(summary_key, "")
            if not summary and not selected:
                continue
            result.append({"title": title, "summary": summary, "items": selected})
        return result

    def generate_weekly_report_markdown(self, report: WeeklyReport) -> str:
        local_tz = ZoneInfo("Asia/Shanghai")
        summaries = {
            "product_summary": report.product_summary,
            "market_summary": report.market_summary,
            "social_trend": report.social_trend,
            "open_source_summary": report.open_source_summary,
        }
        return WEEKLY_REPORT_TEMPLATE.render(
            competitor_name=report.competitor_name,
            period_start=report.period_start.astimezone(local_tz).strftime("%Y-%m-%d"),
            period_end=report.period_end.astimezone(local_tz).strftime("%Y-%m-%d"),
            total_items=report.total_items,
            executive_summary=report.executive_summary,
            sections=self._sections(report.items, summaries),
            generated_at=datetime.now(local_tz),
        ).strip()

    def generate_monitor_digest_markdown(
        self, competitor_name: str, items: List[AnalyzedItem], summary: Dict
    ) -> str:
        return DIGEST_TEMPLATE.render(
            competitor_name=competitor_name,
            overview=summary.get("executive_summary") or f"本次发现 {len(items)} 条新动态。",
            sections=self._sections(items, summary, compact=True),
        ).strip()

    def generate_alert_markdown(self, competitor_name: str, item: AnalyzedItem) -> str:
        """兼容旧调用：单条提醒也使用事实摘要，不展示建议。"""
        return self.generate_monitor_digest_markdown(
            competitor_name,
            [item],
            {"executive_summary": item.detailed_analysis or item.summary},
        )
