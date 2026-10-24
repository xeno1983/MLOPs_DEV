#!/usr/bin/env python3
"""MLOPS 側のアノテーション入口。SAM 本体は Autoware_e2e の tools/annotate.py。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ugv_mlops.annotate_io import import_jsonl, import_masks, init_session, status  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="UGV annotation (LS / SALT / Xtreme1 / SAM)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_init = sub.add_parser("init")
    p_init.add_argument("mission_id")
    p_init.add_argument("--kind", default="camera", choices=["camera", "lidar"])
    p_imp = sub.add_parser("import-masks")
    p_imp.add_argument("session")
    p_imp.add_argument("--masks-dir", default="")
    p_js = sub.add_parser("import-jsonl")
    p_js.add_argument("path")
    p_js.add_argument("--tool", default="salt")
    args = parser.parse_args()
    if args.cmd == "status":
        print(json.dumps(status(), indent=2, ensure_ascii=False))
        return
    if args.cmd == "init":
        print(json.dumps(init_session(args.mission_id, args.kind), indent=2, ensure_ascii=False))
        return
    if args.cmd == "import-masks":
        print(json.dumps(import_masks(args.session, args.masks_dir), indent=2, ensure_ascii=False))
        return
    print(json.dumps(import_jsonl(Path(args.path), args.tool), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
