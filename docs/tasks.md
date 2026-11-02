# 学習タスク一覧（いま動くもの）

Catalog の `dataset.task` と、版切り・学習・ゲート・デモの対応を固定する。フォルダ契約の詳細は [dataset.md](dataset.md)、工程の流れは [pipeline-stages.md](pipeline-stages.md)、ポリシーは `configs/artifact_policy.yaml`。

コード上の入口:

| 段 | 関数 |
|----|------|
| 版切り | `ugv_mlops.dataset.build_dataset` → タスク別 `build_*` |
| 学習 | `ugv_mlops.train.train_from_dataset` → タスク別 `train_*` |
| ゲート | `ugv_mlops.gate.evaluate_and_promote`（指標は metrics キーで分岐） |
| ワーカー | 既定は compose `worker`。`autoe2e` だけ `runner=host`（GPU ホストワーカー） |

---

## 早見表

| task（正） | 別名（API で可） | 人手ラベル | データ format | 学習器 | 成果物 kind | ゲート主指標 | 実行場所 | ワンクリック確認 |
|------------|------------------|------------|---------------|--------|-------------|--------------|----------|------------------|
| `obstacle_cls` | （その他未知 task はここに落ちる） | **必須** `class` | `imagefolder` | LogisticRegression | `joblib` | `accuracy` / FPR | Docker worker | ポータル「知覚デモ」`POST /demo` |
| `detection` | `yolox`, `yolox_nano` | **必須** `bbox` | `coco-detection-v1` | YOLOX-Nano fine-tune | `full_weights` | `map50` | Docker worker（CPU 可） | 「YOLOXデモ」`POST /demo-yolox` |
| `e2e` | `e2e_sequence`, `end_to_end` | **不要**（cmd_vel） | `e2e-records-v1` | Ridge | `joblib` | `mae_steer` / `mae_speed` | Docker worker | 「E2Eデモ」`POST /demo-e2e` / 「rosbag2デモ」`POST /demo-rosbag` |
| `autoe2e` | `auto_e2e`, `autoe2e_finetune` | **不要**（bag→scene） | `autoe2e-scenes-v1` | Autoware AutoE2E 微調整 | `full_weights` | `ade_6p4s_m` 系 | **ホスト GPU ワーカーのみ** | ポータルで task=autoe2e（デモボタンなし） |

ポータル工程7の選択肢は上表の4つ（`obstacle_cls` / `e2e` / `detection` / `autoe2e`）。

---

## 1. `obstacle_cls`（知覚・画像分類）

| 項目 | 内容 |
|------|------|
| 目的 | 前方画像を `clear` / `obstacle` / `person` に分類 |
| 教師 | `annotation.label_type=class`、`label_json.class` が上記3クラス |
| 版切り | ラベル付きカメラを ImageFolder（`train|val|test/<class>/*.jpg`）に zip |
| 学習 | `train_obstacle_classifier`。MLflow 実験 `ugv-obstacle-cls` |
| 成果物 | `model.joblib`（`artifact_kind=joblib`）。S3/S4 梱包向き。S1 へは工程10で `adapter` 等へ |
| ゲート | `accuracy`（staging≥0.70 / production≥0.82）、`false_positive_rate` |
| 工程スキップ | 工程5–6 **不可**（ラベル必須）。工程1・10は状況次第 |
| 確認方法 | `POST /demo` または inbox に画像+`labels.jsonl` → ingest → data-qa → annotate → dataset(`obstacle_cls`) → train → gate |

クラス定義の正本（実行は現状 `train.py` の `CLASSES` も参照）: `configs/artifact_policy.yaml` → `tasks.obstacle_cls.classes`。

---

## 2. `detection`（物体検出・YOLOX）

| 項目 | 内容 |
|------|------|
| 目的 | カメラ上の bbox 検出。学習器は YOLOX-Nano（COCO 80 クラス重みから短 fine-tune） |
| 教師 | `label_type=bbox`。`label_json.boxes[]` に `class` / `class_id` / `x,y,w,h` |
| 版切り | `build_detection_dataset` → COCO JSON `annotations/instances.json` + `images/{split}/` |
| 学習 | `train_yolox`。指標 `map50`。MLflow 実験は実装側ログ参照（`ugv-yolox` 想定） |
| 成果物 | `yolox_nano.pth`（`full_weights`）。**S3/S4 のみ**（S1 ホワイトリスト外） |
| ゲート | `map50`（staging≥0.05 / production≥0.20 / smoke≥0.0） |
| 最低枚数 | bbox 付き画像 **8 枚以上**（未満は版切り失敗） |
| 確認方法 | `POST /demo-yolox`（初回は COCO128 + 公式 nano 重み取得）。または `prepare-coco128` → ingest → dataset(`detection`) → train |

ポリシーの `tasks.detection.classes`（person/vehicle/…）は任務向けの宣言。公開デモは COCO 80 クラス重みを使う。

---

## 3. `e2e`（簡易 end-to-end・操舵/速度）

| 項目 | 内容 |
|------|------|
| 目的 | カメラ＋センサ特徴から `steer` / `speed` を回帰（ベースライン Ridge） |
| 教師 | bag または `series/action.jsonl` の cmd_vel 等。**人手クラスラベル不要** |
| 版切り | `build_e2e_dataset` → `records.jsonl`（format `e2e-records-v1`） |
| 学習 | `train_e2e`。MLflow 実験 `ugv-e2e` |
| 成果物 | `model.joblib` |
| ゲート | `mae_steer` / `mae_speed`（staging: 0.45 / 2.0、production: 0.25 / 1.0、smoke: 緩い） |
| 揃え条件 | カメラと action の時刻差 ≤ `E2E_ACTION_MAX_DT_SEC`（既定 0.25s）。揃うフレームが少なすぎると失敗 |
| 工程スキップ | 工程5–6 **可**。公開短クリップはゲート `smoke` |
| 確認方法 | `POST /demo-e2e`（合成）または `POST /demo-rosbag`（CostNav 公開 MCAP → inbox `M-AVBAG-001`） |

本番の時系列ネットに差し替えるときは `src/ugv_mlops/e2e.py` の `train_e2e` を置き換える想定。

---

## 4. `autoe2e`（Autoware AutoE2E 微調整）

| 項目 | 内容 |
|------|------|
| 目的 | Autoware_e2e の Reactive / AutoE2E をオフロード bag から scene 切出し→微調整 |
| 教師 | rosbag2 からツールが scene を生成（人手ラベル不要） |
| 版切り | `build_autoe2e_dataset`（**mission_id 必須**）。format `autoe2e-scenes-v1`。実装は Autoware_e2e の `tools/rosbag2_autoe2e` をサブプロセス呼び出し |
| 学習 | `train_autoe2e` → `tools/auto_e2e_finetune_offroad.py`。**CUDA 必須** |
| 成果物 | `full_weights`。学習段は **S3 / S4 のみ** |
| ゲート | ADE/FDE 系（`ade_6p4s_m` 等）。親より改善・定速度比などの閾値は `artifact_policy.yaml` の staging/production |
| ワーカー | `jobs.runner_for` が `host` を付ける。compose の docker worker では動かない → `python -m ugv_mlops.host_worker`（または `launcher/MLOPS_gpu_worker.*`） |
| 前提 | `.env` の `AUTOWARE_E2E_ROOT`、Autoware 側の Python/venv、ホストから Garage/MLflow/Postgres に届くこと |
| 確認方法 | ポータル工程7で `autoe2e` + ミッション ID → 版切りジョブが host 待ち → ホストワーカー起動 → train → gate。**ワンクリックデモは未用意** |

---

## ラベル種別（アノテーション）との対応

| `label_type` | 使う task | ツール目安 |
|--------------|-----------|------------|
| `class` | `obstacle_cls` | Label Studio（工程5） |
| `bbox` | `detection` | Label Studio + `ugv_detection.xml`、または `labels.jsonl` |
| （なし・action 系列） | `e2e` | bag / `series/action.jsonl` |
| （なし・scene 切出し） | `autoe2e` | Autoware ツール |

マスク（Freespace / SAM）や LiDAR semantic（SALT）は現状 **版切り・学習タスクとしては未配線**（アノテ工程と import のみ）。将来タスク追加時の候補。

---

## デモ API と想定パス

| API | 想定 task | 備考 |
|-----|-----------|------|
| `POST /demo` | `obstacle_cls` | 合成 JPEG + class。11 工程のうちデモ用に短縮 |
| `POST /demo-e2e` | `e2e` | 合成カメラ + cmd_vel |
| `POST /demo-yolox` | `detection` | 初回ネット取得あり |
| `POST /demo-rosbag` | `e2e` | 公開 CostNav。ゲートは `smoke` |

---

## まだ学習タスクになっていないもの

| 領域 | 現状 |
|------|------|
| Freespace / SAM マスク | annotate import まで。`task` なし |
| LiDAR semantic（SALT） | annotate import まで。`task` なし |
| 独自検出クラス集合のみの学習 | ポリシーに宣言あり。学習器は COCO80 YOLOX 固定 |
| Genesis / CARLA 直接学習 | CARLA は [carla.md](carla.md) 経由で bag→inbox。学習は上表の task 経由（シミュ内ループはしない） |

---

## 変更するときの目安

- **クラス追加（obstacle_cls）**: Label Studio 設定 + `artifact_policy.yaml` + `train.CLASSES` + 新 `dataset_version`
- **新 task**: `build_*` / `train_*` / gate 指標 / ポータル選択肢 / 本表を追加（詳細は設計時のタスクレジストリ案）
- **format 変更**: 新 `dataset_version.format` を切り、学習ローダだけ分岐。旧版 ID は残す

---

## 関連

- [dataset.md](dataset.md) — inbox / labels.jsonl / bag 契約
- [annotation.md](annotation.md) — 工程5–6 のツール分担
- [usage.md](usage.md) — 日常操作とデモ
- [design.md](design.md) §5.4 — アーキ上のタスク表
- `configs/artifact_policy.yaml` — ゲート閾値・段ホワイトリスト・tasks 宣言
