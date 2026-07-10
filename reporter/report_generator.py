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
WEEKLY_REPORT_TEMPLATE = Template("""# 📊 竞品动态周报 —— {{ competitor_name }}

> 报告周期：{{ period_start }} ~ {{ period_end }}
> 生成时间：{{ generated_at }}

---

## 高管摘要

{{ executive_summary }}

---

## 关键亮点

{% for h in key_highlights %}
- **{{ h }}**
{% endfor %}

---

## 详细动态

### 🔴 高优先级（{{ high_priority_count }} 条）

{% for item in high_items %}
**{{ loop.index }}. {{ item.summary }}**
- 来源：{{ item.source_name }}
- 类型：{{ item.content_type.value }}
- 链接：{{ item.url }}
- 关键信号：{{ item.key_signals | join('、') }}
- 影响评估：{{ item.potential_impact }}
- 建议行动：{{ item.recommended_actions | join('；') }}

{% endfor %}

### 🟡 中优先级（{{ medium_priority_count }} 条）

{% for item in medium_items %}
**{{ loop.index }}. {{ item.summary }}**
- 来源：{{ item.source_name }} | 链接：[查看]({{ item.url }})

{% endfor %}

### 🟢 低优先级（{{ low_priority_count }} 条）

{% for item in low_items %}
- {{ item.summary }}（[{{ item.source_name }}]({{ item.url }})）
{% endfor %}

---

## 威胁评估

{{ threat_assessment }}

## 机会评估

{{ opportunity_assessment }}

---

*本报告由竞品监控 Agent 自动生成，仅供内部参考。*
""")

# 即时简报模板（用于高优动态的实时推送）
ALERT_TEMPLATE = Template("""🚨 **竞品动态提醒**

**{{ competitor_name }}** - {{ item.summary }}

- 来源：{{ item.source_name }}
- 类型：{{ item.content_type.value }}
- 链接：{{ item.url }}

**关键信号：**
{% for s in item.key_signals %}
• {{ s }}
{% endfor %}

**影响评估：** {{ item.potential_impact }}

**建议行动：**
{% for a in item.recommended_actions %}
→ {{ a }}
{% endfor %}

---
*竞品监控 Agent · 即时推送*""")


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
