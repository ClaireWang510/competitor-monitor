"""
全局配置加载模块
从环境变量 / .env 文件加载所有配置项
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LLMConfig(BaseModel):
    """大模型 API 配置 —— 请填入你自己的 key / base_url"""

    api_key: str = Field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    base_url: str = Field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    )
    model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o"))


class TikHubConfig(BaseModel):
    """TikHub API 配置"""

    token: str = Field(default_factory=lambda: os.getenv("TIKHUB_API_TOKEN", ""))
    base_url: str = Field(
        default_factory=lambda: os.getenv("TIKHUB_BASE_URL", "https://api.tikhub.io")
    )
    timeout: int = 30
    max_retries: int = 3
    qps: int = 10  # TikHub 速率限制
    mcp_base_url: str = Field(
        default_factory=lambda: os.getenv("TIKHUB_MCP_BASE_URL", "https://mcp.tikhub.io")
    )
    mcp_command: str = Field(default_factory=lambda: os.getenv("TIKHUB_MCP_COMMAND", "npx"))
    mcp_remote_package: str = Field(
        default_factory=lambda: os.getenv("TIKHUB_MCP_REMOTE_PACKAGE", "mcp-remote")
    )
    mcp_timeout: int = Field(
        default_factory=lambda: int(os.getenv("TIKHUB_MCP_TIMEOUT", "120"))
    )
    mcp_max_results: int = Field(
        default_factory=lambda: int(os.getenv("TIKHUB_MCP_MAX_RESULTS", "20"))
    )
    mcp_insecure_tls: bool = Field(
        default_factory=lambda: os.getenv("TIKHUB_MCP_INSECURE_TLS", "").lower()
        in {"1", "true", "yes", "on"}
    )


class GitHubConfig(BaseModel):
    """GitHub API 配置"""

    token: Optional[str] = Field(default_factory=lambda: os.getenv("GITHUB_TOKEN"))


class WeChatWorkConfig(BaseModel):
    """企业微信机器人配置"""

    webhook_url: str = Field(
        default_factory=lambda: os.getenv("WECHAT_WORK_WEBHOOK_URL", "")
    )


class FeishuConfig(BaseModel):
    """飞书机器人配置"""

    webhook_url: str = Field(
        default_factory=lambda: os.getenv("FEISHU_WEBHOOK_URL", "")
    )


class DingTalkConfig(BaseModel):
    """钉钉机器人配置"""

    webhook_url: str = Field(
        default_factory=lambda: os.getenv("DINGTALK_WEBHOOK_URL", "")
    )
    secret: str = Field(default_factory=lambda: os.getenv("DINGTALK_SECRET", ""))


class StorageConfig(BaseModel):
    """数据存储配置"""

    db_path: Path = Field(
        default_factory=lambda: (
            PROJECT_ROOT / os.getenv("SQLITE_DB_PATH", "data/competitor_monitor.db")
        )
    )


class ScheduleConfig(BaseModel):
    """调度配置"""

    weekly_report_cron: str = Field(
        default_factory=lambda: os.getenv("WEEKLY_REPORT_CRON", "0 9 * * 1")
    )
    monitor_interval_hours: int = Field(
        default_factory=lambda: int(os.getenv("MONITOR_INTERVAL_HOURS", "2"))
    )


class Settings(BaseModel):
    """聚合所有配置"""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    tikhub: TikHubConfig = Field(default_factory=TikHubConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    wechat_work: WeChatWorkConfig = Field(default_factory=WeChatWorkConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)


# 全局单例
settings = Settings()
