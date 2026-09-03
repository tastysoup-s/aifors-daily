import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

from src.models import (
    AI4SAnalysis,
    AI4SSummary,
    Analysis,
    AnalyzerResult,
    Item,
    Score,
    Summary,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    first_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);

CREATE TABLE IF NOT EXISTS summaries (
    url TEXT PRIMARY KEY REFERENCES items(url),
    score INTEGER NOT NULL,
    tags_json TEXT NOT NULL,
    scorer_model TEXT NOT NULL,
    scorer_cost_usd REAL NOT NULL DEFAULT 0,
    innovation TEXT,
    approach TEXT,
    metrics TEXT,
    links TEXT,
    why_relevant TEXT,
    summarizer_model TEXT,
    summarizer_cost_usd REAL,
    created_at TEXT NOT NULL,
    surfaced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_summaries_score ON summaries(score);
-- idx_summaries_surfaced_at is created by _migrate_add_surfaced_at after the
-- column is guaranteed to exist on both fresh and pre-feature DBs.

CREATE TABLE IF NOT EXISTS ai4s_analyses (
    url TEXT PRIMARY KEY REFERENCES items(url),
    is_ai4s INTEGER NOT NULL CHECK(is_ai4s IN (0, 1)),
    primary_category TEXT,
    secondary_categories_json TEXT NOT NULL DEFAULT '[]',
    content_type TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 10),
    tags_json TEXT NOT NULL,
    analyzer_model TEXT NOT NULL,
    analyzer_cost_usd REAL NOT NULL DEFAULT 0,
    analyzed_at TEXT NOT NULL,
    scientific_problem TEXT,
    ai_method TEXT,
    main_result TEXT,
    innovation TEXT,
    scientific_significance TEXT,
    resources TEXT,
    summarizer_model TEXT,
    summarizer_cost_usd REAL,
    summarized_at TEXT,
    surfaced_at TEXT
);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.row_factory = sqlite3.Row
        # If legacy Stage-1-only table is present and new `items` is not, drop it.
        # We don't migrate; data will be re-fetched. Documented in plan.
        legacy = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='seen_urls'"
        ).fetchone()
        has_items = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
        ).fetchone()
        if legacy and not has_items:
            self._conn.executescript("DROP TABLE seen_urls;")
        self._conn.executescript(_SCHEMA)
        # Stage 3-dedup migration: if summaries.surfaced_at is missing on a
        # pre-existing table, add it and backfill from created_at so existing
        # rows are treated as already archived (not "new today").
        self._migrate_add_surfaced_at()
        self._conn.commit()

    def _migrate_add_surfaced_at(self) -> None:
        assert self._conn is not None
        cols = self._conn.execute("PRAGMA table_info(summaries)").fetchall()
        col_names = {c[1] for c in cols}
        if "surfaced_at" not in col_names:
            self._conn.executescript(
                "ALTER TABLE summaries ADD COLUMN surfaced_at TEXT;"
                "UPDATE summaries SET surfaced_at = created_at;"
            )
        # Always ensure the index exists (handles both fresh and migrated DBs).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_summaries_surfaced_at"
            " ON summaries(surfaced_at)"
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _conn_or_die(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Storage.init() not called")
        return self._conn

    # --- items (Stage 1 + 2) ---

    def seen_urls(self, urls: Iterable[str]) -> set[str]:
        urls = list(urls)
        if not urls:
            return set()
        conn = self._conn_or_die()
        placeholders = ",".join("?" * len(urls))
        rows = conn.execute(
            f"SELECT url FROM items WHERE url IN ({placeholders})", urls
        ).fetchall()
        return {r["url"] for r in rows}

    def record_items(self, items: Iterable[Item]) -> None:
        conn = self._conn_or_die()
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                it.url, it.title, it.content, it.source,
                it.published_at.isoformat(),
                json.dumps(it.raw, default=str),
                now,
            )
            for it in items
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO items"
            " (url, title, content, source, published_at, raw_json, first_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    def fetch_item_row(self, url: str) -> dict:
        conn = self._conn_or_die()
        row = conn.execute(
            "SELECT * FROM items WHERE url = ?", (url,)
        ).fetchone()
        return dict(row) if row else {}

    def get_items_by_urls(self, urls: list[str]) -> list[Item]:
        if not urls:
            return []
        conn = self._conn_or_die()
        placeholders = ",".join("?" * len(urls))
        rows = conn.execute(
            f"SELECT * FROM items WHERE url IN ({placeholders})", urls
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def get_unscored_items(self, within_days: int) -> list[Item]:
        conn = self._conn_or_die()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        rows = conn.execute(
            "SELECT i.* FROM items i LEFT JOIN summaries s ON s.url = i.url"
            " WHERE s.url IS NULL AND i.first_seen >= ?"
            " ORDER BY i.first_seen DESC",
            (cutoff,),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    # --- AI4S analyses (Phase 4+) ---

    def get_unanalyzed_items(self, within_days: int) -> list[Item]:
        conn = self._conn_or_die()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        rows = conn.execute(
            "SELECT i.* FROM items i"
            " LEFT JOIN ai4s_analyses a ON a.url = i.url"
            " WHERE a.url IS NULL AND i.first_seen >= ?"
            " ORDER BY i.first_seen DESC",
            (cutoff,),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def save_analyzer_result(self, url: str, result: AnalyzerResult) -> None:
        conn = self._conn_or_die()
        conn.execute(
            "INSERT INTO ai4s_analyses"
            " (url, is_ai4s, primary_category, secondary_categories_json,"
            "  content_type, score, tags_json, analyzer_model,"
            "  analyzer_cost_usd, analyzed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(url) DO UPDATE SET"
            "   is_ai4s=excluded.is_ai4s,"
            "   primary_category=excluded.primary_category,"
            "   secondary_categories_json=excluded.secondary_categories_json,"
            "   content_type=excluded.content_type,"
            "   score=excluded.score,"
            "   tags_json=excluded.tags_json,"
            "   analyzer_model=excluded.analyzer_model,"
            "   analyzer_cost_usd=excluded.analyzer_cost_usd,"
            "   analyzed_at=excluded.analyzed_at",
            (
                url,
                int(result.is_ai4s),
                result.primary_category,
                json.dumps(result.secondary_categories, ensure_ascii=False),
                result.content_type,
                result.score,
                json.dumps(result.tags, ensure_ascii=False),
                result.model,
                result.cost_usd,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    def save_ai4s_summary(self, url: str, summary: AI4SSummary) -> None:
        conn = self._conn_or_die()
        cur = conn.execute(
            "UPDATE ai4s_analyses SET"
            "  scientific_problem=?, ai_method=?, main_result=?, innovation=?,"
            "  scientific_significance=?, resources=?, summarizer_model=?,"
            "  summarizer_cost_usd=?, summarized_at=?"
            " WHERE url=?",
            (
                summary.scientific_problem,
                summary.ai_method,
                summary.main_result,
                summary.innovation,
                summary.scientific_significance,
                summary.resources,
                summary.model,
                summary.cost_usd,
                datetime.now(timezone.utc).isoformat(),
                url,
            ),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"save_ai4s_summary: no analyzer row exists for {url};"
                " call save_analyzer_result first"
            )
        conn.commit()

    def get_ai4s_analysis(self, url: str) -> AI4SAnalysis | None:
        conn = self._conn_or_die()
        row = conn.execute(
            "SELECT i.*,"
            "       a.is_ai4s, a.primary_category,"
            "       a.secondary_categories_json, a.content_type, a.score,"
            "       a.tags_json, a.analyzer_model, a.analyzer_cost_usd,"
            "       a.scientific_problem, a.ai_method, a.main_result,"
            "       a.innovation, a.scientific_significance, a.resources,"
            "       a.summarizer_model, a.summarizer_cost_usd, a.surfaced_at"
            " FROM items i JOIN ai4s_analyses a ON a.url = i.url"
            " WHERE i.url = ?",
            (url,),
        ).fetchone()
        return self._row_to_ai4s_analysis(row) if row else None

    # --- summaries (Stage 2) ---

    def save_score(self, url: str, score: Score) -> None:
        conn = self._conn_or_die()
        conn.execute(
            "INSERT INTO summaries"
            " (url, score, tags_json, scorer_model, scorer_cost_usd, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(url) DO UPDATE SET"
            "   score=excluded.score, tags_json=excluded.tags_json,"
            "   scorer_model=excluded.scorer_model,"
            "   scorer_cost_usd=excluded.scorer_cost_usd",
            (
                url, score.score, json.dumps(score.tags),
                score.model, score.cost_usd,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    def save_summary(self, url: str, summary: Summary) -> None:
        conn = self._conn_or_die()
        cur = conn.execute(
            "UPDATE summaries SET"
            "  innovation=?, approach=?, metrics=?, links=?, why_relevant=?,"
            "  summarizer_model=?, summarizer_cost_usd=?"
            " WHERE url=?",
            (
                summary.innovation, summary.approach, summary.metrics,
                summary.links, summary.why_relevant,
                summary.model, summary.cost_usd, url,
            ),
        )
        if cur.rowcount == 0:
            raise ValueError(f"save_summary: no score row exists for {url}; call save_score first")
        conn.commit()

    def get_top_summaries(
        self, min_score: int, limit: int, within_days: int
    ) -> list[Analysis]:
        conn = self._conn_or_die()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        rows = conn.execute(
            "SELECT i.url, i.title, i.source, i.content, i.published_at,"
            "       s.score, s.tags_json, s.scorer_model, s.scorer_cost_usd,"
            "       s.innovation, s.approach, s.metrics, s.links, s.why_relevant,"
            "       s.summarizer_model, s.summarizer_cost_usd, s.surfaced_at"
            " FROM items i JOIN summaries s ON s.url = i.url"
            " WHERE s.score >= ? AND i.first_seen >= ?"
            " ORDER BY s.score DESC, i.published_at DESC"
            " LIMIT ?",
            (min_score, cutoff, limit),
        ).fetchall()
        return [self._row_to_analysis(r) for r in rows]

    def get_today_summaries(self, min_score: int) -> list[Analysis]:
        """Items with summary + score >= threshold that have NOT been surfaced yet."""
        conn = self._conn_or_die()
        rows = conn.execute(
            "SELECT i.url, i.title, i.source, i.content, i.published_at,"
            "       s.score, s.tags_json, s.scorer_model, s.scorer_cost_usd,"
            "       s.innovation, s.approach, s.metrics, s.links, s.why_relevant,"
            "       s.summarizer_model, s.summarizer_cost_usd, s.surfaced_at"
            " FROM items i JOIN summaries s ON s.url = i.url"
            " WHERE s.score >= ? AND s.innovation IS NOT NULL"
            "   AND s.surfaced_at IS NULL"
            " ORDER BY s.score DESC, i.published_at DESC",
            (min_score,),
        ).fetchall()
        return [self._row_to_analysis(r) for r in rows]

    def get_archive_summaries(
        self, min_score: int, within_days: int
    ) -> list[Analysis]:
        """Items with summary + score >= threshold that have been surfaced
        already and are still within the archive window (filtered by surfaced_at)."""
        conn = self._conn_or_die()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
        rows = conn.execute(
            "SELECT i.url, i.title, i.source, i.content, i.published_at,"
            "       s.score, s.tags_json, s.scorer_model, s.scorer_cost_usd,"
            "       s.innovation, s.approach, s.metrics, s.links, s.why_relevant,"
            "       s.summarizer_model, s.summarizer_cost_usd, s.surfaced_at"
            " FROM items i JOIN summaries s ON s.url = i.url"
            " WHERE s.score >= ? AND s.innovation IS NOT NULL"
            "   AND s.surfaced_at IS NOT NULL AND s.surfaced_at >= ?"
            " ORDER BY s.surfaced_at DESC, s.score DESC",
            (min_score, cutoff),
        ).fetchall()
        return [self._row_to_analysis(r) for r in rows]

    def mark_surfaced(self, urls: list[str]) -> int:
        """Mark a batch of summaries as surfaced (NULL -> now). No-op for
        already-surfaced rows. Returns the number of rows actually updated."""
        if not urls:
            return 0
        conn = self._conn_or_die()
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(urls))
        cur = conn.execute(
            f"UPDATE summaries SET surfaced_at = ?"
            f" WHERE url IN ({placeholders}) AND surfaced_at IS NULL",
            [now, *urls],
        )
        conn.commit()
        return cur.rowcount

    # --- helpers ---

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Item:
        return Item(
            url=row["url"],
            title=row["title"],
            content=row["content"],
            source=row["source"],
            published_at=datetime.fromisoformat(row["published_at"]),
            raw=json.loads(row["raw_json"]) if row["raw_json"] else {},
        )

    @staticmethod
    def _row_to_analysis(row: sqlite3.Row) -> Analysis:
        score = Score(
            score=row["score"],
            tags=json.loads(row["tags_json"]),
            model=row["scorer_model"],
            cost_usd=row["scorer_cost_usd"],
        )
        summary = None
        if row["innovation"] is not None:
            summary = Summary(
                innovation=row["innovation"],
                approach=row["approach"],
                metrics=row["metrics"],
                links=row["links"],
                why_relevant=row["why_relevant"],
                model=row["summarizer_model"] or "",
                cost_usd=row["summarizer_cost_usd"] or 0.0,
            )
        # surfaced_at is present in all three SELECTs that hit this helper.
        # Use sqlite3.Row.keys() to stay safe if a future SELECT drops it.
        surfaced_at = None
        if "surfaced_at" in row.keys() and row["surfaced_at"]:
            surfaced_at = datetime.fromisoformat(row["surfaced_at"])
        return Analysis(
            url=row["url"],
            title=row["title"],
            source=row["source"],
            content=row["content"],
            published_at=datetime.fromisoformat(row["published_at"]),
            score=score,
            summary=summary,
            surfaced_at=surfaced_at,
        )

    @staticmethod
    def _row_to_ai4s_analysis(row: sqlite3.Row) -> AI4SAnalysis:
        analyzer = AnalyzerResult(
            is_ai4s=bool(row["is_ai4s"]),
            primary_category=row["primary_category"],
            secondary_categories=json.loads(row["secondary_categories_json"]),
            content_type=row["content_type"],
            score=row["score"],
            tags=json.loads(row["tags_json"]),
            model=row["analyzer_model"],
            cost_usd=row["analyzer_cost_usd"],
        )
        summary = None
        if row["scientific_problem"] is not None:
            summary = AI4SSummary(
                scientific_problem=row["scientific_problem"],
                ai_method=row["ai_method"],
                main_result=row["main_result"],
                innovation=row["innovation"],
                scientific_significance=row["scientific_significance"],
                resources=row["resources"],
                model=row["summarizer_model"] or "",
                cost_usd=row["summarizer_cost_usd"] or 0.0,
            )
        surfaced_at = (
            datetime.fromisoformat(row["surfaced_at"])
            if row["surfaced_at"] else None
        )
        return AI4SAnalysis(
            item=Storage._row_to_item(row),
            analyzer=analyzer,
            summary=summary,
            surfaced_at=surfaced_at,
        )
