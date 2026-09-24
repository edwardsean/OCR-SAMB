import os
from minio import Minio


def client():
    return Minio(os.environ["MINIO_ENDPOINT"],
                 access_key=os.environ["MINIO_ROOT_USER"],
                 secret_key=os.environ["MINIO_ROOT_PASSWORD"],
                 secure=False)


def bucket():
    return os.environ.get("MINIO_BUCKET", "scans")


def ensure_bucket(c=None):
    c = c or client()
    if not c.bucket_exists(bucket()):
        c.make_bucket(bucket())
    return c
