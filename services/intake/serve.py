"""The intake worker (service rtm-intake): consumes q.intake, one uploaded scan per message. It splits the scan into
pages (common/intake.py split), then puts one ticket per page on q.pages (enqueue). Replaces n8n's intake workflow
(the mentor, 2026-10-02: n8n struggles with thousands of records and several workers).

  A message is acknowledged after its scan is done, so a worker that dies leaves it to be taken again. Splitting is
  safe to repeat: one worker at a time per scan, and a scan past this step is left alone. Several intake workers can
  run (one scan each at a time); a 288-page scan takes minutes, and the connection's heartbeats keep going meanwhile.

  python -m intake.serve          the service (docker compose: rtm-intake)
"""
import json
import threading
import time
import traceback

from common import health, intake, queue


def run(batch_id):
    """Split the scan, then queue its pages. Returns what happened (a failure is recorded on the scan by split)."""
    t0 = time.time()
    try:
        out = intake.split(batch_id)
        if out.get("status") == "split":            # split now, or split earlier by a worker that died before queuing
            out["queued"] = intake.enqueue(batch_id)["published"]
        print(f"{batch_id}: {out} in {time.time() - t0:.0f} s", flush=True)
        return out
    except Exception as e:
        traceback.print_exc()
        return {"batch_id": batch_id, "error": f"{type(e).__name__}: {e}"}


def serve():
    while True:
        try:
            conn = queue.connect()
            ch = conn.channel()
            queue.declare(ch)
            ch.basic_qos(prefetch_count=1)               # one scan at a time per worker
            print("intake worker consuming", queue.Q_INTAKE, flush=True)
            for method, _, body in ch.consume(queue.Q_INTAKE, inactivity_timeout=5):
                if not method:
                    continue
                try:
                    batch_id = json.loads(body)["batch_id"]
                except Exception:
                    ch.basic_ack(method.delivery_tag)   # not a scan: dropped, never retried
                    continue
                work = threading.Thread(target=run, args=(batch_id,), daemon=True)
                work.start()
                while work.is_alive():                   # the scan renders in the thread; this keeps the heartbeats
                    conn.sleep(1)
                ch.basic_ack(method.delivery_tag)
        except Exception as e:
            print("intake worker reconnecting:", e, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    health.serve("intake", role="q.intake → split the scan into pages → one ticket per page on q.pages")
    serve()
