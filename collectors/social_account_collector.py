"""
SocialAccountCollector - track official and key-person social accounts.

Accounts are read from files/social_media_accounts.xlsx. The collector uses
TikHub REST endpoints directly, preferring platform account/timeline APIs and
falling back to search when the sheet only provides display names.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional

from loguru import logger

from collectors.tikhub_client import TikHubClient
from config.competitors import SourceConfig
from config.settings import PROJECT_ROOT, settings
from models.data_models import RawItem


XLSX_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

ACCOUNT_FILE = PROJECT_ROOT / "files" / "social_media_accounts.xlsx"
EMPTY_MARKERS = {"", "-", "—", "–", "无", "none", "n/a", "na", "null"}

PLATFORM_ALIASES = {
    "x": "twitter",
    "x (twitter)": "twitter",
    "twitter": "twitter",
    "youtube": "youtube",
    "reddit": "reddit",
    "instagram": "instagram",
    "threads": "threads",
    "tiktok": "tiktok",
    "tik tok": "tiktok",
    "微博": "weibo",
    "weibo": "weibo",
    "小红书": "xiaohongshu",
    "xiaohongshu": "xiaohongshu",
    "知乎": "zhihu",
    "zhihu": "zhihu",
    "bilibili": "bilibili",
    "b站": "bilibili",
    "抖音": "douyin",
    "douyin": "douyin",
}


@dataclass
class AccountTarget:
    competitor_name: str
    platform: str
    account: str
    role: str
    owner: str = ""
    url: str = ""
    note: str = ""


class SocialAccountCollector(TikHubClient):
    """Collect posts from official accounts and key-person accounts."""

    def __init__(self, account_file: Path | None = None):
        super().__init__()
        configured = getattr(settings, "social_accounts_file", None)
        self.account_file = account_file or Path(configured or ACCOUNT_FILE)

    async def collect(
        self, source: SourceConfig, competitor_name: str = "", **kwargs
    ) -> List[RawItem]:
        if not self.token:
            logger.error("SocialAccountCollector: 未配置 TIKHUB_API_TOKEN 或 TIKHUB_API_KEY")
            return []

        targets = self._targets_for_competitor(competitor_name)
        if not targets:
            logger.debug(f"SocialAccountCollector: {competitor_name} 未找到账号配置")
            return []

        max_items = int(source.tikhub_params.get("max_items", settings.tikhub.max_results))
        items: List[RawItem] = []
        for target in targets:
            try:
                target_items = await self._collect_target(target, max_items=max_items)
                items.extend(target_items)
            except Exception as exc:
                logger.warning(
                    f"SocialAccountCollector: {competitor_name} {target.platform} "
                    f"{target.account} 采集失败: {exc}"
                )

        logger.debug(
            f"SocialAccountCollector: {competitor_name} {len(targets)} accounts -> {len(items)} items"
        )
        return items

    async def _collect_target(self, target: AccountTarget, max_items: int) -> List[RawItem]:
        account = self._clean_account_value(target.account)
        if not account:
            return []

        platform = target.platform
        endpoint = ""
        params: dict[str, Any] = {}
        method = "GET"

        if platform == "twitter":
            endpoint = "/api/v1/twitter/web/fetch_user_post_tweet"
            params = {"screen_name": self._strip_at(account)}
        elif platform == "instagram":
            endpoint = "/api/v1/instagram/v2/fetch_user_posts"
            params = {"username": self._strip_at(account)}
        elif platform == "reddit":
            endpoint, params = self._reddit_request(account)
        elif platform == "threads":
            user_id = await self._threads_user_id(account)
            if user_id:
                endpoint = "/api/v1/threads/web/fetch_user_posts"
                params = {"user_id": user_id}
            else:
                endpoint = "/api/v1/threads/web/search_recent"
                params = {"query": self._strip_at(account)}
        elif platform == "tiktok":
            sec_uid = await self._tiktok_sec_uid(account)
            if sec_uid:
                endpoint = "/api/v1/tiktok/web/fetch_user_post"
                params = {"secUid": sec_uid, "count": max_items}
            else:
                endpoint = "/api/v1/tiktok/web/fetch_user_profile"
                params = {"uniqueId": self._strip_at(account)}
        elif platform == "youtube":
            channel_id = await self._youtube_channel_id(account, target.url)
            if channel_id:
                endpoint = "/api/v1/youtube/web_v2/get_channel_videos"
                params = {"channel_id": channel_id, "need_format": True}
            else:
                endpoint = "/api/v1/youtube/web_v2/get_general_search_v2"
                params = {"keyword": self._search_label(account, target)}
        elif platform == "bilibili":
            uid = self._first_number(account) or self._first_number(target.url)
            if uid:
                endpoint = "/api/v1/bilibili/web/fetch_user_dynamic"
                params = {"uid": uid}
            else:
                endpoint = "/api/v1/bilibili/web/fetch_general_search"
                params = {"keyword": self._search_label(account, target), "order": "pubdate", "page": 1}
        elif platform == "weibo":
            uid = self._first_number(account) or self._first_number(target.url)
            if uid:
                endpoint = "/api/v1/weibo/web_v2/fetch_user_posts"
                params = {"uid": uid, "page": 1}
            else:
                endpoint = "/api/v1/weibo/web_v2/fetch_realtime_search"
                params = {"query": self._search_label(account, target), "page": 1}
        elif platform == "xiaohongshu":
            user_id = self._extract_user_id(account, target.url)
            if user_id:
                endpoint = "/api/v1/xiaohongshu/app_v2/get_user_posted_notes"
                params = {"user_id": user_id}
            else:
                endpoint = "/api/v1/xiaohongshu/app_v2/search_notes"
                params = {"keyword": self._search_label(account, target), "page": 1}
        elif platform == "zhihu":
            token = self._zhihu_token(account, target.url)
            if token:
                endpoint = "/api/v1/zhihu/web/fetch_user_articles"
                params = {"user_url_token": token, "limit": "20", "sort_type": "created"}
            else:
                endpoint = "/api/v1/zhihu/web/fetch_article_search_v3"
                params = {"keyword": self._search_label(account, target), "offset": "0", "limit": "20"}
        elif platform == "douyin":
            sec_user_id = self._sec_user_id(account, target.url)
            if sec_user_id:
                endpoint = "/api/v1/douyin/web/fetch_user_post_videos"
                params = {"sec_user_id": sec_user_id, "count": max_items}
            else:
                endpoint = "/api/v1/douyin/search/fetch_general_search_v2"
                params = {"keyword": self._search_label(account, target), "cursor": 0}
                method = "POST"
        else:
            logger.debug(f"SocialAccountCollector: unsupported platform {platform}")
            return []

        data = await self._request(endpoint, params, method=method)
        source = SourceConfig(
            name=f"{target.role}-{self._display_platform(platform)}-{target.owner or account}",
            type="social_account",
            tikhub_platform=platform,
        )
        items = self._parse_items(data, source, target.competitor_name, platform)
        for item in items:
            item.source_type = f"social_account:{platform}"
            item.raw_metadata.update(
                {
                    "via": "social_account_tikhub_rest",
                    "account_platform": platform,
                    "account_name": account,
                    "account_role": target.role,
                    "account_owner": target.owner,
                    "account_note": target.note,
                    "account_url": target.url,
                    "endpoint": endpoint,
                }
            )
            if target.owner and target.owner not in item.title:
                item.title = f"{target.owner}: {item.title}"[:200]

        return items[:max_items]

    async def _threads_user_id(self, account: str) -> str:
        try:
            data = await self._request(
                "/api/v1/threads/web/fetch_user_info",
                {"username": self._strip_at(account)},
            )
            return str(self._find_first(data, ("user_id", "id", "pk", "pk_id")) or "")
        except Exception as exc:
            logger.debug(f"SocialAccountCollector: Threads user lookup failed: {account}: {exc}")
            return ""

    async def _tiktok_sec_uid(self, account: str) -> str:
        try:
            data = await self._request(
                "/api/v1/tiktok/web/fetch_user_profile",
                {"uniqueId": self._strip_at(account)},
            )
            return str(self._find_first(data, ("secUid", "sec_uid", "sec_user_id")) or "")
        except Exception as exc:
            logger.debug(f"SocialAccountCollector: TikTok user lookup failed: {account}: {exc}")
            return ""

    async def _youtube_channel_id(self, account: str, url: str = "") -> str:
        channel_id = self._youtube_id_from_text(account) or self._youtube_id_from_text(url)
        if channel_id:
            return channel_id

        channel_url = self._youtube_channel_url(account, url)
        if not channel_url:
            return ""

        try:
            data = await self._request(
                "/api/v1/youtube/web_v2/get_channel_id",
                {"channel_url": channel_url},
            )
            return str(self._find_first(data, ("channel_id", "channelId", "id")) or "")
        except Exception as exc:
            logger.debug(f"SocialAccountCollector: YouTube channel lookup failed: {account}: {exc}")
            return ""

    @staticmethod
    def _reddit_request(account: str) -> tuple[str, dict[str, Any]]:
        value = account.strip()
        lowered = value.lower()
        if lowered.startswith("r/"):
            return (
                "/api/v1/reddit/app/fetch_subreddit_feed",
                {"subreddit_name": value.split("/", 1)[1], "sort": "new", "need_format": True},
            )
        if lowered.startswith("u/"):
            return (
                "/api/v1/reddit/app/fetch_user_posts",
                {"username": value.split("/", 1)[1], "sort": "new", "need_format": True},
            )
        return (
            "/api/v1/reddit/app/fetch_user_posts",
            {"username": value.lstrip("@"), "sort": "new", "need_format": True},
        )

    def _targets_for_competitor(self, competitor_name: str) -> List[AccountTarget]:
        if not self.account_file.exists():
            logger.warning(f"SocialAccountCollector: account file not found: {self.account_file}")
            return []

        try:
            sheets = self._read_xlsx(self.account_file)
        except Exception as exc:
            logger.error(f"SocialAccountCollector: 读取账号表失败: {exc}")
            return []

        targets: List[AccountTarget] = []
        targets.extend(self._official_targets(sheets, competitor_name))
        targets.extend(self._key_person_targets(sheets, competitor_name))
        return targets

    def _official_targets(self, sheets: dict[str, list[list[str]]], competitor_name: str) -> List[AccountTarget]:
        rows = sheets.get("竞品官方账号总览") or []
        if len(rows) < 2:
            return []
        headers = rows[0]
        targets: List[AccountTarget] = []
        for row in rows[1:]:
            product = self._cell(row, 0)
            if not self._same_competitor(product, competitor_name):
                continue
            for index, header in enumerate(headers[1:], start=1):
                platform = self._platform(header)
                if not platform:
                    continue
                for account in self._split_accounts(self._cell(row, index), platform):
                    targets.append(
                        AccountTarget(
                            competitor_name=competitor_name,
                            platform=platform,
                            account=account,
                            role="官方账号",
                            owner=product or competitor_name,
                        )
                    )
        return targets

    def _key_person_targets(self, sheets: dict[str, list[list[str]]], competitor_name: str) -> List[AccountTarget]:
        rows = sheets.get("关键人物账号") or []
        if len(rows) < 2:
            return []
        targets: List[AccountTarget] = []
        for row in rows[1:]:
            person = self._cell(row, 0)
            product = self._cell(row, 1)
            platform = self._platform(self._cell(row, 2))
            handle = self._cell(row, 3)
            url = self._cell(row, 4)
            note = self._cell(row, 5)
            if not platform or not handle:
                continue
            if not self._same_competitor(product, competitor_name):
                continue
            for account in self._split_accounts(handle or url, platform):
                targets.append(
                    AccountTarget(
                        competitor_name=competitor_name,
                        platform=platform,
                        account=account,
                        role="关键人物",
                        owner=person,
                        url=url,
                        note=note,
                    )
                )
        return targets

    @classmethod
    def _read_xlsx(cls, path: Path) -> dict[str, list[list[str]]]:
        with zipfile.ZipFile(path) as archive:
            shared_strings = cls._shared_strings(archive)
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            rel_map = {rel.attrib["Id"]: rel.attrib["Target"].lstrip("/") for rel in rels}
            sheets: dict[str, list[list[str]]] = {}
            for sheet in workbook.findall("a:sheets/a:sheet", XLSX_NS):
                name = sheet.attrib["name"]
                rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                target = rel_map[rel_id]
                sheet_path = target if target.startswith("xl/") else f"xl/{target}"
                sheets[name] = cls._sheet_rows(archive, sheet_path, shared_strings)
            return sheets

    @staticmethod
    def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        return [
            "".join(text.text or "" for text in item.findall(".//a:t", XLSX_NS))
            for item in root.findall("a:si", XLSX_NS)
        ]

    @classmethod
    def _sheet_rows(
        cls, archive: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]
    ) -> list[list[str]]:
        root = ET.fromstring(archive.read(sheet_path))
        rows: list[list[str]] = []
        for row in root.findall("a:sheetData/a:row", XLSX_NS):
            cells: dict[int, str] = {}
            max_index = -1
            for cell in row.findall("a:c", XLSX_NS):
                index = cls._column_index(cell.attrib.get("r", ""))
                max_index = max(max_index, index)
                cells[index] = cls._cell_value(cell, shared_strings)
            rows.append([cells.get(index, "") for index in range(max_index + 1)])
        return rows

    @staticmethod
    def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
        value = cell.find("a:v", XLSX_NS)
        cell_type = cell.attrib.get("t")
        if cell_type == "s" and value is not None:
            return shared_strings[int(value.text or "0")]
        if cell_type == "inlineStr":
            return "".join(text.text or "" for text in cell.findall(".//a:t", XLSX_NS)).strip()
        return (value.text or "").strip() if value is not None else ""

    @staticmethod
    def _column_index(cell_ref: str) -> int:
        match = re.match(r"([A-Z]+)", cell_ref or "")
        if not match:
            return 0
        index = 0
        for char in match.group(1):
            index = index * 26 + ord(char) - 64
        return index - 1

    @staticmethod
    def _cell(row: list[str], index: int) -> str:
        return row[index].strip() if index < len(row) and row[index] else ""

    @classmethod
    def _split_accounts(cls, value: str, platform: str) -> list[str]:
        value = (value or "").strip()
        if cls._is_empty(value):
            return []

        if platform in {"twitter", "instagram", "threads", "tiktok"}:
            handles = re.findall(r"@[A-Za-z0-9_.-]+", value)
            if handles:
                return [handle.strip() for handle in handles]

        if platform == "reddit":
            names = re.findall(r"\b[ru]/[A-Za-z0-9_][A-Za-z0-9_-]*", value, flags=re.IGNORECASE)
            if names:
                return names

        pieces = re.split(r"\s*(?:/|,|，|;|；|\n)\s*", value)
        cleaned = []
        for piece in pieces:
            piece = cls._remove_note(piece)
            if piece and not cls._is_empty(piece):
                cleaned.append(piece)
        return cleaned

    @staticmethod
    def _remove_note(value: str) -> str:
        value = re.sub(r"（[^）]*）", "", value)
        value = re.sub(r"\([^@)]*\)", "", value)
        return value.strip()

    @staticmethod
    def _clean_account_value(value: str) -> str:
        return value.strip().strip("\"'")

    @staticmethod
    def _strip_at(value: str) -> str:
        return value.strip().lstrip("@")

    @staticmethod
    def _first_number(*values: str) -> str:
        for value in values:
            match = re.search(r"\d{4,}", value or "")
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _extract_user_id(account: str, url: str = "") -> str:
        for value in (account, url):
            match = re.search(r"(?:user/|profile/|explore/)?([A-Za-z0-9]{16,})", value or "")
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _sec_user_id(account: str, url: str = "") -> str:
        for value in (account, url):
            match = re.search(r"(sec[_-]user[_-]id|secUid)=([^&\s]+)", value or "")
            if match:
                return match.group(2)
            if "MS4wLj" in (value or ""):
                token = re.search(r"(MS4wLj[A-Za-z0-9_.-]+)", value)
                if token:
                    return token.group(1)
        return ""

    @staticmethod
    def _zhihu_token(account: str, url: str = "") -> str:
        for value in (url, account):
            match = re.search(r"people/([^/?#\s]+)", value or "")
            if match:
                return match.group(1)
        if re.match(r"^[A-Za-z0-9_-]{4,}$", account or "") and " " not in account:
            return account
        return ""

    @classmethod
    def _youtube_channel_url(cls, account: str, url: str = "") -> str:
        if "youtube.com" in (url or ""):
            return url
        if "youtube.com" in (account or ""):
            return account
        handle = re.search(r"@[A-Za-z0-9_.-]+", account or "")
        if handle:
            return f"https://www.youtube.com/{handle.group(0)}"
        return ""

    @staticmethod
    def _youtube_id_from_text(*values: str) -> str:
        for value in values:
            match = re.search(r"(UC[A-Za-z0-9_-]{20,})", value or "")
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _search_label(account: str, target: AccountTarget) -> str:
        label = re.sub(r"[@]", "", account or "").strip()
        if target.owner and target.owner not in label:
            return f"{target.owner} {label}".strip()
        return label or target.owner

    @staticmethod
    def _find_first(payload: Any, keys: Iterable[str]) -> Any:
        key_set = set(keys)
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in key_set and value not in (None, ""):
                    return value
                nested = SocialAccountCollector._find_first(value, key_set)
                if nested not in (None, ""):
                    return nested
        elif isinstance(payload, list):
            for item in payload:
                nested = SocialAccountCollector._find_first(item, key_set)
                if nested not in (None, ""):
                    return nested
        return None

    @staticmethod
    def _platform(value: str) -> str:
        normalized = (value or "").strip().lower()
        return PLATFORM_ALIASES.get(normalized, "")

    @staticmethod
    def _display_platform(platform: str) -> str:
        return {
            "twitter": "X",
            "xiaohongshu": "小红书",
            "bilibili": "Bilibili",
            "douyin": "抖音",
            "weibo": "微博",
            "zhihu": "知乎",
        }.get(platform, platform)

    @staticmethod
    def _canonical_competitor(value: str) -> str:
        value = re.sub(r"\([^)]*\)", "", value or "")
        value = re.sub(r"（[^）]*）", "", value)
        return re.sub(r"\s+", " ", value).strip().lower()

    @classmethod
    def _same_competitor(cls, sheet_value: str, competitor_name: str) -> bool:
        sheet = cls._canonical_competitor(sheet_value)
        target = cls._canonical_competitor(competitor_name)
        if not sheet or not target:
            return False
        return sheet == target or sheet in target or target in sheet

    @staticmethod
    def _is_empty(value: str) -> bool:
        return value.strip().lower() in EMPTY_MARKERS
