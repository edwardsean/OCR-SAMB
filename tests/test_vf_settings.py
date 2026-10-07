"""The models set ONLY on the Teknis screen "Model & kunci API": three rows, vision, text and classification, each an
endpoint, its API key and a model from that endpoint's list (the user, 2026-10-07: "only 2 api keys", "there should
not be any env fallback, it just errors if there is no env inputted in the env tab", then an instruct model for
classification in place of Jev). common/settings.py, the screen, the calls that use them, the classifier, and a page
that waits while one isn't set. No test writes to the live settings table: the running services read it within
seconds."""
import os

import pytest

from common import config, settings

needs_db = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs the database")
DS = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
ZAI = "https://api.z.ai/api/paas/v4"
SET = {"VISION_BASE_URL": DS, "VISION_MODEL": "qwen3-vl-plus", "VISION_API_KEY": "v-key",
       "TEXT_BASE_URL": ZAI, "TEXT_MODEL": "glm-4.7", "TEXT_API_KEY": "t-key",
       "CLASSIFY_BASE_URL": DS, "CLASSIFY_MODEL": "qwen-flash", "CLASSIFY_API_KEY": "c-key"}


@pytest.fixture
def lay():
    """settings._lay for a test; what was in effect is laid again after it (the modules' copies too)."""
    before = dict(settings._state["saved"])
    yield settings._lay
    settings._lay(before)


class Answer:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text = code, body, text

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


def test_a_key_is_only_ever_shown_masked():
    assert settings.masked("fake-key-abcdefghijklmnop1234") == "••••••1234"
    assert settings.masked("short") == "•••••" and settings.masked("") == ""


def test_a_known_providers_endpoint_keeps_its_short_name_so_nothing_is_read_again():
    assert settings.ident(DS, "qwen3-vl-plus") == "dashscope:qwen3-vl-plus"
    assert settings.ident(DS + "/chat/completions/", "qwen-plus") == "dashscope:qwen-plus"        # pasted either way
    assert settings.ident("https://api.example.com/v1", "m-1") == "api.example.com:m-1"           # another endpoint
    assert settings.ident("http://localhost:8000/v1", "llama3:8b") == "localhost-8000:llama3:8b"  # model after the 1st ':'
    assert settings.ident(DS, "") == settings.ident("", "qwen-plus") == ""
    assert not settings.needs_key("http://host.docker.internal:11434/v1") and settings.needs_key(DS)


def test_nothing_comes_from_env_nothing_set_means_no_model(lay):
    lay({})
    assert config.VF_AI_OCR == config.VF_AI_MAP == config.TEACHER_MODEL == config.WIKI_TEACHER_MODEL == ""
    assert config.MATCH_MODEL == config.CLASSIFY_MODEL == ""
    assert settings.missing() == ["the vision model", "the text model", "the classification model"]
    with pytest.raises(RuntimeError, match="Model & kunci API"):
        settings.endpoint("dashscope:qwen3-vl-plus")                       # no provider, no .env key to fall back on
    assert not any(hasattr(config, n) for n in ("API_KEYS", "MODEL_KEYS", "VISION_API_KEY", "DASHSCOPE_API_KEY",
                                                 "TYPESAFE_API_KEY", "JEV_MODEL"))


def test_the_teachers_and_the_matcher_use_the_two_rows(lay):
    lay(SET)
    assert settings.missing() == []
    assert config.VF_AI_OCR == config.TEACHER_MODEL == "dashscope:qwen3-vl-plus"                 # sends a page image
    assert config.VF_AI_MAP == config.WIKI_TEACHER_MODEL == config.MATCH_MODEL == "zai:glm-4.7"   # sends only text
    assert config.CLASSIFY_MODEL == "dashscope:qwen-flash"                                        # each page's type
    assert settings.key("vision") == "v-key" and settings.key("text") == "t-key" and settings.key("classify") == "c-key"
    assert settings.endpoint("dashscope:qwen3-vl-plus") == (DS, "v-key", "qwen3-vl-plus")
    assert settings.row("classify")["url"] == DS and settings.row("classify")["model"] == "qwen-flash"
    assert settings.endpoint("zai:glm-4.7") == (ZAI, "t-key", "glm-4.7")
    with pytest.raises(RuntimeError):
        settings.endpoint("zai:glm-4.7-flash")                             # neither row's model


def test_a_row_without_its_key_is_missing_unless_its_endpoint_is_local(lay):
    lay({**SET, "TEXT_API_KEY": ""})
    assert settings.missing() == ["the text model"]
    lay({**SET, "TEXT_API_KEY": "", "TEXT_BASE_URL": "http://host.docker.internal:11434/v1", "TEXT_MODEL": "qwen3"})
    assert settings.missing() == []                                       # a local endpoint needs no key
    lay({**SET, "TEXT_API_KEY": "", "TEXT_BASE_URL": config.PROVIDER_URLS["ollama"], "TEXT_MODEL": "qwen3"})
    assert config.VF_AI_MAP == "ollama:qwen3"
    lay({**SET, "CLASSIFY_API_KEY": ""})
    assert settings.missing() == ["the classification model"]


def test_the_suggested_model_is_one_this_system_was_measured_with():
    ds = ["qwen-max", "qwen-plus", "qwen3-235b-a22b-instruct-2507", "qwen3-vl-flash", "qwen3-vl-plus"]
    assert settings.recommend("vision", ds) == "qwen3-vl-plus"
    assert settings.recommend("text", ds) == "qwen3-235b-a22b-instruct-2507"
    assert settings.recommend("vision", ["openai/gpt-x", "qwen/qwen3.8-27b"]) == "qwen/qwen3.8-27b"
    assert settings.recommend("text", ["some-model"]) is None                       # unknown: a person chooses


def test_the_model_list_comes_from_the_endpoint(monkeypatch):
    import httpx
    asked = []
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: asked.append((url, headers)) or Answer(
        200, {"data": [{"id": "qwen-plus"}, {"id": "models/gemini-3.8-flash"}, {"name": "glm-4.7"}]}))
    ids, why = settings.list_models(DS + "/", "k-1")
    assert why is None and ids == ["gemini-3.8-flash", "glm-4.7", "qwen-plus"]
    assert asked == [(DS + "/models", {"Authorization": "Bearer k-1"})]
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: Answer(401))
    assert settings.list_models(DS, "bad") == ([], "the endpoint refused this key")
    assert settings.list_models("dashscope.example", "k")[1].startswith("the endpoint must start with")


def test_the_test_button_proves_the_vision_model_reads_an_image(lay, monkeypatch):
    import httpx
    lay(SET)
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: Answer(200, {"data": [{"id": "qwen3-vl-plus"}]}))
    sent, reply = [], ["4821"]

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, json))
        return Answer(200, {"choices": [{"message": {"content": reply[0]}}]})
    monkeypatch.setattr(httpx, "post", post)
    assert settings.test_row("vision") == (True, "qwen3-vl-plus works and reads images")
    url, body = sent[0]
    assert url == DS + "/chat/completions" and body["model"] == "qwen3-vl-plus"
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    reply[0] = "I can't see images"
    assert settings.test_row("vision")[0] is False
    lay({})
    assert settings.test_row("vision")[0] is False                           # not set


def test_the_reader_and_the_teachers_call_the_rows_endpoint_with_its_key(lay, monkeypatch):
    import httpx
    from common.models import openai_vlm, teacher
    lay({**SET, "VISION_BASE_URL": "https://vision.example/v1", "VISION_MODEL": "eye-1",
         "TEXT_BASE_URL": DS, "TEXT_MODEL": "qwen-plus"})
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, headers, json["model"]))
        return Answer(200, {"choices": [{"message": {"content": "{\"a\": 1}"}}], "usage": {}})
    monkeypatch.setattr(httpx, "post", post)
    openai_vlm.map_text("prompt", config.VF_AI_MAP)
    teacher.ask_text("prompt", config.WIKI_TEACHER_MODEL)
    teacher.ask(settings._probe_png(), "prompt")                          # the page-type teacher: the vision row
    assert sent == [(DS + "/chat/completions", {"Authorization": "Bearer t-key"}, "qwen-plus"),
                    (DS + "/chat/completions", {"Authorization": "Bearer t-key"}, "qwen-plus"),
                    ("https://vision.example/v1/chat/completions", {"Authorization": "Bearer v-key"}, "eye-1")]
    assert config.VF_AI_OCR == "vision.example:eye-1"                     # an unknown endpoint: named by its host


def test_a_page_waits_while_a_model_isnt_set_and_no_call_is_made(lay, monkeypatch):
    from worker import vf
    lay({**SET, "VISION_BASE_URL": "", "VISION_MODEL": ""})
    why = vf.not_set()
    assert why == "NotSet: the vision model not set: set it on Teknis → Model & kunci API"
    assert vf.retry_kind("failed", why, None) == ("limit", why)            # the waiting room, never a failure
    assert vf.blocked() == why                                             # parked pages stay parked, no page run
    monkeypatch.setattr(vf, "reserve", lambda *a: pytest.fail("no call may be counted"))
    with pytest.raises(vf.NotSet):
        vf.ai_call("transcribe", "b-test", 1, lambda: pytest.fail("no call may be made"))
    lay(SET)
    assert vf.not_set() is None


@needs_db
def test_saved_settings_reach_the_modules_that_keep_a_copy(monkeypatch):
    from worker import learn, vf
    from grouper import matching
    from common.models import teacher
    before = dict(settings._state["saved"])
    rows = {k: {"name": k, "value": v} for k, v in {**SET, "TEXT_BASE_URL": DS, "TEXT_MODEL": "test-model",
                                                     "CLASSIFY_MODEL": "qwen-turbo"}.items()}
    monkeypatch.setattr(settings, "saved", lambda c: rows)
    try:
        settings.refresh(force=True)
        assert config.VF_AI_MAP == vf.AI_MAP == learn.TEACH_MODEL == matching.AI_MODEL == "dashscope:test-model"
        assert "dashscope:test-model" in vf.CAPS and settings.key("text") == "t-key"
        assert teacher.MODEL == vf.AI_OCR == "dashscope:qwen3-vl-plus"
        assert config.CLASSIFY_MODEL == "dashscope:qwen-turbo" and settings.row("classify")["key"] == "c-key"
        assert not settings.refresh(force=True)                                 # nothing new: no hooks
    finally:
        monkeypatch.undo()
        settings._lay(before)
    assert vf.AI_MAP == config.VF_AI_MAP


@needs_db
def test_saving_a_row():
    """In a transaction rolled back: the running services never see it."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as c:
        try:
            c.execute("DELETE FROM staging.setting")                                # this transaction's own view
            settings.save_row(c, "text", ZAI + "/chat/completions", "glm-4.7", "typed-key", "test")
            got = {k: r["value"] for k, r in settings.saved(c).items()}
            assert (got["TEXT_BASE_URL"], got["TEXT_MODEL"], got["TEXT_API_KEY"]) == (ZAI, "glm-4.7", "typed-key")
            settings.save_row(c, "text", ZAI, "glm-4.7-flash", "", "test")         # same endpoint: the key stays
            assert settings.saved(c)["TEXT_API_KEY"]["value"] == "typed-key"
            settings.save_row(c, "vision", "http://host.docker.internal:11434/v1", "qwen3-vl", "", "test")   # local
            for kind, url, model, key, by, why in (
                    ("vision", DS, "qwen3-vl-plus", "", "test", "needs its API key"),
                    ("text", "api.example.com", "m", "k", "test", "must start with"),
                    ("text", ZAI, "", "k", "test", "choose a model"),
                    ("text", ZAI, "m", "k", "", "who you are"),
                    ("jev", ZAI, "m", "k", "test", "isn't a model row")):
                with pytest.raises(ValueError, match=why):
                    settings.save_row(c, kind, url, model, key, by)
            settings.save_row(c, "classify", DS, "qwen-flash", "c-key", "test")
            assert settings.saved(c)["CLASSIFY_MODEL"]["value"] == "qwen-flash"
            assert not hasattr(settings, "save")                                  # no single values: a row is saved whole
        finally:
            c.rollback()


@needs_db
def test_the_screen_says_what_isnt_set_and_shows_keys_masked(monkeypatch, lay):
    from fastapi.testclient import TestClient
    from api.app import app
    secret = "fake-secret-value-9876"
    monkeypatch.setattr(settings, "saved", lambda c: {"TEXT_API_KEY": {
        "name": "TEXT_API_KEY", "value": secret, "updated_by": "Edward", "updated_at": None}})
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)       # the stub stays out of config
    lay({})
    with TestClient(app) as tc:
        r = tc.get("/settings")
    assert r.status_code == 200 and secret not in r.text and "••••••9876" in r.text and "saved by Edward" in r.text
    assert "Not set: the vision model, the text model, the classification model" in r.text and ".env (" not in r.text
    for what in ("Vision model", "Text model", "Classification model", "Find models", "page-type teacher",
                 "knowledge teacher", "product matcher", "copies every page", "instruct (non-thinking)"):
        assert what in r.text


@needs_db
def test_the_screen_lists_an_endpoints_models_without_sending_the_key_back(monkeypatch, lay):
    from fastapi.testclient import TestClient
    from api.app import app
    got = []
    monkeypatch.setattr(settings, "list_models", lambda url, key: got.append((url, key)) or (["qwen-plus", "qwen3-vl-plus"], None))
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)
    lay({**SET, "VISION_API_KEY": "row-key"})
    with TestClient(app) as tc:
        same = tc.post("/settings/models", data={"kind": "vision", "url": "", "key": ""}).json()
        other = tc.post("/settings/models", data={"kind": "vision", "url": ZAI, "key": ""}).json()
    assert got == [(DS, "row-key"), (ZAI, "")]                                 # the row's key only on its own endpoint
    assert same["recommended"] == "qwen3-vl-plus" and same["current"] == "qwen3-vl-plus" and other["current"] is None
    assert "row-key" not in str(same)


@needs_db
def test_saving_a_row_from_the_screen(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import app
    got = []
    monkeypatch.setattr(settings, "save_row", lambda c, *a: got.append(a))
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)
    with TestClient(app) as tc:
        r = tc.post("/settings/row", data={"kind": "text", "url": DS, "model": "qwen-plus", "key": "", "by": "Edward"},
                    follow_redirects=False)
        assert tc.post("/settings/row/reset", data={"kind": "text", "by": "Edward"}).status_code in (404, 405)
    assert r.status_code == 303 and "saved" in r.headers["location"]
    assert got == [("text", DS, "qwen-plus", "", "Edward")]


@needs_db
def test_the_comparison_with_v1_is_gone():
    from fastapi.testclient import TestClient
    from api.app import TECH, app
    assert "/compare" not in [h for h, _ in TECH] and "/settings" in [h for h, _ in TECH]
    with TestClient(app) as tc:
        assert tc.get("/compare").status_code == 404


def logprob_answer(top):
    """An endpoint's answer to a one-token question: its token and the top tokens with their probabilities."""
    import math
    return Answer(200, {"choices": [{"message": {"content": top[0][0]}, "logprobs": {"content": [
        {"token": top[0][0], "logprob": math.log(top[0][1]),
         "top_logprobs": [{"token": t, "logprob": math.log(p)} for t, p in top]}]}}],
        "usage": {"prompt_tokens": 460, "completion_tokens": 1}})


def test_the_classifier_reads_how_sure_it_is_from_the_endpoint_not_from_its_words(monkeypatch):
    import httpx
    from worker import classify
    q = {"doc_type": {"type": "choice", "instructions": "Which kind of document is this page?",
                      "criteria": {"faktur_penjualan": {"what": "SAMB's invoice", "titles": ["FAKTUR PENJUALAN"]},
                                   "tanda_terima": {"what": "a goods receipt"}, "purchase_order": "a purchase order"}}}
    prompt, keys = classify.llm_prompt({"found": {"receipt_no": "123"}}, q)
    assert keys == ["faktur_penjualan", "tanda_terima", "purchase_order"]
    assert "1. faktur_penjualan: SAMB's invoice" in prompt and "printed titles: FAKTUR PENJUALAN" in prompt
    assert prompt.rstrip().endswith("(1-3) only.") and '"receipt_no": "123"' in prompt
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        return reply[0]
    monkeypatch.setattr(httpx, "post", post)
    reply = [logprob_answer([("2", 0.9), ("1", 0.06), ("3", 0.03), ("The", 0.01)])]
    a = classify.llm_ask({}, q, DS, "k", "qwen-flash")
    assert (a["choice"], a["confidence"]) == ("TTG", 0.909)                  # 0.9 of the options' 0.99
    assert a["probabilities"] == {"TTG": 0.909, "FP": 0.061, "PO": 0.03}
    assert sent[0]["max_tokens"] == 1 and sent[0]["logprobs"] is True        # one token: no reasoning
    reply[0] = Answer(200, {"choices": [{"message": {"content": "2"}}]})
    assert "no token probabilities" in classify.llm_ask({}, q, DS, "k", "qwen-flash")["error"]
    reply[0] = logprob_answer([("The", 1.0)])
    assert "not an option number" in classify.llm_ask({}, q, DS, "k", "qwen-flash")["error"]


def test_the_page_type_question_goes_to_the_classification_model(lay, monkeypatch):
    import httpx
    from worker import classify
    lay({})
    assert "isn't set" in classify.jev_ask({}, None)["skipped"]
    lay(SET)
    asked = []
    monkeypatch.setattr(httpx, "post", lambda url, headers=None, json=None, timeout=None: asked.append((url, headers))
                        or logprob_answer([("3", 0.99), ("1", 0.01)]))
    a = classify.jev_ask({}, None)
    assert a["choice"] == "PO" and a["model"] == "dashscope:qwen-flash"
    assert asked == [(DS + "/chat/completions", {"Authorization": "Bearer c-key"})]
    assert classify.ledger_meta(a, 300) == ("dashscope", {"model": "dashscope:qwen-flash", "ms": 300,
                                                          "tokens_in": 460, "tokens_out": 1})


def test_the_classification_rows_test_needs_probabilities(lay, monkeypatch):
    import httpx
    lay(SET)
    monkeypatch.setattr(settings, "refresh", lambda force=False: False)
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: Answer(200, {"data": [{"id": "qwen-flash"}]}))
    reply = [logprob_answer([("2", 1.0)])]
    monkeypatch.setattr(httpx, "post", lambda url, headers=None, json=None, timeout=None: reply[0])
    ok, said = settings.test_row("classify")
    assert ok and "gives probabilities" in said
    reply[0] = Answer(200, {"choices": [{"message": {"content": "2"}}]})
    ok, said = settings.test_row("classify")
    assert not ok and "no token probabilities" in said
