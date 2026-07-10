"""
SQLiteStorage —— 持久化存储采集和分析结果
用于去重、历史查询、周报数据回溯
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from loguru import logger

from config.settings import settings
from models.data_models import AnalyzedItem, ContentType, Priority, RawItem


class SQLiteStorage:
    """轻量级 SQLite 存储，适合 MVP 阶段"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.storage.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

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
                CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_url
                    ON raw_items(url, source_type);
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

    def save_analyzed_item(self, item: AnalyzedItem):
        """保存分析结果"""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO analyzed_items
                    (id, competitor_name, source_name, content_type, priority,
                     summary, detailed_analysis, key_signals, potential_impact,
                     recommended_actions, url, published_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.id,
                        item.competitor_name,
                        item.source_name,
                        item.content_type.value,
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

    def is_new_url(self, url: str, source_type: str) -> bool:
        """检查 URL 是否已存在（用于去重）"""
        if not url:
            return True
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM raw_items WHERE url = ? AND source_type = ?",
                (url, source_type),
            )
            return cursor.fetchone() is None

    @staticmethod
    def _row_to_analyzed(row: sqlite3.Row) -> AnalyzedItem:
        return AnalyzedItem(
            id=row["id"],
            competitor_name=row["competitor_name"],
            source_name=row["source_name"],
            content_type=ContentType(row["content_type"]),
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
