"""The scheduler (service rtm-scheduler): the system's periodic jobs, which n8n's schedules ran before (the mentor,
2026-10-02: n8n struggles with thousands of records and several workers).

  intake   every INTAKE_RETRY_MINUTES   a scan received but not split yet goes back on q.intake
  notify   every NOTIFY_EVERY_MINUTES   orders that newly need a person become one notice (common/notice.py)
  sweep    every SWEEP_EVERY_MINUTES    pages still waiting for the AI go back on q.pages (worker/vf.py sweep)
  lint     every LINT_EVERY_MINUTES     knowledge a person's later correction contradicts is taken out (worker/learn.py)

  A job runs once per interval however many schedulers run: it takes its turn in staging.job_run with one UPDATE
  (last started longer ago than its interval), so two never both run it, and a restart doesn't run it again early.
  What it did, or why it failed, is kept there (the Status page shows it). A failing job never stops the others.

  python -m scheduler.serve         the service (docker compose: rtm-scheduler)
  python -m scheduler.serve once    every job that is due, now, then exit
"""
import json
import sys
import time
import traceback

from psycopg.types.json import Json

from common import config, db, health, settings

TICK_S = 30                                            # how often it looks for a job that is due


def job_intake():
    from common import intake, queue
    waiting = intake.waiting(config.INTAKE_RETRY_MINUTES)
    queue.send(queue.Q_INTAKE, [{"batch_id": b} for b in waiting])
    return {"sent": waiting}


def job_notify():
    from common import notice
    with db.connect() as c:
        n = notice.record(c)
    return {"new": bool(n), **({"id": n["id"], "text": n["text"]} if n else {})}


def job_sweep():
    from worker import vf
    return vf.sweep()


def job_lint():
    from worker import learn
    return learn.lint(show=lambda *a: None)


JOBS = {"intake": (lambda: config.INTAKE_RETRY_MINUTES, job_intake, False),     # name: (interval, run, vlm-first only)
        "notify": (lambda: config.NOTIFY_EVERY_MINUTES, job_notify, True),
        "sweep": (lambda: config.SWEEP_EVERY_MINUTES, job_sweep, True),
        "lint": (lambda: config.LINT_EVERY_MINUTES, job_lint, True)}


def claim(name, minutes):
    """This job's turn, if it is due: True for exactly one caller per interval."""
    with db.connect() as c:
        c.execute("INSERT INTO staging.job_run (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
        return c.execute("""UPDATE staging.job_run SET last_started = now(), runs = runs + 1
                             WHERE name = %s AND (last_started IS NULL OR last_started <= now() - make_interval(mins => %s))
                         RETURNING name""", (name, minutes)).fetchone() is not None


def finish(name, result=None, error=None):
    with db.connect() as c:
        c.execute("UPDATE staging.job_run SET last_finished = now(), last_result = %s, last_error = %s WHERE name = %s",
                  (Json(result, dumps=lambda o: json.dumps(o, default=str)) if result is not None else None, error, name))


def tick():
    """Every job that is due, in order. Returns {name: result or error} for the jobs that ran."""
    settings.refresh()                          # the models and keys saved on the Teknis screen
    ran = {}
    for name, (every, job, vf_only) in JOBS.items():
        if vf_only and not config.VF:
            continue
        try:
            if not claim(name, every()):
                continue
        except Exception as e:                          # the database is down: try again next tick
            print(f"scheduler: can't claim {name}: {e}", flush=True)
            continue
        t0 = time.time()
        try:
            result = job()
            finish(name, result)
            ran[name] = result
            print(f"{name}: {json.dumps(result, default=str)[:300]} in {time.time() - t0:.1f} s", flush=True)
        except Exception as e:
            traceback.print_exc()
            finish(name, error=f"{type(e).__name__}: {e}"[:500])
            ran[name] = {"error": f"{type(e).__name__}: {e}"}
    return ran


def serve():
    print("scheduler: " + ", ".join(f"{n} every {e()} min" for n, (e, _, v) in JOBS.items() if config.VF or not v),
          flush=True)
    while True:
        tick()
        time.sleep(TICK_S)


if __name__ == "__main__":
    if sys.argv[1:] == ["once"]:
        print(json.dumps(tick(), default=str, indent=2))
    else:
        health.serve("scheduler", role="periodic jobs: intake retry, needs-you notice, sweep, knowledge lint")
        serve()
