"""
LLMAnalyzer —— 调用大模型对采集到的原始数据进行结构化分析
注意：LLM 的 API 配置需要用户自行填入 .env 中的 LLM_API_KEY / LLM_BASE_URL
"""

from __future__ import annotations

import json
import asyncio
from typing import Callable, List, Optional

from loguru import logger

from config.settings import settings
from models.data_models import (
    AnalyzedItem,
    ContentType,
    Priority,
    RawItem,
    ReportSection,
    WeeklyReport,
)

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
except ImportError:
    ChatOpenAI = None


# ============================================================
# Prompt 模板
# ============================================================

ANALYSIS_SYSTEM_PROMPT = """你是一个面向 AI 产品团队的竞品信息编辑。你的任务是从单条采集信息中提取可核验的事实信号。

分析原则：
1. 只基于用户提供的标题、摘要、来源、链接和时间判断，不要编造未提供的事实。
2. 优先识别产品发布、能力升级、定价/商业化、企业客户、生态合作、开源项目、开发者工具、文档变化、市场活动。
3. 如果内容是噪音、泛泛讨论、广告转载、信息不足或与竞品弱相关，将 priority 设为 low。
4. priority 规则：
   - high：会影响产品路线、销售话术、客户迁移、生态战略或需要 24 小时内响应。
   - medium：值得产品/增长/销售团队本周跟进，但无需立即响应。
   - low：仅归档或后续观察。

请严格返回以下 JSON 结构，不要添加 Markdown 或额外说明：
{
  "content_type": "feature_release|blog_post|community_post|github_activity|news|documentation|business_update|customer_case|other",
  "report_section": "product|market|social|open_source|other",
  "priority": "high|medium|low",
  "summary": "一句话总结，不超过40个中文字符",
  "detailed_analysis": "60-140字，只说明发生了什么、涉及哪些产品/对象、证据来自哪里",
  "key_signals": ["最多4条，可验证的关键信号"]
}"""

ANALYSIS_USER_TEMPLATE = """请分析以下竞品动态信息：

竞品：{competitor_name}
来源：{source_name}
来源类型：{source_type}
标题：{title}
链接：{url}
发布时间：{published_at}
内容摘要：
{content}

请返回 JSON 格式的分析结果。"""

WEEKLY_SUMMARY_SYSTEM_PROMPT = """你是竞品动态周报编辑。请把本周结构化动态压缩成简短、分类清晰、可核验的事实摘要。

要求：
1. 聚焦高/中优先级动态，不要平均罗列所有信息。
2. 不提供下一步行动、威胁或机会判断，不编造链接、数字和事实。
3. 社交媒体部分综合多个平台的共同观点、分歧和大致情绪；样本少时明确说明覆盖有限，禁止伪造比例。
4. 开源社区部分概括新功能和典型用例；原文链接由报告程序另行附上。
5. 某分类没有有效信息时返回空字符串。

请严格返回以下 JSON 格式：
{
  "executive_summary": "本周事实概述（120字以内）",
  "product_summary": "产品与业务变化概述（120字以内）",
  "market_summary": "外部报道、合作与客户案例概述（120字以内）",
  "social_trend": "跨平台舆论趋势概述（120字以内）",
  "open_source_summary": "开源新功能与典型用例概述（120字以内）"
}"""


class LLMAnalyzer:
    """
    基于 LangChain + OpenAI 兼容 API 的分析器。
    支持任何 OpenAI 兼容的 API（通过修改 base_url）。
    """

    def __init__(self):
        """
        初始化 LLM 客户端。
        TODO: 请确保在 .env 中配置了以下变量：
          - LLM_API_KEY: 你的 API Key
          - LLM_BASE_URL: API Base URL
          - LLM_MODEL: 模型名称
        """
        if ChatOpenAI is None:
            raise ImportError("请安装 langchain-openai: pip install langchain-openai")

        self.llm = ChatOpenAI(
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url,
            model=settings.llm.model,
            temperature=0.3,
            timeout=settings.llm.timeout_seconds,
            max_retries=settings.llm.max_retries,
        )

    async def analyze_item(self, raw_item: RawItem) -> Optional[AnalyzedItem]:
        """分析单条原始数据"""
        user_content = ANALYSIS_USER_TEMPLATE.format(
            competitor_name=raw_item.competitor_name,
            source_name=raw_item.source_name,
            source_type=raw_item.source_type,
            title=raw_item.title,
            url=raw_item.url,
            published_at=raw_item.published_at.isoformat()
            if raw_item.published_at
            else "未知",
            content=self._compact_text(raw_item.content_snippet),
        )

        try:
            response = await asyncio.wait_for(
                self.llm.ainvoke(
                    [
                        SystemMessage(content=ANALYSIS_SYSTEM_PROMPT),
                        HumanMessage(content=user_content),
                    ]
                ),
                timeout=settings.llm.timeout_seconds,
            )

            result = self._parse_llm_json(response.content)
            if not result:
                return self._fallback_analysis(raw_item)

            inferred_section = self._infer_section(raw_item.source_type)
            # 社媒与 GitHub 必须保留渠道维度，才能在汇总阶段做跨平台舆论和开源概述。
            report_section = (
                inferred_section
                if inferred_section in {ReportSection.SOCIAL, ReportSection.OPEN_SOURCE}
                else self._coerce_enum(
                    ReportSection,
                    result.get("report_section"),
                    inferred_section,
                )
            )

            return AnalyzedItem(
                id=raw_item.id,
                competitor_name=raw_item.competitor_name,
                source_name=raw_item.source_name,
                content_type=self._coerce_enum(
                    ContentType, result.get("content_type"), ContentType.OTHER
                ),
                report_section=report_section,
                priority=self._coerce_enum(
                    Priority, result.get("priority"), Priority.MEDIUM
                ),
                summary=result.get("summary", ""),
                detailed_analysis=result.get("detailed_analysis", ""),
                key_signals=self._coerce_list(result.get("key_signals"))[:4],
                potential_impact="",
                recommended_actions=[],
                url=raw_item.url,
                published_at=raw_item.published_at,
            )
        except Exception as e:
            logger.error(f"LLM analysis failed for {raw_item.id}: {e}")
            return self._fallback_analysis(raw_item)

    async def analyze_batch(
        self,
        raw_items: List[RawItem],
        on_result: Optional[Callable[[AnalyzedItem], None]] = None,
    ) -> List[AnalyzedItem]:
        """批量分析，限制并发以兼顾速度和 API 稳定性。"""
        semaphore = asyncio.Semaphore(max(1, settings.llm.concurrency))

        async def worker(item: RawItem) -> Optional[AnalyzedItem]:
            async with semaphore:
                return await self.analyze_item(item)

        tasks = [asyncio.create_task(worker(item)) for item in raw_items]
        results: List[AnalyzedItem] = []
        total = len(tasks)
        for completed, task in enumerate(asyncio.as_completed(tasks), start=1):
            item = await task
            if item:
                results.append(item)
                if on_result:
                    on_result(item)
            if completed == total or completed % 5 == 0:
                logger.info(f"LLM 分析进度: {completed}/{total}")
        return results

    async def generate_weekly_summary(
        self, competitor_name: str, items: List[AnalyzedItem]
    ) -> dict:
        """生成周报摘要"""
        if not items:
            return {
                "executive_summary": "本周期未发现新的有效竞品动态。",
                "product_summary": "",
                "market_summary": "",
                "social_trend": "",
                "open_source_summary": "",
            }

        summary_items = self._select_weekly_summary_items(items)
        items_text = "\n\n".join(
            "\n".join(
                [
                    f"[{i.priority.value}] {i.summary}",
                    f"来源：{i.source_name}",
                    f"类型：{i.content_type.value}",
                    f"分类：{i.report_section.value}",
                    f"信号：{'；'.join(i.key_signals) if i.key_signals else '未提取'}",
                    f"分析：{i.detailed_analysis}",
                    f"链接：{i.url}",
                ]
            )
            for i in summary_items
        )

        prompt = (
            f"竞品：{competitor_name}\n"
            f"本周结构化动态数量：{len(items)}；纳入周报摘要的重点动态：{len(summary_items)}。\n\n"
            f"{items_text}"
        )

        try:
            response = await asyncio.wait_for(
                self.llm.ainvoke(
                    [
                        SystemMessage(content=WEEKLY_SUMMARY_SYSTEM_PROMPT),
                        HumanMessage(content=prompt),
                    ]
                ),
                timeout=settings.llm.timeout_seconds,
            )
            return self._parse_llm_json(response.content) or {}
        except Exception as e:
            logger.error(f"Weekly summary generation failed: {e}")
            return {}

    @staticmethod
    def _parse_llm_json(text: str) -> Optional[dict]:
        """从 LLM 返回的文本中提取 JSON"""
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 markdown code block 中的 JSON
        import re

        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _compact_text(text: str, max_chars: int = 1200) -> str:
        """压缩单条原始内容，避免把网页噪音直接送入 LLM。"""
        clean = " ".join((text or "").split())
        return clean[:max_chars]

    @staticmethod
    def _coerce_enum(enum_cls, value, default):
        try:
            return enum_cls(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_list(value) -> List[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _priority_score(item: AnalyzedItem) -> int:
        return {
            Priority.HIGH: 0,
            Priority.MEDIUM: 1,
            Priority.LOW: 2,
        }.get(item.priority, 2)

    @staticmethod
    def _published_sort_value(item: AnalyzedItem) -> float:
        if not item.published_at:
            return 0
        try:
            return item.published_at.timestamp()
        except Exception:
            return 0

    def _select_weekly_summary_items(
        self, items: List[AnalyzedItem], limit: int = 40
    ) -> List[AnalyzedItem]:
        """周报二次汇总只输入高/中优先级和少量低优代表项，降低噪音与成本。"""
        ranked = sorted(
            items,
            key=lambda item: (
                self._priority_score(item),
                -self._published_sort_value(item),
            ),
        )
        focused = [i for i in ranked if i.priority != Priority.LOW]
        if len(focused) < min(10, len(ranked)):
            focused.extend(i for i in ranked if i.priority == Priority.LOW)
        return focused[:limit]

    @staticmethod
    def _infer_section(source_type: str) -> ReportSection:
        value = (source_type or "").lower()
        if value in {"tikhub", "tikhub_api", "social_accounts"}:
            return ReportSection.SOCIAL
        if value == "github":
            return ReportSection.OPEN_SOURCE
        if value == "web_search":
            return ReportSection.MARKET
        if value == "web":
            return ReportSection.PRODUCT
        return ReportSection.OTHER

    @staticmethod
    def _fallback_analysis(raw_item: RawItem) -> AnalyzedItem:
        """当 LLM 调用失败时的降级处理"""
        return AnalyzedItem(
            id=raw_item.id,
            competitor_name=raw_item.competitor_name,
            source_name=raw_item.source_name,
            content_type=ContentType.OTHER,
            report_section=LLMAnalyzer._infer_section(raw_item.source_type),
            priority=Priority.LOW,
            summary=raw_item.title or "无标题内容",
            detailed_analysis=f"来源: {raw_item.source_name}\n{raw_item.content_snippet[:200]}",
            key_signals=[],
            potential_impact="",
            recommended_actions=[],
            url=raw_item.url,
            published_at=raw_item.published_at,
        )
