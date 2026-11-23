"""Create S3 buckets on SeaweedFS (all-in-one S3 gateway)."""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

from minio import Minio
from minio.error import S3Error

# dvc バケットは廃止（design-seaweed-lakefs.md）
BUCKETS = ("raw", "curated", "datasets", "models", "packages", "mlflow", "lakefs")


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status < 500
    except Exception:
        return False


def main() -> None:
    endpoint = os.environ.get("SEAWEED_S3_ENDPOINT", "seaweed:8333")
    access = os.environ["MINIO_ROOT_USER"]
    secret = os.environ["MINIO_ROOT_PASSWORD"]
    secure = os.environ.get("MINIO_SECURE", "false").lower() in {"1", "true", "yes"}
    region = os.environ.get("S3_REGION", "us-east-1")

    # S3 ゲートウェイ待ち（auth 無しの素の GET は 403 でも到達扱い）
    health = f"http://{endpoint}/"
    for _ in range(60):
        try:
            urllib.request.urlopen(health, timeout=2)
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit(f"seaweed s3 not ready: {endpoint}")

    mc = Minio(endpoint, access_key=access, secret_key=secret, secure=secure, region=region)
    for name in BUCKETS:
        try:
            if not mc.bucket_exists(name):
                mc.make_bucket(name)
                print(f"bucket created: {name}")
            else:
                print(f"bucket exists: {name}")
        except S3Error as exc:
            raise SystemExit(f"bucket {name} failed: {exc}") from exc
    print("seaweed_init ok")


if __name__ == "__main__":
    main()
