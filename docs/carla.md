# CARLA 連動（compose 外）

本スタックは **CARLA を docker-compose に載せない**。起動・走行はホスト／WSL の CarlaUE4、Catalog は **起動指示・状態・bag 取込** を担う。

工程定義は [pipeline-stages.md](pipeline-stages.md) 工程1。全体設計は [design.md](design.md)。**構成図**は [diagrams.md](diagrams.md)。学習タスクは [tasks.md](tasks.md)。CI 観点は [ci-cd-testing.md](ci-cd-testing.md)。

---

## 前提

| 項目 | 内容 |
|------|------|
| Autoware_e2e | `.env` の `AUTOWARE_E2E_ROOT` |
| CARLA 0.9.15 | WSL `~/CARLA`（`.env` の `CARLA_ROOT`） |
| Python API | **PyPI `carla-0-9-15`（cp310 manylinux）**。公式同梱の py3.7 egg は使わない |
| 収集 venv | WSL `~/.venv-carla915`（Python 3.10 + pygame + rosbags） |
| 既定マップ | **Town01**（[公式](https://carla.readthedocs.io/en/latest/map_town01/)）。任意で Mine_01 / Town07 |
| 既定車両 | **Tesla Cybertruck**（`vehicle.tesla.cybertruck`） |
| 既定センサ | RGB×6・LiDAR×3・GNSS・IMU（+ odometry / speed） |
| 起動モード | `CARLA_LAUNCH_MODE=wsl`（既定）/ `native` / `script` |
| host worker | ポータルから起動するとき必須（`launcher/MLOPS_gpu_worker.bat`） |

---

## 設計方針（オフロード収集）

### なぜ ROS 無しクライアントか

- CARLA 0.9.15 公式 `PythonAPI` は **py3.7 の `.egg` / `.whl` のみ**同梱。C 拡張 ABI が 3.8+ と非互換で、3.10 で import すると segfault する。
- WSL 既定の ROS 2 Jazzy は **Python 3.12**。CARLA egg と同一プロセスでは同居できない。
- そのため工程1の **手動走行＋rosbag2 保管**は、ROS を介さず **専用 venv + `carla_offroad_collect.py`** で行う（Town01 / Cybertruck、RGB・LiDAR・GNSS/IMU を bag に書く。AutoE2E は front + odometry を主に使う）。

### 推奨データ生成パス

```text
setup-carla.ps1 (-Download)          # CarlaUE4 本体
    → setup-carla-offroad.ps1        # venv + carla-0-9-15 +（可能なら Mine_01）
    → start-carla.ps1 / start-carla-offroad.ps1
    → WASD 走行 + B で rosbag2 録画
    → Autoware_e2e/datasets/carla_bags/<run_id>/
    → POST /carla/import → data/inbox/M-CARLA-.../
    → 工程2〜 と同じ ingest 線
```

代替（ROS 付き）: Autoware `mining_sim.py` + `ros2 bag record`。Jazzy 同居問題があるため、**新規収集の既定は ROS 無しクライアント**。

### コンポーネント境界

| 層 | 実体 | 役割 |
|----|------|------|
| シミュ本体 | WSL `CarlaUE4` `:2000/:2001` | 物理・描画。compose 外 |
| 収集クライアント | `scripts/carla_offroad_collect.py` | 車両スポーン・カメラ・操作 UI・bag 書き込み |
| Python API | `carla-0-9-15` wheel | RPC クライアント（サーバ 0.9.15 と通信確認済み） |
| Catalog | docker | launch 指示・status・import。起動実体は host worker |
| Host agent | `carla-host-agent.ps1` / GPU worker | ポータルからの launch/stop を実行 |

**同期モード注意:** `--sync` 中にクライアントが落ちるとサーバが tick 待ちで RPC 全体がハングする。復帰は CarlaUE4 の再起動（`scripts/start-carla.ps1`）。コレクタは `atexit` で async 復帰を試みる。

---

## 準備（初回）

```powershell
# 1) WSL に CARLA 本体を取得（数 GB〜）
.\scripts\setup-carla.ps1 -Download

# 2) オフロード収集環境（Python 3.10 venv + carla-0-9-15 + Mine_01 試行）
.\scripts\setup-carla-offroad.ps1
# Mine だけ後回し: .\scripts\setup-carla-offroad.ps1 -SkipMine

# 3) 前提チェック
$env:PYTHONPATH="src"
python -m ugv_mlops.cli carla-prepare

# 4) ポータル工程1から起動する場合は host agent を常駐
.\scripts\carla-host-agent.ps1
```

`setup-carla-offroad-env.sh` がやること:

- `uv` で Python 3.10 venv（`~/.venv-carla915`）
- `carla-0-9-15` / pygame / numpy\<2 / rosbags / gdown を導入
- 公式 py3.7 egg への `carla.pth` があれば無効化（`.egg.bak`）
- Mine_01（UE4 移植コンテンツ）を Drive から取得試行 → Maps へ配置

---

## オフロード走行・rosbag 録画（推奨）

```powershell
.\scripts\start-carla-offroad.ps1          # CARLA 起動（未起動時）+ WASD コレクタ
.\scripts\start-carla-offroad.ps1
# 既定: Town01 + Cybertruck + RGB×6 / LiDAR×3 / GNSS / IMU
.\scripts\start-carla-offroad.ps1 -SkipServer -Map Town01
```

| 操作 | キー |
|------|------|
| 前進 / ブレーキ / 舵 | W S A D（Q=後退） |
| 録画 ON/OFF | **B** |
| 終了 | ESC |

| 項目 | 値 |
|------|-----|
| bag 出力 | `AUTOWARE_E2E_ROOT/datasets/carla_bags/<run_id>/` |
| トピック | `/carla/ego_vehicle/rgb/image`（bgr8） / `/carla/ego_vehicle/odometry` / `/carla/ego_vehicle/speed` |
| 互換 | AutoE2E / `rig.carla.yaml` |
| 実装 | `rosbags` Writer v9（ROS 不要）。Image.data / covariance は `numpy` 配列 |

車両: **`vehicle.tesla.cybertruck` 固定**（[カタログ](https://carla.readthedocs.io/en/latest/catalogue_vehicles/)）。  
センサ bag トピック例:

| 種別 | トピック |
|------|----------|
| RGB | `/carla/ego_vehicle/rgb/{front,front_left,front_right,rear,left,right}/image`（互換で `.../rgb/image`=front） |
| LiDAR | `/carla/ego_vehicle/lidar/{top,front,rear}/points` |
| GNSS / IMU | `/carla/ego_vehicle/gnss` · `/carla/ego_vehicle/imu` |
| Pose | `/carla/ego_vehicle/odometry` · `/carla/ego_vehicle/speed` |

取込: ポータル工程1、または `python -m ugv_mlops.cli carla-import datasets/carla_bags/<run_id>`

**重要:** Catalog は Docker 内のため、ポータルの「CARLA 起動」は `data/work/carla/request.json` に依頼を書き、**ホストの `carla-host-agent.ps1`（または GPU host worker）が実際に CarlaUE4 を起動**する。agent 無しでは工程1のボタンはタイムアウトする。

---

## Mine_01 マップ

| 事実 | 内容 |
|------|------|
| 公式 AdditionalMaps 0.9.15 | **Mine_01 を含まない**（UE5 / 0.10 系のマップ） |
| UE4 向け移植 | Autoware off-road WG / Robotec.ai による `UE4_Content` |
| Drive | https://drive.google.com/drive/folders/15ig-afICo1bZpyeW6u0UaTgDk9-7lJIp |
| 配置先 | `~/CARLA/CarlaUE4/Content/Carla/Maps/`（CarlaUE4 **停止中**にコピー） |
| 自動取得 | `gdown` はログイン必須フォルダで **401** になり得る → ブラウザで手動取得 |
| 未導入時 | `start-carla-offroad.ps1` が **Town07** にフォールバック |

```bash
# 手動例（WSL）
cp -a /path/to/UE4_Content/. "$HOME/CARLA/CarlaUE4/Content/Carla/Maps/"
# その後 CARLA 再起動 → .\scripts\start-carla-offroad.ps1 -Map Mine_01
```

---

## スクリプト対応表

| スクリプト | 役割 |
|------------|------|
| `scripts/setup-carla.ps1` | CARLA 本体・`.env`・bags ディレクトリ |
| `scripts/setup-carla-wsl.sh` | WSL 側本体展開 |
| `scripts/setup-carla-offroad.ps1` / `setup-carla-offroad-env.sh` | 収集 venv + Mine_01 試行 |
| `scripts/start-carla.ps1` / `run-carla-wsl.sh` | CarlaUE4 サーバ起動 |
| `scripts/start-carla-offroad.ps1` / `_launch_collect.sh` | サーバ確認 + コレクタ窓 |
| `scripts/carla_offroad_collect.py` | WASD + rosbag2 本体 |
| `scripts/carla-host-agent.ps1` | ポータル launch 依頼の実行 |
| `scripts/carla-launch.ps1` | CLI 相当の stack 起動 |

`scripts/_*.sh` / `_smoke_*` / `_probe_*` は切り分け用の一時物。運用入口にしない。

---

## 流れ（Catalog 連携）

```text
（推奨）offroad collect → carla_bags/<run>/
    → POST /carla/import → data/inbox/M-CARLA-.../
    → POST /ingest → data-qa → …

（代替）carla-launch (server + mining_sim) → ros2 bag record → 同上
```

---

## API

| 方法 | パス | 内容 |
|------|------|------|
| GET | `/carla/status` | Autoware・CARLA_ROOT・TCP probe・runtime |
| GET | `/carla/bags` | bag 一覧 |
| GET | `/carla/record-hint` | `ros2 bag record` コピペ（ROS 経路用） |
| POST | `/carla/launch` | `{components, dry_run?, wait_ready_sec?}` → **host job** |
| POST | `/carla/stop` | 停止（host job） |
| POST | `/carla/import` | bag → inbox |
| POST | `/pipeline/sim-generate` | `import` / `ack` |

`components`: `server`（CarlaUE4）/ `mining_sim` / 両方（`stack`）。

Catalog がコンテナ内のとき起動は **必ず host worker**。ホスト上 CLI・`start-carla*.ps1` は直接起動可。

---

## CLI / ランチャー

```powershell
$env:PYTHONPATH="src"
python -m ugv_mlops.cli carla-prepare
python -m ugv_mlops.cli carla-status
python -m ugv_mlops.cli carla-launch --component server --wait-ready-sec 40
python -m ugv_mlops.cli carla-import "datasets/carla_bags/mine_001"

.\scripts\setup-carla.ps1 -Download
.\scripts\setup-carla-offroad.ps1
.\scripts\start-carla-offroad.ps1
```

ランチャー `c` / `o` メニュー: 状態・取込・起動・停止・オフロード収集。

---

## ポータル（工程1）

1. **CARLA 起動** / **mining_sim** / **一括**（host worker 要）  
2. 録画（オフroad コレクタまたは ROS ヒント）→ bag を選んで **inbox へ取込**  
3. **確認**（mission 必須）→ 工程3 ingest  

TCP `:2000` の応答有無を状態行に表示する。

---

## 実測（ライブ · 2026-11-14）

| 段 | 結果 |
|----|------|
| 起動 | `start-carla.ps1 -Mode offscreen` → `:2000` listen（Catalog probe OK） |
| 録画 | `scripts/_smoke_collect.py` Town07 · 38 frames → `smoke_20261114_143755` |
| import | `M-CARLA-LIVE-20261114` → `data/inbox/...` |
| ingest | camera 8 / odom 38 · LakeFS raw+parsed commit OK |
| data_qa | **passed**（ラベル無し警告のみ。知覚なら工程5） |

手動 WASD 収集は `start-carla-offroad.ps1`（window モード）を使う。

---

## やらないこと

- CARLA / Genesis を本 `docker-compose.yml` に追加する  
- Catalog コンテナ内から直接 `CarlaUE4` を `Popen` する（host へ委譲）  
- 公式 py3.7 egg を Python 3.10 venv に載せる（segfault）  
- 収集クライアントを ROS Jazzy のシステム Python に結びつける  

---

## 改訂

| 日付 | 内容 |
|------|------|
| 2026-11-16 | 既定を Town01 + Cybertruck + RGB×6/LiDAR×3/GNSS/IMU に変更 |
| 2026-11-14 | ライブ smoke: offscreen → collect → import → ingest → qa（O13） |
| 2026-11-09 | オフロード収集を設計固定: `carla-0-9-15` + py3.10 venv、ROS 無し bag、Mine_01/Town07、sync ハング注意 |
| 2026-11-02 | host launch・import・工程1 連動の初版 |
