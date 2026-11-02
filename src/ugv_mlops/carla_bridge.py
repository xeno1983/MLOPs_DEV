"""CARLA / Autoware_e2e 連動（compose 外）。

契約: シミュはホスト/WSL の CARLA + Autoware_e2e で録り、成果 rosbag2 を
data/inbox/<mission>/ へ載せる。起動は carla_launch（host worker / CLI）経由。
Catalog コンテナ自体は CarlaUE4 を抱えない。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ugv_mlops.settings import settings


BAG_MARKERS = ("metadata.yaml",)
BAG_SUFFIXES = (".db3", ".mcap")
# Autoware_e2e 内で bag を探す相対パス（存在する分だけ）
BAG_SEARCH_RELS = (
    Path("datasets") / "carla_bags",
    Path("datasets") / "missions",
    Path("datasets") / "carla_exports",
    Path("runs"),
)


def resolve_autoware_root() -> Path:
    """ホスト Windows パスと compose の /autoware_e2e マウントの両方を試す。"""
    candidates = [
        Path(settings.autoware_e2e_root),
        Path("/autoware_e2e"),
        Path("data/autoware_stub"),
    ]
    for cand in candidates:
        try:
            p = cand.resolve()
        except OSError:
            p = cand
        if not p.is_dir():
            continue
        # 本物の Autoware_e2e を優先（stub の空ディレクトリだけでは採用しない）
        if (p / "OFFROAD_CARLA_HYBRID.md").is_file() or (p / "tools").is_dir():
            return p
    # 最後の手段: 設定パス（存在しなくても呼び出し側で失敗する）
    return Path(settings.autoware_e2e_root)


def status() -> dict:
    root = resolve_autoware_root()
    hybrid = root / "OFFROAD_CARLA_HYBRID.md"
    mining = root / "autoware.off-road" / "Simulation" / "CARLA" / "scripts" / "mining_sim.py"
    bags = list_bags(limit=20)
    launch_info: dict = {}
    try:
        from ugv_mlops.carla_launch import launch_status

        launch_info = launch_status()
    except Exception as exc:  # noqa: BLE001 — status は壊さない
        launch_info = {"ok": False, "error": str(exc)}
    return {
        "ok": root.is_dir(),
        "autoware_e2e_root": str(root),
        "settings_root": settings.autoware_e2e_root,
        "hybrid_doc": str(hybrid) if hybrid.is_file() else None,
        "mining_sim": str(mining) if mining.is_file() else None,
        "carla_in_compose": False,
        "bag_count": len(bags.get("bags") or []),
        "bags_preview": (bags.get("bags") or [])[:5],
        "launch": launch_info,
        "howto": howto(),
        "next": "録った bag を POST /carla/import で inbox へ。その後工程3 ingest。",
    }


def howto() -> list[str]:
    root = resolve_autoware_root()
    return [
        "1. ポータル／CLI／ランチャーで CARLA を起動（compose 外・host worker 可）。",
        "2. mining_sim.py --sync（同 UI の「mining_sim 起動」または手動）。",
        "3. ros2 bag record でカメラ・odometry・（可能なら）cmd_vel/ackermann を録る。",
        f"4. 出力先の例: {root / 'datasets' / 'carla_bags' / '<run_id>'}",
        "5. ポータル工程1または POST /carla/import で data/inbox/<mission>/ へ取り込む。",
        "6. 工程3 ingest → 工程4 data_qa。E2E なら工程5–6スキップ可。知覚はラベル必須。",
        "詳細: Autoware_e2e/OFFROAD_CARLA_HYBRID.md と docs/carla.md",
    ]


def list_bags(limit: int = 50) -> dict:
    root = resolve_autoware_root()
    found: list[dict] = []
    if not root.is_dir():
        return {"ok": False, "root": str(root), "bags": [], "error": "Autoware_e2e root not found"}

    search_roots = [root / rel for rel in BAG_SEARCH_RELS if (root / rel).is_dir()]
    if not search_roots:
        # datasets 直下も浅く見る
        ds = root / "datasets"
        if ds.is_dir():
            search_roots = [ds]

    seen: set[str] = set()
    for base in search_roots:
        for meta in base.rglob("metadata.yaml"):
            bag_dir = meta.parent
            key = str(bag_dir.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                rel = str(bag_dir.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(bag_dir)
            size = _dir_bytes(bag_dir)
            found.append(
                {
                    "path": str(bag_dir),
                    "relpath": rel,
                    "name": bag_dir.name,
                    "size_bytes": size,
                    "has_db3": any(bag_dir.glob("*.db3")),
                    "has_mcap": any(bag_dir.glob("*.mcap")),
                }
            )
            if len(found) >= limit:
                break
        if len(found) >= limit:
            break

    found.sort(key=lambda b: b["name"])
    return {"ok": True, "root": str(root), "bags": found, "n": len(found)}


def import_bag_to_inbox(
    source: str,
    mission_id: str | None = None,
    note: str = "",
) -> dict:
    """Autoware 配下（または許可ルート）の bag を data/inbox/<mission>/ へコピーする。"""
    root = resolve_autoware_root()
    src = _resolve_source(source, root)
    if not src.exists():
        raise FileNotFoundError(f"source not found: {src}")
    if src.is_file():
        # 単一 .mcap / .db3 → 親ディレクトリを bag とみなすか、ファイルだけコピー
        bag_root = src.parent if src.suffix.lower() in BAG_SUFFIXES else src
        if src.suffix.lower() in BAG_SUFFIXES and not (src.parent / "metadata.yaml").exists():
            bag_root = src  # ファイル単体
    else:
        bag_root = src

    mid = _sanitize_mission_id(mission_id or (bag_root.stem if bag_root.is_file() else bag_root.name))
    if not mid.startswith("M-") and not mid.startswith("m-"):
        mid = f"M-CARLA-{mid}"

    dest = settings.inbox_path / mid
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    copied_files = 0
    if bag_root.is_file():
        shutil.copy2(bag_root, dest / bag_root.name)
        copied_files = 1
    else:
        # bag ディレクトリ全体（metadata + db3/mcap）
        for path in bag_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(bag_root)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied_files += 1

    if not _looks_like_bag(dest) and bag_root.is_file():
        # 単ファイルでも取込は許可（ingest が扱う）
        pass
    elif not _looks_like_bag(dest):
        raise RuntimeError(
            f"コピー先に rosbag2 らしきファイルがありません: {dest} "
            "(metadata.yaml + .db3/.mcap を確認)"
        )

    manifest = {
        "mission_id": mid,
        "vehicle_id": "CARLA-01",
        "classification": "UNCLASSIFIED",
        "started_ts": datetime.now(timezone.utc).isoformat(),
        "theater": "carla",
        "sensors": ["camera_front", "odometry"],
        "source": "carla",
        "source_path": str(src),
        "autoware_e2e_root": str(root),
        "note": note or "imported from CARLA / Autoware_e2e",
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    from ugv_mlops.db import audit, connect

    with connect() as conn:
        audit(
            conn,
            "pipeline.sim_generate",
            "mission",
            mid,
            {
                "action": "carla_import",
                "source": str(src),
                "inbox": str(dest),
                "files": copied_files,
                "note": note,
            },
        )

    return {
        "ok": True,
        "mission_id": mid,
        "inbox_path": str(dest),
        "container_path": f"/data/inbox/{mid}",
        "files_copied": copied_files,
        "manifest": manifest,
        "next": "工程3で POST /ingest path=/data/inbox/" + mid,
        "howto_next": [
            f'POST /ingest {{"path":"/data/inbox/{mid}","source_type":"rosbag"}}',
            "その後 POST /data-qa で確認。E2E なら dataset task=e2e。",
        ],
    }


def ack_sim_generate(note: str = "", mission_id: str | None = None) -> dict:
    """後方互換: pipeline_qa.ack_sim_generate へ委譲。"""
    from ugv_mlops.pipeline_qa import ack_sim_generate as _ack

    return _ack(note=note, mission_id=mission_id)


def _resolve_source(source: str, root: Path) -> Path:
    raw = (source or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("source が空です（list_bags の relpath か絶対パス）")
    p = Path(raw)
    if p.is_absolute() and p.exists():
        _assert_allowed(p, root)
        return p
    # relpath from autoware root
    cand = (root / raw).resolve()
    _assert_allowed(cand, root)
    return cand


def _assert_allowed(path: Path, root: Path) -> None:
    """Autoware root と inbox 配下のみ許可（work 全体は不可・パストラバーサル防止）。"""
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise PermissionError(str(exc)) from exc
    allowed = [root.resolve(), settings.inbox_path.resolve()]
    for extra in (Path("/autoware_e2e"), Path("/data/inbox")):
        if extra.is_dir():
            try:
                allowed.append(extra.resolve())
            except OSError:
                pass
    ok = False
    for base in allowed:
        try:
            resolved.relative_to(base)
            ok = True
            break
        except ValueError:
            continue
    if not ok:
        raise PermissionError(f"source は Autoware_e2e または inbox 配下のみ: {resolved}")


def _sanitize_mission_id(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in (value or "").strip())
    cleaned = cleaned.strip("-_") or "run"
    return cleaned[:80]


def _dir_bytes(folder: Path) -> int:
    total = 0
    try:
        for p in folder.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def _looks_like_bag(folder: Path) -> bool:
    names = {p.name.lower() for p in folder.rglob("*") if p.is_file()}
    has_meta = "metadata.yaml" in names
    has_db = any(n.endswith(".db3") or n.endswith(".mcap") for n in names)
    has_images = any(n.endswith((".jpg", ".jpeg", ".png")) for n in names)
    return (has_meta and has_db) or has_images or has_db


def record_command_hint(out_name: str = "mine_001") -> dict:
    """ros2 bag record のコピペ用（ホスト WSL で実行）。"""
    root = resolve_autoware_root()
    out = root / "datasets" / "carla_bags" / out_name
    cmd = (
        "ros2 bag record \\\n"
        "  /carla/ego_vehicle/rgb_front/image \\\n"
        "  /carla/ego_vehicle/odometry \\\n"
        "  /tf /tf_static \\\n"
        f"  -o {out.as_posix()}"
    )
    return {
        "shell": "bash (WSL / Linux)",
        "output_dir": str(out),
        "command": cmd,
        "note": "cmd_vel / ackermann が取れるトピックがあれば追加すると E2E に有利。",
    }
