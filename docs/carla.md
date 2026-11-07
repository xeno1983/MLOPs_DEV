# CARLA 連動（compose 外）

本スタックは **CARLA を docker-compose に載せない**。起動・走行はホスト／WSL の Autoware_e2e + CarlaUE4、Catalog は **起動指示・状態・bag 取込** を担う。

工程定義は [pipeline-stages.md](pipeline-stages.md) 工程1。学習タスクは [tasks.md](tasks.md)。CI 観点は [ci-cd-testing.md](ci-cd-testing.md)。

---

## 前提

| 項目 | 内容 |
|------|------|
| Autoware_e2e | `.env` の `AUTOWARE_E2E_ROOT` |
| CARLA 0.9.15 | `.env` の `CARLA_ROOT`（推奨: WSL `~/CARLA`） |
| Mine_01 | mining_sim が `load_world('Mine_01')`。**公式 AdditionalMaps には含まれない**場合あり |
| 起動モード | `CARLA_LAUNCH_MODE=wsl`（既定）/ `native` / `script` |
| host worker | ポータルから起動するとき必須（`launcher/MLOPS_gpu_worker.bat`） |

---

## 準備（初回）

```powershell
# 1) WSL に CARLA 本体を取得（数 GB〜。時間がかかる）
.\scripts\setup-carla.ps1 -Download

# 2) 前提チェック
$env:PYTHONPATH="src"
python -m ugv_mlops.cli carla-prepare

# 3) Mine_01 を CARLA Import に配置し ImportAssets.sh（Autoware off-road 手順）

# 4) ポータル工程1から起動する場合は、先にホスト agent を常駐
.\scripts\carla-host-agent.ps1

# 5) 直接起動する場合
.\scripts\start-carla.ps1
# または: .\scripts\carla-launch.ps1 -Component stack -WaitReadySec 90
```

**重要:** Catalog は Docker 内のため、ポータルの「CARLA 起動」は `data/work/carla/request.json` に依頼を書き、**ホストの `carla-host-agent.ps1`（または GPU host worker）が実際に CarlaUE4 を起動**します。agent 無しでは工程1のボタンはタイムアウトします。

`setup-carla.ps1` がやること:

- `.env` に `CARLA_ROOT` / `CARLA_LAUNCH_MODE=wsl` 等を書く  
- WSL で `scripts/setup-carla-wsl.sh` を実行（`-Download` 時に ~8GB tar 取得。既定 URL は Backblaze）  
- `AUTOWARE_E2E_ROOT/datasets/carla_bags` を作成  
- `carla-prepare` チェックリストを表示  

進捗確認（WSL）: `ls -lh ~/carla-downloads/CARLA_0.9.15.tar.gz` / `tail -f ~/carla-downloads/setup.log`  

---

## 流れ

```text
setup-carla → carla-launch (server + mining_sim)
    → ros2 bag record → Autoware_e2e/datasets/carla_bags/<run>/
    → POST /carla/import → data/inbox/M-CARLA-.../
    → POST /ingest → data-qa → …
```

---

## API

| 方法 | パス | 内容 |
|------|------|------|
| GET | `/carla/status` | Autoware・CARLA_ROOT・TCP probe・runtime |
| GET | `/carla/bags` | bag 一覧 |
| GET | `/carla/record-hint` | `ros2 bag record` コピペ |
| POST | `/carla/launch` | `{components, dry_run?, wait_ready_sec?}` → **host job** |
| POST | `/carla/stop` | 停止（host job） |
| POST | `/carla/import` | bag → inbox |
| POST | `/pipeline/sim-generate` | `import` / `ack` |

`components`: `server`（CarlaUE4）/ `mining_sim` / 両方（`stack`）。

Catalog がコンテナ内のとき起動は **必ず host worker**。ホスト上 CLI は直接起動可。

---

## CLI / スクリプト / ランチャー

```powershell
$env:PYTHONPATH="src"
python -m ugv_mlops.cli carla-prepare
python -m ugv_mlops.cli carla-status
python -m ugv_mlops.cli carla-launch --component server --wait-ready-sec 40
python -m ugv_mlops.cli carla-launch --component mining_sim
python -m ugv_mlops.cli carla-stop --component stack
python -m ugv_mlops.cli carla-import "datasets/carla_bags/mine_001"

.\scripts\setup-carla.ps1 -Download
.\scripts\carla-launch.ps1 -Component stack
```

ランチャー `c` メニュー: 状態・取込・起動・停止・ローカルスクリプト。

---

## ポータル（工程1）

1. **CARLA 起動** / **mining_sim** / **一括**（host worker 要）  
2. 録画（ヒントボタン）→ bag を選んで **inbox へ取込**  
3. **確認**（mission 必須）→ 工程3 ingest  

TCP `:2000` の応答有無を状態行に表示する。

---

## やらないこと

- CARLA / Genesis を本 `docker-compose.yml` に追加する  
- Catalog コンテナ内から直接 `CarlaUE4` を `Popen` する（host へ委譲）
