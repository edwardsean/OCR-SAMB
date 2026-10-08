"""vf-grouper: grouping and the bundle checks, off the page workers (vlm-first). Consumes q.group.

  A page worker sends a wake-up after every page it reads ({"batch_id"}). Wake-ups are only wake-ups: the pages are
  the state. The ones waiting together are taken as one, so a 288-page upload doesn't regroup 288 times in a row:
  each batch is grouped once per round (group.run, serialised per batch by its own lock), and the round's messages
  are acknowledged after it, so a crash leaves them to be taken again.

  After grouping, a page whose knowledge (read-then-map, worker/learn.py) was chosen for another customer than its
  order's is done again with its order's customer, and the batch regrouped once more.

  After grouping, the pages a bundle's check asked to look again (crosscheck.ask_again) go back to q.pages. Only
  pages that are 'read' are sent: a page with a ticket (on q.pages or in the waiting room) keeps its one. A page
  whose look-again failed waits under another reason, so a failing call can't bounce between the two.

  python -m grouper.serve          the service (docker compose: vf-grouper)
"""
import json
import time
import traceback

from common import health, queue, settings, trace

POLL_S = 1.0


def dispatch(bid):
    """Send back to the page workers the pages of this batch that a bundle asked to look again."""
    from grouper.crosscheck import ASK_WAIT
    from worker import vf
    return vf.enqueue(bid, vf.waiting(bid), waits=ASK_WAIT, why="an order asked the AI to look again")


def round_(batches):
    """One round: group each batch once, then send what its bundles asked. A failure in one batch never stops the
    others (the next wake-up tries again)."""
    from grouper import group
    settings.refresh()                          # the models and keys saved on the Teknis screen
    done = {}
    for bid in batches:
        try:
            t0 = time.time()
            with trace.span("group", batch=bid) as sp:
                res = group.run(bid) or {}
                sp.note(orders=sorted(res.get("bundles") or {}) or None,
                        waiting=sum(1 for d in res.get("documents") or [] if d.get("hold")) or None)
                try:                                       # read-then-map 2c: knowledge chosen for another customer
                    from worker import learn
                    redone = learn.after_grouping(bid, show=lambda *a: None)
                    if redone:
                        print(f"{bid}: knowledge done again for its order's customer on {sorted(redone)}", flush=True)
                        sp.note(knowledge_redone=sorted(redone))
                        group.run(bid)
                except Exception:
                    traceback.print_exc()
                sent = dispatch(bid)
                sp.note(look_again_sent=sent or None)
            done[bid] = sent
            print(f"{bid}: grouped in {time.time() - t0:.1f} s" + (f" · look again sent for pages {sent}" if sent else ""),
                  flush=True)
        except Exception:
            traceback.print_exc()
    return done


def take(ch):
    """Every wake-up waiting now: (batches in arrival order, the last delivery tag), or ([], None)."""
    batches, last = [], None
    while True:
        method, _, body = ch.basic_get(queue.Q_GROUP)
        if not method:
            return batches, last
        last = method.delivery_tag
        try:
            bid = json.loads(body)["batch_id"]
        except Exception:
            continue                                   # not a wake-up: acknowledged with the round, never retried
        if bid not in batches:
            batches.append(bid)


def serve():
    while True:
        try:
            conn = queue.connect()
            ch = conn.channel()
            queue.declare(ch)
            queue.declare_wait(ch)
            print("vf-grouper consuming", queue.Q_GROUP, flush=True)
            while True:
                batches, last = take(ch)
                if last is None:
                    conn.sleep(POLL_S)                 # keeps the connection's heartbeats while idle
                    continue
                round_(batches)
                ch.basic_ack(last, multiple=True)
        except Exception as e:
            print("vf-grouper reconnecting:", e, flush=True)
            time.sleep(3)


if __name__ == "__main__":
    health.serve("grouper", role="vlm-first: q.group → group the batch → bundle checks → pages to look again")
    serve()
