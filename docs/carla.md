# CARLA 連動（compose 外）

本スタックは **CARLA を docker-compose に載せない**。Autoware_e2e 側で走行を録り、rosbag2 を `data/inbox/<mission>/` に載せた時点から Catalog の管轄になる（実機帰還と同じ入口）。

工程定義は [pipeline-stages.md](pipeline-stages.md) 工程1。学習タスクは [tasks.md](tasks.md)。

---

## 前提

| 項目 | 内容 |
|------|------|
| Autoware_e2e | `.env` の `AUTOWARE_E2E_ROOT`（compose では `/autoware_e2e` にマウント） |
| CARLA | ホスト / WSL で `CarlaUE4`（本リポジトリ外） |
| 録り方 | 同マシンの `Autoware_e2e/OFFROAD_CARLA_HYBRID.md` |

---

## 流れ

```text
CARLA + mining_sim.py
    → ros2 bag record → Autoware_e2e/datasets/carla_bags/<run>/
    → POST /carla/import（またはポータル工程1）
    → data/inbox/M-CARLA-.../
    → POST /ingest → data-qa →（E2E なら）dataset/train …
```

---

## API

| 方法 | パス | 内容 |
|------|------|------|
| GET | `/carla/status` | Autoware ルート・mining_sim・bag 件数 |
| GET | `/carla/bags` | `datasets/carla_bags` 等の bag 一覧 |
| GET | `/carla/record-hint` | `ros2 bag record` のコピペ |
| POST | `/carla/import` | `{source, mission_id?, note?}` → inbox へコピー＋監査 |
| POST | `/pipeline/sim-generate` | `action=import` でも同じ取込。`action=ack` は確認のみ |

`source` は Autoware ルートからの相対パス（`list_bags` の `relpath`）か、許可ディレクトリ内の絶対パス。

---

## CLI / ランチャー

```powershell
$env:PYTHONPATH="src"
python -m ugv_mlops.cli carla-status
python -m ugv_mlops.cli carla-bags
python -m ugv_mlops.cli carla-import "datasets/carla_bags/mine_001" --mission-id M-CARLA-mine-001
```

ランチャー `MLOPS_run.bat` の **c** … CARLA bag 一覧と inbox 取込。

---

## ポータル

工程1「シミュでデータを作る」:

1. Autoware 配下の bag を選ぶ  
2. 「CARLA bag を inbox へ取り込む」  
3. 工程3 で ingest  

「録画コマンドを表示」で WSL 用 `ros2 bag record` を出す。

---

## E2E 向けヒント

可能なら bag に操舵・速度系トピック（`cmd_vel` / ackermann 等）を含める。無い場合は inbox に `series/action.jsonl` を足す。知覚分類・検出は工程5で人手ラベルが必要。

---

## やらないこと

- CARLA / Genesis を本 `docker-compose.yml` に追加する  
- Catalog から CarlaUE4 を起動する（手順案内と bag 取込のみ）
