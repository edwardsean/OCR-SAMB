"""A stand-in AI endpoint for trying the pipeline with no real model and no cost (2026-10-15, written to try stopping a
batch while pages are really being read). OpenAI-compatible: POST /v1/chat/completions answers every call after
FAKE_DELAY seconds with something the pipeline accepts:
  - the classification question (it asks for logprobs): option 1, sure;
  - a page image with the copy prompt: a short copy of the page (two printed lines);
  - anything else (mapping, look-again, store question): an empty JSON object.
GET /calls says how many calls it answered, and the last ones. Set it on Teknis → Model & kunci API as the vision,
text and classification endpoint (http://<this service>:9000/v1, any key).

  python scripts/fake_ai.py                    # FAKE_DELAY=5 FAKE_PORT=9000"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = float(os.environ.get("FAKE_DELAY", "5"))
PORT = int(os.environ.get("FAKE_PORT", "9000"))
COPY = {"blocks": [{"id": "b1", "kind": "printed", "box": [40, 100, 70, 900], "text": "FAKTUR PENJUALAN"},
                   {"id": "b2", "kind": "printed", "box": [90, 100, 120, 700], "text": "Sales Order [SO] # : SOR00000000001"}]}
calls, lock = [], threading.Lock()


def answer(body):
    content = (body.get("messages") or [{}])[-1].get("content")
    parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
    image = any(p.get("type") == "image_url" for p in parts)
    text = " ".join(p.get("text") or "" for p in parts if p.get("type") == "text")
    if body.get("logprobs"):
        return "classify", "1", {"content": [{"token": "1", "logprob": 0.0, "top_logprobs": [{"token": "1", "logprob": 0.0}]}]}
    if image and "copying one scanned page" in text.lower():
        return "copy", json.dumps(COPY), None
    return ("image" if image else "text"), "{}", None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._send(200, {"data": [{"id": "fake-vision"}, {"id": "fake-text"}, {"id": "fake-classify"}]})
        with lock:
            return self._send(200, {"calls": len(calls), "last": calls[-10:]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        kind, content, logprobs = answer(body)
        started = time.time()
        time.sleep(DELAY)
        with lock:
            calls.append({"kind": kind, "model": body.get("model"), "at": round(started, 2)})
        choice = {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        if logprobs:
            choice["logprobs"] = logprobs
        self._send(200, {"id": "fake", "object": "chat.completion", "model": body.get("model"), "choices": [choice],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


if __name__ == "__main__":
    print(f"fake AI on :{PORT}, {DELAY} s a call", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
