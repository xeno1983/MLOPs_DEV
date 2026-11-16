# 運用手順

日常の流れはポータル **11 工程**（[pipeline-stages.md](pipeline-stages.md)）に合わせる。画面つき手順は [usage.md](usage.md) と [procedure/UGV_MLOps_データ取込からデプロイ手順.pptx](procedure/UGV_MLOps_データ取込からデプロイ手順.pptx)。

## 日常の流れ

1. **（任意）シミュ** — CARLA / Autoware_e2e で bag を録り `data/inbox/<mission_id>/` へ。`POST /pipeline/sim-generate`（ミッション指定で実在検証）。
2. **置く** — 実機帰還・シミュ・公開サンプルを同じ inbox に。
3. **`POST /ingest`** — カメラ JPEG・センサ系列・元 bag を SeaweedFS `raw` / LakeFS へ。
4. **`POST /data-qa`** — 空 bag・未取込・カメラなし等は不合格（ジョブ失敗）。
5. **知覚ならアノテ** — `POST /annotate/init`（Label Studio / SALT）。E2E はスキップ可。LS 投入失敗はジョブ失敗。
6. **知覚なら確認** — Xtreme1 で突き合わせ後 `POST /label-review`。
7. **`POST /datasets/build`** — 知覚は `obstacle_cls` / `detection`（`mission_id` で絞り可）。E2E は `task=e2e`。
8. **`POST /train`** → MLflow（知覚 accuracy、E2E mae_steer / mae_speed、検出 map50）。
9. **`POST /gate`** — staging / production / smoke。実サイズも検査。
10. **`POST /models/export`** — ONNX（S3/S4）または adapter（S1）。失敗は偽成功しない。
11. **`POST /packages` → `/sync` → `/sync/{tx}/chunk`** — zip を `data/work/vehicles/<車両>/inbox/` へ実書き込み。完了で `acked`。

## 初期 ID / パスワード

手順の一覧は [usage.md の「画面の場所と初期 ID / パスワード」](usage.md#画面の場所と初期-id--パスワード) です。よく使うものだけここにも書きます（`.env` のデモ値。機密区画では変更）。

| ツール | ID | パスワード |
|--------|----|------------|
| Label Studio `:8081` | `admin@ugv.local` | `label_change_me` |
| SeaweedFS S3 `:9000` | `.env` の `MINIO_ROOT_USER` | `.env` の `MINIO_ROOT_PASSWORD` |
| Grafana `:3001`（拡張） | `admin` | `grafana_change_me` |
| LakeFS `:8001`（中核） | Access Key `AKIAUGVLAKEFS01` | Secret `lakefs_secret_change_me_32chars!` |
| Xtreme1 `:8190` | 初回 Sign up | 自分で設定 |
| Xtreme1 MinIO `:8194` | `admin` | `1tQB970y` |
| ポータル / Catalog / MLflow / SALT | なし | なし |
| Gitea `:3000`（拡張） | 初回に管理者を新規登録 | 登録時に設定 |

## Label Studio

初回ログイン: `admin@ugv.local` / `label_change_me`（`.env` の `LABEL_STUDIO_USERNAME` / `LABEL_STUDIO_PASSWORD`）。

プロジェクト作成時、ラベル構成に次を貼り付けます。

- 検出: `configs/labelstudio/ugv_detection.xml`
- 走行可能性: `configs/labelstudio/ugv_traversability.xml`

ストレージは SeaweedFS（コンテナ `http://seaweed:8333`、ホスト `localhost:9000`、バケット `raw`）を Label Studio の S3 ソースに指定できます。

Freespace / LiDAR / カメラ点群の突き合わせは [annotation.md](annotation.md)（SAM・SALT・Xtreme1）。Xtreme1 は **工程6**（Autoware_e2e の compose `:8190`）で、本スタックの SeaweedFS とは分けます。

## LakeFS / Gitea

LakeFS は中核（既定起動）。http://127.0.0.1:8001。Access Key `AKIAUGVLAKEFS01` / Secret `lakefs_secret_change_me_32chars!`。ブロックストアは SeaweedFS バケット `lakefs`。初回は `lakefs-init` が setup + `ugv-datasets` を作成。データセット zip の学習 ID の正は Catalog `dataset_version`、履歴の正は LakeFS。

Gitea は拡張プロファイル:

```powershell
docker compose --profile full up -d
```

http://127.0.0.1:3000（初回に管理者を新規登録）。

## ゲートに落ちるとき

`promotion_event.gate_json.reasons` を見ます。典型:

- 精度不足 → データ不足またはクラス偏り
- FPR 超過 → clear を障害に誤る
- mae_steer / mae_speed 超過 → E2E の操作予測がゲート上限を超えている
- アーティファクト種別が段で不許可 → S1 に `joblib` / `onnx` を載せていないか
- アーティファクトサイズ超過 → 段の `max_bytes`
- 任務モードが production ゲートで許可されていない

## 監査

`audit_log` に `pipeline.sim_generate` / `data_qa.*` / `label_review.*` / ingest / dataset / train / gate / `model.lightened` / package / sync が時系列で残ります。
