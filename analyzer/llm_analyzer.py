"""
LLMAnalyzer —— 调用大模型对采集到的原始数据进行结构化分析
注意：LLM 的 API 配置需要用户自行填入 .env 中的 LLM_API_KEY / LLM_BASE_URL
"""

from __future__ import annotations

import json
from typing import List, Optional

from loguru import logger

from config.settings import settings
from models.data_models import (
    AnalyzedItem,
    ContentType,
    Priority,
    RawItem,
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

ANALYSIS_SYSTEM_PROMPT = """你是一个专业的竞品分析师 AI 助手。你的任务是分析竞品的最新动态，
提取关键信号，评估对我们产品的影响，并给出建议。

对于每条信息，请返回以下 JSON 结构的分析结果：
{
  "content_type": "feature_release|blog_post|community_post|github_activity|news|documentation|other",
  "priority": "high|medium|low",
  "summary": "一句话总结（不超过30字）",
  "detailed_analysis": "详细分析（100-300字）",
  "key_signals": ["关键信号1", "关键信号2"],
  "potential_impact": "对我们产品的潜在影响分析",
  "recommended_actions": ["建议行动1", "建议行动2"]
}

请严格按照 JSON 格式返回，不要添加其他说明文字。"""

ANALYSIS_USER_TEMPLATE = """请分析以下竞品动态信息：

竞品：{competitor_name}
来源：{source_name}
标题：{title}
内容摘要：
{content}

请返回 JSON 格式的分析结果。"""

WEEKLY_SUMMARY_SYSTEM_PROMPT = """你是一个专业的竞品分析师。请根据以下竞品动态列表，
生成一份结构化的周报摘要。

请返回以下 JSON 格式：
{
  "executive_summary": "高管摘要（150字以内）",
  "key_highlights": ["重点事项1", "重点事项2", "重点事项3"],
  "threat_assessment": "威胁评估分析（100字以内）",
  "opportunity_assessment": "机会评估分析（100字以内）"
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
        )

    async def analyze_item(self, raw_item: RawItem) -> Optional[AnalyzedItem]:
        """分析单条原始数据"""
        user_content = ANALYSIS_USER_TEMPLATE.format(
            competitor_name=raw_item.competitor_name,
            source_name=raw_item.source_name,
            title=raw_item.title,
            content=raw_item.content_snippet[:1000],
        )

        try:
            response = await self.llm.ainvoke(
                [
                    SystemMessage(content=ANALYSIS_SYSTEM_PROMPT),
                    HumanMessage(content=user_content),
                ]
            )

            result = self._parse_llm_json(response.content)
            if not result:
                return self._fallback_analysis(raw_item)

            return AnalyzedItem(
                id=raw_item.id,
                competitor_name=raw_item.competitor_name,
                source_name=raw_item.source_name,
                content_type=ContentType(result.get("content_type", "other")),
                priority=Priority(result.get("priority", "medium")),
                summary=result.get("summary", ""),
                detailed_analysis=result.get("detailed_analysis", ""),
                key_signals=result.get("key_signals", []),
                potential_impact=result.get("potential_impact", ""),
                recommended_actions=result.get("recommended_actions", []),
                url=raw_item.url,
                published_at=raw_item.published_at,
            )
        except Exception as e:
            logger.error(f"LLM analysis failed for {raw_item.id}: {e}")
            return self._fallback_analysis(raw_item)

    async def analyze_batch(self, raw_items: List[RawItem]) -> List[AnalyzedItem]:
        """批量分析，逐条调用（可后续优化为并行）"""
        results = []
        for item in raw_items:
            analyzed = await self.analyze_item(item)
            if analyzed:
                results.append(analyzed)
        return results

    async def generate_weekly_summary(
        self, competitor_name: str, items: List[AnalyzedItem]
    ) -> dict:
        """生成周报摘要"""
        items_text = "\n\n".join(
            f"[{i.priority.value}] {i.summary} ({i.source_name})\n{i.detailed_analysis}"
            for i in items
        )

        prompt = f"竞品：{competitor_name}\n\n以下是本周的动态汇总：\n{items_text}"

        try:
            response = await self.llm.ainvoke(
                [
                    SystemMessage(content=WEEKLY_SUMMARY_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
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
    def _fallback_analysis(raw_item: RawItem) -> AnalyzedItem:
        """当 LLM 调用失败时的降级处理"""
        return AnalyzedItem(
            id=raw_item.id,
            competitor_name=raw_item.competitor_name,
            source_name=raw_item.source_name,
            content_type=ContentType.OTHER,
            priority=Priority.LOW,
            summary=raw_item.title or "无标题内容",
            detailed_analysis=f"来源: {raw_item.source_name}\n{raw_item.content_snippet[:200]}",
            key_signals=[],
            potential_impact="待分析",
            recommended_actions=["请人工查看原始链接"],
            url=raw_item.url,
            published_at=raw_item.published_at,
        )
