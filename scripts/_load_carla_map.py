#!/usr/bin/env python3
"""Load a CARLA map on a running server (UE4 CLI map args are unreliable on 0.9.15)."""
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("-p", "--port", type=int, default=2000)
    ap.add_argument("--map", default="Town01")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    try:
        import carla
    except ImportError:
        print("carla import failed", file=sys.stderr)
        return 2

    client = carla.Client(args.host, args.port)
    client.set_timeout(args.timeout)
    before = client.get_world().get_map().name
    print(f"before {before}", flush=True)
    if args.map in before or before.endswith("/" + args.map):
        print(f"already on {before}", flush=True)
        return 0
    print(f"loading {args.map}...", flush=True)
    try:
        client.load_world(args.map)
    except Exception as exc:
        alt = f"{args.map}_Opt"
        print(f"{args.map} failed ({exc}); trying {alt}", flush=True)
        client.load_world(alt)
    time.sleep(1.5)
    after = client.get_world().get_map().name
    print(f"after {after}", flush=True)
    if args.map not in after:
        print(f"map mismatch: wanted {args.map}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
