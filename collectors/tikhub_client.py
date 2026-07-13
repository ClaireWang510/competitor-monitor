"""
TikHub REST collector for social media search.

This collector intentionally uses the REST API directly instead of the MCP
bridge. The official SDK is a generated wrapper over the same OpenAPI paths,
so keeping the integration here on httpx avoids adding another runtime
dependency while preserving the SDK endpoint/parameter conventions.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional

import httpx
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from config.competitors import SourceConfig
from config.settings import settings
from models.data_models import RawItem


SEARCH_CONFIGS: dict[str, dict[str, Any]] = {
    "weibo": {
        "endpoint": "/api/v1/weibo/web_v2/fetch_realtime_search",
        "keyword_param": "query",
        "defaults": {"page": 1},
    },
    "xiaohongshu": {
        "endpoint": "/api/v1/xiaohongshu/app_v2/search_notes",
        "keyword_param": "keyword",
        "defaults": {"page": 1},
    },
    "zhihu": {
        "endpoint": "/api/v1/zhihu/web/fetch_article_search_v3",
        "keyword_param": "keyword",
        "defaults": {"offset": "0", "limit": "20"},
    },
    "bilibili": {
        "endpoint": "/api/v1/bilibili/web/fetch_general_search",
        "keyword_param": "keyword",
        "defaults": {"order": "pubdate", "page": 1, "page_size": 20},
    },
    "douyin": {
        "endpoint": "/api/v1/douyin/search/fetch_general_search_v2",
        "method": "POST",
        "keyword_param": "keyword",
        "defaults": {"cursor": 0},
    },
    "twitter": {
        "endpoint": "/api/v1/twitter/web/fetch_search_timeline",
        "keyword_param": "keyword",
        "defaults": {"search_type": "Latest"},
    },
    "youtube": {
        "endpoint": "/api/v1/youtube/web_v2/get_general_search_v2",
        "keyword_param": "keyword",
        "defaults": {"need_format": True},
    },
    "reddit": {
        "endpoint": "/api/v1/reddit/app/fetch_dynamic_search",
        "keyword_param": "query",
        "defaults": {
            "search_type": "posts",
            "sort": "new",
            "time_range": "week",
            "need_format": True,
        },
    },
    "instagram": {
        "endpoint": "/api/v1/instagram/v2/general_search",
        "keyword_param": "keyword",
        "defaults": {},
    },
    "threads": {
        "endpoint": "/api/v1/threads/web/search_top",
        "keyword_param": "query",
        "defaults": {},
    },
}

PLATFORM_ALIASES = {
    "x": "twitter",
    "twitter": "twitter",
    "youtube": "youtube",
    "reddit": "reddit",
    "instagram": "instagram",
    "threads": "threads",
    "weibo": "weibo",
    "微博": "weibo",
    "xiaohongshu": "xiaohongshu",
    "小红书": "xiaohongshu",
    "zhihu": "zhihu",
    "知乎": "zhihu",
    "bilibili": "bilibili",
    "b站": "bilibili",
    "douyin": "douyin",
    "抖音": "douyin",
}

CONTAINER_KEYS = (
    "data",
    "items",
    "item_list",
    "list",
    "results",
    "records",
    "statuses",
    "tweets",
    "notes",
    "videos",
    "posts",
    "feeds",
    "cards",
    "contents",
    "response",
    "result",
    "timeline",
    "instructions",
    "entries",
)

RECORD_HINT_KEYS = {
    "id",
    "id_str",
    "tweet_id",
    "rest_id",
    "mid",
    "mblogid",
    "note_id",
    "aweme_id",
    "bvid",
    "bv_id",
    "video_id",
    "videoId",
    "shortcode",
    "code",
    "url",
    "share_url",
    "link",
    "permalink",
    "title",
    "desc",
    "description",
    "text",
    "content",
    "full_text",
    "caption",
    "author",
    "user",
    "owner",
    "created_at",
    "publish_time",
}


class TikHubClient(BaseCollector):
    """TikHub REST API client with endpoint fallback and JSON normalization."""

    def __init__(self):
        self.token = settings.tikhub.token
        self.timeout = settings.tikhub.timeout
        self.qps = max(1, settings.tikhub.qps)
        self._semaphore = asyncio.Semaphore(self.qps)
        self._clients: dict[tuple[str, bool], httpx.AsyncClient] = {}
        self._base_urls = self._candidate_base_urls(settings.tikhub.base_url)

    @staticmethod
    def _candidate_base_urls(configured: str) -> list[str]:
        configured = (configured or "").rstrip("/")
        candidates = [configured] if configured else []
        for url in ("https://api.tikhub.io", "https://api.tikhub.dev"):
            if url not in candidates:
                candidates.append(url)
        return candidates

    async def _get_client(self, base_url: str, verify: bool = True) -> httpx.AsyncClient:
        key = (base_url, verify)
        if key not in self._clients or self._clients[key].is_closed:
            self._clients[key] = httpx.AsyncClient(
                base_url=base_url,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "competitor-monitor/1.0",
                },
                follow_redirects=True,
                verify=verify,
            )
        return self._clients[key]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=20),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
        reraise=True,
    )
    async def _request_once(
        self,
        base_url: str,
        endpoint: str,
        params: Dict[str, Any],
        method: str = "GET",
        verify: bool = True,
    ) -> Dict[str, Any]:
        async with self._semaphore:
            client = await self._get_client(base_url, verify=verify)
            if method.upper() == "POST":
                resp = await client.post(endpoint, json=self._drop_empty(params))
            else:
                resp = await client.get(endpoint, params=self._drop_empty(params))
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return {"data": data}
            return data

    async def _request(
        self, endpoint: str, params: Dict[str, Any], method: str = "GET"
    ) -> Dict[str, Any]:
        last_error: Exception | None = None
        for base_url in self._base_urls:
            try:
                data = await self._request_once(base_url, endpoint, params, method=method)
                if self._api_error(data):
                    raise TikHubAPIError(str(data.get("message") or data.get("error")))
                return data
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                if status in {401, 403, 404, 422}:
                    logger.warning(
                        f"TikHub REST {base_url}{endpoint} HTTP {status}: {exc.response.text[:200]}"
                    )
                    if status in {401, 403, 422}:
                        break
                else:
                    logger.warning(f"TikHub REST {base_url}{endpoint} HTTP {status}")
            except Exception as exc:
                last_error = exc
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    logger.warning(
                        f"TikHub REST {base_url}{endpoint} SSL 校验失败，使用 verify=False 重试"
                    )
                    try:
                        data = await self._request_once(
                            base_url, endpoint, params, method=method, verify=False
                        )
                        if self._api_error(data):
                            raise TikHubAPIError(
                                str(data.get("message") or data.get("error"))
                            )
                        return data
                    except Exception as insecure_exc:
                        last_error = insecure_exc
                        logger.warning(
                            f"TikHub REST {base_url}{endpoint} insecure retry failed: {insecure_exc}"
                        )
                        continue
                logger.warning(f"TikHub REST {base_url}{endpoint} failed: {exc}")
        if last_error:
            raise last_error
        return {}

    @staticmethod
    def _api_error(data: Dict[str, Any]) -> bool:
        code = data.get("code")
        if code in (None, 0, 200, "0", "200", "success", "ok"):
            return False
        return bool(data.get("error") or data.get("message"))

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        if not self.token:
            logger.error("TikHubClient: 未配置 TIKHUB_API_TOKEN 或 TIKHUB_API_KEY")
            return []

        platform = self._resolve_platform(source)
        if not platform:
            logger.warning(f"TikHubClient: {source.name} 无法识别平台，跳过")
            return []

        endpoint, params, method = self._build_request(source, platform)
        logger.debug(
            f"TikHub REST: [{platform}] {method} {endpoint} params={self._safe_params(params)}"
        )

        try:
            data = await self._request(endpoint, params, method=method)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                logger.error(
                    f"TikHubClient: {source.name} 认证或权限失败 HTTP {status}，请检查 token、余额和域名"
                )
                return []
            if status in {404, 422}:
                logger.error(
                    f"TikHubClient: {source.name} endpoint/参数不匹配 HTTP {status}: {endpoint}"
                )
                return []
            raise
        items = self._parse_items(data, source, competitor_name, platform)
        if not items:
            logger.warning(
                f"TikHub REST: {source.name} 返回 0 条可解析内容；"
                f"response_shape={self._response_shape(data)}"
            )
        logger.debug(f"TikHub REST: {source.name} -> {len(items)} items")
        return items[: settings.tikhub.max_results]

    def _build_request(
        self, source: SourceConfig, platform: str
    ) -> tuple[str, Dict[str, Any], str]:
        config = SEARCH_CONFIGS.get(platform, {})
        endpoint = source.tikhub_endpoint or config.get("endpoint", "")
        method = config.get("method", "GET")
        params: dict[str, Any] = dict(config.get("defaults") or {})
        params.update(source.tikhub_params or {})

        keyword = self._keyword_from_params(params)
        keyword_param = config.get("keyword_param", "keyword")
        if keyword and keyword_param not in params:
            params[keyword_param] = keyword
        if keyword_param != "keyword" and keyword_param in params and "keyword" in params:
            params.pop("keyword", None)

        if endpoint.endswith("/fetch_realtime_search") and "keyword" in params:
            params.setdefault("query", params.pop("keyword"))
        if endpoint.endswith("/fetch_dynamic_search") and "keyword" in params:
            params.setdefault("query", params.pop("keyword"))
        if endpoint.endswith("/get_general_search") and "keyword" in params:
            params.setdefault("search_query", params.pop("keyword"))

        return endpoint, params, method

    def _parse_items(
        self,
        data: Dict[str, Any],
        source: SourceConfig,
        competitor_name: str,
        platform: str,
    ) -> List[RawItem]:
        records = list(self._extract_records(data))
        items: List[RawItem] = []
        seen = set()

        for record in records:
            item = self._record_to_item(record, source, competitor_name, platform)
            if not item:
                continue
            key = item.url or item.raw_metadata.get("original_id") or item.title
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)

        return items

    def _extract_records(self, payload: Any) -> Iterable[dict[str, Any]]:
        if isinstance(payload, list):
            for item in payload:
                yield from self._extract_records(item)
            return

        if not isinstance(payload, dict):
            return

        normalized = self._unwrap_common_record(payload)
        if normalized is not payload:
            yield from self._extract_records(normalized)
            return

        if self._looks_like_record(payload):
            yield payload
            return

        for key in CONTAINER_KEYS:
            if key in payload:
                yield from self._extract_records(payload[key])

        # Several TikHub endpoints preserve platform-specific renderer/object
        # wrappers. Walk remaining children so a successful response is not
        # mistaken for an empty search merely because its wrapper name changed.
        for key, value in payload.items():
            if key not in CONTAINER_KEYS and isinstance(value, (dict, list)):
                yield from self._extract_records(value)

    @staticmethod
    def _unwrap_common_record(payload: dict[str, Any]) -> Any:
        for key in ("tweet", "legacy", "note_card", "note", "video", "post", "aweme_info"):
            value = payload.get(key)
            if isinstance(value, dict):
                merged = dict(value)
                for outer_key in ("id", "entryId", "sortIndex"):
                    if outer_key in payload and outer_key not in merged:
                        merged[outer_key] = payload[outer_key]
                return merged

        content = payload.get("content")
        if isinstance(content, dict):
            item = content.get("itemContent") or content.get("tweetResult")
            if isinstance(item, dict):
                return item

        result = payload.get("result")
        if isinstance(result, dict) and "legacy" in result:
            legacy = result.get("legacy") or {}
            if isinstance(legacy, dict):
                user = (
                    result.get("core", {})
                    .get("user_results", {})
                    .get("result", {})
                    .get("legacy", {})
                )
                if isinstance(user, dict):
                    legacy = dict(legacy)
                    legacy["user"] = user
                return legacy

        return payload

    @staticmethod
    def _looks_like_record(payload: dict[str, Any]) -> bool:
        keys = set(payload.keys())
        if not keys & RECORD_HINT_KEYS:
            return False
        return bool(
            keys
            & {
                "title",
                "desc",
                "description",
                "text",
                "full_text",
                "content",
                "caption",
                "url",
                "share_url",
                "permalink",
            }
        )

    def _record_to_item(
        self,
        record: dict[str, Any],
        source: SourceConfig,
        competitor_name: str,
        platform: str,
    ) -> Optional[RawItem]:
        title = self._first_text(
            record,
            "title",
            "full_text",
            "text",
            "desc",
            "description",
            "caption",
            "name",
        )
        content = self._first_text(
            record,
            "content",
            "full_text",
            "text",
            "desc",
            "description",
            "caption",
            "summary",
            "excerpt",
        )
        author = self._author(record)
        url = self._url(record, platform)
        original_id = self._original_id(record, platform)
        published_at = self._published_at(record)

        if not title and not content:
            return None

        return RawItem(
            competitor_name=competitor_name,
            source_name=source.name,
            source_type=f"tikhub:{platform}",
            url=url,
            title=title[:200],
            content_snippet=(content or title)[:800],
            author=author,
            published_at=published_at,
            raw_metadata={
                "platform": platform,
                "original_id": original_id,
                "search_keyword": self._keyword_from_params(source.tikhub_params),
                "via": "tikhub_rest",
            },
        )

    @staticmethod
    def _first_text(record: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = record.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                value = (
                    value.get("simpleText")
                    or value.get("text")
                    or value.get("content")
                    or " ".join(
                        str(run.get("text", ""))
                        for run in value.get("runs", [])
                        if isinstance(run, dict)
                    )
                )
            elif isinstance(value, list):
                value = " ".join(
                    str(part.get("text", "") if isinstance(part, dict) else part)
                    for part in value
                )
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if text:
                return text
        return ""

    def _author(self, record: dict[str, Any]) -> str:
        for key in ("author", "user", "owner", "creator", "user_info"):
            value = record.get(key)
            if isinstance(value, dict):
                return self._first_text(
                    value,
                    "name",
                    "nickname",
                    "screen_name",
                    "username",
                    "unique_id",
                    "login",
                    "user_id",
                    "id",
                )
            if value:
                return str(value)
        return self._first_text(record, "author_name", "nickname", "username", "screen_name")

    def _url(self, record: dict[str, Any], platform: str) -> str:
        direct = self._first_text(
            record,
            "url",
            "share_url",
            "link",
            "permalink",
            "jump_url",
            "video_url",
            "arcurl",
            "uri",
            "display_url",
        )
        if direct:
            if platform == "reddit" and direct.startswith("/"):
                return f"https://www.reddit.com{direct}"
            return direct

        original_id = self._original_id(record, platform)
        if not original_id:
            return ""
        if platform == "twitter":
            user = self._author(record)
            return f"https://x.com/{user}/status/{original_id}" if user else f"https://x.com/i/status/{original_id}"
        if platform == "youtube":
            return f"https://www.youtube.com/watch?v={original_id}"
        if platform == "instagram":
            return f"https://www.instagram.com/p/{original_id}/"
        if platform == "reddit":
            return f"https://www.reddit.com/comments/{original_id}/"
        if platform == "xiaohongshu":
            return f"https://www.xiaohongshu.com/explore/{original_id}"
        if platform == "bilibili":
            return f"https://www.bilibili.com/video/{original_id}"
        if platform == "weibo":
            return f"https://weibo.com/detail/{original_id}"
        if platform == "douyin":
            return f"https://www.douyin.com/video/{original_id}"
        return ""

    @staticmethod
    def _original_id(record: dict[str, Any], platform: str) -> str:
        keys_by_platform = {
            "twitter": ("tweet_id", "rest_id", "id_str", "id"),
            "youtube": ("video_id", "videoId", "id"),
            "instagram": ("shortcode", "code", "pk", "id"),
            "reddit": ("post_id", "id", "name"),
            "xiaohongshu": ("note_id", "id"),
            "bilibili": ("bvid", "bv_id", "id"),
            "weibo": ("mid", "mblogid", "id"),
            "douyin": ("aweme_id", "video_id", "id"),
        }
        for key in keys_by_platform.get(platform, ("id",)):
            value = record.get(key)
            if value:
                return str(value)
        return ""

    def _published_at(self, record: dict[str, Any]) -> Optional[datetime]:
        value = self._first_text(
            record,
            "created_at",
            "create_time",
            "publish_time",
            "published_at",
            "pubdate",
            "date",
            "time",
            "timestamp",
        )
        if not value:
            return None

        if value.isdigit():
            timestamp = int(value)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OSError, ValueError):
                return None

        value = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _keyword_from_params(params: dict[str, Any]) -> str:
        for key in ("keyword", "query", "q", "search_query", "word", "keywords"):
            value = params.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _drop_empty(params: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if value not in (None, "")}

    @staticmethod
    def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
        return {
            key: ("***" if "token" in key.lower() or "auth" in key.lower() else value)
            for key, value in params.items()
        }

    @staticmethod
    def _response_shape(payload: Any, depth: int = 0) -> Any:
        if depth >= 3:
            return type(payload).__name__
        if isinstance(payload, dict):
            return {
                str(key): TikHubClient._response_shape(value, depth + 1)
                for key, value in list(payload.items())[:12]
            }
        if isinstance(payload, list):
            return [
                TikHubClient._response_shape(payload[0], depth + 1)
            ] if payload else []
        return type(payload).__name__

    @staticmethod
    def _resolve_platform(source: SourceConfig) -> str:
        configured = getattr(source, "tikhub_platform", "")
        if configured:
            return PLATFORM_ALIASES.get(configured.lower(), configured.lower())

        endpoint = source.tikhub_endpoint or ""
        if endpoint.startswith("/api/v1/"):
            parts = endpoint.split("/")
            if len(parts) > 3:
                return PLATFORM_ALIASES.get(parts[3].lower(), parts[3].lower())

        name = source.name.lower()
        for alias, platform in PLATFORM_ALIASES.items():
            if alias in name:
                return platform
        return ""

    async def close(self):
        for client in self._clients.values():
            if not client.is_closed:
                await client.aclose()
        self._clients.clear()


class TikHubAPIError(RuntimeError):
    pass
