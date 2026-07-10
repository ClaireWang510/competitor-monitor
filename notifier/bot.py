"""
BotNotifier —— 向工作群机器人推送消息
支持：企业微信、飞书、钉钉
通过 Webhook 方式发送 Markdown 格式消息
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import time
import urllib.parse
from abc import ABC, abstractmethod
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings


class BaseNotifier(ABC):
    """通知器基类"""

    @abstractmethod
    async def send_markdown(self, content: str) -> bool:
        """发送 Markdown 格式消息，返回是否成功"""
        ...

    async def safe_send(self, content: str) -> bool:
        try:
            return await self.send_markdown(content)
        except Exception as e:
            logger.error(f"[{self.__class__.__name__}] 发送失败: {e}")
            return False


class WeChatWorkNotifier(BaseNotifier):
    """
    企业微信群机器人 Webhook
    文档：https://developer.work.weixin.qq.com/document/path/91770
    """

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.wechat_work.webhook_url

    async def send_markdown(self, content: str) -> bool:
        if not self.webhook_url:
            logger.warning("WeChatWork: 未配置 webhook URL，跳过")
            return False

        # 企业微信 Markdown 有长度限制，超长则截断
        if len(content) > 4096:
            content = content[:4000] + "\n\n...(内容过长已截断)"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.webhook_url,
                json={
                    "msgtype": "markdown",
                    "markdown": {"content": content},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") != 0:
                logger.error(f"WeChatWork API error: {data}")
                return False
            logger.info("WeChatWork: 消息发送成功")
            return True


class FeishuNotifier(BaseNotifier):
    """
    飞书群机器人 Webhook
    文档：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or settings.feishu.webhook_url

    async def send_markdown(self, content: str) -> bool:
        if not self.webhook_url:
            logger.warning("Feishu: 未配置 webhook URL，跳过")
            return False

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.webhook_url,
                json={
                    "msgtype": "interactive",
                    "card": {
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": content,
                            }
                        ]
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"Feishu API error: {data}")
                return False
            logger.info("Feishu: 消息发送成功")
            return True


class DingTalkNotifier(BaseNotifier):
    """
    钉钉群机器人 Webhook（加签模式）
    文档：https://open.dingtalk.com/document/robots/custom-robot-access
    """

    def __init__(self, webhook_url: Optional[str] = None, secret: Optional[str] = None):
        self.webhook_url = webhook_url or settings.dingtalk.webhook_url
        self.secret = secret or settings.dingtalk.secret

    def _sign(self) -> str:
        """生成加签参数"""
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"

    async def send_markdown(self, content: str) -> bool:
        if not self.webhook_url:
            logger.warning("DingTalk: 未配置 webhook URL，跳过")
            return False

        url = self._sign() if self.secret else self.webhook_url

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": "竞品动态监控",
                        "text": content,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("errcode") != 0:
                logger.error(f"DingTalk API error: {data}")
                return False
            logger.info("DingTalk: 消息发送成功")
            return True


class CompositeNotifier(BaseNotifier):
    """
    组合通知器 —— 同时向多个渠道发送。
    自动过滤未配置的渠道。
    """

    def __init__(self):
        self.notifiers: list[BaseNotifier] = []
        if settings.wechat_work.webhook_url:
            self.notifiers.append(WeChatWorkNotifier())
        if settings.feishu.webhook_url:
            self.notifiers.append(FeishuNotifier())
        if settings.dingtalk.webhook_url:
            self.notifiers.append(DingTalkNotifier())

    async def send_markdown(self, content: str) -> bool:
        if not self.notifiers:
            logger.warning("CompositeNotifier: 未配置任何通知渠道，仅打印到控制台")
            print("\n" + "=" * 60)
            print(content)
            print("=" * 60 + "\n")
            return True

        results = []
        for notifier in self.notifiers:
            ok = await notifier.safe_send(content)
            results.append(ok)
        return any(results)
