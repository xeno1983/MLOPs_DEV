# データセット契約

一次データは **ROS2 bag** でも **ミッションフォルダ（画像＋センサ JSON）** でも入れられます。`manifest.json` が無ければ ingest が自動生成します。

## ミッションフォルダ（bag を含む）

```
data/inbox/<mission_id>/
  manifest.json                 # 任意。無ければフォルダ名から自動生成
  metadata.yaml + *.db3         # rosbag2（sqlite）
  *.mcap                        # rosbag2（MCAP）
  *.bag                         # ROS1 bag（rosbags が読める範囲）
  cameras/front/*.jpg           # 展開済み画像でも可。ファイル名末尾が Unix 秒または ns なら時刻に使う
  cameras/rear/*.jpg
  series/imu.jsonl              # bag が無いときのセンサ系列
  series/gnss.jsonl
  series/odom.jsonl
  series/action.jsonl           # cmd_vel / steering（E2E の教師）
  series/lidar.jsonl
  labels.jsonl                  # 知覚タスク用。E2E では不要
```

bag だけを置く場合:

```
data/inbox/M-20260831-001/          # または bag ディレクトリそのもの
  metadata.yaml
  rosbag2_2026_08_31_01_00_00_0.db3
```

単体ファイル `foo.mcap` を inbox に置いて `path` にそのファイルを渡しても取り込めます。

### manifest.json

```json
{
  "mission_id": "M-20260831-001",
  "vehicle_id": "UGV-01",
  "classification": "UNCLASSIFIED",
  "started_ts": "2026-08-31T01:00:00+00:00",
  "theater": "range-a",
  "sensors": ["camera_front", "imu", "lidar", "cmd_vel"],
  "notes": "after-action dump"
}
```

`classification` が UNCLASSIFIED 以外の場合、`learning_sample.declassified` が真になるまでデータセットに入りません。

## ROS2 bag から取り出すもの

ROS のインストールは不要です（Python の `rosbags`）。トピック種別はメッセージ型と名前から自動判定します。

| 種別 | 例 | カタログ modality |
|------|----|-------------------|
| カメラ | `sensor_msgs/Image`, `CompressedImage` | `camera_front` / `rear` / `left` / `right` |
| IMU | `sensor_msgs/Imu` | `imu` |
| GNSS | `sensor_msgs/NavSatFix` | `gnss` |
| オドメトリ | `nav_msgs/Odometry` | `odom` |
| 操作 | `Twist`, `AckermannDrive`, `/cmd_vel`, Autoware `control_cmd` | `action` |
| LiDAR | `PointCloud2`, `LaserScan` | `lidar`（キーフレームを `lidar/*.bin`（xyz float32）として保管。元 bag も残す） |
| 元 bag | `.db3` / `.mcap` / `.bag` + `metadata.yaml` | `rosbag` |

既定の間引き: 画像は 5 フレームに 1、トピックあたり最大 400 枚。LiDAR キーフレームは 20 に 1、最大 80、点は最大 80000。`.env` の `ROSBAG_FRAME_STRIDE` などで変更できます。

DVC GUI と同じ段階（取込 → 追跡 → パース → 保管）も使えます。`POST /ingest` はこれを一括でやります。段階だけ回すときは:

```
data/dvc/raw/missions/<id>/     # 取込した bag
data/dvc/parsed/<id>/cameras/
data/dvc/parsed/<id>/lidar/*.bin
data/dvc/parsed/<id>/localization/gnss.jsonl
```

```powershell
# 段階ごと
POST /missions/import          { "source_path": "C:/bags/run1", "mission_id": "M-001" }
POST /missions/M-001/track
POST /missions/M-001/parse
POST /missions/M-001/store
GET  /missions
```

Autoware 独自メッセージは bag 内に定義が無いとデシリアライズできません。その場合は `series/*.jsonl` を別途置いてください。

トピック一覧だけ見る:

```powershell
docker compose exec catalog python -m ugv_mlops.cli bag-topics /data/inbox/M-20260831-001
```

## 知覚（obstacle_cls）

**人手アノテーションが必須。** ingest だけでは `annotation` が空のままなので、`POST /datasets/build` は失敗する。`labels.jsonl` または Label Studio。クラスは `clear` / `obstacle` / `person`。

```json
{"sample_id":"smp_ab12...","label_type":"class","label_json":{"task":"obstacle_cls","class":"obstacle"},"annotator":"nco-1","tool":"labelstudio","gold":true}
```

`sample_id` はファイル SHA-256 の先頭 16 桁で `smp_<hex>` です。

検出へ拡張する場合は `label_type=bbox`。学習ジョブの追加が必要です。

## E2E（end-to-end）

カメラフレームと、同時刻付近の操作（`cmd_vel` 等）を揃えた `records.jsonl` を作ります。IMU / GNSS / odom / LiDAR メタがあれば特徴に含めます。人手ラベルは不要です。

1. bag または `series/action.jsonl` を ingest する。
2. `POST /datasets/build` で `"task":"e2e"`。
3. `POST /train`。指標は `mae_steer` / `mae_speed`（MLflow 実験 `ugv-e2e`）。
4. ゲートは staging で MAE 上限（既定: steer 0.45、speed 2.0）。

許容時間差は `E2E_ACTION_MAX_DT_SEC`（既定 0.25 秒）。揃うフレームが 8 未満だとデータセット構築は失敗します。

ベースライン学習器は画像統計＋センサを入力とする Ridge です。本番の時系列ネットワークに差し替えるときは `src/ugv_mlops/e2e.py` の `train_e2e` を置き換えてください。

## 公開 rosbag2（CostNav → E2E）

公式 Autoware サンプル bag はプライバシーのため **カメラが無い**。代わりに公開の [CostNav Teleop](https://huggingface.co/datasets/maum-ai/CostNav-Teleop-Dataset)（ROS 2 Jazzy MCAP、前方圧縮画像・LiDAR・IMU・odom・`/cmd_vel`）を短く切って inbox に置きます。人手ラベルは不要で E2E の 8 工程が回ります。クリップが短いためゲートは `smoke`（MAE 上限は staging より緩い）。実任務モデルは staging / production を使います。

```powershell
.\scripts\prepare-av-rosbag.ps1
# またはポータルの「rosbag2デモ」
Invoke-RestMethod -Method POST http://localhost:8000/demo-rosbag
```

inbox は `data/inbox/M-AVBAG-001/`（`metadata.yaml` + `.db3`）。元エピソードは cache `data/cache/av_rosbag/`。任務データではなくパイプライン確認用です。

## 検出（YOLOX + 公開データセット）

サンプルは **COCO128**（Ultralytics が配っている COCO train2017 の 128 枚）と公式 **YOLOX-Nano** 重みです。

```powershell
.\scripts\prepare-yolox-sample.ps1
# またはポータルの「YOLOXデモ」
Invoke-RestMethod -Method POST http://localhost:8000/demo-yolox
```

inbox は `data/inbox/M-COCO128/`。bbox は `labels.jsonl` の `label_type=bbox`。

```json
{"sample_id":"smp_ab12...","label_type":"bbox","label_json":{"task":"detection","boxes":[{"class":"person","class_id":0,"x":10,"y":20,"w":30,"h":40}]},"annotator":"coco","tool":"coco128-public","gold":true}
```

データセットは `task=detection` で COCO JSON を切り、学習は YOLOX-Nano（COCO 80 クラス、公式 nano 重みから短い fine-tune）。ゲート指標は `map50`。成果物は `full_weights`（`.pth`）で S3/S4 パッケージ向けです。

COCO128 は学習用 COCO の部分集合なので、このデモの mAP はパイプライン確認用です。実任務データでは holdout を別ミッションから取ってください。

