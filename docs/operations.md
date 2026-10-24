# 運用手順

## 日常の流れ

1. UGV 帰還または物理媒体から `data/inbox/<mission_id>/` へコピーする。ROS2 bag（`metadata.yaml` + `.db3` / `.mcap`）でも、画像＋`series/*.jsonl` でも可。
2. `POST /ingest` でカタログ化する。bag はカメラ JPEG・センサ系列・元ファイルを Garage `raw` へ。
3. 知覚（分類・検出）は **アノテーション必須**。Label Studio または `labels.jsonl`。未ラベルではデータセットを切らない。E2E だけ `cmd_vel` が教師で画像クラスは不要（知覚の省略ではない）。
4. `POST /datasets/build`。知覚は `task=obstacle_cls`、E2E は `task=e2e`。
5. `POST /train` → MLflow で指標確認（知覚は accuracy、E2E は mae_steer / mae_speed）。
6. `POST /gate`（staging）。本番相当は `gate_name=production`。
7. `POST /packages` で車載向け zip を切る。
8. `POST /sync` のあと `POST /sync/{tx_id}/chunk` をリンクが開いている間繰り返す。

## 初期 ID / パスワード

手順の一覧は [usage.md の「画面の場所と初期 ID / パスワード」](usage.md#画面の場所と初期-id--パスワード) です。よく使うものだけここにも書きます（`.env` のデモ値。機密区画では変更）。

| ツール | ID | パスワード |
|--------|----|------------|
| Label Studio `:8081` | `admin@ugv.local` | `label_change_me` |
| Garage S3 `:9000` | `GKabcdef0123456789abcdef01234567` | `ugv_garage_secret_change_me_do_not_use_in_prod` |
| Grafana `:3001`（拡張） | `admin` | `grafana_change_me` |
| LakeFS `:8001`（拡張） | Access Key `AKIAUGVLAKEFS01` | Secret `lakefs_secret_change_me_32chars!` |
| Xtreme1 `:8190` | 初回 Sign up | 自分で設定 |
| Xtreme1 MinIO `:8194` | `admin` | `1tQB970y` |
| ポータル / Catalog / MLflow / SALT | なし | なし |
| Gitea `:3000`（拡張） | 初回に管理者を新規登録 | 登録時に設定 |

## Label Studio

初回ログイン: `admin@ugv.local` / `label_change_me`（`.env` の `LABEL_STUDIO_USERNAME` / `LABEL_STUDIO_PASSWORD`）。

プロジェクト作成時、ラベル構成に次を貼り付けます。

- 検出: `configs/labelstudio/ugv_detection.xml`
- 走行可能性: `configs/labelstudio/ugv_traversability.xml`

ストレージは Garage（http://garage:3900、ホストからは `localhost:9000`、バケット `raw`）を Label Studio の S3 ソースに指定できます。Web コンソールはありません。

Freespace / LiDAR / カメラ点群の突き合わせは [annotation.md](annotation.md)（SAM・SALT・Xtreme1）。Xtreme1 は Autoware_e2e の compose（`:8190`）で、本スタックの Garage とは分けます。

## LakeFS / Gitea（任意）

既定起動では止まります。使うときだけ:

```powershell
docker compose --profile full up -d
```

LakeFS は http://127.0.0.1:8001。Access Key `AKIAUGVLAKEFS01` / Secret `lakefs_secret_change_me_32chars!`。ブロックストアは Garage バケット `lakefs`。データセット zip の版の正は Catalog `dataset_version` です。Gitea は http://127.0.0.1:3000（初回に管理者を新規登録）。

## ゲートに落ちるとき

`promotion_event.gate_json.reasons` を見ます。典型:

- 精度不足 → データ不足またはクラス偏り
- FPR 超過 → clear を障害に誤る
- mae_steer / mae_speed 超過 → E2E の操作予測がゲート上限を超えている
- アーティファクト種別が段で不許可 → S1 に `joblib` フル重みを載せていないか
- 任務モードが production ゲートで許可されていない

## 監査

`audit_log` に ingest / dataset / train / gate / package / sync が時系列で残ります。
