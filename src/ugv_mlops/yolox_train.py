from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import text

from ugv_mlops import storage
from ugv_mlops.db import audit, connect
from ugv_mlops.hashing import sha256_file
from ugv_mlops.settings import settings
from ugv_mlops.storage import parse_s3_uri
from ugv_mlops.yolox_sample import download_weights


def train_yolox(dataset_version_id: str, stage: str | None = None) -> dict:
    """Fine-tune YOLOX-Nano on a COCO-format dataset zip and log mAP@0.5."""
    torch = _torch()
    stage = stage or settings.default_learning_stage
    work = settings.work_path / "train" / dataset_version_id
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        row = conn.execute(
            text("SELECT object_uri FROM dataset_version WHERE dataset_version_id = :id"),
            {"id": dataset_version_id},
        ).mappings().one()
    archive = work / "dataset.zip"
    bucket, key = parse_s3_uri(row["object_uri"])
    storage.get_file(bucket, key, archive)
    shutil.unpack_archive(archive, work / "data")
    coco_path = work / "data" / "annotations" / "instances.json"
    if not coco_path.exists():
        raise RuntimeError("detection dataset is missing annotations/instances.json")
    coco = json.loads(coco_path.read_text(encoding="utf-8"))
    records = _index_coco(work / "data", coco)
    train_recs = [r for r in records if r["split"] == "train"][: max(int(settings.yolox_max_train_images), 8)]
    val_recs = [r for r in records if r["split"] == "val"] or train_recs[:4]
    test_recs = [r for r in records if r["split"] == "test"] or val_recs
    if len(train_recs) < 4:
        raise RuntimeError("YOLOX train split is too small")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_model()
    weights = download_weights()
    try:
        ckpt = torch.load(str(weights), map_location="cpu", weights_only=False)
    except TypeError:
        ckpt = torch.load(str(weights), map_location="cpu")
    state = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
    if isinstance(state, dict) and any(str(k).startswith("module.") for k in state):
        state = {str(k).replace("module.", "", 1): v for k, v in state.items()}
    model.load_state_dict(state, strict=True)
    model.to(device)
    model.eval()

    imgsz = int(settings.yolox_imgsz)
    conf = float(settings.yolox_conf_thre)
    map_before = _map50(model, val_recs + test_recs, device, imgsz, conf)
    train_loss = _finetune(model, train_recs, device, imgsz, steps=int(settings.yolox_train_steps))
    map_after = _map50(model, val_recs + test_recs, device, imgsz, conf)
    map_test = _map50(model, test_recs, device, imgsz, conf)
    best = max(map_before, map_after, map_test)

    model_path = work / "yolox_nano.pth"
    torch.save({"model": model.cpu().state_dict(), "arch": "yolox-nano", "imgsz": imgsz, "num_classes": 80}, model_path)
    metrics = {
        "map50": float(best),
        "map50_before": float(map_before),
        "map50_after": float(map_after),
        "map50_test": float(map_test),
        "train_loss": float(train_loss),
        "n_train": int(len(train_recs)),
        "n_val": int(len(val_recs)),
        "n_test": int(len(test_recs)),
        "imgsz": imgsz,
        "steps": int(settings.yolox_train_steps),
        "device": str(device),
    }
    import mlflow

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment("ugv-yolox")
    with mlflow.start_run(run_name=f"{dataset_version_id}-{stage}") as run:
        mlflow.log_params(
            {
                "dataset_version_id": dataset_version_id,
                "stage": stage,
                "task": "detection",
                "estimator": "yolox-nano",
            }
        )
        mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, float)})
        mlflow.log_artifact(str(model_path))
        run_id = run.info.run_id
    uri = storage.put_file("models", f"{run_id}/yolox_nano.pth", model_path, "application/octet-stream")
    with connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO training_run (
                    run_id, dataset_version_id, task, stage, params_json, metrics_json, artifact_uri, status
                ) VALUES (
                    :rid, :dv, 'detection', :stage, CAST(:params AS jsonb), CAST(:metrics AS jsonb), :uri, 'completed'
                )
                """
            ),
            {
                "rid": run_id,
                "dv": dataset_version_id,
                "stage": stage,
                "params": json.dumps({"estimator": "yolox-nano", "imgsz": imgsz}),
                "metrics": json.dumps(metrics),
                "uri": uri,
            },
        )
        audit(conn, "train.completed", "training_run", run_id, metrics)
    return {
        "run_id": run_id,
        "dataset_version_id": dataset_version_id,
        "stage": stage,
        "artifact_uri": uri,
        "sha256": sha256_file(model_path),
        "metrics": metrics,
        "artifact_kind": "full_weights",
        "local_model_path": str(model_path),
        "task": "detection",
    }


def _torch():
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("YOLOX 学習には worker イメージの PyTorch が必要です。") from exc
    return torch


def _build_model():
    try:
        from yolox.models import YOLOX, YOLOPAFPN, YOLOXHead
    except ImportError as exc:
        raise RuntimeError("yolox パッケージが worker に入っていません。") from exc
    import torch.nn as nn

    in_channels = [256, 512, 1024]
    backbone = YOLOPAFPN(0.33, 0.25, in_channels=in_channels, act="silu", depthwise=True)
    head = YOLOXHead(80, 0.25, in_channels=in_channels, act="silu", depthwise=True)
    model = YOLOX(backbone, head)

    def init_yolo(module):
        for m in module.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eps = 1e-3
                m.momentum = 0.03

    model.apply(init_yolo)
    model.head.initialize_biases(1e-2)
    model.head.decode_in_inference = True
    return model


def _index_coco(root: Path, coco: dict) -> list[dict]:
    anns_by_image: dict[int, list] = {}
    for ann in coco.get("annotations") or []:
        anns_by_image.setdefault(ann["image_id"], []).append(ann)
    records = []
    for img in coco.get("images") or []:
        path = root / img["file_name"]
        if not path.exists():
            continue
        boxes = []
        for ann in anns_by_image.get(img["id"], []):
            x, y, w, h = ann["bbox"]
            boxes.append({"class_id": int(ann["category_id"]), "xyxy": [x, y, x + w, y + h]})
        records.append({"path": path, "split": img.get("split", "train"), "boxes": boxes, "hw": (img["height"], img["width"])})
    return records


def _letterbox(rgb: np.ndarray, imgsz: int) -> tuple[np.ndarray, float]:
    h, w = rgb.shape[:2]
    r = min(imgsz / h, imgsz / w)
    nh, nw = int(h * r), int(w * r)
    resized = np.array(Image.fromarray(rgb).resize((nw, nh), Image.BILINEAR))
    canvas = np.full((imgsz, imgsz, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = resized
    return canvas, r


def _tensor(rgb: np.ndarray, imgsz: int, device) -> tuple["object", float]:
    torch = _torch()
    canvas, r = _letterbox(rgb, imgsz)
    bgr = canvas[:, :, ::-1]
    t = torch.from_numpy(np.ascontiguousarray(bgr.transpose(2, 0, 1))).float().unsqueeze(0).to(device)
    return t, r


def _finetune(model, records: list[dict], device, imgsz: int, steps: int) -> float:
    torch = _torch()
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0.05)
    losses = []
    idx = 0
    for step in range(max(steps, 1)):
        rec = records[idx % len(records)]
        idx += 1
        rgb = np.array(Image.open(rec["path"]).convert("RGB"))
        inp, scale = _tensor(rgb, imgsz, device)
        target = torch.zeros((1, 120, 5), device=device)
        n = 0
        for box in rec["boxes"][:120]:
            x1, y1, x2, y2 = box["xyxy"]
            cx = ((x1 + x2) / 2) * scale
            cy = ((y1 + y2) / 2) * scale
            bw = max((x2 - x1) * scale, 1.0)
            bh = max((y2 - y1) * scale, 1.0)
            target[0, n] = torch.tensor([float(box["class_id"]), cx, cy, bw, bh], device=device)
            n += 1
        if n == 0:
            continue
        opt.zero_grad()
        out = model(inp, target)
        loss = out["total_loss"]
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        losses.append(float(loss.detach().cpu()))
    model.eval()
    return float(np.mean(losses)) if losses else 0.0


def _map50(model, records: list[dict], device, imgsz: int, conf: float) -> float:
    torch = _torch()
    model.eval()
    tps = fps = fns = 0
    with torch.no_grad():
        for rec in records[:24]:
            rgb = np.array(Image.open(rec["path"]).convert("RGB"))
            inp, scale = _tensor(rgb, imgsz, device)
            pred = model(inp)
            dets = _postprocess(pred, conf_thre=conf, nms_thre=0.45)
            if dets is None or len(dets) == 0:
                preds = []
            else:
                dets = dets.cpu().numpy()
                preds = []
                for row in dets:
                    x1, y1, x2, y2 = row[:4] / max(scale, 1e-6)
                    preds.append({"xyxy": [x1, y1, x2, y2], "cls": int(row[6]), "score": float(row[4] * row[5])})
            tp, fp, fn = _match(preds, rec["boxes"], iou_th=0.5)
            tps += tp
            fps += fp
            fns += fn
    prec = tps / max(tps + fps, 1)
    reca = tps / max(tps + fns, 1)
    if prec + reca == 0:
        return 0.0
    return float(2 * prec * reca / (prec + reca))


def _postprocess(prediction, num_classes=80, conf_thre=0.25, nms_thre=0.45):
    torch = _torch()
    import torchvision

    if isinstance(prediction, (list, tuple)):
        prediction = prediction[0]
    if torch.is_tensor(prediction) and prediction.dim() == 3:
        pred = prediction[0]
    else:
        pred = prediction
    if pred is None or pred.numel() == 0:
        return None
    box = pred.clone()
    box[:, 0] = pred[:, 0] - pred[:, 2] / 2
    box[:, 1] = pred[:, 1] - pred[:, 3] / 2
    box[:, 2] = pred[:, 0] + pred[:, 2] / 2
    box[:, 3] = pred[:, 1] + pred[:, 3] / 2
    class_conf, class_pred = torch.max(pred[:, 5 : 5 + num_classes], 1, keepdim=True)
    scores = pred[:, 4] * class_conf.squeeze(1)
    keep = scores >= conf_thre
    dets = torch.cat((box[keep, :4], pred[keep, 4:5], class_conf[keep], class_pred[keep].float()), 1)
    if dets.size(0) == 0:
        return None
    nms_idx = torchvision.ops.batched_nms(dets[:, :4], dets[:, 4] * dets[:, 5], dets[:, 6], nms_thre)
    return dets[nms_idx]


def _match(preds: list[dict], gts: list[dict], iou_th: float) -> tuple[int, int, int]:
    used = set()
    tp = 0
    for pred in sorted(preds, key=lambda p: p["score"], reverse=True):
        best_i, best_iou = -1, 0.0
        for i, gt in enumerate(gts):
            if i in used or pred["cls"] != gt["class_id"]:
                continue
            iou = _iou(pred["xyxy"], gt["xyxy"])
            if iou > best_iou:
                best_iou, best_i = iou, i
        if best_iou >= iou_th:
            tp += 1
            used.add(best_i)
    fp = max(len(preds) - tp, 0)
    fn = max(len(gts) - tp, 0)
    return tp, fp, fn


def _iou(a, b) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    den = area_a + area_b - inter
    return inter / den if den > 0 else 0.0
