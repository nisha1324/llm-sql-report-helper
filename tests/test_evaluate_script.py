from types import SimpleNamespace

import pytest

from scripts.evaluate import require_key, run_section, write_report
from sqlhelper.claude import ClaudeConfig, Usage


def test_claude_run_stops_without_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(SystemExit, match="Nothing was sent"):
        require_key(tmp_path / ".env")  # no .env at all
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n")
    with pytest.raises(SystemExit):
        require_key(tmp_path / ".env")  # .env copied from the example but not filled in


def test_key_from_env_file_or_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=from-file\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    require_key(env)
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "from-file"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    require_key(env)
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_run_section_only_for_api_backends():
    assert run_section(SimpleNamespace(name="offline")) == []
    b = SimpleNamespace(name="claude", config=ClaudeConfig("claude-x", 256, 1, 1.0, 5.0),
                        usage=Usage(calls=80, input_tokens=130_000, output_tokens=12_000, retries=3, fixed_by_retry=2))
    text = "\n".join(run_section(b))
    assert "Model `claude-x`" in text and "| API calls" in text and "$0.19" in text
    assert "\n".join(run_section(b)) in write_report([], "claude", run_section(b))
