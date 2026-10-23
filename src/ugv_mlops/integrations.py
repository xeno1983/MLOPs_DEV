from __future__ import annotations

import json
from base64 import b64encode
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from ugv_mlops.db import connect
from ugv_mlops.settings import settings

LS_CONFIG = """<View>
  <Header value="UGV 障害物分類"/>
  <Image name="image" value="$image"/>
  <Choices name="class" toName="image" choice="single">
    <Choice value="clear"/>
    <Choice value="obstacle"/>
    <Choice value="person"/>
  </Choices>
</View>
"""

LS_PROJECT = "UGV-obstacle-cls"
LAKEFS_REPO = "ugv-datasets"


CORE_TOOLS = frozenset({"catalog", "postgres", "garage", "mlflow", "labelstudio"})


def probe() -> dict[str, dict[str, Any]]:
    """Reachability + whether Catalog actually exchanges data with each tool."""
    out = {
        "catalog": _ok("ジョブとメタデータの中枢。ポータル・ワーカーがここを叩く。", True),
        "postgres": _probe_http_skip("カタログ／MLflow／Label Studio のDB。", True),
        "garage": _probe_garage(),
        "mlflow": _probe_mlflow(),
        "labelstudio": _probe_labelstudio(),
        "dvc": _probe_dvc(),
        "salt": _probe_salt(),
        "xtreme1": _probe_xtreme1(),
        "lakefs": _probe_lakefs(),
        "gitea": _probe_gitea(),
        "grafana": _probe_url(settings.grafana_url + "/api/health", "Prometheus をデータソースに接続済み。パイプライン本体は未接続。拡張プロファイル。", wired=False),
        "prometheus": _probe_url(settings.prometheus_url + "/-/ready", "Catalog /metrics をスクレイプ。拡張プロファイル。", wired=True),
    }
    for key, info in out.items():
        info["core"] = key in CORE_TOOLS
    return out


def push_samples_to_labelstudio(sample_ids: list[str]) -> dict:
    headers = _ls_headers()
    project_id = _ls_ensure_project(headers)
    tasks = []
    for sid in sample_ids:
        tasks.append(
            {
                "data": {"image": f"{settings.sample_public_base}/samples/{sid}/file"},
                "meta": {"sample_id": sid},
            }
        )
    if not tasks:
        return {"pushed": 0, "project_id": project_id}
    r = httpx.post(
        f"{settings.label_studio_url}/api/projects/{project_id}/import",
        headers=headers,
        json=tasks,
        timeout=60.0,
    )
    r.raise_for_status()
    return {"pushed": len(tasks), "project_id": project_id}


def pull_labelstudio_annotations() -> dict:
    headers = _ls_headers()
    project_id = _ls_ensure_project(headers)
    r = httpx.get(
        f"{settings.label_studio_url}/api/projects/{project_id}/export",
        headers=headers,
        params={"exportType": "JSON"},
        timeout=120.0,
    )
    r.raise_for_status()
    items = r.json()
    inserted = 0
    with connect() as conn:
        for item in items:
            sample_id = (item.get("meta") or {}).get("sample_id")
            if not sample_id:
                continue
            label = _choice_from_ls(item)
            if not label:
                continue
            exists = conn.execute(
                text(
                    """
                    SELECT 1 FROM annotation
                    WHERE sample_id = :sid AND tool = 'labelstudio' AND label_json->>'class' = :cls
                    LIMIT 1
                    """
                ),
                {"sid": sample_id, "cls": label},
            ).first()
            if exists:
                continue
            sample = conn.execute(
                text("SELECT 1 FROM learning_sample WHERE sample_id = :sid"),
                {"sid": sample_id},
            ).first()
            if not sample:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO annotation (sample_id, label_type, label_json, annotator, tool, gold)
                    VALUES (:sid, 'class', CAST(:lj AS jsonb), 'labelstudio', 'labelstudio', FALSE)
                    """
                ),
                {"sid": sample_id, "lj": json.dumps({"task": "obstacle_cls", "class": label})},
            )
            inserted += 1
    return {"project_id": project_id, "exported": len(items), "inserted": inserted}


def commit_dataset_to_lakefs(dataset_version_id: str, object_uri: str) -> dict:
    from ugv_mlops.storage import client, parse_s3_uri

    _lakefs_ensure_repo()
    bucket, key = parse_s3_uri(object_uri)
    data = client().get_object(bucket, key).read()
    path = f"datasets/{dataset_version_id}.zip"
    put = httpx.put(
        f"{settings.lakefs_url}/api/v1/repositories/{LAKEFS_REPO}/branches/main/objects",
        params={"path": path},
        headers=_lakefs_headers() | {"Content-Type": "application/octet-stream"},
        content=data,
        timeout=120.0,
    )
    put.raise_for_status()
    commit = httpx.post(
        f"{settings.lakefs_url}/api/v1/repositories/{LAKEFS_REPO}/branches/main/commits",
        headers=_lakefs_headers(),
        json={"message": f"dataset {dataset_version_id}", "metadata": {"source": object_uri}},
        timeout=30.0,
    )
    commit.raise_for_status()
    body = commit.json()
    return {
        "repo": LAKEFS_REPO,
        "path": path,
        "commit_id": body.get("id") or body.get("commit_id"),
        "uri": f"lakefs://{LAKEFS_REPO}/main/{path}",
    }


def attach_dataset_remotes(dataset_version_id: str, object_uri: str) -> dict:
    """LakeFS（任意）と DVC→Garage（既定の大きい成果物の版）。どちらも失敗しても学習は続く。"""
    out: dict[str, Any] = {}
    try:
        out["lakefs"] = commit_dataset_to_lakefs(dataset_version_id, object_uri)
        out["lakefs"]["ok"] = True
    except Exception as exc:
        out["lakefs"] = {"ok": False, "error": str(exc)}
    try:
        from ugv_mlops.dvc_sync import publish_dataset

        out["dvc"] = publish_dataset(dataset_version_id, object_uri)
    except Exception as exc:
        out["dvc"] = {"ok": False, "error": str(exc)}
    return out


def _ok(detail: str, wired: bool, extra: dict | None = None) -> dict:
    out = {"ok": True, "wired": wired, "detail": detail}
    if extra:
        out.update(extra)
    return out


def _fail(detail: str, wired: bool = False) -> dict:
    return {"ok": False, "wired": wired, "detail": detail}


def _probe_http_skip(detail: str, wired: bool) -> dict:
    return _ok(detail, wired)


def _probe_url(url: str, detail: str, wired: bool) -> dict:
    try:
        r = httpx.get(url, timeout=3.0)
        if r.status_code < 500:
            return _ok(detail, wired, {"http": r.status_code})
        return _fail(f"HTTP {r.status_code}", wired)
    except Exception as exc:
        return _fail(str(exc), wired)


def _probe_dvc() -> dict:
    try:
        from ugv_mlops.dvc_sync import status as dvc_status

        st = dvc_status()
        wired = bool(st.get("pointers"))
        detail = st.get("detail") or "DVC ポインタ + Garage s3://dvc"
        if not st.get("initialized"):
            return _fail("DVC 未初期化（リポジトリの .dvc/config）", False)
        if not st.get("dvc_installed"):
            return _ok("ポインタ設定済み。add/push は Worker（requirements-dvc.txt）。", wired, st)
        return _ok(detail, wired, st)
    except Exception as exc:
        return _fail(str(exc), False)


def _probe_salt() -> dict:
    root = Path(settings.autoware_e2e_root)
    salt = root / "SALT" / "bin" / "SALT"
    if salt.exists():
        return _ok("LiDAR semantic は SALT GUI（WSL）。Catalog には jsonl import。", False, {"path": str(salt)})
    return _fail(f"SALT GUI が無い: {salt}（Autoware_e2e を AUTOWARE_E2E_ROOT で指す）", False)


def _probe_xtreme1() -> dict:
    return _probe_url(
        settings.xtreme1_url,
        "カメラと点群の確認 UI。本 compose 外（:8190）。Freespace マスクには使わない。",
        wired=False,
    )


def _probe_garage() -> dict:
    try:
        from ugv_mlops.storage import client

        ok = client().bucket_exists("raw")
        return _ok("取込・データセット・モデル・パッケージの実体を保存（Garage S3）。Catalog が読み書きする。", True, {"raw_bucket": ok})
    except Exception as exc:
        return _fail(str(exc), True)


def _probe_mlflow() -> dict:
    try:
        r = httpx.get(f"{settings.mlflow_tracking_uri}/health", timeout=3.0)
        wired = r.status_code < 400
        return _ok("学習ジョブが run / 指標 / 成果物を記録。成果物は Garage の mlflow バケット。", wired)
    except Exception as exc:
        return _fail(str(exc), True)


def _probe_labelstudio() -> dict:
    try:
        r = httpx.get(f"{settings.label_studio_url}/api/version", timeout=5.0)
        if r.status_code >= 500:
            return _fail(f"HTTP {r.status_code}", False)
        try:
            headers = _ls_headers()
            proj = httpx.get(f"{settings.label_studio_url}/api/projects", headers=headers, timeout=10.0)
            proj.raise_for_status()
            return _ok("取込後にタスク投入、データセット構築前にアノテーションを引き戻す。", True)
        except Exception as exc:
            return {
                "ok": True,
                "wired": False,
                "detail": f"起動はしているが API 認証前: {exc}",
            }
    except Exception as exc:
        return _fail(str(exc), False)


def _probe_lakefs() -> dict:
    try:
        r = httpx.get(f"{settings.lakefs_url}/api/v1/healthcheck", timeout=3.0)
        if r.status_code >= 500:
            return _fail(f"HTTP {r.status_code}", False)
        listed = httpx.get(
            f"{settings.lakefs_url}/api/v1/repositories",
            headers=_lakefs_headers(),
            timeout=8.0,
        )
        if listed.status_code < 400:
            return _ok("データセット zip を Git ライクにコミット。Garage lakefs バケットをブロックストアに使用。", True)
        return {
            "ok": True,
            "wired": False,
            "detail": f"起動済み。初期セットアップまたは認証が未完了 (HTTP {listed.status_code})",
        }
    except Exception as exc:
        return _fail(str(exc), False)


def _probe_gitea() -> dict:
    try:
        r = httpx.get(f"{settings.gitea_url}/api/v1/version", timeout=3.0)
        if r.status_code < 500:
            return {
                "ok": True,
                "wired": False,
                "detail": "コード保管用に起動。学習ジョブへの自動コミット紐付けは未接続。",
                "http": r.status_code,
            }
        return _fail(f"HTTP {r.status_code}", False)
    except Exception as exc:
        return _fail(str(exc), False)


def _ls_headers() -> dict[str, str]:
    if settings.label_studio_api_key:
        return {"Authorization": f"Token {settings.label_studio_api_key}"}
    for path, payload in (
        ("/api/token/login", {"email": settings.label_studio_username, "password": settings.label_studio_password}),
        ("/api/auth/login", {"email": settings.label_studio_username, "password": settings.label_studio_password}),
        ("/api/token/login", {"username": settings.label_studio_username, "password": settings.label_studio_password}),
    ):
        r = httpx.post(f"{settings.label_studio_url}{path}", json=payload, timeout=10.0)
        if r.status_code < 400:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            token = body.get("token") or body.get("key") or body.get("access_token")
            if token:
                return {"Authorization": f"Token {token}"}
    raise RuntimeError("Label Studio にログインできません。ユーザ作成とパスワードを確認してください。")


def _ls_ensure_project(headers: dict[str, str]) -> int:
    r = httpx.get(f"{settings.label_studio_url}/api/projects", headers=headers, timeout=15.0)
    r.raise_for_status()
    body = r.json()
    results = body.get("results", body if isinstance(body, list) else [])
    for proj in results:
        if proj.get("title") == LS_PROJECT:
            return int(proj["id"])
    created = httpx.post(
        f"{settings.label_studio_url}/api/projects",
        headers=headers,
        json={"title": LS_PROJECT, "label_config": LS_CONFIG},
        timeout=15.0,
    )
    created.raise_for_status()
    return int(created.json()["id"])


def _choice_from_ls(item: dict) -> str | None:
    for ann in item.get("annotations") or []:
        for res in ann.get("result") or []:
            choices = (res.get("value") or {}).get("choices") or []
            if choices:
                return str(choices[0])
    return None


def _lakefs_headers() -> dict[str, str]:
    raw = f"{settings.lakefs_access_key_id}:{settings.lakefs_secret_access_key}".encode()
    return {"Authorization": "Basic " + b64encode(raw).decode(), "Accept": "application/json"}


def _lakefs_ensure_repo() -> None:
    listed = httpx.get(
        f"{settings.lakefs_url}/api/v1/repositories",
        headers=_lakefs_headers(),
        timeout=10.0,
    )
    listed.raise_for_status()
    body = listed.json()
    results = body.get("results", body if isinstance(body, list) else [])
    if any(r.get("id") == LAKEFS_REPO or r.get("name") == LAKEFS_REPO for r in results):
        return
    created = httpx.post(
        f"{settings.lakefs_url}/api/v1/repositories",
        headers=_lakefs_headers(),
        json={
            "name": LAKEFS_REPO,
            "storage_namespace": "s3://lakefs/ugv-datasets",
            "default_branch": "main",
        },
        timeout=15.0,
    )
    created.raise_for_status()
