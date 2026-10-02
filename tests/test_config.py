"""Settings come from one place (common/config.py; the user, 2026-10-02: "make a config file so that files can just
take environments from there"): no other backend module reads the environment, every setting is listed for people
in .env.example, and a missing required setting says which one."""
import importlib
import os
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent          # the container's /app, or the repo
CODE = HERE if (HERE / "common").is_dir() else HERE / "services"
EXAMPLE = next((p for p in (HERE / ".env.example", HERE.parent / ".env.example") if p.is_file()), None)


def test_only_the_config_file_reads_the_environment():
    hits = []
    for f in CODE.rglob("*.py"):
        if f.name == "config.py" and f.parent.name == "common" or "tests" in f.parts or "testdata" in f.parts:
            continue
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if re.search(r"\bos\.(environ|getenv)\b", line):
                hits.append(f"{f.relative_to(CODE)}:{i}: {line.strip()[:90]}")
    assert not hits, hits


def test_every_setting_is_listed_for_people():
    if not EXAMPLE:
        pytest.skip(".env.example isn't mounted here")
    listed = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", EXAMPLE.read_text(), re.M))
    read = set(re.findall(r'_(?:str|int|float|bool|url)\("([A-Z0-9_]+)"', (CODE / "common" / "config.py").read_text()))
    read |= {"GROQ_API_KEY", "OPENROUTER_API_KEY", "ZAI_API_KEY", "MISTRAL_API_KEY", "DASHSCOPE_API_KEY", "GEMINI_API_KEY"}
    # set by docker-compose.yml from other settings, never by hand
    composed = {"DATABASE_URL", "MAIN_DATABASE_URL", "AMQP_URL"}
    assert not (read - listed - composed), sorted(read - listed - composed)


def test_a_missing_required_setting_says_which(monkeypatch):
    from common import config
    monkeypatch.setattr(config, "DATABASE_URL", "")
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        config.required("DATABASE_URL")


def test_values_are_typed(monkeypatch):
    from common import config
    monkeypatch.setenv("MINIO_SECURE", "true")
    monkeypatch.setenv("VF_MAP_TWICE", "0")
    monkeypatch.setenv("HEALTH_PORT", "9090")
    monkeypatch.setenv("WEB_URL", "http://web.example/")
    monkeypatch.setenv("GEMINI_FALLBACK_MODELS", "a, b,,c")
    try:
        c = importlib.reload(config)
        assert c.MINIO_SECURE is True and c.VF_MAP_TWICE is False and c.HEALTH_PORT == 9090
        assert c.WEB_URL == "http://web.example" and c.GEMINI_FALLBACK_MODELS == ["a", "b", "c"]
    finally:
        monkeypatch.undo()
        importlib.reload(config)                                  # back to this container's own settings
