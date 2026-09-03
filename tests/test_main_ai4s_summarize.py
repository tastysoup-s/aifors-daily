import pytest

from src.main import _parse_args


def test_summarize_ai4s_cli_accepts_db_and_limit():
    args = _parse_args([
        "summarize-ai4s",
        "--db", "data/ai4s_dev.db",
        "--limit", "3",
    ])

    assert args.command == "summarize-ai4s"
    assert args.db == "data/ai4s_dev.db"
    assert args.limit == 3


def test_summarize_ai4s_cli_rejects_non_positive_limit():
    with pytest.raises(SystemExit):
        _parse_args(["summarize-ai4s", "--limit", "0"])
