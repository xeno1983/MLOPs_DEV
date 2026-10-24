# Windows 新規環境の構築手順

1 台の Windows PC に、本スタック（bag 取込 → アノテ → データセット → MLflow）をゼロから載せる手順です。載せたあとの日常操作は [usage.md](usage.md)、ID / パスワードは [usage.md の一覧](usage.md#画面の場所と初期-id--パスワード)、閉鎖網への再搬入は [airgap.md](airgap.md)。

---

## 0. 何が立ち上がるか

| 段 | 中身 | いつ入れるか |
|----|------|----------------|
| **必須（中核）** | Docker、本リポジトリ、PostgreSQL、Garage、Catalog、Worker、ポータル、MLflow、Label Studio | すべての新規 PC |
| **任意（点群・融合確認）** | WSL Ubuntu 24.04、Autoware_e2e、SALT GUI、Xtreme1 | LiDAR semantic / カメラ点群の突き合わせをするとき |
| **任意（拡張）** | LakeFS、Gitea、Grafana、Prometheus | データ版の Git 風 UI、コード置き場、監視が要るとき |

ホストに Python や ROS は不要です。bag 展開はコンテナ内の `rosbags` です。

---

## 1. 機械の条件

| 項目 | 目安 |
|------|------|
| OS | **Windows 11**（SALT の GUI は WSLg が要る）。中核だけなら Windows 10 22H2 でも可 |
| CPU / RAM | 8 コア以上、**16 GB 以上**（拡張 + Xtreme1 同時なら 32 GB 推奨） |
| ディスク | **空き 50 GB 以上**（初回イメージ + Worker ビルド。bag 本体は別途） |
| 仮想化 | BIOS で VT-x / AMD-V が有効。Docker Desktop は **WSL2 バックエンド** |
| GPU | 中核のデモ学習は CPU で動く。SALT / SAM は WSL 側の GPU（任意） |
| ネットワーク | **初回だけ** Docker Hub と PyPI に出る。以降はオフライン可 |

ポートが空いていること（他ソフトとぶつからないこと）:

| ポート | 用途 |
|--------|------|
| 8080 | ポータル |
| 8000 | Catalog API |
| 8081 | Label Studio |
| 5000 | MLflow |
| 9000 / 3903 | Garage S3 / 管理 API |
| 5432 | PostgreSQL |
| 8190–8194 | Xtreme1（入れる場合。Garage `:9000` とは別） |
| 8001 / 3000 / 3001 / 9090 | 拡張プロファイル |

ブラウザは `localhost` ではなく **http://127.0.0.1:...** を使います（IPv6 で繋がらないことがあるため）。

---

## 2. 先に入れるソフトウェア

管理者 PowerShell またはそれぞれのインストーラで。

1. **Git for Windows**（リポジトリを clone する場合）
2. **Docker Desktop**（WSL2 エンジン）。既定のインストール先は `C:\Program Files\Docker\Docker\Docker Desktop.exe`
3. 初回起動して、鯨アイコンが安定し、`docker version` が通るまで待つ

```powershell
wsl --status
docker version
docker info
```

`npipe:////./pipe/dockerDesktopLinuxEngine` が見つからない、と出るときは Desktop がまだエンジンを出していません。鯨が止まってからやり直します。

**SALT / SAM もこの PC でやるときだけ**、続けて:

4. `wsl --install -d Ubuntu-24.04`（再起動が掛かることがある）
5. Ubuntu-24.04 を一度開き、UNIX ユーザーを作る
6. WSLg 確認: Ubuntu 内で `echo $DISPLAY` が空でないこと（Win11）

---

## 3. リポジトリを置く

置き場所は任意です。例: `%USERPROFILE%\MLOPS`。パスに空白が少ない方が無難です。

**ネットがある / Git リモートがある場合:**

```powershell
cd %USERPROFILE%
git clone <このリポジトリの URL> MLOPS
cd MLOPS
```

**媒体で運ぶ場合:** 既存ホストのリポジトリ一式（`.env` に本番秘密を入れたまま運ばない）をコピーします。閉鎖網なら [airgap.md](airgap.md) の `images.tar` も一緒です。

中に `docker-compose.yml` と `launcher\MLOPS_run.bat` があることを確認します。

---

## 4. 設定ファイル

```powershell
cd %USERPROFILE%\MLOPS
copy .env.example .env
notepad .env
```

最低限見ること:

| 変数 | 新規 PC での扱い |
|------|------------------|
| `POSTGRES_PASSWORD` など一式 | デモ値のままでも起動はする。**機密区画では起動前に全部変える** |
| `AUTOWARE_E2E_ROOT` | Autoware_e2e が無いなら `./data/autoware_stub` のまま。あるならその絶対パス（例 `C:/Users/<you>/Autoware_e2e`） |
| `XTREME1_URL` | `http://127.0.0.1:8190` |
| `DEFAULT_LEARNING_STAGE` | 前線拠点なら `S3` |

フォルダは初回起動時にランチャーが作ります。手で作るなら:

```powershell
mkdir data\inbox, data\work, data\keys, data\annotate, data\dvc\datasets
```

---

## 5. 初回起動（インターネットあり）

Docker Desktop を起動した状態で:

```powershell
cd %USERPROFILE%\MLOPS
docker compose up -d --build
```

初回は次を Hub / PyPI から取ります。

- 公開イメージ: `postgres:16-alpine`、`dxflrs/garage:v2.3.0`、`heartexlabs/label-studio`、`nginx`、`python:3.12-slim-bookworm` など
- ローカルビルド: Catalog、Worker（YOLOX を git clone するため **ネット必須**）、MLflow

`garage-init` がバケット（`raw` / `datasets` / `models` / `mlflow` / `dvc` など）を作り、終わると消えます。失敗したら `docker compose logs garage-init` を見て、直してから `docker compose up -d` をもう一度。

待てる目安: 回線次第で 10〜40 分。Worker のビルドが一番長いです。

確認:

```powershell
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
Start-Process http://127.0.0.1:8080
```

ポータルが開き、Catalog の `/status` が JSON を返せば中核は成功です。

Label Studio: http://127.0.0.1:8081 （`admin@ugv.local` / `label_change_me`）

### ランチャーからやる場合

```powershell
.\launcher\MLOPS_run.bat
```

メニュー **1** が中核起動、**2** がポータルです。Docker が止まっていれば Desktop を起こしてエンジンを待ちます。

デスクトップに置くときは、`launcher\MLOPS_run.bat` へのショートカットを作る（中身のパスを書き換えない）。既存ホストの `起動スクリプト\MLOPS\MLOPS起動.bat` はユーザー名が直書きなので、新規 PC では使わずショートカットにします。

---

## 6. 動作確認（合成デモ）

Worker が `running` になってから:

```powershell
$job = Invoke-RestMethod -Method POST http://127.0.0.1:8000/demo
Start-Sleep 15
Invoke-RestMethod "http://127.0.0.1:8000/jobs/$($job.job_id)"
Invoke-RestMethod http://127.0.0.1:8000/status
```

`ok: true` なら取込〜学習のパイプは通っています。中身は色付きの合成画像です。YOLOX 全体は初回が重いので、余裕があるとき `POST /demo-yolox`。

---

## 7. 任意 — 拡張（LakeFS / Gitea / Grafana）

```powershell
docker compose --profile full up -d
```

またはランチャー **7**。ログインは [usage.md](usage.md#画面の場所と初期-id--パスワード)。Gitea は初回に管理者を新規登録します。

---

## 8. 任意 — Autoware_e2e / SALT / Xtreme1

本 compose には載せません。別リポジトリを隣に置き、`.env` の `AUTOWARE_E2E_ROOT` で指します。

### 8.1 リポジトリ

Autoware_e2e を `%USERPROFILE%\Autoware_e2e` に置く（既存ホストからコピー、またはその Git）。中に `SALT\bin\SALT` と `Xtreme1\docker-compose.yml` がある状態が目標です。

`.env`:

```
AUTOWARE_E2E_ROOT=C:/Users/<you>/Autoware_e2e
```

スラッシュは `/` でも `\` でも構いません。

### 8.2 Xtreme1

初回だけネットが要ります（イメージ取得）。2 回目以降は Hub に出ないようにします。

```powershell
cd %USERPROFILE%\Autoware_e2e\Xtreme1
docker compose up -d --pull never
```

まだイメージが無い初回は `--pull never` を外して一度だけ `docker compose up -d`。http://127.0.0.1:8190 で **Sign up**。MinIO コンソール http://127.0.0.1:8194 は `admin` / `1tQB970y`。Garage のキーとは混ぜないこと。

ランチャー **5 → 4** でも上がります。

### 8.3 SALT（点群 GUI）

Windows ネイティブでは動きません。WSL Ubuntu-24.04 で `Autoware_e2e\docs\reference\SALT_SETUP_WSL.md` を一度だけ実行します。

できたことの確認:

```powershell
wsl -d Ubuntu-24.04 -- bash -lc "source ~/anaconda3/etc/profile.d/conda.sh && conda activate SALT && ls /mnt/c/Users/$env:USERNAME/Autoware_e2e/SALT/bin/SALT"
```

以降はランチャー **5 → 3**（別窓。閉じると GUI も終わる）。

Freespace の SAM も同じ conda `SALT` と `tools/annotate.py` です。手順は [annotation.md](annotation.md)。

---

## 9. 任意 — ホストで DVC CLI を使う

学習の正は Catalog の ID です。ホストから `dvc push` するときだけ:

```powershell
pip install -r requirements-dvc.txt
$env:AWS_ACCESS_KEY_ID = (Get-Content .env | Where-Object { $_ -like "MINIO_ROOT_USER=*" }).Split("=",2)[1]
$env:AWS_SECRET_ACCESS_KEY = (Get-Content .env | Where-Object { $_ -like "MINIO_ROOT_PASSWORD=*" }).Split("=",2)[1]
$env:AWS_DEFAULT_REGION = "garage"
dvc status
```

リモートは `.dvc/config` の `garage`（`http://127.0.0.1:9000`、バケット `dvc`）。コンテナ内 Worker は `garage-docker`（`http://garage:3900`）を使います。

---

## 10. 閉鎖網の新規 PC

接続できる側:

```powershell
.\scripts\airgap-export.ps1
```

運ぶもの: リポジトリ + `data/airgap/images.tar`。`.env` の本番秘密は別経路。

閉鎖側:

```powershell
.\scripts\airgap-import.ps1
copy .env.example .env
# パスワードを変える
docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --no-build --pull never
```

閉鎖側では `docker compose pull` と `--build` を使わないこと。Xtreme1 も同様に `--pull never`。詳細は [airgap.md](airgap.md)。

---

## 11. 止める・消す

```powershell
docker compose --profile full down          # データボリュームは残る
cd %USERPROFILE%\Autoware_e2e\Xtreme1
docker compose stop                        # Xtreme1 だけ止める
```

DB と Garage の中身まで消すのは `docker compose down -v`（取り消しできない）。inbox の bag はホストの `data\` に残ります。

---

## 12. うまくいかないとき

| 症状 | 見ること |
|------|----------|
| `dockerDesktopLinuxEngine` が無い | Docker Desktop を起動し、鯨が止まってから再実行 |
| ポータル `ERR_CONNECTION_REFUSED` | `docker compose ps`。URL は `http://127.0.0.1:8080` |
| Catalog `{"detail":"Not Found"}` をルートで見る | `/docs` か `/health`。ルートは HTML ランディング |
| Label Studio に入れない | `.env` の `LABEL_STUDIO_USERNAME` / `PASSWORD`。サインアップは閉じている |
| ジョブが `queued` のまま | `docker compose logs worker`。初回は `--build` 済みか |
| PowerShell が `}` の構文エラー | `launcher\MLOPS_run.ps1` は UTF-8 **BOM 付き**。`MLOPS_run.bat` 経由で起動 |
| Xtreme1 が上がらない | `docker compose up -d --pull never`（オフラインで Hub に取りに行かない） |
| SALT の窓がすぐ閉じる | WSL `Ubuntu-24.04`、conda 環境 `SALT`、`DISPLAY`。ランチャーは別窓を残す |
| Garage にファイルが無い | ingest の path はコンテナから見て `/data/inbox/...`。旧 MinIO ボリュームは自動移行しない |

日常のフローは [usage.md](usage.md)、層の対応は [workflow.md](workflow.md) です。
