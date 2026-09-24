import os
import psycopg
from psycopg.rows import dict_row


def connect(**kw):
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, **kw)
