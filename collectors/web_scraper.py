"""
WebScraper —— 官网 / 博客 / 新闻页面爬取与解析
支持：静态 HTML、SSR 页面、基础反爬处理
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import urljoin, urlparse

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
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.7,zh;q=0.6",
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
        self._insecure_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self, verify: bool = True) -> httpx.AsyncClient:
        attr = "_client" if verify else "_insecure_client"
        client = getattr(self, attr)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
                verify=verify,
            )
            setattr(self, attr, client)
        return client

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def _fetch(self, url: str) -> str:
        """带重试的 HTTP GET"""
        client = await self._get_client()
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.ConnectError as exc:
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                raise
            logger.warning(f"WebScraper: {url} SSL 校验失败，使用 verify=False 重试")
            insecure_client = await self._get_client(verify=False)
            resp = await insecure_client.get(url)
            resp.raise_for_status()
            return resp.text

    def _extract_articles(self, html: str, base_url: str) -> List[RawItem]:
        """
        从 HTML 中提取文章/内容块。
        启发式策略：优先 <article>，回退到 <main>，最后用 <body>。
        """
        soup = BeautifulSoup(html, "lxml")

        jsonld_items = self._extract_jsonld_items(soup, base_url)
        meta_title = self._meta_content(soup, "og:title") or self._page_title(soup)
        meta_desc = (
            self._meta_content(soup, "og:description")
            or self._meta_content(soup, "description")
            or self._meta_content(soup, "twitter:description")
        )
        meta_date = self._extract_meta_date(soup)

        # 尝试移除导航 / 页脚 / 脚本 / 样式
        for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        # 寻找主内容区
        main = soup.find("article") or soup.find("main") or soup.find("body")
        if not main:
            return []

        items: List[RawItem] = list(jsonld_items)

        # 按 <article> 或 <section> 分块
        blocks = main.find_all(["article", "section"], recursive=False)
        if not blocks:
            blocks = [main]

        for block in blocks:
            title_el = block.find(["h1", "h2", "h3"])
            title = title_el.get_text(strip=True) if title_el else meta_title

            # 提取正文段落
            paragraphs = block.find_all("p")
            text = "\n".join(
                p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)
            )
            if len(text) < 80:
                text = self._visible_text(block)
            if len(text) < 80 and meta_desc:
                text = meta_desc
            if not text and not title:
                continue

            # 尝试提取链接
            link_el = block.find("a", href=True)
            url = urljoin(base_url, link_el["href"]) if link_el else base_url

            # 尝试提取发布时间
            pub_date = self._extract_date(block)
            if not pub_date:
                pub_date = meta_date

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

        if not items:
            fallback_text = meta_desc or self._visible_text(main)
            if meta_title or fallback_text:
                items.append(
                    RawItem(
                        competitor_name="",
                        source_name="",
                        source_type="web",
                        url=base_url,
                        title=meta_title,
                        content_snippet=fallback_text[:800],
                        published_at=meta_date,
                    )
                )

        items.extend(self._extract_link_items(soup, base_url))
        return self._dedupe_items(items)

    @staticmethod
    def _page_title(soup: BeautifulSoup) -> str:
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else ""

    @staticmethod
    def _meta_content(soup: BeautifulSoup, name: str) -> str:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
        return ""

    def _extract_meta_date(self, soup: BeautifulSoup) -> Optional[datetime]:
        for name in (
            "article:published_time",
            "article:modified_time",
            "date",
            "pubdate",
            "publish_date",
            "last-modified",
        ):
            value = self._meta_content(soup, name)
            if value:
                parsed = self._parse_datetime(value)
                if parsed:
                    return parsed
        return None

    def _extract_jsonld_items(self, soup: BeautifulSoup, base_url: str) -> List[RawItem]:
        items: List[RawItem] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw or not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for record in self._iter_jsonld_records(payload):
                item = self._jsonld_to_item(record, base_url)
                if item:
                    items.append(item)
        return items

    def _iter_jsonld_records(self, payload) -> List[dict]:
        records: List[dict] = []
        if isinstance(payload, list):
            for item in payload:
                records.extend(self._iter_jsonld_records(item))
        elif isinstance(payload, dict):
            graph = payload.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    records.extend(self._iter_jsonld_records(item))
            records.append(payload)
        return records

    def _jsonld_to_item(self, record: dict, base_url: str) -> Optional[RawItem]:
        raw_type = record.get("@type", "")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if not any(
            t in {"Article", "NewsArticle", "BlogPosting", "WebPage", "Product"}
            for t in types
        ):
            return None

        title = record.get("headline") or record.get("name") or ""
        content = (
            record.get("description")
            or record.get("articleBody")
            or record.get("abstract")
            or ""
        )
        if not title and not content:
            return None

        url = record.get("url") or record.get("mainEntityOfPage") or base_url
        if isinstance(url, dict):
            url = url.get("@id") or url.get("url") or base_url
        published = self._parse_datetime(
            record.get("datePublished") or record.get("dateModified") or ""
        )
        author = record.get("author") or {}
        if isinstance(author, list):
            author = author[0] if author else {}
        author_name = author.get("name", "") if isinstance(author, dict) else str(author)

        return RawItem(
            competitor_name="",
            source_name="",
            source_type="web",
            url=urljoin(base_url, str(url)),
            title=str(title)[:200],
            content_snippet=str(content)[:800],
            author=author_name,
            published_at=published,
        )

    def _extract_link_items(self, soup: BeautifulSoup, base_url: str) -> List[RawItem]:
        items: List[RawItem] = []
        base_host = urlparse(base_url).netloc
        for link in soup.find_all("a", href=True):
            text = link.get_text(" ", strip=True)
            if len(text) < 12:
                continue
            url = urljoin(base_url, link["href"])
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue
            if not self._looks_like_content_link(url, text):
                continue
            items.append(
                RawItem(
                    competitor_name="",
                    source_name="",
                    source_type="web",
                    url=url,
                    title=text[:200],
                    content_snippet=text[:800],
                )
            )
            if len(items) >= 10:
                break
        return items

    @staticmethod
    def _looks_like_content_link(url: str, text: str) -> bool:
        haystack = f"{url} {text}".lower()
        return any(
            marker in haystack
            for marker in (
                "blog",
                "news",
                "release",
                "changelog",
                "update",
                "docs",
                "help",
                "guide",
                "product",
                "ai",
                "agent",
            )
        )

    @staticmethod
    def _visible_text(element) -> str:
        text = element.get_text(" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _dedupe_items(items: List[RawItem]) -> List[RawItem]:
        unique: List[RawItem] = []
        seen = set()
        for item in items:
            key = item.url or item.title
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    @staticmethod
    def _extract_date(element) -> Optional[datetime]:
        """启发式提取日期：<time datetime="..."> 或文本中的日期模式"""
        time_tag = element.find("time", attrs={"datetime": True})
        if time_tag:
            parsed = WebScraper._parse_datetime(time_tag["datetime"])
            if parsed:
                return parsed

        date_pattern = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
        text = element.get_text()
        match = date_pattern.search(text)
        if match:
            parsed = WebScraper._parse_datetime(match.group())
            if parsed:
                return parsed
        return None

    @staticmethod
    def _parse_datetime(value: str) -> Optional[datetime]:
        if not value:
            return None
        value = value.strip().replace("/", "-").replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%Y-%m", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
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
        if self._insecure_client and not self._insecure_client.is_closed:
            await self._insecure_client.aclose()
