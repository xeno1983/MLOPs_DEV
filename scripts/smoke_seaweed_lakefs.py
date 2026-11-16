"""Short Seaweed+LakeFS smoke: generate → ingest → dataset → train."""

from __future__ import annotations

import json
import sys

from ugv_mlops.dataset import build_dataset
from ugv_mlops.ingest import ingest_mission_dir
from ugv_mlops.settings import settings
from ugv_mlops.storage import get_bytes, parse_s3_uri
from ugv_mlops.synthetic import generate_mission
from ugv_mlops.train import train_obstacle_classifier

MID = "M-SEAWEED-SMOKE-E2E"
DS_ID = "ds-seaweed-smoke"


def main() -> int:
    mission_dir = settings.inbox_path / MID
    print("=== generate", mission_dir)
    # seed != 7 so hashes don't collide with M-DEMO-001
    generate_mission(mission_dir, n_images=16, seed=42, mission_id=MID)
    n_img = len(list((mission_dir / "cameras" / "front").glob("*.jpg")))
    print("images", n_img)

    print("=== ingest")
    ing = ingest_mission_dir(mission_dir)
    print(
        "mission",
        ing.get("mission_id"),
        "files",
        ing.get("files_count"),
        "samples",
        len(ing.get("sample_ids") or []),
    )
    print("lakefs", json.dumps(ing.get("lakefs"), default=str)[:700])
    print("labelstudio", json.dumps(ing.get("labelstudio"), default=str)[:300])
    if not (ing.get("lakefs") or {}).get("ok"):
        print("INGEST_LAKEFS_FAIL", file=sys.stderr)
        return 1

    print("=== dataset")
    ds = build_dataset(DS_ID, "Seaweed LakeFS short smoke", mission_id=MID)
    lakefs = ds.get("lakefs") or {}
    print(
        json.dumps(
            {
                "dataset_version_id": ds.get("dataset_version_id"),
                "object_uri": ds.get("object_uri"),
                "n_train": ds.get("n_train"),
                "n_val": ds.get("n_val"),
                "n_test": ds.get("n_test"),
                "lakefs_ok": lakefs.get("ok"),
                "lakefs": lakefs,
            },
            indent=2,
            default=str,
        )
    )
    bucket, key = parse_s3_uri(ds["object_uri"])
    print("dataset s3 bytes", len(get_bytes(bucket, key)))
    if not lakefs.get("ok"):
        print("DATASET_LAKEFS_FAIL", file=sys.stderr)
        return 1

    print("=== train")
    tr = train_obstacle_classifier(ds["dataset_version_id"], settings.default_learning_stage)
    print(
        json.dumps(
            {
                "run_id": tr.get("run_id"),
                "artifact_uri": tr.get("artifact_uri"),
                "metrics": tr.get("metrics"),
                "stage": tr.get("stage"),
            },
            indent=2,
            default=str,
        )
    )
    ok = bool(tr.get("run_id")) and bool(tr.get("artifact_uri"))
    if ok:
        b, k = parse_s3_uri(tr["artifact_uri"])
        print("model s3 bytes", len(get_bytes(b, k)))
    print("SMOKE_OK" if ok else "SMOKE_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
