"""Models and API keys set from the Teknis screen "Model & kunci API" (2026-10-06; the mentor: the image OCR, the text
model and the teachers, with their keys, set from the UI). common/settings.py, the screen, and the teacher's
provider:model. No test writes to the live settings table: the running services read it within seconds."""
import os

import pytest

from common import config, settings

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")


def test_a_key_is_only_ever_shown_masked():
    assert settings.masked("fake-key-abcdefghijklmnop1234") == "••••••1234"
    assert settings.masked("short") == "•••••" and settings.masked("") == ""


def test_a_model_setting_is_provider_and_model():
    assert settings.check_model("VF_AI_OCR", "gemini") is None
    assert settings.check_model("VF_AI_MAP", "dashscope:qwen-plus") is None
    assert settings.check_model("VF_AI_MAP", "") is None                         # empty: back to .env
    assert "provider:model" in settings.check_model("VF_AI_MAP", "qwen-plus")
    assert "provider:model" in settings.check_model("VF_AI_MAP", "nowhere:model")
    assert settings.check_model("WIKI_TEACHER_MODEL", "glm-4.7-flash") is None    # a teacher's bare name is Z.ai's
    assert settings.spec_of("WIKI_TEACHER_MODEL", "glm-4.7-flash") == "zai:glm-4.7-flash"
    assert settings.key_for("VF_AI_MAP", "dashscope:qwen-plus") == "DASHSCOPE_API_KEY"
    assert settings.key_for("TEACHER_MODEL", "glm-4.6v-flash") == "ZAI_API_KEY"
    assert settings.key_for("VF_AI_OCR", "gemini") == "GEMINI_API_KEY"


def test_the_teacher_takes_a_bare_zai_name_or_provider_and_model():
    from common.models import teacher
    assert teacher.provider_of("glm-4.7-flash") == "zai" and teacher.provider_of("dashscope:qwen-max") == "dashscope"
    url, key, name = teacher._where("dashscope:qwen3-max")
    assert url.endswith("/chat/completions") and "dashscope" in url and key == "DASHSCOPE_API_KEY" and name == "qwen3-max"
    assert teacher._where("glm-4.7-flash")[1:] == ("ZAI_API_KEY", "glm-4.7-flash")
    with pytest.raises(RuntimeError):
        teacher._where("nowhere:model")


@needs_db
def test_saved_settings_lay_over_env_and_reach_the_modules_that_keep_a_copy(monkeypatch):
    from worker import learn, vf
    rows = {"VF_AI_MAP": {"name": "VF_AI_MAP", "value": "dashscope:test-model"},
            "ZAI_API_KEY": {"name": "ZAI_API_KEY", "value": "zai-test-key-0000"}}
    monkeypatch.setattr(settings, "saved", lambda c: rows)
    try:
        assert settings.refresh(force=True)
        assert config.VF_AI_MAP == vf.AI_MAP == "dashscope:test-model" and "dashscope:test-model" in vf.CAPS
        assert config.API_KEYS["ZAI_API_KEY"] == config.ZAI_API_KEY == "zai-test-key-0000"
        assert learn.TEACH_MODEL == config.WIKI_TEACHER_MODEL                   # untouched: still .env's
        assert not settings.refresh(force=True)                                 # nothing new: no hooks
    finally:
        monkeypatch.setattr(settings, "saved", lambda c: {})
        settings.refresh(force=True)                                            # back to .env
    assert config.VF_AI_MAP == vf.AI_MAP == settings.BASE["VF_AI_MAP"]
    assert config.API_KEYS["ZAI_API_KEY"] == settings.BASE["ZAI_API_KEY"]


@needs_db
def test_saving_writes_one_row_and_empty_removes_it():
    """In a transaction rolled back: the running services never see it."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as c:
        try:
            settings.save(c, "MATCH_MODEL", "glm-4.7-flash", "test")
            assert settings.saved(c)["MATCH_MODEL"]["value"] == "glm-4.7-flash"
            settings.save(c, "MATCH_MODEL", "zai:glm-4.6", "test")
            assert settings.saved(c)["MATCH_MODEL"]["value"] == "zai:glm-4.6"
            settings.save(c, "MATCH_MODEL", "", "test")
            assert "MATCH_MODEL" not in settings.saved(c)
            for name, value, by in (("VF_AI_MAP", "qwen-plus", "test"), ("DATABASE_URL", "x", "test"),
                                    ("VF_AI_MAP", "dashscope:x", "")):
                with pytest.raises(ValueError):
                    settings.save(c, name, value, by)
        finally:
            c.rollback()


@needs_db
def test_the_screen_shows_keys_masked_and_where_each_value_comes_from(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import app
    secret = "fake-secret-value-9876"
    monkeypatch.setattr(settings, "saved", lambda c: {"GROQ_API_KEY": {
        "name": "GROQ_API_KEY", "value": secret, "updated_by": "Edward", "updated_at": None}})
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)       # the stub stays out of config
    with TestClient(app) as tc:
        r = tc.get("/settings")
    assert r.status_code == 200 and secret not in r.text and "••••••9876" in r.text
    for what in ("Image OCR", "Text model", "Teacher", "VF_AI_OCR", "VF_AI_MAP", "WIKI_TEACHER_MODEL", "DASHSCOPE_API_KEY"):
        assert what in r.text


@needs_db
def test_saving_from_the_screen_and_a_refused_value(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import app
    got = []
    monkeypatch.setattr(settings, "save", lambda c, name, value, by: got.append((name, value, by)))
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)
    with TestClient(app) as tc:
        r = tc.post("/settings", data={"name": "VF_AI_MAP", "value": "dashscope:qwen3-max", "by": "Edward"},
                    follow_redirects=False)
        assert r.status_code == 303 and "saved" in r.headers["location"]
    assert got == [("VF_AI_MAP", "dashscope:qwen3-max", "Edward")]


def test_a_key_is_checked_by_the_providers_model_list(monkeypatch):
    import httpx

    class Answer:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    monkeypatch.setattr(settings, "refresh", lambda force=False: False)
    monkeypatch.setitem(config.API_KEYS, "MISTRAL_API_KEY", "m-key")
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: Answer(200, {"data": [{}, {}]}))
    assert settings.check_key("MISTRAL_API_KEY") == (True, "accepted (2 models available)")
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: Answer(401, {}))
    assert settings.check_key("MISTRAL_API_KEY")[0] is False
    monkeypatch.setitem(config.API_KEYS, "MISTRAL_API_KEY", "")
    assert settings.check_key("MISTRAL_API_KEY") == (False, "no key set")


@needs_db
def test_the_comparison_with_v1_is_gone():
    from fastapi.testclient import TestClient
    from api.app import TECH, app
    assert "/compare" not in [h for h, _ in TECH] and "/settings" in [h for h, _ in TECH]
    with TestClient(app) as tc:
        assert tc.get("/compare").status_code == 404
