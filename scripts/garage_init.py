"""Create S3 buckets on Garage and grant the default key read/write/owner."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BUCKETS = ("raw", "curated", "datasets", "models", "packages", "mlflow", "lakefs", "dvc")


def _req(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict | list | str]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            parsed: dict | list | str = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return exc.code, parsed


def main() -> None:
    base = os.environ.get("GARAGE_ADMIN_URL", "http://garage:3903").rstrip("/")
    token = os.environ["GARAGE_ADMIN_TOKEN"]
    key_id = os.environ["GARAGE_DEFAULT_ACCESS_KEY"]
    last_err = "admin API not ready"
    for _ in range(40):
        code, _body = _req("GET", f"{base}/v2/GetClusterHealth", token)
        if code < 500:
            break
        last_err = f"health HTTP {code}"
        time.sleep(2)
    else:
        raise SystemExit(f"garage admin not ready: {last_err}")

    for _ in range(40):
        code, _body = _req("GET", f"{base}/v2/GetKeyInfo?id={key_id}", token)
        if code < 400:
            break
        time.sleep(2)
    else:
        raise SystemExit(f"default access key not ready: {key_id}")

    for name in BUCKETS:
        code, body = _req(
            "POST",
            f"{base}/v2/CreateBucket",
            token,
            {
                "globalAlias": name,
                "localAlias": {
                    "accessKeyId": key_id,
                    "alias": name,
                    "allow": {"read": True, "write": True, "owner": True},
                },
            },
        )
        if code in (200, 201):
            print(f"bucket created: {name}")
            continue
        info_code, info = _req("GET", f"{base}/v2/GetBucketInfo?globalAlias={name}", token)
        if info_code >= 400 or not isinstance(info, dict) or not info.get("id"):
            raise SystemExit(f"create {name} failed HTTP {code}: {body}")
        allow_code, allow_body = _req(
            "POST",
            f"{base}/v2/AllowBucketKey",
            token,
            {
                "bucketId": info["id"],
                "accessKeyId": key_id,
                "permissions": {"read": True, "write": True, "owner": True},
            },
        )
        if allow_code >= 400:
            raise SystemExit(f"allow {name} failed HTTP {allow_code}: {allow_body}")
        print(f"bucket exists, key allowed: {name}")
    print("buckets ready")


if __name__ == "__main__":
    main()
