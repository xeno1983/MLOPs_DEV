from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path

from minio import Minio

from ugv_mlops.settings import settings

BUCKETS = ("raw", "curated", "datasets", "models", "packages", "mlflow", "lakefs", "dvc")


@lru_cache(maxsize=1)
def client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_root_user,
        secret_key=settings.minio_root_password,
        secure=settings.minio_secure,
        region=settings.s3_region,
    )


def ensure_buckets() -> None:
    mc = client()
    for name in BUCKETS:
        if not mc.bucket_exists(name):
            mc.make_bucket(name)


def put_file(bucket: str, key: str, path: Path, content_type: str = "application/octet-stream") -> str:
    mc = client()
    mc.fput_object(bucket, key, str(path), content_type=content_type)
    return f"s3://{bucket}/{key}"


def put_bytes(bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    mc = client()
    mc.put_object(bucket, key, BytesIO(data), length=len(data), content_type=content_type)
    return f"s3://{bucket}/{key}"


def get_file(bucket: str, key: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    client().fget_object(bucket, key, str(dest))
    return dest


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri}")
    rest = uri[5:]
    bucket, _, key = rest.partition("/")
    return bucket, key
