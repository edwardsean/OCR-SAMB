"""The "Matikan thinking" switch per model row (the user, 2026-10-16: "add the non enable thinking button for each step,
so it will be flexible"). On the server, qwen3.8-flash as the classification model thought before answering (~600
hidden tokens, 7 s a page) and often came back without token probabilities, so pages fell to "unsure". Pure: no
database, no model."""
import types

from common import settings
from worker import classify

MS = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
PLAN = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
GROQ = "https://api.groq.com/openai/v1"


def test_until_saved_thinking_is_off_only_for_qwen_at_alibabas_endpoints():
    assert settings.no_thinking(None, MS, "qwen-flash") and settings.no_thinking(None, PLAN, "qwen3.8-flash")
    assert not settings.no_thinking(None, PLAN, "deepseek-v4-flash")    # another model may refuse the field
    assert not settings.no_thinking(None, GROQ, "qwen/qwen3.8-27b")     # and another endpoint


def test_the_saved_switch_wins_either_way():
    assert not settings.no_thinking("on", PLAN)
    assert settings.no_thinking("off", GROQ) and settings.no_thinking("off", PLAN, "glm-5.3")


def test_a_row_carries_its_switch():
    got = {"TEXT_BASE_URL": PLAN, "TEXT_MODEL": "qwen3.8-flash", "TEXT_THINKING": "on"}
    assert settings._resolve("text", got)["no_thinking"] is False
    assert settings._resolve("classify", {"CLASSIFY_BASE_URL": PLAN, "CLASSIFY_MODEL": "qwen3.8-flash"})["no_thinking"]
    assert "TEXT_THINKING" in settings.NAMES                    # saved and read like the row's other settings


def test_the_same_model_in_two_rows_keeps_each_rows_switch(monkeypatch):
    rows = {"vision": {"kind": "vision", "ident": "plan:qwen3.7-plus", "no_thinking": False},
            "text": {"kind": "text", "ident": "plan:qwen3.7-plus", "no_thinking": True}}
    monkeypatch.setattr(settings, "EFFECTIVE", rows)
    assert settings.extra(settings.row_of("plan:qwen3.7-plus", "vision")) == {}
    assert settings.extra(settings.row_of("plan:qwen3.7-plus", "text")) == {"enable_thinking": False}
    assert settings.row_of("plan:other") is None and settings.extra(None) == {}


def test_an_image_in_a_call_means_the_vision_row():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}
    assert settings.has_image([image, {"type": "text", "text": "read"}])
    assert settings.has_image([{"role": "user", "content": [image]}])          # a list of messages (the teachers)
    assert not settings.has_image("only text") and not settings.has_image([{"role": "user", "content": "x"}])


def test_the_classification_call_sends_the_switch(monkeypatch):
    sent = {}

    def post(url, headers=None, json=None, timeout=None):
        sent.update(json)
        body = {"choices": [{"message": {"content": "2"}, "logprobs": {"content": [
            {"token": "2", "logprob": 0.0, "top_logprobs": [{"token": "2", "logprob": 0.0}]}]}}], "usage": {}}
        return types.SimpleNamespace(status_code=200, json=lambda: body, headers={}, text="")
    monkeypatch.setattr(classify.httpx, "post", post)
    monkeypatch.setattr(classify.trace, "ai_request", lambda *a, **k: None)
    monkeypatch.setattr(classify.trace, "ai_http", lambda *a, **k: None)
    out = classify.llm_ask({"page_text": "GOODS RECEIVE NOTE"}, None, PLAN, "k", "qwen3.8-flash", {"enable_thinking": False})
    assert sent["enable_thinking"] is False and sent["max_tokens"] == 1 and "choice" in out
    sent.clear()
    classify.llm_ask({"page_text": "x"}, None, GROQ, "k", "m")
    assert "enable_thinking" not in sent


def test_a_gateway_gets_its_own_headers_with_the_key():
    """opencode's Go plan answers 400 (MissingSessionID) without them (the server's change, 2026-10-16)."""
    h = settings.headers("https://opencode.ai/zen/go/v1", "k")
    assert h["Authorization"] == "Bearer k" and h["x-opencode-session"] == "samb-rtm"
    assert settings.headers(PLAN, "") == {} and settings.headers(MS, "k") == {"Authorization": "Bearer k"}
