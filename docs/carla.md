# CARLA 連動（compose 外）

本スタックは **CARLA を docker-compose に載せない**。起動・走行はホスト／WSL の Autoware_e2e + CarlaUE4、Catalog は **起動指示・状態・bag 取込** を担う。

工程定義は [pipeline-stages.md](pipeline-stages.md) 工程1。学習タスクは [tasks.md](tasks.md)。CI 観点は [ci-cd-testing.md](ci-cd-testing.md)。

---

## 前提

| 項目 | 内容 |
|------|------|
| Autoware_e2e | `.env` の `AUTOWARE_E2E_ROOT` |
| CARLA | `.env` の `CARLA_ROOT`（CarlaUE4.sh / .exe） |
| 起動モード | `CARLA_LAUNCH_MODE=wsl`（既定）/ `native` / `script` |
| host worker | ポータルから起動するとき必須（`launcher/MLOPS_gpu_worker.bat`） |

---

## 流れ

```text
ポータル / CLI / ランチャー
    → POST /carla/launch（host job）または scripts/carla-launch.ps1
    → CarlaUE4 + mining_sim.py
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
$env:CARLA_ROOT="C:\path\to\CARLA"   # または WSL 内パスを CARLA_ROOT に
python -m ugv_mlops.cli carla-status
python -m ugv_mlops.cli carla-launch --component server --wait-ready-sec 40
python -m ugv_mlops.cli carla-launch --component mining_sim
python -m ugv_mlops.cli carla-launch --component stack --dry-run
python -m ugv_mlops.cli carla-stop --component stack
python -m ugv_mlops.cli carla-import "datasets/carla_bags/mine_001"

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
