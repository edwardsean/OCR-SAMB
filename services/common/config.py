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

# ================================================================= models
# Jev (TypeSafe System One) classifies each page
TYPESAFE_API_KEY = _str("TYPESAFE_API_KEY")
TYPESAFE_URL = _url("TYPESAFE_URL", "https://api.typesafe.ai/v1/systemone")
JEV_MODEL = _str("JEV_MODEL", "jev-latest")
JEV_AT_ONCE = _int("JEV_AT_ONCE", 6)                 # replay calls asked at once when the teacher tests a change

# The AI OCR: "gemini", or provider:model for an OpenAI-compatible vision model
VF_AI_OCR = _str("VF_AI_OCR", "gemini")
VF_AI_MAP = _str("VF_AI_MAP") or VF_AI_OCR          # the text model that maps a transcript
VF_READER = _str("VF_READER", "one_step")            # two_step: transcribe everything, then map
VF_MAP_TWICE = _bool("VF_MAP_TWICE", True)           # map each transcript twice and merge
VF_AI_OCR_DAILY_CAP = _int("VF_AI_OCR_DAILY_CAP", 40 if VF_AI_OCR == "gemini" else 150)
VF_AI_MAP_DAILY_CAP = _int("VF_AI_MAP_DAILY_CAP", 300)
VF_WAIT_MINUTES = _float("VF_WAIT_MINUTES", 15)      # a page the daily limit refused waits this long
VF_QUOTA_TZ = _str("VF_QUOTA_TZ", "America/Los_Angeles")   # the day the providers' daily quotas count in

# API keys of the model providers (empty: that provider isn't used)
API_KEYS = {name: _str(name) for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "ZAI_API_KEY", "MISTRAL_API_KEY",
                                          "DASHSCOPE_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY")}
GEMINI_API_KEY = API_KEYS["GEMINI_API_KEY"]
ZAI_API_KEY = API_KEYS["ZAI_API_KEY"]

# Where each provider answers: <PROVIDER>_BASE_URL for another region or a proxy
PROVIDER_URLS = {
    "groq": _url("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    "openrouter": _url("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    "zai": _url("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4"),
    "mistral": _url("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
    "dashscope": _url("DASHSCOPE_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    "ollama": OLLAMA_URL + "/v1",
}

# Gemini, when VF_AI_OCR=gemini
GEMINI_BASE_URL = _url("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_MODEL = _str("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODELS = [m.strip() for m in _str("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.5-flash").split(",")
                          if m.strip()]               # tried in order when the pinned model is overloaded

# The teacher and the product matcher (Z.ai)
TEACHER_MODEL = _str("TEACHER_MODEL", "glm-4.6v-flash")
WIKI_TEACHER_MODEL = _str("WIKI_TEACHER_MODEL", "glm-4.7-flash")
MATCH_MODEL = _str("MATCH_MODEL", "glm-4.7-flash")
MATCH_PAUSE = _int("MATCH_PAUSE", 30)                # seconds between the matcher's calls
