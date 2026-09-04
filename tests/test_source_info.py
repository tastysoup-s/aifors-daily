import pytest

from src.source_info import source_info


@pytest.mark.parametrize(
    ("raw_source", "display_name", "family", "type_label"),
    (
        ("rss:biorxiv-ai4s", "bioRxiv", "preprints", "Preprint"),
        ("arxiv:arxiv-physics-earth", "arXiv", "papers", "Paper"),
        ("github:github-drug-discovery", "GitHub", "code", "Open Source"),
        (
            "rss:deepmind-blog",
            "Google DeepMind",
            "research_labs",
            "Research Lab",
        ),
    ),
)
def test_source_mapping(raw_source, display_name, family, type_label):
    info = source_info(raw_source)

    assert info.display_name == display_name
    assert info.family == family
    assert info.type_label == type_label
