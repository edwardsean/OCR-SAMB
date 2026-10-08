"""Dependency probes shared by every Python service, plus a tiny /health server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config, db, queue, storage


def probe_postgres():
    with db.connect(connect_timeout=3) as c:
        n = c.execute("select count(*) as n from information_schema.tables "
                      "where table_schema in ('satellite','staging')").fetchone()["n"]
    return {"ok": True, "detail": f"{n} tables"}


def probe_rabbitmq():
    conn = queue.connect()
    try:
        ch = conn.channel()
        queue.declare(ch)
        depth = ch.queue_declare(queue=queue.Q_PAGES, passive=True).method.message_count
        return {"ok": True, "detail": f"{queue.Q_PAGES} depth {depth}"}
    finally:
        conn.close()


def probe_minio():
    c = storage.ensure_bucket()
    return {"ok": True, "detail": f"bucket '{storage.bucket()}' ready" if c else ""}


PROBES = {"postgres": probe_postgres, "rabbitmq": probe_rabbitmq, "minio": probe_minio}


def run_probes(names=PROBES):
    out = {}
    for name in names:
        try:
            out[name] = PROBES[name]()
        except Exception as e:  # report, don't crash the service
            out[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}"[:200]}
    return out


def serve(service_name, port=None, role=""):
    """Background /health endpoint the API's Status page polls: this service is up and can reach its dependencies.
    port: HEALTH_PORT (8080). Also names the process for the trace (common/trace.py)."""
    from common import trace
    trace.named(service_name)
    port = int(port or config.HEALTH_PORT)
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            deps = run_probes()
            body = json.dumps({"service": service_name, "role": role,
                               "ok": all(d["ok"] for d in deps.values()), "deps": deps}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    t = threading.Thread(target=HTTPServer(("0.0.0.0", port), H).serve_forever, daemon=True)
    t.start()
    return t
