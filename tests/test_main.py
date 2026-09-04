from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.main import run_fetch
from src.models import Item


def _item(url: str, source: str = "rss:test") -> Item:
    return Item(
        url=url,
        title=url,
        content="",
        published_at=datetime(2026, 5, 14, tzinfo=timezone.utc),
        source=source,
    )


@pytest.mark.asyncio
async def test_run_fetch_dedup_and_store(monkeypatch, tmp_path: Path, caplog):
    sources_file = tmp_path / "sources.yaml"
    prefs_file = tmp_path / "preferences.yaml"
    db_file = tmp_path / "ai_daily.db"

    sources_file.write_text(
        "sources:\n"
        "  - name: s1\n"
        "    type: rss\n"
        "    url: https://x\n",
        encoding="utf-8",
    )
    prefs_file.write_text("keywords: []\nfetch_window_hours: 36\n", encoding="utf-8")

    fetch_all = AsyncMock(
        return_value=[_item("https://a"), _item("https://a"), _item("https://b")]
    )
    monkeypatch.setattr("src.main.fetch_all", fetch_all)

    with caplog.at_level("INFO"):
        summary = await run_fetch(
            sources_path=sources_file,
            preferences_path=prefs_file,
            db_path=db_file,
        )
    assert summary == {"fetched": 3, "deduped": 2, "stored": 2}
    assert db_file.exists()
    assert any(
        "total fetched=3" in rec.message and "new items=2" in rec.message
        for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_run_fetch_skips_already_seen(monkeypatch, tmp_path: Path):
    sources_file = tmp_path / "sources.yaml"
    prefs_file = tmp_path / "preferences.yaml"
    db_file = tmp_path / "ai_daily.db"
    sources_file.write_text(
        "sources:\n"
        "  - name: s1\n"
        "    type: rss\n"
        "    url: https://x\n",
        encoding="utf-8",
    )
    prefs_file.write_text("keywords: []\nfetch_window_hours: 36\n", encoding="utf-8")

    # First run: stores two items.
    monkeypatch.setattr(
        "src.main.fetch_all",
        AsyncMock(return_value=[_item("https://a"), _item("https://b")]),
    )
    first = await run_fetch(
        sources_path=sources_file,
        preferences_path=prefs_file,
        db_path=db_file,
    )
    assert first == {"fetched": 2, "deduped": 2, "stored": 2}

    # Second run: same URLs return, all should be filtered.
    monkeypatch.setattr(
        "src.main.fetch_all",
        AsyncMock(return_value=[_item("https://a"), _item("https://b")]),
    )
    second = await run_fetch(
        sources_path=sources_file,
        preferences_path=prefs_file,
        db_path=db_file,
    )
    assert second == {"fetched": 2, "deduped": 0, "stored": 0}
