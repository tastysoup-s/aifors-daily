from pathlib import Path

import yaml


WORKFLOW_PATH = Path(".github/workflows/daily.yml")


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_yaml_is_valid_and_runs_once_daily():
    data = yaml.load(_workflow_text(), Loader=yaml.BaseLoader)

    assert data["name"] == "ai4s-daily"
    assert data["on"]["schedule"] == [{"cron": "30 0 * * *"}]
    assert "workflow_dispatch" in data["on"]


def test_workflow_uses_one_production_database_path():
    text = _workflow_text()

    assert "AI4S_DB: data/ai4s.db" in text
    assert "data/ai4s_dev.db" not in text
    assert "data/ai_daily.db" not in text
    assert text.count('--db "$AI4S_DB"') == 6


def test_workflow_pipeline_order_is_safe():
    text = _workflow_text()
    commands = [
        "src.main fetch",
        "src.main analyze",
        "src.main summarize-ai4s",
        "src.main generate-daily",
        "src.main generate-weekly",
        "src.main render-ai4s",
    ]

    positions = [text.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "test -s docs/index.html" in text


def test_weekly_generation_is_limited_to_wednesday_and_sunday_utc():
    text = _workflow_text()

    assert 'weekday="$(date -u +%u)"' in text
    assert '[ "$weekday" = "3" ] || [ "$weekday" = "7" ]' in text
    assert "Asia/Shanghai" not in text


def test_workflow_uses_only_required_secrets_and_data_branch_persistence():
    text = _workflow_text()

    assert "secrets.DEEPSEEK_API_KEY" in text
    assert "secrets.GITHUB_TOKEN" in text
    for unused in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "MOONSHOT_API_KEY",
    ):
        assert unused not in text
    assert "origin data" in text
    assert "data-branch/data/ai4s.db" in text
    assert "git add data/ai4s.db docs" in text
