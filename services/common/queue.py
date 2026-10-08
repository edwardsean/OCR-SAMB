import pika

from common import config

# Queue names are the vocabulary of the architecture diagram.
Q_INTAKE = "q.intake"        # one message per uploaded scan: the intake worker splits it into pages
Q_PAGES = "q.pages"          # one message per page (fan-out)
Q_GROUP = "q.group"          # one message per batch, fired at N of N pages (fan-in)
Q_PAGES_DLQ = "q.pages.dlq"  # pages that failed after retries
Q_LESSONS = "q.lessons"      # vlm-first: wake-ups for the teacher (a label made a lesson; a context was approved)
Q_WAIT = "q.pages.wait"      # vlm-first: pages parked until the AI OCR can be asked again; each goes back to q.pages
WAIT_MS = int(config.VF_WAIT_MINUTES * 60_000)   # by itself after WAIT_MS (RabbitMQ's TTL)


def connect():
    params = pika.URLParameters(config.required("AMQP_URL"))
    params.heartbeat = 600   # a page can take ~30 s; keep the connection alive through it
    return pika.BlockingConnection(params)


def declare(ch):
    ch.queue_declare(queue=Q_INTAKE, durable=True)
    ch.queue_declare(queue=Q_PAGES_DLQ, durable=True)
    ch.queue_declare(queue=Q_PAGES, durable=True,
                     arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": Q_PAGES_DLQ})
    ch.queue_declare(queue=Q_GROUP, durable=True)


def declare_wait(ch):
    """The waiting room (vlm-first): a message sits WAIT_MS, then RabbitMQ dead-letters it back to q.pages. One TTL
    for the whole queue, so no message can hold up a shorter one behind it; a page that must wait longer (the daily
    limit) is parked again by the worker's check before any work, which costs one query."""
    ch.queue_declare(queue=Q_WAIT, durable=True,
                     arguments={"x-message-ttl": WAIT_MS, "x-dead-letter-exchange": "",
                                "x-dead-letter-routing-key": Q_PAGES})


def send(queue_name, messages):
    """Publish JSON messages, persistent. Publish AFTER the database commit that the messages describe."""
    import json
    if not messages:
        return
    conn = connect()
    try:
        ch = conn.channel()
        declare(ch)
        declare_wait(ch)
        for m in messages:
            ch.basic_publish("", queue_name, json.dumps(m).encode(),
                             pika.BasicProperties(delivery_mode=2, content_type="application/json"))
    finally:
        conn.close()


def wake_grouper(bid, reason="a page was read"):
    """Tell the grouper a batch may regroup (vlm-first: vf-grouper). Only a wake-up: the pages are the state, and
    wake-ups for one batch that arrive together are handled as one."""
    send(Q_GROUP, [{"batch_id": bid, "reason": reason}])


def wake_teacher(reason):
    """Tell vf-teacher there may be work. The message is only a wake-up: staging.lesson is the state, so several
    wake-ups for the same work are harmless. Publish AFTER the database commit, or the teacher may look too early."""
    from common import trace
    if trace.is_muted():                   # a test's correction or label: never a real teacher call (2026-10-08: the
        return                             # test suite woke it and it paid the text model for a test's lesson)
    import json
    conn = connect()
    try:
        ch = conn.channel()
        ch.queue_declare(queue=Q_LESSONS, durable=True)
        ch.basic_publish("", Q_LESSONS, json.dumps({"reason": reason}).encode(),
                         pika.BasicProperties(delivery_mode=2, content_type="application/json"))
    finally:
        conn.close()
