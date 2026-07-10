"""
WebScraper —— 官网 / 博客 / 新闻页面爬取与解析
支持：静态 HTML、SSR 页面、基础反爬处理
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from config.competitors import SourceConfig
from models.data_models import ContentType, RawItem

# 通用请求头，模拟浏览器
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class WebScraper(BaseCollector):
    """
    通用网页爬取器。
    策略：
      1. 抓取目标 URL 的 HTML
      2. 提取页面中的 <article> / <h1-h3> / <p> 内容
      3. 对于新闻列表页，尝试提取子链接并逐一抓取（可配置深度）
    """

    def __init__(self, timeout: int = 30, follow_links: int = 0):
        self.timeout = timeout
        self.follow_links = follow_links  # MVP 阶段暂不跟踪子链接
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
            )
        return self._client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def _fetch(self, url: str) -> str:
        """带重试的 HTTP GET"""
        client = await self._get_client()
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    def _extract_articles(self, html: str, base_url: str) -> List[RawItem]:
        """
        从 HTML 中提取文章/内容块。
        启发式策略：优先 <article>，回退到 <main>，最后用 <body>。
        """
        soup = BeautifulSoup(html, "lxml")

        # 尝试移除导航 / 页脚 / 脚本 / 样式
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # 寻找主内容区
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if not main:
            return []

        items: List[RawItem] = []

        # 按 <article> 或 <section> 分块
        blocks = main.find_all(["article", "section"], recursive=False)
        if not blocks:
            blocks = [main]

        for block in blocks:
            title_el = block.find(["h1", "h2", "h3"])
            title = title_el.get_text(strip=True) if title_el else ""

            # 提取正文段落
            paragraphs = block.find_all("p")
            text = "\n".join(
                p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)
            )
            if not text and not title:
                continue

            # 尝试提取链接
            link_el = block.find("a", href=True)
            url = urljoin(base_url, link_el["href"]) if link_el else base_url

            # 尝试提取发布时间
            pub_date = self._extract_date(block)

            items.append(
                RawItem(
                    competitor_name="",  # 由上层填充
                    source_name="",  # 由上层填充
                    source_type="web",
                    url=url,
                    title=title,
                    content_snippet=text[:800],
                    published_at=pub_date,
                )
            )

        return items

    @staticmethod
    def _extract_date(element) -> Optional[datetime]:
        """启发式提取日期：<time datetime="..."> 或文本中的日期模式"""
        time_tag = element.find("time", attrs={"datetime": True})
        if time_tag:
            try:
                return datetime.fromisoformat(
                    time_tag["datetime"].replace("Z", "+00:00")
                )
            except ValueError:
                pass

        date_pattern = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
        text = element.get_text()
        match = date_pattern.search(text)
        if match:
            try:
                return datetime.strptime(match.group(), "%Y-%m-%d")
            except ValueError:
                pass
        return None

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        html = await self._fetch(source.url)
        items = self._extract_articles(html, source.url)

        # 填充来源信息
        for item in items:
            item.competitor_name = competitor_name
            item.source_name = source.name

        logger.debug(f"WebScraper: {source.url} -> {len(items)} items")
        return items

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
