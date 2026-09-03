from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.storage import Storage


CATEGORIES = (
    ("all", "All"),
    ("biology", "Biology"),
    ("medicine", "Medicine"),
    ("chemistry", "Chemistry"),
    ("materials", "Materials"),
    ("physics", "Physics"),
    ("earth", "Earth"),
    ("general", "General"),
)

CONTENT_TYPE_LABELS = {
    "paper": "Paper",
    "model": "Model",
    "dataset": "Dataset",
    "benchmark": "Benchmark",
    "tool": "Tool",
    "project": "Project",
    "research_news": "Research News",
}


def render_ai4s_site(
    storage: Storage,
    *,
    output_dir: Path = Path("site"),
    templates_dir: Path = Path("templates"),
) -> dict[str, object]:
    """Render persisted AI4S reports without repeating report selection logic."""
    daily = storage.get_latest_daily_report()
    weekly = storage.get_latest_weekly_report()

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    template = env.get_template("ai4s_index.html.j2")
    html = template.render(
        daily=daily,
        weekly=weekly,
        categories=CATEGORIES,
        category_labels=dict(CATEGORIES),
        content_type_labels=CONTENT_TYPE_LABELS,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(html, encoding="utf-8")
    return {
        "daily_report_id": daily.id if daily else None,
        "daily_items": len(daily.items) if daily else 0,
        "weekly_report_id": weekly.id if weekly else None,
        "weekly_items": len(weekly.items) if weekly else 0,
        "output": str(output_path),
    }
