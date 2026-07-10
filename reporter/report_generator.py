"""
ReportGenerator —— 将分析结果渲染为 Markdown 周报
使用 Jinja2 模板引擎，方便自定义格式
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from jinja2 import Template

from models.data_models import AnalyzedItem, Priority, WeeklyReport

# ============================================================
# Markdown 周报模板
# ============================================================
WEEKLY_REPORT_TEMPLATE = Template("""# 📊 竞品动态周报｜{{ competitor_name }}

> 报告周期：{{ period_start }} ~ {{ period_end }}
> 生成时间：{{ generated_at }}

---

## 本周概览

- 监测到新动态：**{{ total_items }}** 条
- 高优先级：**{{ high_priority_count }}** 条
- 中优先级：**{{ medium_priority_count }}** 条
- 低优先级：**{{ low_priority_count }}** 条

## 一句话结论

{{ executive_summary or "本周期暂无可归纳的核心结论。" }}

---

## 重点事项

{% for h in key_highlights %}
- {{ h }}
{% else %}
- 暂无高价值重点事项。
{% endfor %}

---

## 高优动态

{% for item in high_items[:10] %}
**{{ loop.index }}. {{ item.summary }}**
- 来源：{{ item.source_name }}
- 类型：{{ item.content_type.value }}
- 时间：{{ item.published_at.strftime('%Y-%m-%d') if item.published_at else '未知' }}
- 链接：[查看原文]({{ item.url }})
- 关键信号：{{ item.key_signals | join('、') if item.key_signals else '未提取' }}
- 影响评估：{{ item.potential_impact }}
- 建议行动：{{ item.recommended_actions | join('；') if item.recommended_actions else '人工复核' }}

{% else %}
本周期暂无高优先级动态。
{% endfor %}

---

## 其他值得关注

### 中优先级
{% for item in medium_items[:12] %}
- **{{ item.summary }}**｜{{ item.source_name }}｜[查看]({{ item.url }})
{% else %}
- 暂无中优先级动态。
{% endfor %}

### 低优先级归档
{% for item in low_items[:10] %}
- {{ item.summary }}｜{{ item.source_name }}
{% else %}
- 暂无低优先级归档。
{% endfor %}

---

## 威胁评估

{{ threat_assessment }}

## 机会评估

{{ opportunity_assessment }}

---

*自动生成，仅供内部参考；请以原文链接和人工复核为准。*
""")

# 即时简报模板（用于高优动态的实时推送）
ALERT_TEMPLATE = Template("""🚨 **竞品动态提醒｜{{ competitor_name }}**

**{{ item.summary }}**

- 来源：{{ item.source_name }}
- 类型：{{ item.content_type.value }}
- 时间：{{ item.published_at.strftime('%Y-%m-%d') if item.published_at else '未知' }}
- 链接：[查看原文]({{ item.url }})

**关键信号：**
{% for s in item.key_signals %}
• {{ s }}
{% else %}
• 未提取到明确关键信号，请人工复核原文。
{% endfor %}

**影响评估：** {{ item.potential_impact }}

**建议行动：**
{% for a in item.recommended_actions %}
→ {{ a }}
{% else %}
→ 人工复核并判断是否需要跟进。
{% endfor %}

---
*竞品监控 Agent · 高优先级自动推送*""")


class ReportGenerator:
    """报告生成器"""

    def generate_weekly_report_markdown(self, report: WeeklyReport) -> str:
        """将 WeeklyReport 数据渲染为 Markdown"""
        high_items = [i for i in report.items if i.priority == Priority.HIGH]
        medium_items = [i for i in report.items if i.priority == Priority.MEDIUM]
        low_items = [i for i in report.items if i.priority == Priority.LOW]

        # 从 report 中提取摘要信息
        summary_data = {}
        if report.executive_summary:
            summary_data = {
                "executive_summary": report.executive_summary,
                "key_highlights": report.key_highlights,
                "threat_assessment": report.threat_assessment,
                "opportunity_assessment": report.opportunity_assessment,
            }
        else:
            summary_data = {
                "executive_summary": f"本周 {report.competitor_name} 共监控到 {report.total_items} 条动态，"
                f"其中高优先级 {report.high_priority_count} 条。",
                "key_highlights": [i.summary for i in high_items[:3]],
                "threat_assessment": "待分析",
                "opportunity_assessment": "待分析",
            }

        return WEEKLY_REPORT_TEMPLATE.render(
            competitor_name=report.competitor_name,
            period_start=report.period_start.strftime("%Y-%m-%d"),
            period_end=report.period_end.strftime("%Y-%m-%d"),
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            total_items=report.total_items,
            high_priority_count=report.high_priority_count,
            medium_priority_count=len(medium_items),
            low_priority_count=len(low_items),
            high_items=high_items,
            medium_items=medium_items,
            low_items=low_items,
            **summary_data,
        )

    def generate_alert_markdown(self, competitor_name: str, item: AnalyzedItem) -> str:
        """生成即时简报"""
        return ALERT_TEMPLATE.render(
            competitor_name=competitor_name,
            item=item,
        )
