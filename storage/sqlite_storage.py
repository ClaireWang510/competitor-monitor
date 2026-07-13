"""
SQLiteStorage —— 持久化存储采集和分析结果
用于去重、历史查询、周报数据回溯
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from loguru import logger

from config.settings import settings
from models.data_models import (
    AnalyzedItem,
    ContentType,
    Priority,
    RawItem,
    ReportSection,
)


class SQLiteStorage:
    """轻量级 SQLite 存储，适合 MVP 阶段"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.storage.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        """初始化表结构"""
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS raw_items (
                    id TEXT PRIMARY KEY,
                    competitor_name TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    url TEXT,
                    title TEXT,
                    content_snippet TEXT,
                    author TEXT,
                    published_at TEXT,
                    collected_at TEXT NOT NULL,
                    raw_metadata TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS analyzed_items (
                    id TEXT PRIMARY KEY,
                    competitor_name TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    report_section TEXT NOT NULL DEFAULT 'other',
                    priority TEXT NOT NULL,
                    summary TEXT,
                    detailed_analysis TEXT,
                    key_signals TEXT,
                    potential_impact TEXT,
                    recommended_actions TEXT,
                    url TEXT,
                    published_at TEXT,
                    analyzed_at TEXT DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_raw_competitor
                    ON raw_items(competitor_name, collected_at);
                CREATE INDEX IF NOT EXISTS idx_analyzed_competitor
                    ON analyzed_items(competitor_name, analyzed_at);
                DROP INDEX IF EXISTS idx_raw_url;
                DROP INDEX IF EXISTS idx_raw_url_competitor;
                CREATE UNIQUE INDEX idx_raw_url_competitor
                    ON raw_items(url, source_type, competitor_name)
                    WHERE url IS NOT NULL AND url <> '';
            """)
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(analyzed_items)")
            }
            if "report_section" not in columns:
                conn.execute(
                    "ALTER TABLE analyzed_items ADD COLUMN report_section TEXT NOT NULL DEFAULT 'other'"
                )
            conn.execute("""UPDATE analyzed_items
                SET report_section = CASE
                    WHEN content_type = 'community_post' THEN 'social'
                    WHEN content_type = 'github_activity' THEN 'open_source'
                    WHEN content_type IN ('feature_release', 'documentation') THEN 'product'
                    WHEN content_type IN ('blog_post', 'news', 'business_update', 'customer_case') THEN 'market'
                    ELSE report_section
                END
                WHERE report_section = 'other'
            """)
        logger.debug(f"SQLiteStorage: 数据库初始化完成 {self.db_path}")

    def save_raw_item(self, item: RawItem) -> bool:
        """保存原始条目，返回是否为新插入（False = 已存在）"""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO raw_items
                    (id, competitor_name, source_name, source_type, url, title,
                     content_snippet, author, published_at, collected_at, raw_metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.competitor_name,
                        item.source_name,
                        item.source_type,
                        item.url,
                        item.title,
                        item.content_snippet,
                        item.author,
                        item.published_at.isoformat() if item.published_at else None,
                        item.collected_at.isoformat(),
                        json.dumps(item.raw_metadata, ensure_ascii=False),
                    ),
                )
                return conn.total_changes > 0
        except Exception as e:
            logger.error(f"Storage: save_raw_item error: {e}")
            return False

    def save_raw_items(self, items: List[RawItem]) -> int:
        """批量保存原始条目，避免长任务在分析前丢失采集结果。"""
        if not items:
            return 0
        rows = [
            (
                item.id,
                item.competitor_name,
                item.source_name,
                item.source_type,
                item.url,
                item.title,
                item.content_snippet,
                item.author,
                item.published_at.isoformat() if item.published_at else None,
                item.collected_at.isoformat(),
                json.dumps(item.raw_metadata, ensure_ascii=False),
            )
            for item in items
        ]
        with self._get_conn() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO raw_items
                (id, competitor_name, source_name, source_type, url, title,
                 content_snippet, author, published_at, collected_at, raw_metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            return conn.total_changes - before

    def save_analyzed_item(self, item: AnalyzedItem):
        """保存分析结果"""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO analyzed_items
                    (id, competitor_name, source_name, content_type, report_section, priority,
                     summary, detailed_analysis, key_signals, potential_impact,
                     recommended_actions, url, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.competitor_name,
                        item.source_name,
                        item.content_type.value,
                        item.report_section.value,
                        item.priority.value,
                        item.summary,
                        item.detailed_analysis,
                        json.dumps(item.key_signals, ensure_ascii=False),
                        item.potential_impact,
                        json.dumps(item.recommended_actions, ensure_ascii=False),
                        item.url,
                        item.published_at.isoformat() if item.published_at else None,
                    ),
                )
        except Exception as e:
            logger.error(f"Storage: save_analyzed_item error: {e}")

    def get_recent_items(
        self, competitor_name: str, days: int = 7
    ) -> List[AnalyzedItem]:
        """获取最近 N 天的分析结果"""
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """SELECT * FROM analyzed_items
                WHERE competitor_name = ?
                  AND analyzed_at >= datetime('now', ?)
                ORDER BY analyzed_at DESC""",
                (competitor_name, f"-{days} days"),
            )
            rows = cursor.fetchall()

        return [self._row_to_analyzed(row) for row in rows]

    def get_items_published_between(
        self,
        competitor_name: str,
        period_start: datetime,
        period_end: datetime,
    ) -> List[AnalyzedItem]:
        """Read a product's durable weekly memory using publication time."""
        start = self._as_utc(period_start).isoformat()
        end = self._as_utc(period_end).isoformat()
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM analyzed_items
                WHERE competitor_name = ?
                  AND published_at IS NOT NULL
                  AND datetime(published_at) >= datetime(?)
                  AND datetime(published_at) <= datetime(?)
                ORDER BY datetime(published_at) DESC""",
                (competitor_name, start, end),
            ).fetchall()
        return [self._row_to_analyzed(row) for row in rows]

    def get_unanalyzed_raw_items_published_between(
        self,
        competitor_name: str,
        period_start: datetime,
        period_end: datetime,
    ) -> List[RawItem]:
        """读取已采集但尚未分析的周内条目，用于中断后续跑。"""
        start = self._as_utc(period_start).isoformat()
        end = self._as_utc(period_end).isoformat()
        with self._get_conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT r.* FROM raw_items r
                LEFT JOIN analyzed_items a ON a.id = r.id
                WHERE r.competitor_name = ?
                  AND r.published_at IS NOT NULL
                  AND datetime(r.published_at) >= datetime(?)
                  AND datetime(r.published_at) <= datetime(?)
                  AND a.id IS NULL
                ORDER BY datetime(r.published_at) DESC""",
                (competitor_name, start, end),
            ).fetchall()
        return [self._row_to_raw(row) for row in rows]

    def is_new_url(
        self, url: str, source_type: str, competitor_name: str = ""
    ) -> bool:
        """检查 URL 是否已存在（用于去重）"""
        if not url:
            return True
        with self._get_conn() as conn:
            cursor = conn.execute(
                """SELECT 1 FROM raw_items
                WHERE url = ? AND source_type = ? AND competitor_name = ?""",
                (url, source_type, competitor_name),
            )
            return cursor.fetchone() is None

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _row_to_raw(row: sqlite3.Row) -> RawItem:
        return RawItem(
            id=row["id"],
            competitor_name=row["competitor_name"],
            source_name=row["source_name"],
            source_type=row["source_type"],
            url=row["url"] or "",
            title=row["title"] or "",
            content_snippet=row["content_snippet"] or "",
            author=row["author"] or "",
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"]
                else None
            ),
            collected_at=datetime.fromisoformat(row["collected_at"]),
            raw_metadata=json.loads(row["raw_metadata"] or "{}"),
        )

    @staticmethod
    def _row_to_analyzed(row: sqlite3.Row) -> AnalyzedItem:
        return AnalyzedItem(
            id=row["id"],
            competitor_name=row["competitor_name"],
            source_name=row["source_name"],
            content_type=ContentType(row["content_type"]),
            report_section=ReportSection(
                row["report_section"] if "report_section" in row.keys() else "other"
            ),
            priority=Priority(row["priority"]),
            summary=row["summary"] or "",
            detailed_analysis=row["detailed_analysis"] or "",
            key_signals=json.loads(row["key_signals"]) if row["key_signals"] else [],
            potential_impact=row["potential_impact"] or "",
            recommended_actions=(
                json.loads(row["recommended_actions"])
                if row["recommended_actions"]
                else []
            ),
            url=row["url"] or "",
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"]
                else None
            ),
        )
