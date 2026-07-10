"""
TikHub MCP collector.

Instead of hardcoding TikHub REST endpoints, this collector treats each TikHub
MCP server as a tool provider. For every social source it lists the server tools,
selects a search-like tool that fits the source params, calls it, and normalizes
the returned payload into RawItem records.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from loguru import logger

from collectors.base import BaseCollector
from collectors.mcp_stdio import MCPError, MCPStdioClient
from config.competitors import SourceConfig
from config.settings import settings
from models.data_models import RawItem


TIKHUB_MCP_PLATFORMS = {
    "tiktok",
    "douyin",
    "instagram",
    "xiaohongshu",
    "weibo",
    "others",
    "bilibili",
    "youtube",
    "kuaishou",
    "zhihu",
    "linkedin",
    "reddit",
    "tikhub",
    "wechat",
    "twitter",
    "threads",
}

SEARCH_ALIASES = (
    "keyword",
    "keywords",
    "query",
    "q",
    "word",
    "search",
    "search_term",
    "search_query",
)

COUNT_ALIASES = ("count", "limit", "size", "page_size", "max_results")

CONTAINER_KEYS = (
    "data",
    "items",
    "item_list",
    "list",
    "results",
    "records",
    "notes",
    "tweets",
    "videos",
    "aweme_list",
    "feeds",
    "cards",
    "statuses",
    "response",
    "result",
)

RECORD_HINT_KEYS = {
    "id",
    "mid",
    "note_id",
    "aweme_id",
    "bvid",
    "video_id",
    "tweet_id",
    "shortcode",
    "url",
    "share_url",
    "title",
    "desc",
    "description",
    "text",
    "content",
    "author",
    "user",
    "nickname",
    "created_at",
    "publish_time",
}


class TikHubMCPCollector(BaseCollector):
    """Agentic collector that selects and calls TikHub MCP tools per source."""

    def __init__(self):
        self.token = settings.tikhub.token
        self.base_url = settings.tikhub.mcp_base_url.rstrip("/")
        self.command = settings.tikhub.mcp_command
        self.remote_package = settings.tikhub.mcp_remote_package
        self.timeout = settings.tikhub.mcp_timeout
        self.max_results = settings.tikhub.mcp_max_results
        self._clients: dict[str, MCPStdioClient] = {}
        self._tool_cache: dict[str, list[dict[str, Any]]] = {}

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        if not self.token:
            logger.error("TikHubMCPCollector: 未配置 TIKHUB_API_TOKEN")
            return []

        platform = self._resolve_platform(source)
        if not platform:
            logger.warning(f"TikHubMCPCollector: {source.name} 无法识别平台，跳过")
            return []

        params = dict(source.tikhub_params or {})
        keyword = self._keyword_from_params(params)
        if keyword:
            params.setdefault("keyword", keyword)

        try:
            tool = await self._select_tool(platform, source, params)
            if not tool:
                logger.warning(
                    f"TikHubMCPCollector: {platform} 未找到适合 {source.name} 的 MCP 搜索工具"
                )
                return []

            arguments = self._build_arguments(tool, params)
            logger.debug(
                f"TikHub MCP: [{platform}] call {tool.get('name')} args={self._safe_args(arguments)}"
            )
            result = await self._client(platform).call_tool(tool["name"], arguments)
            items = self._parse_result(result, source, competitor_name, platform)
            logger.debug(f"TikHub MCP: {source.name} -> {len(items)} items")
            return items[: self.max_results]
        except Exception as exc:
            logger.error(f"TikHubMCPCollector: {source.name} 采集失败: {exc}")
            return []

    def _client(self, platform: str) -> MCPStdioClient:
        if platform not in self._clients:
            url = f"{self.base_url}/{platform}/mcp"
            args = [
                "-y",
                self.remote_package,
                url,
                "--header",
                f"Authorization: Bearer {self.token}",
            ]
            self._clients[platform] = MCPStdioClient(
                name=f"tikhub-{platform}",
                command=self.command,
                args=args,
                timeout=self.timeout,
                env=self._node_env(),
            )
        return self._clients[platform]

    @staticmethod
    def _node_env() -> dict[str, str]:
        if settings.tikhub.mcp_insecure_tls:
            return {"NODE_TLS_REJECT_UNAUTHORIZED": "0"}
        return {}

    async def _tools(self, platform: str) -> list[dict[str, Any]]:
        if platform not in self._tool_cache:
            self._tool_cache[platform] = await self._client(platform).list_tools()
            logger.debug(
                f"TikHub MCP: [{platform}] discovered {len(self._tool_cache[platform])} tools"
            )
        return self._tool_cache[platform]

    async def _select_tool(
        self,
        platform: str,
        source: SourceConfig,
        params: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        tools = await self._tools(platform)
        preferred = getattr(source, "tikhub_tool", "")
        if preferred:
            for tool in tools:
                if tool.get("name") == preferred:
                    return tool
            raise MCPError(f"configured tool not found: {preferred}")

        ranked = sorted(
            tools,
            key=lambda tool: self._tool_score(tool, params),
            reverse=True,
        )
        if not ranked:
            return None

        best = ranked[0]
        return best if self._tool_score(best, params) > 0 else None

    def _tool_score(self, tool: dict[str, Any], params: dict[str, Any]) -> int:
        name = str(tool.get("name", "")).lower()
        description = str(tool.get("description", "")).lower()
        schema_text = json.dumps(tool.get("inputSchema") or {}, ensure_ascii=False).lower()
        haystack = f"{name} {description} {schema_text}"

        score = 0
        if any(word in haystack for word in ("search", "keyword", "query", "检索", "搜索")):
            score += 30
        if any(alias in schema_text for alias in SEARCH_ALIASES):
            score += 20
        if "keyword" in params or any(alias in params for alias in SEARCH_ALIASES):
            score += 10
        if any(word in haystack for word in ("detail", "comment", "comments", "reply")):
            score -= 15
        if any(word in haystack for word in ("user", "profile", "creator")):
            score -= 8

        properties = self._schema_properties(tool)
        for key in params:
            if key in properties:
                score += 8

        return score

    def _build_arguments(
        self, tool: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        properties = self._schema_properties(tool)
        required = set((tool.get("inputSchema") or {}).get("required") or [])
        if not properties:
            return params

        arguments: dict[str, Any] = {}
        for key, value in params.items():
            if key in properties:
                arguments[key] = value

        keyword = self._keyword_from_params(params)
        if keyword:
            for alias in SEARCH_ALIASES:
                if alias in properties and alias not in arguments:
                    arguments[alias] = keyword
                    break

        for key in required:
            if key in arguments:
                continue
            lowered = key.lower()
            if keyword and any(alias in lowered for alias in SEARCH_ALIASES):
                arguments[key] = keyword
            elif any(alias in lowered for alias in COUNT_ALIASES):
                arguments[key] = self.max_results
            elif "page" in lowered:
                arguments[key] = 1

        for alias in COUNT_ALIASES:
            if alias in properties and alias not in arguments:
                arguments[alias] = self.max_results
                break

        return arguments

    @staticmethod
    def _schema_properties(tool: dict[str, Any]) -> dict[str, Any]:
        schema = tool.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        return properties if isinstance(properties, dict) else {}

    def _parse_result(
        self,
        result: dict[str, Any],
        source: SourceConfig,
        competitor_name: str,
        platform: str,
    ) -> List[RawItem]:
        records: list[dict[str, Any]] = []
        for payload in self._result_payloads(result):
            records.extend(self._extract_records(payload))

        items: List[RawItem] = []
        seen = set()
        for record in records:
            item = self._record_to_item(record, source, competitor_name, platform)
            if not item:
                continue
            key = item.url or item.raw_metadata.get("original_id") or item.title
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
        return items

    def _result_payloads(self, result: dict[str, Any]) -> Iterable[Any]:
        if "structuredContent" in result:
            yield result["structuredContent"]

        if "content" not in result:
            yield result
            return

        for part in result.get("content") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                parsed = self._parse_json_text(text)
                yield parsed if parsed is not None else {"text": text}
            resource = part.get("resource")
            if isinstance(resource, dict):
                resource_text = resource.get("text")
                if isinstance(resource_text, str):
                    parsed = self._parse_json_text(resource_text)
                    yield parsed if parsed is not None else {"text": resource_text}

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            records: list[dict[str, Any]] = []
            for item in payload:
                records.extend(self._extract_records(item))
            return records

        if not isinstance(payload, dict):
            return []

        if self._looks_like_record(payload):
            return [payload]

        records: list[dict[str, Any]] = []
        for key in CONTAINER_KEYS:
            if key in payload:
                records.extend(self._extract_records(payload[key]))
        return records

    @staticmethod
    def _looks_like_record(payload: dict[str, Any]) -> bool:
        keys = set(payload.keys())
        if keys & RECORD_HINT_KEYS and (
            keys & {"title", "desc", "description", "text", "content", "url", "share_url"}
        ):
            return True
        return False

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
            "desc",
            "description",
            "text",
            "content",
            "name",
        )
        content = self._first_text(
            record,
            "content",
            "description",
            "text",
            "desc",
            "summary",
            "caption",
        )
        author = self._author(record)
        url = self._url(record, platform)
        published_at = self._published_at(record)
        original_id = self._original_id(record, platform)

        if not title and not content:
            return None

        return RawItem(
            competitor_name=competitor_name,
            source_name=source.name,
            source_type=f"tikhub_mcp:{platform}",
            url=url,
            title=title[:200],
            content_snippet=(content or title)[:800],
            author=author,
            published_at=published_at,
            raw_metadata={
                "platform": platform,
                "original_id": original_id,
                "via": "tikhub_mcp",
            },
        )

    @staticmethod
    def _first_text(record: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = record.get(key)
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _author(self, record: dict[str, Any]) -> str:
        for key in ("author", "user", "owner", "creator"):
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
                )
            if value:
                return str(value)
        return self._first_text(record, "author_name", "nickname", "username")

    def _url(self, record: dict[str, Any], platform: str) -> str:
        direct = self._first_text(record, "url", "share_url", "link", "permalink")
        if direct:
            if platform == "reddit" and direct.startswith("/"):
                return f"https://www.reddit.com{direct}"
            return direct

        original_id = self._original_id(record, platform)
        if not original_id:
            return ""
        if platform == "twitter":
            return f"https://x.com/i/status/{original_id}"
        if platform == "youtube":
            return f"https://www.youtube.com/watch?v={original_id}"
        if platform == "instagram":
            return f"https://www.instagram.com/p/{original_id}/"
        if platform == "xiaohongshu":
            return f"https://www.xiaohongshu.com/explore/{original_id}"
        if platform == "bilibili":
            return f"https://www.bilibili.com/video/{original_id}"
        if platform == "weibo":
            return f"https://weibo.com/{original_id}"
        if platform == "douyin":
            return f"https://www.douyin.com/video/{original_id}"
        return ""

    @staticmethod
    def _original_id(record: dict[str, Any], platform: str) -> str:
        keys_by_platform = {
            "twitter": ("tweet_id", "rest_id", "id_str", "id"),
            "youtube": ("video_id", "id"),
            "instagram": ("shortcode", "code", "id"),
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
            "publish_time",
            "published_at",
            "date",
            "time",
            "timestamp",
        )
        if not value:
            return None

        if value.isdigit():
            timestamp = int(value)
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OSError, ValueError):
                return None

        value = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_json_text(text: str) -> Any:
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _keyword_from_params(params: dict[str, Any]) -> str:
        for key in SEARCH_ALIASES:
            value = params.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _safe_args(arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            key: ("***" if "token" in key.lower() or "auth" in key.lower() else value)
            for key, value in arguments.items()
        }

    @staticmethod
    def _resolve_platform(source: SourceConfig) -> str:
        configured = getattr(source, "tikhub_platform", "")
        if configured:
            return configured.lower()

        endpoint = source.tikhub_endpoint or ""
        if endpoint.startswith("/api/v1/"):
            platform = endpoint.split("/")[3].lower()
            return platform

        name = source.name.lower()
        for platform in TIKHUB_MCP_PLATFORMS:
            if platform in name:
                return platform
        if name.startswith("x-") or "twitter" in name:
            return "twitter"
        if "小红书" in source.name:
            return "xiaohongshu"
        if "微博" in source.name:
            return "weibo"
        if "知乎" in source.name:
            return "zhihu"
        if "b站" in source.name.lower() or "bilibili" in name:
            return "bilibili"
        return ""

    async def close(self):
        for client in self._clients.values():
            await client.close()
        self._clients.clear()
