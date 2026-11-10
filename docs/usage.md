# 使い方

学習・データセット構築・署名・車載同期は、**起動後はインターネット不要**です。クラウド API も呼びません。

入口の画面 http://127.0.0.1:8080 に、ダッシュボードと 11 工程が出ます（定義は [pipeline-stages.md](pipeline-stages.md)）。学習タスク（`obstacle_cls` / `detection` / `e2e` / `autoe2e`）は [tasks.md](tasks.md)。四脚 PJ の層に合わせた一連の流れは [workflow.md](workflow.md)、アノテーション分担は [annotation.md](annotation.md)、ツール内部のつながりは [design.md](design.md) です。**別の Windows PC に初めて載せるとき**は [setup-windows.md](setup-windows.md) です。

## ランニングコスト

**ソフトウェア利用料とクラウド API 課金は発生しません**（OSS を自前ホストするため）。

| 項目 | 発生するか |
|------|------------|
| SaaS / クラウド学習 API | しない |
| OSS ライセンス料 | しない（本スタックは OSS） |
| 電気・ディスク・実学習時の GPU | する（オンプレの設備費） |
| アノテーション等人件費 | する |
| Docker Desktop | 個人・小規模は無償。大企業条件に当たると有償の場合あり |

閉鎖網へ移すときも、イメージ tar の搬入以外に月額課金はありません。

## オフラインで使える範囲

| できる（閉鎖網） | できない／事前準備が要る |
|------------------|--------------------------|
| ミッション取込、アノテーション、データセット版切り | **初回だけ** Docker イメージと Python 依存の取得 |
| MLflow 学習、評価ゲート、Ed25519 署名 | `docker compose up --build` は PyPI / Hub に出る |
| Garage 上の生データ・モデル保管 | 新しい Python パッケージの追加（wheel を別途搬入） |
| チャンク同期の模擬、監査ログ | GPU 用 CUDA イメージ（別途搬入） |

この PC ではすでにイメージがローカルにあります。ケーブルを抜いても `docker compose up -d` で再起動できます。

**別の閉鎖ホストへ移すとき**（接続できる側）:

```powershell
.\scripts\airgap-export.ps1
```

`data/airgap/images.tar` とリポジトリ一式を物理媒体で運び、閉鎖側で:

```powershell
.\scripts\airgap-import.ps1
```

閉鎖側では `docker compose pull` と `--build` を使わないでください。

---

## 画面の場所と初期 ID / パスワード

値は `.env`（無ければ `.env.example`）のデモ初期値です。**機密区画・本番では必ず変えてから起動**してください。ブラウザは `localhost` ではなく **http://127.0.0.1:...** を使ってください（IPv6 で繋がらないことがあります）。

### 中核（メニュー 1）

| ツール | URL | ID / キー | パスワード / シークレット |
|--------|-----|-----------|---------------------------|
| ポータル | http://127.0.0.1:8080 | なし | なし |
| Catalog API | http://127.0.0.1:8000/docs | なし | なし |
| 件数確認 | http://127.0.0.1:8000/status | なし | なし |
| MLflow | http://127.0.0.1:5000 | なし | なし |
| Label Studio | http://127.0.0.1:8081 | `admin@ugv.local` | `label_change_me` |
| Garage S3（コンソールなし） | http://127.0.0.1:9000 | Access Key `GKabcdef0123456789abcdef01234567` | Secret `ugv_garage_secret_change_me_do_not_use_in_prod` |
| Garage 管理 API | http://127.0.0.1:3903 | ヘッダ `Authorization: Bearer …` | トークン `ugv_garage_admin_change_me` |
| PostgreSQL | `127.0.0.1:5432` | `ugv` | `ugv_change_me` |
| DVC remote `garage` | S3 `s3://dvc`（上記と同じキー） | 同上 Access Key | 同上 Secret |

Label Studio はサインアップ閉鎖（`LABEL_STUDIO_DISABLE_SIGNUP_WITHOUT_LINK`）なので、上の初期ユーザー以外は作れません。S3 ソースに Garage を足すときも同じ Access Key / Secret、エンドポイント `http://garage:3900`（コンテナから）または `http://127.0.0.1:9000`（ホストから）、バケット `raw`、region `garage`、path-style。

### アノテーション（本 compose 外）

| ツール | URL / 場所 | ID | パスワード |
|--------|------------|----|------------|
| SALT GUI | WSL `Ubuntu-24.04`、conda 環境 `SALT` | ログインなし | なし |
| Xtreme1（確認 UI） | http://127.0.0.1:8190 | 初回は **Sign up**（メール形式、英字+数字 8 文字以上） | 自分で設定 |
| Xtreme1 MinIO コンソール | http://127.0.0.1:8194 | `admin` | `1tQB970y` |
| Xtreme1 MySQL（通常は触らない） | `127.0.0.1:8191` DB `xtreme1` | `xtreme1`（root は `ImOxO8Lz`） | `Rc4K3L6f` |

Xtreme1 の MinIO は本スタックの Garage（`:9000`）とは別です。キーを混ぜないでください。

### 拡張（メニュー 7 / `--profile full`）

| ツール | URL | ID / キー | パスワード / シークレット |
|--------|-----|-----------|---------------------------|
| LakeFS | http://127.0.0.1:8001 | Access Key `AKIAUGVLAKEFS01`（初期ユーザー名 `ugv`） | Secret `lakefs_secret_change_me_32chars!` |
| Gitea | http://127.0.0.1:3000 | 初回アクセスで管理者を **新規登録**（初期ユーザーは無い） | 登録時に自分で設定 |
| Grafana | http://127.0.0.1:3001 | `admin` | `grafana_change_me` |
| Prometheus | http://127.0.0.1:9090 | なし | なし |
| Redis | `127.0.0.1:6379` | なし | なし |

`.env` の対応変数: `LABEL_STUDIO_USERNAME` / `LABEL_STUDIO_PASSWORD`、`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`、`GARAGE_ADMIN_TOKEN`、`POSTGRES_USER` / `POSTGRES_PASSWORD`、`LAKEFS_ACCESS_KEY_ID` / `LAKEFS_SECRET_ACCESS_KEY`、`GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`。

---

## 起動と停止

既定は中核だけ（bag 取込・パース・Label Studio・データセット・MLflow）。

```powershell
cd C:\Users\xeno1\MLOPS
.\launcher\MLOPS_run.bat     # メニュー（起動・ポータル・デモ・アノテ・DVC・停止）
docker compose up -d
docker compose ps
docker compose down          # 停止（データは残る）
```

デスクトップ: `起動スクリプト\MLOPS\MLOPS起動.bat`（スタック）/ `MLOPS_ROS2.bat`（スクリプト・ROS2 ノード）。配置は `.\scripts\install-desktop-launchers.ps1`。

LakeFS / Gitea / Grafana / Prometheus / Redis も要るときは:

```powershell
docker compose --profile full up -d
```

閉鎖網では:

```powershell
docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --no-build --pull never
```

---

## 動作確認（合成データ）

ワーカーが動いている状態で:

```powershell
$job = Invoke-RestMethod -Method POST http://localhost:8000/demo
# 数秒待って
Invoke-RestMethod "http://localhost:8000/jobs/$($job.job_id)"
Invoke-RestMethod http://localhost:8000/status
```

成功すると `ok: true`、サンプル約 60、モデル 1、同期 `acked` になります。中身はデモ用の色付き画像です。機密データは使いません。

公開検出データ（COCO128）と YOLOX-Nano で同じパイプラインを回す:

```powershell
$job = Invoke-RestMethod -Method POST http://localhost:8000/demo-yolox
# 初回は重みと画像の取得＋CPU fine-tune のため数分かかる
Invoke-RestMethod "http://localhost:8000/jobs/$($job.job_id)"
```

またはポータルの「YOLOXデモ」。データ契約は [docs/dataset.md](dataset.md) の検出セクションです。

公開 ROS2 bag（CostNav Teleop: 前方カメラ + LiDAR + `/cmd_vel`）で bag 取込から E2E まで回す:

```powershell
$job = Invoke-RestMethod -Method POST http://localhost:8000/demo-rosbag
# 初回は Hugging Face から約 360MB 取得。inbox は data/inbox/M-AVBAG-001/
Invoke-RestMethod "http://localhost:8000/jobs/$($job.job_id)"
```

またはポータルの「rosbag2デモ」。Autoware 公式サンプル bag はカメラが削られているので使いません。短いクリップなのでゲートは `smoke` です。

---

## 本番に近い一連の作業（11 工程）

ポータルの「11工程フロー」と同じ順です。定義・スキップ規則は [pipeline-stages.md](pipeline-stages.md)。画面操作の PPT は [procedure/UGV_MLOps_データ取込からデプロイ手順.pptx](procedure/UGV_MLOps_データ取込からデプロイ手順.pptx)。

### 工程1（任意）シミュでデータを作る（CARLA）

Autoware_e2e / CARLA（本 compose 外）で rosbag2 を録る。ポータル工程1で発見した bag を「inbox へ取り込む」、または:

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/carla/import `
  -ContentType application/json `
  -Body '{"source":"datasets/carla_bags/mine_001","mission_id":"M-CARLA-mine-001"}'
```

状態確認は `GET /carla/status` / `GET /carla/bags`。手順の正本は [carla.md](carla.md)。実機帰還のみなら工程1スキップ。

### 工程2 帰還／シミュ結果を置く

ホストの `data/inbox/<mission_id>/` はコンテナの `/data/inbox/<mission_id>/` と同じです。

```
data/inbox/M-20260831-001/
  manifest.json                 # 任意
  metadata.yaml + *.db3         # ROS2 bag（または *.mcap）
  cameras/front/*.jpg           # 展開済み画像でも可
  series/action.jsonl           # bag に cmd_vel が無いときの E2E 教師
  labels.jsonl                  # 知覚用。E2E では不要
```

`manifest.json` の例:

```json
{
  "mission_id": "M-20260831-001",
  "vehicle_id": "UGV-01",
  "classification": "UNCLASSIFIED",
  "started_ts": "2026-08-31T01:00:00+00:00",
  "theater": "range-a",
  "sensors": ["camera_front"]
}
```

`UNCLASSIFIED` 以外は、解除フラグが立つまで学習セットに入りません。

### 工程3 取り込む

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/ingest `
  -ContentType application/json `
  -Body '{"path":"/data/inbox/M-20260831-001","source_type":"physical_media"}'
```

返った `job_id` を `GET /jobs/{id}` で `completed` になるまで見ます。中では DVC プロジェクトと同じ 4 段階です。

1. **取込** — `data/dvc/raw/missions/<id>/` へコピー  
2. **パース** — カメラ JPEG、LiDAR `*.bin`、GNSS/odom、cmd_vel  
3. **追跡** — DVC ポインタ（実体は Git に入らない）  
4. **保管** — Garage `raw` と `s3://dvc`

段階だけ回すときはランチャー **6**、または `POST /missions/import` → `/track` → `/parse` → `/store`。一覧は `GET /missions`。

### 工程4 取込データを確認する

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/data-qa `
  -ContentType application/json `
  -Body '{"mission_id":"M-20260831-001","confirm":true}'
```

カメラ・action/cmd_vel・サンプル件数を検査する。空 bag・未取込・知覚なのにカメラなしはジョブが `failed` になる。ポータルのデータタブでも中身を見られる。

### 工程5 アノテーションする（知覚では必須）

四脚のシミュと違い、bag を取り込んだだけでは教師がありません。分類・検出はラベルが付くまでデータセットを切らないでください。E2E（cmd_vel 教師）は工程5–6をスキップ可。

1. ポータル工程5で **Label Studio** または **SALT** を選ぶ（Xtreme1 は工程6）。
2. http://127.0.0.1:8081 に `admin@ugv.local` / `label_change_me` でログインする。
3. ingest が作った `UGV-obstacle-cls` を使う。検出・走行面はプロジェクトを作り、`configs/labelstudio/ugv_detection.xml` または `ugv_traversability.xml` を貼る。
4. 画像をラベルする。Label Studio への投入に失敗したジョブは `failed` になる。
5. データセット構築時に Catalog が Label Studio から export して `annotation` へ戻す。手作業なら `labels.jsonl` を inbox に置いて再 ingest する。

デモ分類器が読むクラスは `clear` / `obstacle` / `person` です。Freespace / LiDAR は [annotation.md](annotation.md)。

### 工程6 ラベル・センサを突き合わせ確認

ポータル工程6で確認用データを出し、http://127.0.0.1:8190（Xtreme1）でカメラと点群を見る。終わったら `POST /label-review`（ポータルの「確認完了として記録」）。ラベルも cmd_vel も無いと不合格。

### 工程7 データセットの版を切る

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/datasets/build `
  -ContentType application/json `
  -Body '{"dataset_id":"ds-obstacle-01","name":"obstacle v1","task":"obstacle_cls","mission_id":"M-20260831-001"}'
```

`mission_id` は任意（知覚でもミッション単位に絞れる）。E2E は `"task":"e2e"`（カメラと cmd_vel を時刻同期。画像クラスの人手ラベルは不要）。

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/datasets/build `
  -ContentType application/json `
  -Body '{"dataset_id":"ds-e2e-01","name":"e2e v1","task":"e2e"}'
```

完了後 `GET /datasets` で `dataset_version_id`（例: `ds-obstacle-01-v1`）を控えます。

### 工程8 学習する

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/train `
  -ContentType application/json `
  -Body '{"dataset_version_id":"ds-obstacle-01-v1","stage":"S3"}'
```

指標は http://localhost:5000 の実験 `ugv-obstacle-cls`（知覚）または `ugv-e2e`（操舵・速度の MAE）です。`run_id` を控えます。既定の学習段は前線拠点 **S3** です。後方フル学習にするときは `.env` の `DEFAULT_LEARNING_STAGE=S4`。

### 工程9 ゲート（昇格）

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/gate `
  -ContentType application/json `
  -Body '{"run_id":"<run_id>","gate_name":"staging","target_status":"staging","mission_mode":"training"}'
```

本番相当は `"gate_name":"production"`。公開短クリップ確認は `"gate_name":"smoke"`（ポータルでは合格後 status は `staging`）。閾値と段ごとの許容成果物は `configs/artifact_policy.yaml`。ゲートは実アーティファクトのサイズも検査する。通ると `model_id` と署名が付く。

### 工程10 モデルを軽量化する

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/models/export `
  -ContentType application/json `
  -Body '{"model_id":"<model_id>","kind":"onnx","target_stage":"S3"}'
```

S1（車載）向けは `"kind":"adapter"`（S1 は onnx 不可）。変換に失敗したジョブは `failed` になり、偽の軽量成果物は作らない。既に同じ kind ならスキップ可。TensorRT エンジン化は車載／Autoware 側。

### 工程11 梱包して UGV へ同期

ポータルでは「梱包して同期」でまとめて実行する。API では:

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/packages `
  -ContentType application/json `
  -Body '{"model_id":"<light_model_id>","target_stage":"S3"}'

$sync = Invoke-RestMethod -Method POST http://localhost:8000/sync `
  -ContentType application/json `
  -Body '{"package_id":"<package_id>","vehicle_id":"UGV-01"}'

# チャンク送信（実バイトを車両 inbox へ書く）
do {
  $chunk = Invoke-RestMethod -Method POST "http://localhost:8000/sync/$($sync.tx_id)/chunk"
} while (-not $chunk.done)
```

同期完了後、ホストの `data/work/vehicles/<車両ID>/inbox/<package_id>.zip` に実ファイルが残る（sha256 検証付き）。S1 向けは許可 kind・サイズが `artifact_policy.yaml` で制限される。フル `joblib` / `full_weights` は S3/S4 向け。

### デプロイ後 — 課題

ゲート通過または梱包済みのモデルに対して、ポータルの **課題** タブから起票し、着手 → 解決まで残します。API でも同じです。

```powershell
Invoke-RestMethod http://localhost:8000/models/deployed

Invoke-RestMethod -Method POST http://localhost:8000/issues `
  -ContentType application/json `
  -Body '{"model_id":"<model_id>","title":"夜間の歩行者見逃し","severity":"high","category":"false_negative","vehicle_id":"UGV-01"}'

Invoke-RestMethod -Method PATCH http://localhost:8000/issues/<issue_id> `
  -ContentType application/json `
  -Body '{"status":"in_progress","note":"再現データをinboxへ戻した"}'

Invoke-RestMethod -Method PATCH http://localhost:8000/issues/<issue_id> `
  -ContentType application/json `
  -Body '{"status":"resolved","resolution":"閾値を上げて再学習しゲート通過"}'
```

状態は `open` → `in_progress` → `resolved`（または `wont_fix` / `duplicate`）。解決時は `resolution` か `note` が必要です。タイムラインは `GET /issues/{id}` の `events` です。

---

## 学習コードの置き場

http://localhost:3000 の Gitea にリポジトリを作り、`configs/artifact_policy.yaml` と学習コードを置きます。モデル版と Git のコミットを運用で対応づけてください。

---

## うまくいかないとき

- ジョブが `queued` のまま → `docker compose logs worker`
- 工程4 データ確認失敗 → 未取込・サンプル 0・知覚なのにカメラなし。データタブで確認
- 工程5 Label Studio 失敗 → `:8081` の起動と初期ユーザー。ジョブは失敗扱い（ソフト成功しない）
- 工程10 軽量化失敗 → S1+onnx は不可。torch/skl2onnx 変換エラーは `failed`（重い成果物のまま「成功」にしない）
- 工程11 同期先が空 → `data/work/vehicles/<車両>/inbox/` を確認。Garage から zip を読んで書く
- ゲート失敗 → ジョブ結果の `reasons`（精度、FPR、mae_steer、段ホワイトリスト、サイズ、任務モード）
- E2E が「操作系列がありません」→ bag に `/cmd_vel` 等が無いか。`series/action.jsonl` を置く
- Garage にファイルが無い → ingest の `path` がコンテナ側 `/data/inbox/...` か。旧 MinIO ボリュームは自動移行しない
- 閉鎖網で compose が落ちる → `--build` や `pull` を使っていないか。`airgap-import.ps1` を使う
