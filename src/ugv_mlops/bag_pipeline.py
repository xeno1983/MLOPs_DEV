"""ROS2 bag: 取込 → 追跡 → パース → 保管（DVC GUI と同じ段階）。

配置:
  data/dvc/raw/missions/<id>/   生 bag / コピーしたフォルダ
  data/dvc/parsed/<id>/         cameras / lidar/*.bin / localization / series
実体の遠隔保管は Garage s3://dvc（ポインタだけ Git）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ugv_mlops.dvc_sync import _add_and_push, _dvc_importable, repo_root
from ugv_mlops.rosbag import discover_bags, extract_bags, list_topics
from ugv_mlops.settings import settings

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def raw_root() -> Path:
    return repo_root() / "data" / "dvc" / "raw" / "missions"


def parsed_root() -> Path:
    return repo_root() / "data" / "dvc" / "parsed"


def sanitize_mission_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip())
    cleaned = cleaned.strip("-._")
    if not cleaned:
        raise ValueError("ミッションIDが空です")
    return cleaned[:80]


def raw_dir(mission_id: str) -> Path:
    return raw_root() / mission_id


def parsed_dir(mission_id: str) -> Path:
    return parsed_root() / mission_id


def tracked_pointer(mission_id: str) -> Path:
    return raw_root() / f"{mission_id}.dvc"


def parsed_pointer(mission_id: str) -> Path:
    return parsed_root() / f"{mission_id}.dvc"


def import_mission(source: Path, mission_id: str | None = None) -> dict:
    """ディスク上の bag / フォルダを raw に置く（ブラウザアップロードはしない）。"""
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(str(src))
    mid = sanitize_mission_id(mission_id or (src.stem if src.is_file() else src.name))
    dest = raw_dir(mid)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.resolve() == src.resolve():
        return {"ok": True, "mission_id": mid, "raw_dir": str(dest), "copied": False}
    if dest.exists() and dest.resolve() != src.resolve():
        if src.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / src.name)
        else:
            shutil.copytree(src, dest, dirs_exist_ok=True)
        return {"ok": True, "mission_id": mid, "raw_dir": str(dest), "copied": True, "merged": True}
    if src.is_file():
        dest.mkdir(parents=True)
        shutil.copy2(src, dest / src.name)
    else:
        shutil.copytree(src, dest)
    return {"ok": True, "mission_id": mid, "raw_dir": str(dest), "copied": True}


def parse_mission(mission_id: str) -> dict:
    """raw の bag を cameras / lidar.bin / localization に展開する。"""
    src = raw_dir(mission_id)
    dest = parsed_dir(mission_id)
    if not src.exists():
        inbox = Path(settings.inbox_dir) / mission_id
        if inbox.exists():
            import_mission(inbox, mission_id)
            src = raw_dir(mission_id)
        else:
            raise FileNotFoundError(f"ミッションがありません: {src}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    bags = discover_bags(src)
    summary = {
        "mission_id": mission_id,
        "images": 0,
        "lidar_keyframes": 0,
        "gnss": 0,
        "odom": 0,
        "imu": 0,
        "action": 0,
        "topics": [],
        "bags": [_rel_to(src, p) for p in bags],
        "source": "copy",
    }
    frames: list[dict] = []
    lidar_files: list[dict] = []
    bag_files: list[Path] = []
    if bags:
        try:
            dumped = extract_bags(bags, dest)
            frames = dumped["frames"]
            lidar_files = dumped.get("lidar_files") or []
            bag_files = dumped.get("bag_files") or []
            counts = dumped.get("counts") or {}
            summary.update({k: counts.get(k, summary.get(k, 0)) for k in ("images", "lidar_keyframes", "gnss", "odom", "imu", "action")})
            summary["topics"] = dumped.get("topics") or []
            summary["source"] = "rosbag"
        except Exception as exc:
            summary["bag_error"] = str(exc)
            copied = _copy_existing(src, dest)
            summary.update(copied)
            summary["source"] = "copy_after_bag_error"
            frames, lidar_files = _scan_parsed(dest)
            bag_files = [p for b in bags for p in _files_of(b)]
    else:
        copied = _copy_existing(src, dest)
        summary.update(copied)
        frames, lidar_files = _scan_parsed(dest)

    (dest / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in frames:
        item["path"] = str(item["path"])
    for item in lidar_files:
        item["path"] = str(item["path"])
    return {
        "ok": True,
        "mission_id": mission_id,
        "raw_dir": str(src),
        "parsed_dir": str(dest),
        "summary": summary,
        "frames": frames,
        "lidar_files": lidar_files,
        "bag_files": [str(p) for p in bag_files],
        "series_files": _series_files(dest),
        "stage": mission_detail(mission_id)["stage"],
    }


def track_mission(mission_id: str, push: bool = False) -> dict:
    """raw を DVC 追跡する（実体は Git に入らない）。"""
    if not _dvc_importable():
        return {"ok": False, "error": "dvc not installed (requirements-dvc.txt)"}
    dest = raw_dir(mission_id)
    if not dest.exists():
        raise FileNotFoundError(str(dest))
    return _add_and_push(dest, push=push)


def store_mission(mission_id: str, push: bool = True) -> dict:
    """パース結果も追跡し、Garage へ上げる。"""
    if not _dvc_importable():
        return {"ok": False, "error": "dvc not installed (requirements-dvc.txt)"}
    raw = raw_dir(mission_id)
    parsed = parsed_dir(mission_id)
    out: dict = {"ok": True, "mission_id": mission_id}
    if raw.exists():
        out["raw"] = _add_and_push(raw, push=push)
        out["ok"] = out["ok"] and bool(out["raw"].get("ok"))
    if parsed.exists():
        out["parsed"] = _add_and_push(parsed, push=push)
        out["ok"] = out["ok"] and bool(out["parsed"].get("ok"))
    if "raw" not in out and "parsed" not in out:
        return {"ok": False, "error": f"mission missing: {mission_id}"}
    out["stage"] = mission_detail(mission_id)["stage"]
    return out


def run_pipeline(source: Path, mission_id: str | None = None, push: bool = True) -> dict:
    """取込 → パース → 追跡 → 保管を一度に。"""
    imported = import_mission(source, mission_id)
    mid = imported["mission_id"]
    parsed = parse_mission(mid)
    tracked = track_mission(mid, push=False)
    stored = store_mission(mid, push=push)
    return {
        "ok": bool(parsed.get("ok") and stored.get("ok")),
        "mission_id": mid,
        "import": imported,
        "parse": {k: parsed[k] for k in ("summary", "parsed_dir", "ok") if k in parsed},
        "track": tracked,
        "store": stored,
        "stage": mission_detail(mid)["stage"],
        "frames": parsed.get("frames") or [],
        "lidar_files": parsed.get("lidar_files") or [],
        "bag_files": parsed.get("bag_files") or [],
        "series_files": parsed.get("series_files") or [],
        "raw_dir": imported.get("raw_dir"),
        "parsed_dir": parsed.get("parsed_dir"),
        "topics": (parsed.get("summary") or {}).get("topics") or [],
    }


def list_missions() -> list[dict]:
    raw_root().mkdir(parents=True, exist_ok=True)
    parsed_root().mkdir(parents=True, exist_ok=True)
    ids = {p.name for p in raw_root().iterdir() if p.is_dir()}
    ids |= {p.name for p in parsed_root().iterdir() if p.is_dir()}
    inbox = Path(settings.inbox_dir)
    if inbox.exists():
        ids |= {p.name for p in inbox.iterdir() if p.is_dir()}
    return [mission_detail(mid) for mid in sorted(ids)]


def mission_detail(mission_id: str) -> dict:
    raw = raw_dir(mission_id)
    parsed = parsed_dir(mission_id)
    inbox = Path(settings.inbox_dir) / mission_id
    summary = {}
    if (parsed / "summary.json").exists():
        summary = json.loads((parsed / "summary.json").read_text(encoding="utf-8"))
    manifest = {}
    for cand in (raw / "manifest.json", inbox / "manifest.json"):
        if cand.exists():
            manifest = json.loads(cand.read_text(encoding="utf-8"))
            break
    bags = []
    src = raw if raw.exists() else inbox
    if src.exists():
        for ext in ("*.mcap", "*.bag", "*.db3"):
            bags.extend(str(p.relative_to(src)) for p in src.rglob(ext))
        if (src / "metadata.yaml").exists():
            bags.append("metadata.yaml")
    tracked = tracked_pointer(mission_id).exists()
    parsed_ok = parsed.exists() and any(parsed.iterdir())
    return {
        "id": mission_id,
        "raw_exists": raw.exists() or inbox.exists(),
        "parsed_exists": parsed_ok,
        "tracked": tracked,
        "stored": tracked and parsed_pointer(mission_id).exists(),
        "manifest": manifest,
        "summary": summary,
        "bags": bags,
        "images": _count_images(parsed),
        "lidar_keyframes": _count_lidar(parsed),
        "stage": _stage(raw.exists() or inbox.exists(), tracked, parsed_ok, parsed_pointer(mission_id).exists()),
        "raw_dir": str(raw if raw.exists() else inbox),
        "parsed_dir": str(parsed),
    }


def mission_topics(mission_id: str) -> dict:
    src = raw_dir(mission_id)
    if not src.exists():
        src = Path(settings.inbox_dir) / mission_id
    if not src.exists():
        raise FileNotFoundError(mission_id)
    bags = discover_bags(src)
    if not bags:
        return {"topics": [], "note": "ROS2 bag が見つかりません。展開済みフォルダとして扱います。"}
    try:
        return {"topics": list_topics(bags[0]), "bag": str(bags[0])}
    except Exception as exc:
        return {"topics": [], "error": str(exc)}


def _stage(has_raw: bool, tracked: bool, parsed: bool, stored: bool) -> str:
    if stored:
        return "stored"
    if parsed and tracked:
        return "parsed"
    if tracked:
        return "tracked"
    if parsed:
        return "parsed"
    if has_raw:
        return "imported"
    return "empty"


def _copy_existing(src: Path, dest: Path) -> dict:
    counts = {"images": 0, "lidar_keyframes": 0, "gnss": 0, "odom": 0, "imu": 0, "action": 0}
    mapping = {
        "cameras": dest / "cameras",
        "lidar": dest / "lidar",
        "localization": dest / "localization",
        "series": dest / "series",
        "frames": dest / "cameras" / "front",
    }
    for name, target in mapping.items():
        origin = src / name
        if origin.exists():
            shutil.copytree(origin, target, dirs_exist_ok=True)
    counts["images"] = _count_images(dest)
    counts["lidar_keyframes"] = _count_lidar(dest)
    for kind in ("gnss", "odom", "imu", "action"):
        path = dest / "localization" / f"{kind}.jsonl"
        alt = dest / "series" / f"{kind}.jsonl"
        file = path if path.exists() else alt
        if file.exists():
            counts[kind] = sum(1 for line in file.read_text(encoding="utf-8").splitlines() if line.strip())
            if kind in {"gnss", "odom"}:
                loc = dest / "localization"
                loc.mkdir(exist_ok=True)
                if not path.exists():
                    shutil.copy(file, loc / f"{kind}.jsonl")
    return counts


def _scan_parsed(dest: Path) -> tuple[list[dict], list[dict]]:
    frames = []
    for img in sorted((dest / "cameras").rglob("*") if (dest / "cameras").exists() else []):
        if img.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        rel = img.relative_to(dest).as_posix()
        frames.append(
            {
                "path": img,
                "relpath": rel,
                "ts": None,
                "modality": _modality_from_rel(rel),
                "topic": "",
                "msgtype": "",
                "width": None,
                "height": None,
            }
        )
    lidar_files = []
    folder = dest / "lidar"
    if folder.exists():
        for path in sorted(folder.glob("*.bin")):
            lidar_files.append(
                {
                    "path": path,
                    "relpath": f"lidar/{path.name}",
                    "ts": None,
                    "topic": "",
                    "msgtype": "",
                    "n_points": path.stat().st_size // 12,
                }
            )
    return frames, lidar_files


def _series_files(dest: Path) -> list[dict]:
    out = []
    for folder in (dest / "localization", dest / "series", dest / "lidar"):
        if not folder.exists():
            continue
        for path in folder.glob("*.jsonl"):
            n = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            out.append({"kind": path.stem, "path": str(path), "n": n, "relpath": path.relative_to(dest).as_posix()})
    return out


def _count_images(parsed: Path) -> int:
    if not (parsed / "cameras").exists():
        return 0
    return sum(1 for p in (parsed / "cameras").rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


def _count_lidar(parsed: Path) -> int:
    folder = parsed / "lidar"
    if not folder.exists():
        return 0
    bins = list(folder.glob("*.bin"))
    if bins:
        return len(bins)
    meta = folder / "keyframes.jsonl"
    if meta.exists():
        return sum(1 for line in meta.read_text(encoding="utf-8").splitlines() if line.strip())
    return 0


def _modality_from_rel(rel: str) -> str:
    lower = rel.lower()
    if "rear" in lower:
        return "camera_rear"
    if "left" in lower:
        return "camera_left"
    if "right" in lower:
        return "camera_right"
    return "camera_front"


def _rel_to(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _files_of(bag: Path) -> list[Path]:
    bag = Path(bag)
    if bag.is_file():
        files = [bag]
        meta = bag.parent / "metadata.yaml"
        if meta.exists():
            files.append(meta)
        return files
    return [p for p in bag.rglob("*") if p.is_file()]
