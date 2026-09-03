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
    Report,
    ReportItem,
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

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL CHECK(report_type IN ('daily', 'weekly')),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    overview TEXT,
    category_trends_json TEXT NOT NULL DEFAULT '{}',
    watchlist_json TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    UNIQUE(report_type, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS report_items (
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    url TEXT NOT NULL REFERENCES items(url),
    rank INTEGER NOT NULL,
    category TEXT NOT NULL,
    section TEXT,
    PRIMARY KEY(report_id, url),
    UNIQUE(report_id, rank)
);
CREATE INDEX IF NOT EXISTS idx_reports_latest
    ON reports(report_type, generated_at DESC);
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

    def get_unsummarized_ai4s_analyses(
        self,
        min_score: int,
    ) -> list[AI4SAnalysis]:
        conn = self._conn_or_die()
        rows = conn.execute(
            "SELECT i.*,"
            "       a.is_ai4s, a.primary_category,"
            "       a.secondary_categories_json, a.content_type, a.score,"
            "       a.tags_json, a.analyzer_model, a.analyzer_cost_usd,"
            "       a.scientific_problem, a.ai_method, a.main_result,"
            "       a.innovation, a.scientific_significance, a.resources,"
            "       a.summarizer_model, a.summarizer_cost_usd,"
            "       a.summarized_at, a.surfaced_at"
            " FROM items i JOIN ai4s_analyses a ON a.url = i.url"
            " WHERE a.is_ai4s = 1 AND a.score >= ?"
            "   AND a.summarized_at IS NULL"
            " ORDER BY a.score DESC, i.published_at DESC",
            (min_score,),
        ).fetchall()
        return [self._row_to_ai4s_analysis(row) for row in rows]

    def get_ai4s_analysis(self, url: str) -> AI4SAnalysis | None:
        conn = self._conn_or_die()
        row = conn.execute(
            "SELECT i.*,"
            "       a.is_ai4s, a.primary_category,"
            "       a.secondary_categories_json, a.content_type, a.score,"
            "       a.tags_json, a.analyzer_model, a.analyzer_cost_usd,"
            "       a.scientific_problem, a.ai_method, a.main_result,"
            "       a.innovation, a.scientific_significance, a.resources,"
            "       a.summarizer_model, a.summarizer_cost_usd,"
            "       a.summarized_at, a.surfaced_at"
            " FROM items i JOIN ai4s_analyses a ON a.url = i.url"
            " WHERE i.url = ?",
            (url,),
        ).fetchone()
        return self._row_to_ai4s_analysis(row) if row else None

    # --- reports (Phase 8+) ---

    def get_report_candidates(
        self,
        period_start: datetime,
        period_end: datetime,
        min_score: int,
        limit: int | None = None,
    ) -> list[AI4SAnalysis]:
        conn = self._conn_or_die()
        query = (
            "SELECT i.*,"
            "       a.is_ai4s, a.primary_category,"
            "       a.secondary_categories_json, a.content_type, a.score,"
            "       a.tags_json, a.analyzer_model, a.analyzer_cost_usd,"
            "       a.scientific_problem, a.ai_method, a.main_result,"
            "       a.innovation, a.scientific_significance, a.resources,"
            "       a.summarizer_model, a.summarizer_cost_usd,"
            "       a.summarized_at, a.surfaced_at"
            " FROM items i JOIN ai4s_analyses a ON a.url = i.url"
            " WHERE a.is_ai4s = 1 AND a.score >= ?"
            "   AND a.summarized_at IS NOT NULL"
            "   AND a.summarized_at >= ? AND a.summarized_at <= ?"
            " ORDER BY a.score DESC, i.published_at DESC"
        )
        params: list = [
            min_score,
            period_start.isoformat(),
            period_end.isoformat(),
        ]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [self._row_to_ai4s_analysis(row) for row in rows]

    def create_report(
        self,
        report_type: str,
        period_start: datetime,
        period_end: datetime,
        analyses: list[AI4SAnalysis],
        *,
        overview: str | None = None,
        category_trends: dict[str, str] | None = None,
        watchlist: list[str] | None = None,
        model: str | None = None,
        cost_usd: float = 0.0,
    ) -> tuple[Report, bool]:
        existing = self.get_report_by_period(report_type, period_start, period_end)
        if existing is not None:
            return existing, False

        conn = self._conn_or_die()
        generated_at = datetime.now(timezone.utc)
        cur = conn.execute(
            "INSERT INTO reports"
            " (report_type, period_start, period_end, generated_at, overview,"
            "  category_trends_json, watchlist_json, model, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report_type,
                period_start.isoformat(),
                period_end.isoformat(),
                generated_at.isoformat(),
                overview,
                json.dumps(category_trends or {}, ensure_ascii=False),
                json.dumps(watchlist or [], ensure_ascii=False),
                model,
                cost_usd,
            ),
        )
        report_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO report_items (report_id, url, rank, category)"
            " VALUES (?, ?, ?, ?)",
            [
                (report_id, analysis.item.url, rank, analysis.analyzer.primary_category)
                for rank, analysis in enumerate(analyses, start=1)
            ],
        )
        conn.commit()
        report = self.get_report_by_period(report_type, period_start, period_end)
        assert report is not None
        return report, True

    def get_report_by_period(
        self,
        report_type: str,
        period_start: datetime,
        period_end: datetime,
    ) -> Report | None:
        row = self._conn_or_die().execute(
            "SELECT * FROM reports"
            " WHERE report_type=? AND period_start=? AND period_end=?",
            (report_type, period_start.isoformat(), period_end.isoformat()),
        ).fetchone()
        return self._row_to_report(row) if row else None

    def get_daily_report(self, report_date) -> Report | None:
        row = self._conn_or_die().execute(
            "SELECT * FROM reports WHERE report_type='daily'"
            " AND substr(period_start, 1, 10)=? ORDER BY generated_at DESC LIMIT 1",
            (report_date.isoformat(),),
        ).fetchone()
        return self._row_to_report(row) if row else None

    def get_weekly_report(
        self, period_start: datetime, period_end: datetime
    ) -> Report | None:
        return self.get_report_by_period("weekly", period_start, period_end)

    def get_latest_daily_report(self) -> Report | None:
        return self._get_latest_report("daily")

    def get_latest_weekly_report(self) -> Report | None:
        return self._get_latest_report("weekly")

    def _get_latest_report(self, report_type: str) -> Report | None:
        row = self._conn_or_die().execute(
            "SELECT * FROM reports WHERE report_type=?"
            " ORDER BY period_end DESC LIMIT 1",
            (report_type,),
        ).fetchone()
        return self._row_to_report(row) if row else None

    def get_report_items(self, report_id: int) -> list[ReportItem]:
        rows = self._conn_or_die().execute(
            "SELECT i.*, a.is_ai4s, a.primary_category,"
            "       a.secondary_categories_json, a.content_type, a.score,"
            "       a.tags_json, a.analyzer_model, a.analyzer_cost_usd,"
            "       a.scientific_problem, a.ai_method, a.main_result,"
            "       a.innovation, a.scientific_significance, a.resources,"
            "       a.summarizer_model, a.summarizer_cost_usd,"
            "       a.summarized_at, a.surfaced_at,"
            "       ri.rank report_rank, ri.category report_category,"
            "       ri.section report_section"
            " FROM report_items ri"
            " JOIN items i ON i.url=ri.url"
            " JOIN ai4s_analyses a ON a.url=i.url"
            " WHERE ri.report_id=? ORDER BY ri.rank",
            (report_id,),
        ).fetchall()
        return [
            ReportItem(
                analysis=self._row_to_ai4s_analysis(row),
                rank=row["report_rank"],
                category=row["report_category"],
                section=row["report_section"],
            )
            for row in rows
        ]

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
        if row["summarized_at"] is not None:
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

    def _row_to_report(self, row: sqlite3.Row) -> Report:
        return Report(
            id=row["id"],
            report_type=row["report_type"],
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            generated_at=datetime.fromisoformat(row["generated_at"]),
            items=self.get_report_items(row["id"]),
            overview=row["overview"],
            category_trends=json.loads(row["category_trends_json"]),
            watchlist=json.loads(row["watchlist_json"]),
            model=row["model"],
            cost_usd=row["cost_usd"],
        )
