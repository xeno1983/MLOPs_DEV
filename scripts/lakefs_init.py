"""Initialize lakeFS (Postgres では INSTALLATION_* が効かない) と ugv-datasets リポジトリ。"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

REPO = "ugv-datasets"
STORAGE_NS = "s3://lakefs/ugv-datasets"


def _req(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict | list | None]:
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            parsed = json.loads(raw.decode()) if raw else None
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode()) if raw else None
        except Exception:
            parsed = {"message": raw.decode(errors="replace")}
        return exc.code, parsed


def main() -> None:
    base = os.environ.get("LAKEFS_URL", "http://lakefs:8000").rstrip("/")
    user = os.environ.get("LAKEFS_INSTALLATION_USER_NAME", "ugv")
    access = os.environ["LAKEFS_ACCESS_KEY_ID"]
    secret = os.environ["LAKEFS_SECRET_ACCESS_KEY"]
    auth = (access, secret)

    for _ in range(60):
        code, _ = _req("GET", f"{base}/api/v1/healthcheck")
        if code < 500:
            break
        time.sleep(2)
    else:
        raise SystemExit(f"lakefs not ready: {base}")

    code, listed = _req("GET", f"{base}/api/v1/repositories", auth=auth)
    if code in (401, 403):
        _, setup_state = _req("GET", f"{base}/api/v1/setup_lakefs")
        state = (setup_state or {}).get("state") if isinstance(setup_state, dict) else None
        if state == "not_initialized":
            code, body = _req(
                "POST",
                f"{base}/api/v1/setup_lakefs",
                data={
                    "username": user,
                    "key": {"access_key_id": access, "secret_access_key": secret},
                },
            )
            if code not in (200, 201, 409):
                raise SystemExit(f"setup_lakefs failed: {code} {body}")
            print(f"lakefs setup ok user={user}")
        else:
            raise SystemExit(
                f"lakefs auth failed (state={state}). "
                "Reset lakefs DB or match LAKEFS_ACCESS_KEY_ID/SECRET to existing install."
            )
        code, listed = _req("GET", f"{base}/api/v1/repositories", auth=auth)
    if code >= 400:
        raise SystemExit(f"list repositories failed: {code} {listed}")
    print("lakefs auth ok")

    results = []
    if isinstance(listed, dict):
        results = listed.get("results") or []
    elif isinstance(listed, list):
        results = listed
    if any(r.get("id") == REPO or r.get("name") == REPO for r in results):
        print(f"repo exists: {REPO}")
    else:
        code, body = _req(
            "POST",
            f"{base}/api/v1/repositories",
            auth=auth,
            data={"name": REPO, "storage_namespace": STORAGE_NS, "default_branch": "main"},
        )
        if code not in (200, 201, 409):
            raise SystemExit(f"create repo failed: {code} {body}")
        print(f"repo created: {REPO} ns={STORAGE_NS}")

    print("lakefs_init ok")


if __name__ == "__main__":
    main()
