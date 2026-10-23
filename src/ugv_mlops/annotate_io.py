"""アノテーション入出力。Label Studio / SALT / Xtreme1 を役割分担する。

画像クラス・bbox … Label Studio（本 compose :8081）
走行領域マスク   … Autoware_e2e の SAM 2.1（tools/annotate.py）
LiDAR semantic   … SALT GUI（WSL、点群）
カメラと点群の確認 … Xtreme1（:8190、本 compose とは別。ポート衝突しない）
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from ugv_mlops.db import audit, connect
from ugv_mlops.settings import settings
from ugv_mlops.storage import get_file, parse_s3_uri, put_file

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def autoware_root() -> Path:
    return Path(settings.autoware_e2e_root)


def session_dir(name: str) -> Path:
    return Path(settings.annotate_dir) / name


def status() -> dict:
    root = autoware_root()
    salt = root / "SALT" / "bin" / "SALT"
    ckpt = root / "SALT" / "3rdparty" / "sam2" / "checkpoints" / "sam2.1_hiera_tiny.pt"
    xtreme = root / "Xtreme1" / "docker-compose.yml"
    xtreme_ok = False
    xtreme_detail = settings.xtreme1_url
    try:
        import httpx

        r = httpx.get(settings.xtreme1_url, timeout=3.0, follow_redirects=True)
        xtreme_ok = r.status_code < 500
        xtreme_detail = f"{settings.xtreme1_url} HTTP {r.status_code}"
    except Exception as exc:
        xtreme_detail = str(exc)
    sessions = []
    base = Path(settings.annotate_dir)
    if base.exists():
        sessions = sorted(p.name for p in base.iterdir() if p.is_dir())
    return {
        "ok": True,
        "labelstudio": {"url": settings.label_studio_url, "role": "画像クラス / bbox。ingest 後に必須"},
        "salt": {
            "ok": salt.exists(),
            "path": str(salt),
            "role": "LiDAR 点の semantic。GUI は WSL。本 compose には入れない",
        },
        "sam": {"ok": ckpt.is_file(), "path": str(ckpt), "role": "画像・赤外の領域分け（Freespace）"},
        "xtreme1": {
            "ok": xtreme_ok,
            "compose": xtreme.is_file(),
            "url": settings.xtreme1_url,
            "detail": xtreme_detail,
            "role": "カメラと点群の突き合わせ。YOLOR 箱検出。Freespace マスクには使わない",
        },
        "autoware_e2e_root": str(root),
        "sessions": sessions,
        "steps": [
            "1. ingest する（教師はまだ無い）",
            "2. 知覚クラス/bbox は Label Studio",
            "3. Freespace は annotate-init → Autoware SAM → import-masks",
            "4. 点群は SALT GUI。確認は Xtreme1 :8190",
        ],
    }


def init_session(mission_id: str, kind: str = "camera", limit: int = 200) -> dict:
    """Catalog のサンプルを data/annotate/<mission>/ に出し、人手ツールへ渡す。"""
    root = session_dir(mission_id)
    inbox = root / "inbox"
    lidar = root / "lidar"
    inbox.mkdir(parents=True, exist_ok=True)
    lidar.mkdir(parents=True, exist_ok=True)
    mapping: list[dict] = []
    with connect() as conn:
        if kind == "lidar":
            rows = conn.execute(
                text(
                    """
                    SELECT sample_id, object_uri, modality
                    FROM learning_sample
                    WHERE mission_id = :m AND modality = 'lidar'
                    ORDER BY ts NULLS LAST
                    LIMIT :lim
                    """
                ),
                {"m": mission_id, "lim": limit},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT sample_id, object_uri, modality
                    FROM learning_sample
                    WHERE mission_id = :m AND modality LIKE 'camera%'
                    ORDER BY ts NULLS LAST
                    LIMIT :lim
                    """
                ),
                {"m": mission_id, "lim": limit},
            ).mappings().all()
    for row in rows:
        bucket, key = parse_s3_uri(row["object_uri"])
        suffix = Path(key).suffix.lower() or ".jpg"
        dest = (lidar if kind == "lidar" else inbox) / f"{row['sample_id']}{suffix}"
        try:
            get_file(bucket, key, dest)
        except Exception as exc:
            mapping.append({"sample_id": row["sample_id"], "error": str(exc)})
            continue
        mapping.append(
            {
                "sample_id": row["sample_id"],
                "file": dest.name,
                "modality": row["modality"],
                "object_uri": row["object_uri"],
            }
        )
    (root / "manifest.json").write_text(
        json.dumps({"mission_id": mission_id, "kind": kind, "files": mapping}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "STEPS.txt").write_text(_steps(mission_id, kind), encoding="utf-8")
    aw = autoware_root() / "runs" / "annotate" / mission_id / "inbox"
    copied = 0
    if kind == "camera" and autoware_root().exists() and inbox.exists():
        aw.mkdir(parents=True, exist_ok=True)
        for src in inbox.iterdir():
            if src.suffix.lower() in IMAGE_SUFFIXES:
                target = aw / src.name
                if not target.exists():
                    target.write_bytes(src.read_bytes())
                    copied += 1
    return {
        "ok": True,
        "session": str(root),
        "n_files": sum(1 for x in mapping if "error" not in x),
        "autoware_inbox": str(aw) if copied else None,
        "copied_to_autoware": copied,
        "next": "Label Studio :8081 / SALT / python tools/annotate.py segment " + mission_id,
    }


def import_masks(session_name: str, masks_dir: str = "", tool: str = "sam-freespace") -> dict:
    """gt_masks または work/*_instances から Catalog annotation へ戻す。"""
    root = session_dir(session_name)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing {manifest_path}; run annotate-init first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_stem = {}
    for item in manifest.get("files") or []:
        stem = Path(item["file"]).stem
        by_stem[stem] = item["sample_id"]
    search = Path(masks_dir) if masks_dir else root / "gt_masks"
    if not search.exists():
        search = autoware_root() / "datasets" / "AORAS" / "FreespaceSeg" / "real" / session_name / "gt_masks"
    if not search.exists():
        raise FileNotFoundError(f"masks not found: {search}")
    imported = 0
    with connect() as conn:
        for path in sorted(search.glob("*")):
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            sample_id = by_stem.get(path.stem)
            if not sample_id:
                continue
            uri = put_file("curated", f"masks/{session_name}/{path.name}", path, content_type="image/png")
            conn.execute(
                text(
                    """
                    INSERT INTO annotation (sample_id, label_type, label_json, annotator, tool, gold)
                    VALUES (:sid, 'mask', CAST(:lj AS jsonb), :who, :tool, FALSE)
                    """
                ),
                {
                    "sid": sample_id,
                    "lj": json.dumps({"task": "freespace", "mask_uri": uri, "class": "drivable"}),
                    "who": "annotator",
                    "tool": tool,
                },
            )
            imported += 1
        audit(conn, "annotation.imported", "mission", session_name, {"n": imported, "tool": tool})
    return {"ok": True, "imported": imported, "tool": tool, "masks": str(search)}


def import_jsonl(path: Path, tool: str = "salt") -> dict:
    """SALT / 手書き jsonl。各行に sample_id と label_json。"""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    imported = 0
    with connect() as conn:
        for rec in rows:
            sid = rec.get("sample_id")
            if not sid:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO annotation (sample_id, label_type, label_json, annotator, tool, gold)
                    VALUES (:sid, :lt, CAST(:lj AS jsonb), :who, :tool, COALESCE(:gold, FALSE))
                    """
                ),
                {
                    "sid": sid,
                    "lt": rec.get("label_type") or "class",
                    "lj": json.dumps(rec.get("label_json") or rec),
                    "who": rec.get("annotator") or tool,
                    "tool": rec.get("tool") or tool,
                    "gold": rec.get("gold"),
                },
            )
            imported += 1
        audit(conn, "annotation.imported", "file", str(path), {"n": imported, "tool": tool})
    return {"ok": True, "imported": imported, "tool": tool}


def _steps(mission_id: str, kind: str) -> str:
    return f"""Annotation session: {mission_id} ({kind})

ingest しただけでは教師はありません。

画像クラス / bbox:
  Label Studio  http://localhost:8081
  プロジェクト UGV-obstacle-cls（ingest が import 済み）

走行領域（Freespace）:
  1. この inbox/ の画像（Autoware_e2e/runs/annotate/{mission_id}/inbox にもコピーされます）
  2. WSL: python tools/annotate.py segment {mission_id}
  3. work/*_overlay.jpg を見て classes.txt に drivable: 1, 4
  4. python tools/annotate.py export {mission_id}
  5. こちらで: python -m ugv_mlops.cli annotate-import-masks {mission_id}

LiDAR 点の semantic:
  SALT GUI（WSL）。docs は Autoware_e2e/docs/reference/SALT_SETUP_WSL.md
  書き出した jsonl は annotate-import-jsonl で Catalog へ

カメラと点群の確認:
  Xtreme1 http://localhost:8190
  （Autoware_e2e/Xtreme1 の compose。Garage :9000 とは別。Xtreme1 MinIO は :8193）
  Freespace マスクには使わない（箱検出）
"""
