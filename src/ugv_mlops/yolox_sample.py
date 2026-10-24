from __future__ import annotations

import json
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

from ugv_mlops.coco_constants import COCO_CLASSES
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings

COCO128_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip"
YOLOX_NANO_URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.pth"
MISSION_ID = "M-COCO128"


def _cache_dir() -> Path:
    path = Path(settings.yolox_cache_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


def download_weights(dest: Path | None = None) -> Path:
    dest = Path(dest or settings.yolox_weights_path)
    return _download(YOLOX_NANO_URL, dest)


def prepare_coco128(dest: Path | None = None, max_images: int | None = None) -> dict:
    """Download COCO128 (Ultralytics, COCO train2017 subset) and write an inbox mission."""
    dest = Path(dest or (settings.inbox_path / MISSION_ID))
    dest.mkdir(parents=True, exist_ok=True)
    cache = _cache_dir()
    zip_path = _download(COCO128_URL, cache / "coco128.zip")
    extract = cache / "coco128"
    marker = extract / "images" / "train2017"
    nested = extract / "coco128" / "images" / "train2017"
    if not marker.exists() and not nested.exists():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract)
    root = extract / "coco128" if (extract / "coco128" / "images").exists() else extract
    img_dir = root / "images" / "train2017"
    label_dir = root / "labels" / "train2017"
    if not img_dir.exists():
        raise FileNotFoundError(f"COCO128 images not found under {root}")

    cam = dest / "cameras" / "front"
    cam.mkdir(parents=True, exist_ok=True)
    images = sorted(p for p in img_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    if max_images:
        images = images[: int(max_images)]

    labels_out = []
    used = 0
    for src in images:
        stem = src.stem
        lbl = label_dir / f"{stem}.txt"
        boxes = _read_yolo_txt(lbl, src) if lbl.exists() else []
        if not boxes:
            continue
        target = cam / f"{stem}.jpg"
        if src.suffix.lower() != ".jpg":
            Image.open(src).convert("RGB").save(target, quality=92)
        else:
            target.write_bytes(src.read_bytes())
        digest = sha256_file(target)
        sample_id = f"smp_{digest[:16]}"
        labels_out.append(
            {
                "sample_id": sample_id,
                "label_type": "bbox",
                "label_json": {"task": "detection", "dataset": "coco128", "boxes": boxes},
                "annotator": "coco",
                "tool": "coco128-public",
                "gold": True,
            }
        )
        used += 1

    manifest = {
        "mission_id": MISSION_ID,
        "vehicle_id": "UGV-01",
        "classification": "UNCLASSIFIED",
        "sensors": ["camera_front"],
        "notes": "COCO128 public sample (Ultralytics subset of COCO train2017) for YOLOX-Nano pipeline smoke",
        "license": "COCO images: Flickr terms / COCO annotations CC-BY 4.0. See https://cocodataset.org",
        "source_url": COCO128_URL,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    with (dest / "labels.jsonl").open("w", encoding="utf-8") as fh:
        for rec in labels_out:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    weights = download_weights()
    return {
        "mission_dir": str(dest),
        "images": used,
        "labels": len(labels_out),
        "weights": str(weights),
        "dataset": "coco128",
    }


def _read_yolo_txt(path: Path, image_path: Path) -> list[dict]:
    with Image.open(image_path) as img:
        width, height = img.size
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls_id = int(float(parts[0]))
        xc, yc, bw, bh = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        w = bw * width
        h = bh * height
        x = xc * width - w / 2
        y = yc * height - h / 2
        name = COCO_CLASSES[cls_id] if 0 <= cls_id < len(COCO_CLASSES) else "unknown"
        boxes.append(
            {
                "class": name,
                "class_id": cls_id,
                "x": round(max(x, 0.0), 2),
                "y": round(max(y, 0.0), 2),
                "w": round(max(w, 1.0), 2),
                "h": round(max(h, 1.0), 2),
            }
        )
    return boxes
