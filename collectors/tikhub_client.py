"""
TikHubClient —— 通过 TikHub API 采集社交媒体数据
支持平台：X/Twitter、Instagram、YouTube、Reddit、小红书、B站、微博、知乎
文档：https://docs.tikhub.io | Swagger: https://api.tikhub.io
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from config.competitors import SourceConfig
from config.settings import settings
from models.data_models import RawItem

# TikHub API 各平台的搜索端点映射
# 参考 Swagger 文档：https://api.tikhub.io
PLATFORM_ENDPOINTS = {
    # 国内平台
    "weibo": {
        "search": "/api/v1/weibo/web_v2/fetch_realtime_search",
        "user_posts": "/api/v1/weibo/user_posts",
    },
    "xiaohongshu": {
        "search": "/api/v1/xiaohongshu/app_v2/search_notes",
        "note_detail": "/api/v1/xiaohongshu/app_v2/get_image_note_detail",
    },
    "bilibili": {
        "search": "/api/v1/bilibili/web/fetch_general_search",
        "video_detail": "/api/v1/bilibili/web/fetch_one_video_v3",
    },
    "zhihu": {
        "search": "/api/v1/zhihu/web/fetch_article_search_v3",
    },
    "douyin": {
        "search": "/api/v1/douyin/web/search_by_keyword",
    },
    # 海外平台
    "twitter": {
        "search": "/api/v1/twitter/web/search_by_keyword",
        "user_tweets": "/api/v1/twitter/web/user_tweets",
    },
    "instagram": {
        "search": "/api/v1/instagram/web/search_by_keyword",
        "user_posts": "/api/v1/instagram/web/user_posts",
    },
    "youtube": {
        "search": "/api/v1/youtube/web_v2/get_general_search_v2",
        "video_detail": "/api/v1/youtube/web/video_detail",
    },
    "reddit": {
        "search": "/api/v1/reddit/web/search_by_keyword",
        "subreddit_posts": "/api/v1/reddit/web/subreddit_posts",
    },
    "threads": {
        "search": "/api/v1/threads/web/search_by_keyword",
    },
}


class TikHubClient(BaseCollector):
    """
    TikHub API 客户端。
    - 自动处理 Bearer Token 认证
    - 内置 QPS 限流（令牌桶）
    - 统一的数据解析与 RawItem 转换
    """

    def __init__(self):
        self.base_url = settings.tikhub.base_url.rstrip("/")
        self.token = settings.tikhub.token
        self.timeout = settings.tikhub.timeout
        self.qps = settings.tikhub.qps
        self._semaphore = asyncio.Semaphore(self.qps)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                },
            )
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    async def _request(
        self, endpoint: str, params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """带限流和重试的 API 请求"""
        async with self._semaphore:
            client = await self._get_client()
            resp = await client.get(endpoint, params=params or {})
            resp.raise_for_status()
            return resp.json()

    def _parse_items(
        self,
        data: Dict[str, Any],
        source: SourceConfig,
        competitor_name: str,
        platform: str,
    ) -> List[RawItem]:
        """
        将 TikHub 返回的 JSON 解析为 RawItem 列表。
        不同平台的返回结构略有差异，此方法做统一适配。
        """
        items: List[RawItem] = []

        # TikHub 通用返回结构：{code, data: [...], message}
        records = data.get("data", [])
        if isinstance(records, dict):
            # 有些端点返回嵌套结构
            records = records.get("data", records.get("items", []))
        if not isinstance(records, list):
            records = [records] if records else []

        for record in records:
            if not isinstance(record, dict):
                continue

            item = self._parse_single_record(record, platform)
            if item:
                item.competitor_name = competitor_name
                item.source_name = source.name
                item.source_type = f"tikhub:{platform}"
                items.append(item)

        return items

    def _parse_single_record(self, record: Dict, platform: str) -> Optional[RawItem]:
        """解析单条记录，适配各平台字段差异"""

        title = ""
        content = ""
        url = ""
        author = ""
        published_at = None

        # 通用字段提取
        title = (
            record.get("title")
            or record.get("desc")
            or record.get("text")
            or record.get("name")
            or ""
        )
        content = (
            record.get("content")
            or record.get("description")
            or record.get("text")
            or record.get("desc")
            or ""
        )[:800]
        author = (
            record.get("author", {}).get("name")
            if isinstance(record.get("author"), dict)
            else record.get("author_name", record.get("username", ""))
        )

        # 平台特定字段
        if platform == "twitter":
            url = f"https://x.com/i/status/{record.get('id', '')}"
            content = record.get("full_text", record.get("text", content))
            author = (
                record.get("user", {}).get("screen_name", author)
                if isinstance(record.get("user"), dict)
                else author
            )

        elif platform == "youtube":
            video_id = record.get("video_id", record.get("id", ""))
            url = f"https://www.youtube.com/watch?v={video_id}"
            title = record.get("title", title)

        elif platform == "instagram":
            shortcode = record.get("shortcode", record.get("id", ""))
            url = f"https://www.instagram.com/p/{shortcode}/"

        elif platform == "reddit":
            permalink = record.get("permalink", record.get("id", ""))
            url = (
                f"https://www.reddit.com{permalink}"
                if permalink.startswith("/")
                else permalink
            )
            title = record.get("title", title)

        elif platform == "xiaohongshu":
            note_id = record.get("note_id", record.get("id", ""))
            url = f"https://www.xiaohongshu.com/explore/{note_id}"

        elif platform == "bilibili":
            bvid = record.get("bvid", record.get("bv_id", ""))
            url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

        elif platform == "weibo":
            mid = record.get("mid", record.get("id", ""))
            url = f"https://weibo.com/{mid}"

        elif platform == "zhihu":
            url = record.get("url", "")

        # 尝试解析时间
        time_str = record.get("created_at", record.get("publish_time", ""))
        if time_str:
            try:
                published_at = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        if not title and not content:
            return None

        return RawItem(
            url=url,
            title=title[:200],
            content_snippet=content,
            author=str(author),
            published_at=published_at,
            raw_metadata={"platform": platform, "original_id": record.get("id", "")},
        )

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        """
        通过 TikHub API 搜索社交媒体内容。
        source.tikhub_endpoint: API 端点路径
        source.tikhub_params: 查询参数（通常包含 keyword）
        """
        endpoint = source.tikhub_endpoint
        params = source.tikhub_params.copy()

        # 确定平台类型
        platform = (
            endpoint.split("/")[3] if endpoint.startswith("/api/v1/") else "unknown"
        )

        logger.debug(f"TikHub: [{platform}] {endpoint} params={params}")
        data = await self._request(endpoint, params)
        items = self._parse_items(data, source, competitor_name, platform)

        logger.debug(f"TikHub: {source.name} -> {len(items)} items")
        return items

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
