import os
import pika

# Queue names are the vocabulary of the architecture diagram.
Q_PAGES = "q.pages"          # one message per page (fan-out)
Q_GROUP = "q.group"          # one message per batch, fired at N of N pages (fan-in)
Q_PAGES_DLQ = "q.pages.dlq"  # pages that failed after retries
Q_LESSONS = "q.lessons"      # vlm-first: wake-ups for the teacher (a label made a lesson; a context was approved)


def connect():
    params = pika.URLParameters(os.environ["AMQP_URL"])
    params.heartbeat = 600   # a page can take ~30 s; keep the connection alive through it
    return pika.BlockingConnection(params)


def declare(ch):
    ch.queue_declare(queue=Q_PAGES_DLQ, durable=True)
    ch.queue_declare(queue=Q_PAGES, durable=True,
                     arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": Q_PAGES_DLQ})
    ch.queue_declare(queue=Q_GROUP, durable=True)


def wake_teacher(reason):
    """Tell vf-teacher there may be work. The message is only a wake-up: staging.lesson is the state, so several
    wake-ups for the same work are harmless. Publish AFTER the database commit, or the teacher may look too early."""
    import json
    conn = connect()
    try:
        ch = conn.channel()
        ch.queue_declare(queue=Q_LESSONS, durable=True)
        ch.basic_publish("", Q_LESSONS, json.dumps({"reason": reason}).encode(),
                         pika.BasicProperties(delivery_mode=2, content_type="application/json"))
    finally:
        conn.close()
