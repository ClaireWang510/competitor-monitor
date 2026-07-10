"""
Scheduler —— 定时任务调度
支持两种调度模式：
  1. 周报定时任务（cron: 每周一 09:00）
  2. 实时监控轮询（interval: 每 2 小时）

使用 APScheduler 实现，支持持久化任务状态。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config.settings import settings


class MonitorScheduler:
    """竞品监控定时调度器"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._agent = None  # 延迟初始化，避免启动时就校验所有配置

    def _get_agent(self):
        if self._agent is None:
            from graph.agent_workflow import CompetitorMonitorAgent

            self._agent = CompetitorMonitorAgent()
        return self._agent

    async def _weekly_report_job(self):
        """周报定时任务"""
        logger.info(f"[Scheduler] 触发周报生成 {datetime.now()}")
        agent = self._get_agent()
        try:
            await agent.run_all_competitors(mode="weekly")
        except Exception as e:
            logger.error(f"[Scheduler] 周报生成失败: {e}")

    async def _realtime_monitor_job(self):
        """实时监控轮询任务"""
        logger.debug(f"[Scheduler] 触发实时监控 {datetime.now()}")
        agent = self._get_agent()
        try:
            await agent.run_all_competitors(mode="realtime")
        except Exception as e:
            logger.error(f"[Scheduler] 实时监控失败: {e}")

    def setup(self):
        """
        注册定时任务。
        从 settings 中读取 cron 表达式和间隔时间。
        """
        # 周报：每周一 09:00（Asia/Shanghai）
        cron_parts = settings.schedule.weekly_report_cron.split()
        if len(cron_parts) == 5:
            minute, hour, day, month, day_of_week = cron_parts
            self.scheduler.add_job(
                self._weekly_report_job,
                CronTrigger(
                    minute=minute,
                    hour=hour,
                    day=day,
                    month=month,
                    day_of_week=day_of_week,
                ),
                id="weekly_report",
                name="竞品周报生成",
                replace_existing=True,
            )
            logger.info(f"已注册周报任务: cron={settings.schedule.weekly_report_cron}")

        # 实时监控：每隔 N 小时
        self.scheduler.add_job(
            self._realtime_monitor_job,
            IntervalTrigger(hours=settings.schedule.monitor_interval_hours),
            id="realtime_monitor",
            name="竞品实时监控",
            replace_existing=True,
        )
        logger.info(
            f"已注册实时监控: 每 {settings.schedule.monitor_interval_hours} 小时"
        )

    def start(self):
        """启动调度器"""
        self.setup()
        self.scheduler.start()
        logger.info("竞品监控调度器已启动")

    def stop(self):
        """停止调度器"""
        self.scheduler.shutdown(wait=False)
        logger.info("竞品监控调度器已停止")

    async def run_once(self, mode: str = "realtime"):
        """手动触发一次（用于调试）"""
        agent = self._get_agent()
        if mode == "weekly":
            await agent.run_all_competitors(mode="weekly")
        else:
            await agent.run_all_competitors(mode="realtime")
