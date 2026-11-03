# AutoE2E × MLOPS 連携

別プロジェクト **Autoware_e2e**（`auto_e2e` / `tools/rosbag2_autoe2e` / `tools/auto_e2e_finetune_offroad.py`）の学習を、本スタックの 11 工程に載せる。コードは複製せず **ホスト GPU ワーカーからサブプロセス呼び出し**する。

関連: [tasks.md](tasks.md) §4、[carla.md](carla.md)、[pipeline-stages.md](pipeline-stages.md)、Autoware 側 `docs/reference/AUTO_E2E_OFFROAD.md`。

---

## 役割分担

| 層 | 担当 |
|----|------|
| Autoware_e2e | ROS2 bag → scene 切出し、微調整、評価、rig YAML |
| MLOPS Catalog | ingest / dataset(task=`autoe2e`) / train / gate / package / 監査 |
| host worker | `runner=host` の版切り・学習（CUDA） |
| CARLA + mining_sim | データ源（compose 外） |
| `carla-odom-bridge.py` | mining_sim が出さない Odometry を補完 |

```text
CARLA + mining_sim + odom-bridge
    → ros2 bag record（carla-autoe2e-record.sh）
    → POST /carla/import → inbox
    → POST /ingest（bag 原本を raw に残す）
    → POST /datasets/build task=autoe2e mission_id=…   ← host
    → POST /train                                        ← host
    → POST /gate → package / sync
```

---

## 前提チェック

```powershell
$env:PYTHONPATH="src"
python -m ugv_mlops.cli autoe2e-status
# または GET http://localhost:8000/autoe2e/status
```

必要なもの:

| 項目 | 場所 |
|------|------|
| `AUTOWARE_E2E_ROOT` | `.env` → 実体の Autoware_e2e |
| `.venv-autoe2e` | Autoware 側 Stage 0 |
| `tools/rosbag2_autoe2e/cli.py` | 切出し |
| `tools/auto_e2e_finetune_offroad.py` | 学習 |
| `config/rosbag2_autoe2e/rig.yaml`（既定） | CARLA 用は `rig.carla.yaml` をコピー済み可 |
| GPU host worker | `launcher/MLOPS_gpu_worker.bat` |

任意: `.env` に `AUTOE2E_RIG=C:/Users/xeno1/Autoware_e2e/config/rosbag2_autoe2e/rig.carla.yaml`

---

## CARLA → bag（ROS2）

mining_sim が出す画像は **`/carla/ego_vehicle/rgb/image`**（`rgb_front` ではない）。  
Odometry は mining_sim に無いのでブリッジが必要。

```powershell
# 端末1: CARLA
.\scripts\start-carla.ps1
# 端末2: mining_sim（Autoware 手順）
# 端末3: WSL で録画（odom bridge 込み）
wsl -d Ubuntu -- bash /mnt/c/Users/xeno1/MLOPS/scripts/carla-autoe2e-record.sh mine_001
```

ポータル工程1「録画コマンドを表示」でも同内容のヒントが出る。

---

## MLOPS 工程（ポータル）

| 工程 | 操作 |
|------|------|
| 1 | bag を inbox へ取込（CARLA import）→ 確認 |
| 3 | ingest（`rosbag`）。**JPEG 展開だけでなく bag 原本が raw に残ること** |
| 4 | data_qa |
| 5–6 | **スキップ可**（AutoE2E は人手ラベル不要） |
| 7 | task=`autoe2e`、**mission_id 必須**、データセット生成（host job） |
| 8 | train（host job・長時間） |
| 9 | gate（`ade_6p4s_m` 等） |
| 10–11 | 必要なら lighten / package_sync |

版切り・学習の前に必ず:

```powershell
.\launcher\MLOPS_gpu_worker.bat
```

---

## CLI 例

```powershell
$env:PYTHONPATH="src"
python -m ugv_mlops.cli autoe2e-status

# Catalog API（スタック起動後）
# POST /datasets/build {"dataset_id":"ds-autoe2e-01","name":"autoe2e v1","task":"autoe2e","mission_id":"M-CARLA-mine-001"}
# POST /train {"dataset_version_id":"…","params":{"epochs":3}}
```

---

## 実機 ROS2 bag

`rig.example.yaml` / 車両専用 YAML を `AUTOE2E_RIG` に指定。トピック名が CARLA と違うだけ。inbox → ingest → 以降は同じ。

---

## やらないこと

- AutoE2E 学習を compose の docker worker で回す（CUDA / Autoware パスが無い）
- Autoware の学習コードを MLOPS に複製する
- CARLA を compose に載せる
