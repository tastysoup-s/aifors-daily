import pytest

from src.main import _parse_args


def test_analyze_cli_accepts_db_and_limit():
    args = _parse_args([
        "analyze",
        "--db", "data/ai4s_dev.db",
        "--limit", "10",
    ])

    assert args.command == "analyze"
    assert args.db == "data/ai4s_dev.db"
    assert args.limit == 10


def test_analyze_cli_rejects_non_positive_limit():
    with pytest.raises(SystemExit):
        _parse_args(["analyze", "--limit", "0"])
