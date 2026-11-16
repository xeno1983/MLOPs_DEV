"""LakeFS 版管理（DVC 置き換え）。ブロックストアは SeaweedFS s3://lakefs。"""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from ugv_mlops.settings import settings

LAKEFS_REPO = "ugv-datasets"


def required() -> bool:
    # settings が LAKEFS_REQUIRED を読む（既定 true = P4 fail-closed）
    return bool(settings.lakefs_required)


def _headers() -> dict[str, str]:
    from base64 import b64encode

    raw = f"{settings.lakefs_access_key_id}:{settings.lakefs_secret_access_key}".encode()
    return {"Authorization": f"Basic {b64encode(raw).decode()}"}


def _base() -> str:
    return settings.lakefs_url.rstrip("/")


def ensure_repo(storage_namespace: str = "s3://lakefs/ugv-datasets") -> dict:
    h = _headers()
    listed = httpx.get(f"{_base()}/api/v1/repositories", headers=h, timeout=15.0)
    listed.raise_for_status()
    body = listed.json()
    results = body.get("results", body if isinstance(body, list) else [])
    for repo in results:
        if repo.get("id") == LAKEFS_REPO:
            return {"ok": True, "repo": LAKEFS_REPO, "existed": True}
    created = httpx.post(
        f"{_base()}/api/v1/repositories",
        headers=h,
        json={
            "name": LAKEFS_REPO,
            "storage_namespace": storage_namespace,
            "default_branch": "main",
        },
        timeout=30.0,
    )
    if created.status_code not in (200, 201, 409):
        created.raise_for_status()
    return {"ok": True, "repo": LAKEFS_REPO, "existed": False}


def _put_bytes(path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    # lakeFS 1.x: uploadObject は POST（PUT は stage 用で JSON 必須）
    put = httpx.post(
        f"{_base()}/api/v1/repositories/{LAKEFS_REPO}/branches/main/objects",
        params={"path": path},
        headers=_headers() | {"Content-Type": "application/octet-stream"},
        content=data,
        timeout=120.0,
    )
    if put.status_code >= 400:
        # 一部環境は multipart/form-data のみ
        put = httpx.post(
            f"{_base()}/api/v1/repositories/{LAKEFS_REPO}/branches/main/objects",
            params={"path": path},
            headers=_headers(),
            files={"content": (path.split("/")[-1], data, content_type)},
            timeout=120.0,
        )
    put.raise_for_status()


def _commit(message: str, metadata: dict[str, str] | None = None) -> dict:
    commit = httpx.post(
        f"{_base()}/api/v1/repositories/{LAKEFS_REPO}/branches/main/commits",
        headers=_headers(),
        json={"message": message, "metadata": metadata or {}},
        timeout=60.0,
    )
    if commit.status_code == 400 and "no changes" in (commit.text or "").lower():
        return {"commit_id": None, "message": message, "no_changes": True, "ok": True}
    commit.raise_for_status()
    body = commit.json()
    return {
        "commit_id": body.get("id") or body.get("commit_id"),
        "message": message,
        "ok": True,
    }


def commit_directory(local_dir: Path, lakefs_prefix: str, message: str, *, max_files: int = 500) -> dict:
    """ローカルディレクトリを LakeFS パスへ載せ commit。"""
    ensure_repo()
    if not local_dir.is_dir():
        raise FileNotFoundError(str(local_dir))
    files = [p for p in local_dir.rglob("*") if p.is_file()]
    uploaded = 0
    for path in files[:max_files]:
        rel = path.relative_to(local_dir).as_posix()
        dest = f"{lakefs_prefix.rstrip('/')}/{rel}"
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        _put_bytes(dest, path.read_bytes(), ctype)
        uploaded += 1
    meta = {"files": str(uploaded), "prefix": lakefs_prefix}
    committed = _commit(message, meta)
    return {
        "ok": True,
        "repo": LAKEFS_REPO,
        "prefix": lakefs_prefix,
        "files": uploaded,
        "truncated": len(files) > max_files,
        **committed,
        "uri": f"lakefs://{LAKEFS_REPO}/main/{lakefs_prefix}",
    }


def commit_mission(mission_id: str) -> dict:
    """ingest 後: raw (+ あれば parsed) を LakeFS に載せる。"""
    from ugv_mlops.bag_pipeline import mark_lakefs_commit, parsed_dir, raw_dir

    ensure_repo()
    out: dict[str, Any] = {"ok": True, "mission_id": mission_id}
    raw = raw_dir(mission_id)
    parsed = parsed_dir(mission_id)
    if raw.is_dir():
        out["raw"] = commit_directory(
            raw,
            f"raw/missions/{mission_id}",
            f"raw mission {mission_id}",
        )
        out["ok"] = out["ok"] and bool(out["raw"].get("ok"))
        if out["raw"].get("ok"):
            mark_lakefs_commit(mission_id, "raw", out["raw"])
    if parsed.is_dir():
        out["parsed"] = commit_directory(
            parsed,
            f"parsed/{mission_id}",
            f"parsed mission {mission_id}",
        )
        out["ok"] = out["ok"] and bool(out["parsed"].get("ok"))
        if out["parsed"].get("ok"):
            mark_lakefs_commit(mission_id, "parsed", out["parsed"])
    if "raw" not in out and "parsed" not in out:
        return {"ok": False, "error": f"mission missing locally: {mission_id}"}
    return out


def commit_dataset_zip(dataset_version_id: str, object_uri: str) -> dict:
    """datasets バケット上の zip を LakeFS にも置く（履歴用）。"""
    from ugv_mlops.storage import client, parse_s3_uri

    ensure_repo()
    bucket, key = parse_s3_uri(object_uri)
    data = client().get_object(bucket, key).read()
    path = f"datasets/{dataset_version_id}.zip"
    _put_bytes(path, data, "application/zip")
    meta = {"source": object_uri}
    committed = _commit(f"dataset {dataset_version_id}", meta)
    # 併せて小さなマニフェスト
    manifest = json.dumps({"dataset_version_id": dataset_version_id, "object_uri": object_uri}, indent=2)
    _put_bytes(f"datasets/{dataset_version_id}.json", manifest.encode(), "application/json")
    _commit(f"dataset manifest {dataset_version_id}", meta)
    return {
        "ok": True,
        "repo": LAKEFS_REPO,
        "path": path,
        **committed,
        "uri": f"lakefs://{LAKEFS_REPO}/main/{path}",
    }


def status() -> dict:
    try:
        r = httpx.get(f"{_base()}/api/v1/healthcheck", timeout=5.0)
        healthy = r.status_code < 500
    except Exception as exc:
        return {"ok": False, "wired": False, "error": str(exc), "required": required()}
    try:
        repos = httpx.get(f"{_base()}/api/v1/repositories", headers=_headers(), timeout=10.0)
        repos.raise_for_status()
        return {
            "ok": True,
            "wired": True,
            "required": required(),
            "repo": LAKEFS_REPO,
            "detail": "データ版の正（raw/datasets）。ブロックは SeaweedFS s3://lakefs",
        }
    except Exception as exc:
        return {
            "ok": healthy,
            "wired": False,
            "required": required(),
            "detail": f"起動済みだが認証/API 未完: {exc}",
        }
