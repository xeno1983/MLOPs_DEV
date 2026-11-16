#!/usr/bin/env python3
"""Print project progress from docs/progress.yaml (quick status for operators)."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "docs" / "progress.yaml"


def main() -> int:
    data = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    print(f"updated: {data.get('updated')}")
    print(f"window:  {data.get('baseline_start')} → {data.get('baseline_end')}")
    print()
    print("## milestones")
    for m in data.get("milestones") or []:
        print(f"  [{m.get('status'):12}] {m.get('id')} {m.get('title')} (due {m.get('date')})")
    print()
    print("## stages")
    for s in data.get("stages") or []:
        extra = f"  verified={s['verified']}" if s.get("verified") else ""
        print(f"  {s.get('n'):2} {s.get('id'):14} [{s.get('status'):12}]{extra}")
    print()
    print("## open")
    for o in data.get("open_items") or []:
        if o.get("status") == "done":
            continue
        print(f"  {o.get('id')} P{str(o.get('priority','?')).lstrip('P')} [{o.get('status')}] {o.get('title')} (due {o.get('due')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
