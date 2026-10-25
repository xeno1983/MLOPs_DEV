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
LIDAR_SUFFIXES = {".bin", ".pcd", ".ply"}
ANNOTATE_TOOLS = {
    "labelstudio": {
        "kind": "camera",
        "label": "Label Studio",
        "url": "http://127.0.0.1:8081",
        "role": "画像クラス / bbox",
    },
    "salt": {
        "kind": "lidar",
        "label": "SALT",
        "url": "",
        "role": "LiDAR 点の semantic（WSL GUI）",
    },
    "xtreme1": {
        "kind": "both",
        "label": "Xtreme1",
        "url": "http://127.0.0.1:8190",
        "role": "カメラと点群の突き合わせ",
    },
}


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


def init_session(mission_id: str, kind: str = "camera", limit: int = 200, tool: str = "labelstudio") -> dict:
    """Catalog のサンプルを data/annotate/<mission>/ に出し、人手ツールへ渡す。"""
    tool = (tool or "labelstudio").strip().lower()
    if tool not in ANNOTATE_TOOLS:
        raise ValueError(f"unknown annotate tool: {tool}; use {sorted(ANNOTATE_TOOLS)}")
    spec = ANNOTATE_TOOLS[tool]
    if kind in {"", "auto", None}:
        kind = spec["kind"]
    if tool == "salt" and kind == "camera":
        kind = "lidar"
    if tool == "xtreme1" and kind == "camera":
        kind = "both"
    root = session_dir(mission_id)
    inbox = root / "inbox"
    lidar = root / "lidar"
    inbox.mkdir(parents=True, exist_ok=True)
    lidar.mkdir(parents=True, exist_ok=True)
    mapping: list[dict] = []
    with connect() as conn:
        rows = _select_samples(conn, mission_id, kind, limit)
    sample_ids = [str(row["sample_id"]) for row in rows]
    camera_ids = [str(row["sample_id"]) for row in rows if str(row["modality"]).startswith("camera")]
    ls_result = None
    if tool == "labelstudio" and camera_ids:
        try:
            from ugv_mlops.integrations import push_samples_to_labelstudio

            ls_result = push_samples_to_labelstudio(camera_ids)
            ls_result["ok"] = True
        except Exception as exc:
            ls_result = {"ok": False, "error": str(exc), "pushed": 0}

    for row in rows:
        bucket, key = parse_s3_uri(row["object_uri"])
        suffix = Path(key).suffix.lower() or (".bin" if row["modality"] == "lidar" else ".jpg")
        if row["modality"] == "lidar" and suffix == ".jsonl":
            continue
        dest_dir = lidar if row["modality"] == "lidar" or suffix in LIDAR_SUFFIXES else inbox
        dest = dest_dir / f"{row['sample_id']}{suffix}"
        try:
            get_file(bucket, key, dest)
            if dest.suffix.lower() == ".bin":
                _bin_to_pcd(dest)
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
        json.dumps(
            {"mission_id": mission_id, "kind": kind, "tool": tool, "files": mapping},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "STEPS.txt").write_text(_steps(mission_id, kind, tool), encoding="utf-8")
    (root / "export.jsonl").touch()
    return {
        "ok": True,
        "session": str(root),
        "mission_id": mission_id,
        "tool": tool,
        "kind": kind,
        "n_files": sum(1 for x in mapping if "error" not in x),
        "n_catalog": len(sample_ids),
        "labelstudio": ls_result,
        "open_url": spec["url"] or None,
        "host_dir": f"data/annotate/{mission_id}",
        "next": _next_hint(mission_id, tool),
    }


def _select_samples(conn, mission_id: str, kind: str, limit: int):
    def fetch(clause: str, lim: int):
        return conn.execute(
            text(
                f"""
                SELECT sample_id, object_uri, modality
                FROM learning_sample
                WHERE mission_id = :m AND ({clause})
                ORDER BY ts NULLS LAST
                LIMIT :lim
                """
            ),
            {"m": mission_id, "lim": lim},
        ).mappings().all()

    if kind == "lidar":
        return fetch("modality = 'lidar'", limit)
    if kind == "both":
        return list(fetch("starts_with(modality, 'camera')", limit)) + list(fetch("modality = 'lidar'", limit))
    return fetch("starts_with(modality, 'camera')", limit)


def _bin_to_pcd(bin_path: Path) -> Path | None:
    try:
        import numpy as np
    except ImportError:
        return None
    raw = bin_path.read_bytes()
    xyz = np.frombuffer(raw, dtype="<f4")
    if xyz.size < 3:
        return None
    xyz = xyz[: xyz.size - (xyz.size % 3)].reshape(-1, 3)
    n = int(xyz.shape[0])
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        "FIELDS x y z\n"
        "SIZE 4 4 4\n"
        "TYPE F F F\n"
        "COUNT 1 1 1\n"
        f"WIDTH {n}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {n}\n"
        "DATA binary\n"
    )
    dest = bin_path.with_suffix(".pcd")
    dest.write_bytes(header.encode("ascii") + xyz.astype("<f4").tobytes())
    return dest


def _next_hint(mission_id: str, tool: str) -> str:
    if tool == "salt":
        return (
            f"WSL で SALT GUI を開き data/annotate/{mission_id}/lidar の点群を塗る。"
            f"書き出した jsonl を同じフォルダの export.jsonl に置いて「結果を戻す」"
        )
    if tool == "xtreme1":
        return (
            "Xtreme1 http://127.0.0.1:8190 でカメラと点群を確認する。"
            f"書き出しがあれば data/annotate/{mission_id}/export.jsonl を「結果を戻す」"
        )
    return "Label Studio http://127.0.0.1:8081 でラベルしたあと、工程4でデータセットを切る"


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


def resolve_import_path(path: str | Path) -> Path:
    text = str(path).strip().strip('"').replace("\\", "/")
    for prefix in ("data/annotate/", "/data/annotate/", "data/inbox/", "/data/inbox/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    raw = Path(text)
    annotate = Path(settings.annotate_dir).resolve()
    inbox = Path(settings.inbox_dir).resolve()
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (annotate / raw).resolve()
        if not resolved.is_file():
            resolved = (inbox / raw).resolve()
    roots = [annotate, inbox]
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError("jsonl は data/annotate または data/inbox の下だけ読めます")
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    return resolved


def import_jsonl(path: Path, tool: str = "salt") -> dict:
    """SALT / Xtreme1 / 手書き jsonl。各行に sample_id と label_json。"""
    path = resolve_import_path(path)
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


def _steps(mission_id: str, kind: str, tool: str = "labelstudio") -> str:
    spec = ANNOTATE_TOOLS.get(tool) or ANNOTATE_TOOLS["labelstudio"]
    return f"""Annotation session: {mission_id} ({kind} / {spec['label']})

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
  点群は data/annotate/{mission_id}/lidar （.bin と .pcd）
  書き出した jsonl は同フォルダの export.jsonl へ。ポータル工程3「結果を戻す」

カメラと点群の確認:
  Xtreme1 http://127.0.0.1:8190
  （Autoware_e2e/Xtreme1 の compose。Garage :9000 とは別。Xtreme1 MinIO は :8193）
  画像は inbox/、点群は lidar/*.pcd
  Freespace マスクには使わない（箱検出）
"""
