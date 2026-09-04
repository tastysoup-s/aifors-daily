from dataclasses import dataclass


SOURCE_FAMILY_LABELS = {
    "papers": "Papers",
    "preprints": "Preprints",
    "code": "Open Source",
    "research_labs": "Research Labs",
    "community": "Community",
    "other": "Other",
}

SOURCE_FAMILY_ORDER = (
    "papers",
    "preprints",
    "code",
    "research_labs",
    "community",
    "other",
)


@dataclass(frozen=True)
class SourceInfo:
    key: str
    display_name: str
    family: str
    type_label: str


def source_info(raw_source: str) -> SourceInfo:
    """Map a stored source id to stable, user-facing provenance."""
    value = raw_source.casefold()
    if value.startswith("arxiv:"):
        return SourceInfo("arxiv", "arXiv", "papers", "Paper")
    if value.startswith("rss:biorxiv"):
        return SourceInfo("biorxiv", "bioRxiv", "preprints", "Preprint")
    if value.startswith("rss:medrxiv"):
        return SourceInfo("medrxiv", "medRxiv", "preprints", "Preprint")
    if value.startswith("github:"):
        return SourceInfo("github", "GitHub", "code", "Open Source")
    if value == "rss:deepmind-blog":
        return SourceInfo(
            "google-deepmind", "Google DeepMind", "research_labs", "Research Lab"
        )
    if value == "rss:microsoft-research":
        return SourceInfo(
            "microsoft-research", "Microsoft Research", "research_labs", "Research Lab"
        )
    if value == "rss:nvidia-dev-blog":
        return SourceInfo("nvidia", "NVIDIA", "research_labs", "Research Lab")
    if value == "rss:apple-ml-research":
        return SourceInfo(
            "apple-ml-research", "Apple ML Research", "research_labs", "Research Lab"
        )
    if value.startswith("hackernews:"):
        return SourceInfo("hacker-news", "Hacker News", "community", "Community")

    display_name = raw_source.split(":", 1)[-1] or "Unknown Source"
    key = "-".join(display_name.casefold().replace("_", "-").split())
    return SourceInfo(key, display_name, "other", "Source")


def source_info_from_config(source: dict[str, object]) -> SourceInfo:
    return source_info(f"{source.get('type', '')}:{source.get('name', '')}")
