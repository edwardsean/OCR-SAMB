import psycopg
from psycopg.rows import dict_row

from common import config


def connect(**kw):
    return psycopg.connect(config.required("DATABASE_URL"), row_factory=dict_row, **kw)
