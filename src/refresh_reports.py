"""Refresh the latest saved report selections for a zero-LLM republish.

Report periods, synthesis, timestamps and costs are preserved. Only membership
rows of the two reports currently displayed by the website may change.
"""

import argparse
import logging
from pathlib import Path

from src.ai4s_daily import select_daily_candidates
from src.ai4s_weekly import select_representative_works
from src.config import Config, load_config
from src.information_sufficiency import has_sufficient_information
from src.storage import Storage


def refresh_report_selections(storage: Storage, cfg: Config) -> list[dict]:
    results = []
    conn = storage._conn_or_die()
    # One transaction: an error cannot leave one of the visible reports empty.
    with conn:
        for report in (storage.get_latest_daily_report(), storage.get_latest_weekly_report()):
            if report is None:
                continue
            candidates = storage.get_report_candidates(
                report.period_start, report.period_end, min_score=cfg.score_threshold,
            )
            qualified = [a for a in candidates if has_sufficient_information(a)]
            selected = (
                select_representative_works(candidates) if report.report_type == "weekly"
                else select_daily_candidates(qualified, cfg.top_n)
            )
            rows = [
                (report.id, a.item.url, rank, a.analyzer.primary_category, None)
                for rank, a in enumerate(selected, start=1)
            ]
            previous = [
                (report.id, i.analysis.item.url, i.rank, i.category, i.section)
                for i in report.items
            ]
            if rows != previous:
                conn.execute("DELETE FROM report_items WHERE report_id=?", (report.id,))
                conn.executemany(
                    "INSERT INTO report_items (report_id,url,rank,category,section) "
                    "VALUES (?,?,?,?,?)", rows,
                )
            results.append(dict(
                report_type=report.report_type, report_id=report.id,
                candidates=len(candidates), qualified=len(qualified),
                filtered_sparse=len(candidates) - len(qualified),
                before=len(previous), selected=len(rows), changed=rows != previous,
            ))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("republishing requires an existing database")
    logging.basicConfig(level=logging.INFO)
    storage = Storage(args.db)
    try:
        storage.init()
        print(refresh_report_selections(storage, load_config()))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
