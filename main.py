"""
竞品动态监控 Agent —— 主入口

用法：
  python main.py weekly          # 手动生成所有竞品的周报
  python main.py monitor         # 手动执行一次实时监控
  python main.py weekly <name>   # 生成指定竞品周报（如 "腾讯WorkBuddy"）
  python main.py monitor <name>  # 监控指定竞品
  python main.py resume-weekly <name>  # 从数据库恢复中断的周报
  python main.py serve           # 启动定时调度服务（后台持续运行）
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

from config.settings import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 配置日志
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
    level="DEBUG",
)
logger.add(
    "logs/monitor_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="INFO",
)


async def main():
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(1)

    command = args[0]
    competitor_name = args[1] if len(args) > 1 else None

    from graph.agent_workflow import CompetitorMonitorAgent

    agent = CompetitorMonitorAgent()

    try:
        if command == "weekly":
            if competitor_name:
                md = await agent.run_weekly_report(competitor_name)
                if agent.notifier.notifiers:
                    print(md)
            else:
                await agent.run_all_competitors(mode="weekly")

        elif command == "monitor":
            if competitor_name:
                alerts = await agent.run_realtime_monitor(competitor_name)
                print(f"发现 {len(alerts)} 条高优动态")
            else:
                await agent.run_all_competitors(mode="realtime")

        elif command == "resume-weekly":
            if not competitor_name:
                raise ValueError("resume-weekly 需要指定竞品名称")
            md = await agent.resume_weekly_report(competitor_name)
            if agent.notifier.notifiers:
                print(md)

        elif command == "serve":
            from graph.scheduler import MonitorScheduler

            scheduler = MonitorScheduler()
            scheduler.start()
            logger.info("调度服务运行中，Ctrl+C 退出")
            try:
                while True:
                    await asyncio.sleep(3600)
            except KeyboardInterrupt:
                scheduler.stop()

        else:
            print(f"未知命令: {command}")
            print(__doc__)
            sys.exit(1)

    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
