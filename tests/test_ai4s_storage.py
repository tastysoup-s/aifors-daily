import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models import AI4SSummary, AnalyzerResult, Item
from src.storage import Storage


def _item(url: str, *, days_ago: int = 0) -> Item:
    return Item(
        url=url,
        title=f"title for {url}",
        content="scientific content",
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        source="test",
        raw={"origin": "fixture"},
    )


def _result(**overrides) -> AnalyzerResult:
    values = {
        "is_ai4s": True,
        "primary_category": "biology",
        "secondary_categories": ["medicine"],
        "content_type": "paper",
        "score": 8,
        "tags": ["protein-design"],
        "model": "test-analyzer",
        "cost_usd": 0.001,
    }
    values.update(overrides)
    return AnalyzerResult(**values)


def _summary() -> AI4SSummary:
    return AI4SSummary(
        scientific_problem="Predict protein structure.",
        ai_method="A diffusion model.",
        main_result="Improved the reported benchmark.",
        innovation="Joint sequence and structure modeling.",
        scientific_significance="Supports protein design experiments.",
        resources="https://example.com/code",
        model="test-summarizer",
        cost_usd=0.002,
    )


def test_init_creates_ai4s_analyses_table(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    row = storage._conn_or_die().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ai4s_analyses'"
    ).fetchone()
    assert row["name"] == "ai4s_analyses"
    storage.close()


def test_save_and_load_analyzer_result(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://a")])
    storage.save_analyzer_result("https://a", _result())

    analysis = storage.get_ai4s_analysis("https://a")
    assert analysis is not None
    assert analysis.item.raw == {"origin": "fixture"}
    assert analysis.analyzer.primary_category == "biology"
    assert analysis.analyzer.secondary_categories == ["medicine"]
    assert analysis.analyzer.tags == ["protein-design"]
    assert analysis.summary is None
    storage.close()


def test_save_and_load_non_ai4s_result(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://not-ai4s")])
    storage.save_analyzer_result(
        "https://not-ai4s",
        _result(
            is_ai4s=False,
            primary_category=None,
            secondary_categories=[],
            content_type="research_news",
            score=1,
        ),
    )

    analysis = storage.get_ai4s_analysis("https://not-ai4s")
    assert analysis is not None
    assert analysis.analyzer.is_ai4s is False
    assert analysis.analyzer.primary_category is None
    assert analysis.analyzer.secondary_categories == []
    storage.close()


def test_get_unanalyzed_items_excludes_analyzed_and_old_items(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://a"), _item("https://b")])
    storage.save_analyzer_result("https://a", _result())

    old_first_seen = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    storage._conn_or_die().execute(
        "INSERT INTO items"
        " (url, title, content, source, published_at, raw_json, first_seen)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("https://old", "old", "body", "test", old_first_seen, "{}", old_first_seen),
    )
    storage._conn_or_die().commit()

    assert [item.url for item in storage.get_unanalyzed_items(7)] == ["https://b"]
    storage.close()


def test_save_analyzer_result_upserts_without_duplicate(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://a")])
    storage.save_analyzer_result("https://a", _result())
    storage.save_analyzer_result(
        "https://a",
        _result(primary_category="chemistry", secondary_categories=[], score=9),
    )

    count = storage._conn_or_die().execute(
        "SELECT COUNT(*) FROM ai4s_analyses WHERE url='https://a'"
    ).fetchone()[0]
    analysis = storage.get_ai4s_analysis("https://a")
    assert count == 1
    assert analysis is not None
    assert analysis.analyzer.primary_category == "chemistry"
    assert analysis.analyzer.score == 9
    storage.close()


def test_save_and_load_ai4s_summary(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://a")])
    storage.save_analyzer_result("https://a", _result())
    storage.save_ai4s_summary("https://a", _summary())

    analysis = storage.get_ai4s_analysis("https://a")
    assert analysis is not None
    assert analysis.summary is not None
    assert analysis.summary.scientific_problem == "Predict protein structure."
    assert analysis.total_cost_usd == pytest.approx(0.003)
    storage.close()


def test_get_unsummarized_ai4s_analyses_filters_and_sorts(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([
        _item("https://score-8"),
        _item("https://score-9"),
        _item("https://low"),
        _item("https://non-ai4s"),
        _item("https://done"),
    ])
    storage.save_analyzer_result("https://score-8", _result(score=8))
    storage.save_analyzer_result("https://score-9", _result(score=9))
    storage.save_analyzer_result("https://low", _result(score=5))
    storage.save_analyzer_result(
        "https://non-ai4s",
        _result(
            is_ai4s=False,
            primary_category=None,
            secondary_categories=[],
            score=1,
        ),
    )
    storage.save_analyzer_result("https://done", _result(score=10))
    storage.save_ai4s_summary("https://done", _summary())

    candidates = storage.get_unsummarized_ai4s_analyses(
        min_score=7,
    )

    assert [analysis.item.url for analysis in candidates] == [
        "https://score-9",
        "https://score-8",
    ]
    assert all(analysis.summary is None for analysis in candidates)
    storage.close()


def test_ai4s_summary_state_is_controlled_by_summarized_at(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://partial")])
    storage.save_analyzer_result("https://partial", _result())
    storage._conn_or_die().execute(
        "UPDATE ai4s_analyses SET scientific_problem=? WHERE url=?",
        ("partial write", "https://partial"),
    )
    storage._conn_or_die().commit()

    analysis = storage.get_ai4s_analysis("https://partial")
    candidates = storage.get_unsummarized_ai4s_analyses(7)

    assert analysis is not None
    assert analysis.summary is None
    assert [candidate.item.url for candidate in candidates] == ["https://partial"]
    storage.close()


def test_save_ai4s_summary_requires_analyzer_result(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://a")])
    with pytest.raises(ValueError, match="call save_analyzer_result first"):
        storage.save_ai4s_summary("https://a", _summary())
    storage.close()


def test_save_analyzer_result_for_unknown_url_raises_fk_violation(tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    with pytest.raises(sqlite3.IntegrityError):
        storage.save_analyzer_result("https://ghost", _result())
    storage.close()


def test_existing_database_gains_ai4s_table_without_changing_old_rows(tmp_path: Path):
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE items (
            url TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            source TEXT NOT NULL, published_at TEXT NOT NULL,
            raw_json TEXT NOT NULL DEFAULT '{}', first_seen TEXT NOT NULL
        );
        CREATE TABLE summaries (
            url TEXT PRIMARY KEY REFERENCES items(url),
            score INTEGER NOT NULL, tags_json TEXT NOT NULL,
            scorer_model TEXT NOT NULL, scorer_cost_usd REAL NOT NULL DEFAULT 0,
            innovation TEXT, approach TEXT, metrics TEXT, links TEXT,
            why_relevant TEXT, summarizer_model TEXT, summarizer_cost_usd REAL,
            created_at TEXT NOT NULL, surfaced_at TEXT
        );
        INSERT INTO items VALUES (
            'https://old', 'old title', 'old content', 'old source',
            '2026-09-01T00:00:00+00:00', '{}', '2026-09-01T00:00:00+00:00'
        );
        INSERT INTO summaries (
            url, score, tags_json, scorer_model, scorer_cost_usd, created_at
        ) VALUES ('https://old', 8, '["legacy"]', 'old-model', 0.01,
                  '2026-09-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()

    storage = Storage(db_path)
    storage.init()
    old_item = storage.fetch_item_row("https://old")
    old_summary = storage._conn_or_die().execute(
        "SELECT score, tags_json FROM summaries WHERE url='https://old'"
    ).fetchone()
    new_table = storage._conn_or_die().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ai4s_analyses'"
    ).fetchone()

    assert old_item["title"] == "old title"
    assert tuple(old_summary) == (8, '["legacy"]')
    assert new_table["name"] == "ai4s_analyses"
    storage.close()
