"""Every setting the backend takes from the environment, in one place. This is the only module that reads
os.environ: the API, the workers, the grouper and the publisher import their settings from here.

Each setting has its type and its default here, and .env.example lists them for people. The values are read once,
when a service starts: after changing .env, recreate the service (docker compose up -d --force-recreate <service>).
A required setting that is missing raises a clear error where it is first used, never at import, so a module that
doesn't need the database or the bucket can still be imported (and tested) without them."""
import os

def _str(name, default=""):
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def _int(name, default):
    return int(_str(name, str(default)))


def _float(name, default):
    return float(_str(name, str(default)))


def _bool(name, default):
    return _str(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


def _url(name, default=""):
    return _str(name, default).rstrip("/")


def env(name, default=""):
    """A setting read where it is used, for the few that must not live here: the grading settings stay with the
    grader (api/app.py), so the pipeline's code never names what it is graded against (the isolation test)."""
    return _str(name, default)


def required(name):
    """A setting the code can't run without (a database, the queue, the bucket's keys): its value, or a clear error."""
    v = globals().get(name)
    if not v:
        raise RuntimeError(f"{name} is not set: add it to .env (see .env.example)")
    return v


# ================================================================= servers
DATABASE_URL = _str("DATABASE_URL")                  # this pipeline's database (required)
MAIN_DATABASE_URL = _str("MAIN_DATABASE_URL")        # v1's database, read-only (cloning, the zoomed check)
AMQP_URL = _str("AMQP_URL")                          # RabbitMQ, with its vhost (required)

MINIO_ENDPOINT = _str("MINIO_ENDPOINT")              # host:port (required)
MINIO_ROOT_USER = _str("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = _str("MINIO_ROOT_PASSWORD")
MINIO_SECURE = _bool("MINIO_SECURE", False)          # TLS (S3, Alibaba OSS)
MINIO_BUCKET = _str("MINIO_BUCKET", "scans")

# ================================================================= this stack
PIPELINE = _str("PIPELINE", "v1")                    # "vlm-first", or v1's
VF = PIPELINE == "vlm-first"
STORAGE_PREFIX = _str("STORAGE_PREFIX", "")          # where this stack writes in the bucket: "" for v1, "vf/", "rtm/"
SERVICE_PREFIX = _str("SERVICE_PREFIX", "vf")        # its services' names (the Status page probes them)
HEALTH_PORT = _int("HEALTH_PORT", 8080)              # where each worker answers /health
RENDER_WORKERS = _int("RENDER_WORKERS", 6)           # pages rendered at once when a scan is split (CPU cores)

# ================================================================= addresses
WEB_URL = _url("WEB_URL")                            # the web app people work in (frontend/)
OLLAMA_URL = _url("OLLAMA_URL", "http://host.docker.internal:11434")
RABBITMQ_CONSOLE_URL = _url("RABBITMQ_CONSOLE_URL", "http://localhost:15672")   # links on the Status page,
MINIO_CONSOLE_URL = _url("MINIO_CONSOLE_URL", "http://localhost:9001")          # opened from a browser

# ================================================================= the scheduler's jobs (services/scheduler), in minutes
INTAKE_RETRY_MINUTES = _int("INTAKE_RETRY_MINUTES", 10)   # a scan received but not split: back on q.intake
NOTIFY_EVERY_MINUTES = _int("NOTIFY_EVERY_MINUTES", 5)    # orders that newly need a person become a notice
SWEEP_EVERY_MINUTES = _int("SWEEP_EVERY_MINUTES", 180)    # pages still waiting for the AI go back on the queue
LINT_EVERY_MINUTES = _int("LINT_EVERY_MINUTES", 360)      # knowledge a later correction contradicts: taken out
TRACE_KEEP_DAYS = _int("TRACE_KEEP_DAYS", 90)             # the trace (Jejak, Metrik) keeps this many days
PAYLOAD_KEEP_DAYS = _int("PAYLOAD_KEEP_DAYS", 14)         # each AI call's exact request and response (~100 KB a page)

# ================================================================= models
# The vision model, the text model and the classification model are set ONLY on the Teknis screen "Model & kunci
# API" (common/settings.py, the table staging.setting), never here or in .env (the user, 2026-10-07). settings.py lays
# them into these names, which the rest of the code reads: empty until it has read the table, and a page waits while
# one isn't set (worker/vf.py NotSet).
VF_AI_OCR = VF_AI_MAP = ""                           # provider:model of the vision model and of the text model
TEACHER_MODEL = WIKI_TEACHER_MODEL = MATCH_MODEL = ""   # the page-type teacher: vision; knowledge teacher, matcher: text
CLASSIFY_MODEL = ""                                  # provider:model of the classification model (replaced Jev)
JEV_AT_ONCE = _int("JEV_AT_ONCE", 6)                 # replay calls asked at once when the page-type teacher tests a change

VF_READER = _str("VF_READER", "one_step")            # two_step: transcribe everything, then map
VF_MAP_TWICE = _bool("VF_MAP_TWICE", True)           # map each transcript twice and merge
# calls a day this system allows itself, per model: a guard against a runaway, not a budget. A day of ~6,800 pages is
# ~7,500 vision calls (copy, look-again, store) and ~14,000 text calls (two mappings each); was 150/300 for the free
# tiers (2026-10-08: it stopped the system at ~150 pages a day)
VF_AI_OCR_DAILY_CAP = _int("VF_AI_OCR_DAILY_CAP", 20_000)
VF_AI_MAP_DAILY_CAP = _int("VF_AI_MAP_DAILY_CAP", 40_000)
# pages each page worker reads at once (worker/main.py): set on Teknis → Model & kunci API (common/settings.py
# NUMBERS), read by running workers within seconds; this is only the value until one is saved there. A worker needs
# ~1 GB at 4 pages (2026-10-08: 3 × 4 on a 3.8 GB Docker VM had one worker killed for memory, its pages paid again)
WORKER_CONCURRENCY = _int("WORKER_CONCURRENCY", 4)
WORKER_CONCURRENCY_DEFAULT = WORKER_CONCURRENCY
WORKER_CONCURRENCY_MAX = 16
# pages Tesseract reads at once per worker: it is the CPU-heavy part (~11 s, a few hundred MB on an enlarged copy of
# the page); 2026-10-08's load test ran 4 at once per worker and saturated 8 CPUs and 2.5 GB, delaying the AI calls
TESSERACT_AT_ONCE = _int("TESSERACT_AT_ONCE", 1)
VF_WAIT_MINUTES = _float("VF_WAIT_MINUTES", 15)      # a page the daily limit refused waits this long
VF_QUOTA_TZ = _str("VF_QUOTA_TZ", "America/Los_Angeles")   # the day the providers' daily quotas count in

# Known providers' addresses: a model at one of them is named provider:model (dashscope:qwen3-vl-plus), as readings
# made before the screen's two rows were, so they are never read again for it (settings.ident). Names only: where a
# model is called is the address saved on the screen.
PROVIDER_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "zai": "https://api.z.ai/api/paas/v4",
    "mistral": "https://api.mistral.ai/v1",
    "dashscope": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "ollama": OLLAMA_URL + "/v1",
}

# The older Gemini adapter (models/vlm.py), unused since the two rows: Gemini is reached through its
# OpenAI-compatible endpoint (https://generativelanguage.googleapis.com/v1beta/openai) like any other.
GEMINI_BASE_URL = _url("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_MODEL = _str("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODELS = [m.strip() for m in _str("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash").split(",")
                          if m.strip()]               # tried in order when the pinned model is overloaded

MATCH_PAUSE = _int("MATCH_PAUSE", 30)                # seconds between the matcher's calls when the text model is Z.ai's
