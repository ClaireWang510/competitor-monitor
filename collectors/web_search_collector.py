"""互联网搜索发现 + 代表页面正文抓取。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from loguru import logger

from collectors.base import BaseCollector
from collectors.web_scraper import WebScraper
from config.competitors import SourceConfig
from config.settings import settings
from models.data_models import RawItem


class WebSearchCollector(BaseCollector):
    BAIDU_URL = "https://qianfan.baidubce.com/v2/ai_search/web_search"
    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, scraper: Optional[WebScraper] = None):
        self.scraper = scraper or WebScraper(timeout=settings.web_search.timeout_seconds)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=settings.web_search.timeout_seconds,
                follow_redirects=True,
            )
        return self._client

    @staticmethod
    def build_queries(name: str, keywords: List[str], limit: int) -> List[str]:
        """生成少量高信息密度查询，并控制在百度的查询长度限制内。"""
        aliases: List[str] = []
        for value in [name, *keywords]:
            value = value.strip()
            if value and value.casefold() not in {v.casefold() for v in aliases}:
                aliases.append(value)
        suffixes = ["发布 更新 新功能", "客户 合作 案例", "产品 新闻 评测"]
        queries = [f'"{aliases[i % len(aliases)]}" {suffixes[i]}'[:72]
                   for i in range(min(max(1, limit), len(suffixes)))] if aliases else []
        return queries

    def _provider(self) -> str:
        provider = settings.web_search.provider
        if provider == "auto":
            if settings.web_search.baidu_api_key:
                return "baidu"
            if settings.web_search.brave_api_key:
                return "brave"
            return "disabled"
        return provider

    async def _search_baidu(self, query: str, count: int) -> List[Dict[str, Any]]:
        client = await self._get_client()
        response = await client.post(
            self.BAIDU_URL,
            headers={"Authorization": f"Bearer {settings.web_search.baidu_api_key}"},
            json={
                "messages": [{"role": "user", "content": query}],
                "search_source": "baidu_search_v2",
                "resource_type_filter": [{"type": "web", "top_k": min(count, 50)}],
                "search_recency_filter": "week",
            },
        )
        response.raise_for_status()
        results = []
        for rank, record in enumerate(response.json().get("references", []), start=1):
            if record.get("type", "web") != "web" or not record.get("url"):
                continue
            results.append({
                "title": record.get("title", ""),
                "url": record["url"],
                "snippet": record.get("content") or record.get("snippet", ""),
                "date": record.get("date"),
                "website": record.get("website", ""),
                "rank": rank,
                "authority_score": record.get("authority_score"),
                "rerank_score": record.get("rerank_score"),
            })
        return results

    async def _search_brave(self, query: str, count: int) -> List[Dict[str, Any]]:
        client = await self._get_client()
        response = await client.get(
            self.BRAVE_URL,
            headers={"X-Subscription-Token": settings.web_search.brave_api_key},
            params={"q": query, "freshness": "pw", "count": min(count, 20),
                    "extra_snippets": "true"},
        )
        response.raise_for_status()
        results = []
        for rank, record in enumerate(response.json().get("web", {}).get("results", []), start=1):
            if not record.get("url"):
                continue
            snippets = [record.get("description", ""), *record.get("extra_snippets", [])]
            results.append({
                "title": record.get("title", ""), "url": record["url"],
                "snippet": " ".join(s for s in snippets if s),
                "date": record.get("page_age") or record.get("age"),
                "website": record.get("profile", {}).get("long_name", ""),
                "rank": rank,
            })
        return results

    @staticmethod
    def _canonical_url(url: str) -> str:
        parts = urlsplit(url.strip())
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))

    @staticmethod
    def _parse_date(value: Any) -> Optional[datetime]:
        if not value:
            return None
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%b %d, %Y"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    parsed = None
            if parsed is None:
                return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

    async def _enrich(self, item: RawItem) -> RawItem:
        try:
            html = await self.scraper._fetch(item.url)
            candidates = self.scraper._extract_articles(html, item.url)
            if not candidates:
                return item
            candidate = max(candidates, key=lambda value: len(value.content_snippet or ""))
            if len(candidate.content_snippet or "") > len(item.content_snippet or ""):
                item.content_snippet = candidate.content_snippet
            item.title = candidate.title or item.title
            item.author = candidate.author or item.author
            item.published_at = candidate.published_at or item.published_at
            item.raw_metadata["page_enriched"] = True
        except Exception as exc:
            logger.debug(f"WebSearch: 正文抓取失败 {item.url}: {exc}")
        return item

    async def collect(self, source: SourceConfig, competitor_name: str = "", **kwargs) -> List[RawItem]:
        provider = self._provider()
        key = (settings.web_search.baidu_api_key if provider == "baidu"
               else settings.web_search.brave_api_key if provider == "brave" else "")
        if provider not in {"baidu", "brave"} or not key:
            logger.info("WebSearch: 未配置 BAIDU_SEARCH_API_KEY 或 BRAVE_SEARCH_API_KEY，跳过互联网搜索")
            return []

        queries = source.search_queries[:settings.web_search.max_queries]
        search = self._search_baidu if provider == "baidu" else self._search_brave
        records: List[Dict[str, Any]] = []
        for query in queries:
            try:
                for record in await search(query, settings.web_search.max_results):
                    record["query"] = query
                    records.append(record)
            except Exception as exc:
                logger.warning(f"WebSearch {provider} 查询失败 [{query}]: {exc}")

        items: List[RawItem] = []
        seen = set()
        for record in records:
            canonical = self._canonical_url(record.get("url", ""))
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            parsed_date = self._parse_date(record.get("date"))
            items.append(RawItem(
                competitor_name=competitor_name,
                source_name=f"互联网搜索/{record.get('website') or provider}",
                source_type="web_search",
                url=record["url"], title=record.get("title", "")[:200],
                content_snippet=record.get("snippet", "")[:1200],
                # 搜索接口已限定最近一周；没有结构化日期时以“发现时间”进入本周记忆。
                published_at=parsed_date or datetime.now(timezone.utc),
                raw_metadata={"provider": provider, "query": record.get("query"),
                              "rank": record.get("rank"),
                              "published_at_inferred": parsed_date is None,
                              "authority_score": record.get("authority_score"),
                              "rerank_score": record.get("rerank_score")},
            ))
            if len(items) >= settings.web_search.max_results:
                break

        enrich_count = min(settings.web_search.fetch_pages, len(items))
        enriched = await asyncio.gather(*(self._enrich(item) for item in items[:enrich_count]))
        result = [*enriched, *items[enrich_count:]]
        logger.info(f"WebSearch {provider}: {len(queries)} 个查询 -> {len(result)} 条去重结果")
        return result

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        await self.scraper.close()
