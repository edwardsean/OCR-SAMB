from minio import Minio

from common import config


def client():
    return Minio(config.required("MINIO_ENDPOINT"),
                 access_key=config.required("MINIO_ROOT_USER"),
                 secret_key=config.required("MINIO_ROOT_PASSWORD"),
                 secure=config.MINIO_SECURE)


def bucket():
    return config.MINIO_BUCKET


def ensure_bucket(c=None):
    c = c or client()
    if not c.bucket_exists(bucket()):
        c.make_bucket(bucket())
    return c
