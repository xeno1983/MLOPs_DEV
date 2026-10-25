# アノテーション（必須）

帰還 bag を ingest しただけでは教師がありません。四脚シミュとはここが違います。ツールは用途で分けます（`C:\Users\xeno1\Autoware_e2e\docs\reference\AUTO_ANNOTATION.md` と同じ分担）。ポータル工程3で **Label Studio / SALT / Xtreme1** を選んで作業データを出せます。

| 何を付けるか | ツール | 場所 |
|--------------|--------|------|
| 画像クラス `clear` / `obstacle` / `person` | **Label Studio** | 本 compose `:8081`。ingest が import |
| 検出 bbox | Label Studio（`ugv_detection.xml`）または `labels.jsonl` | 同上 |
| 走行領域マスク（Freespace） | **SAM 2.1**（Autoware `tools/annotate.py`） | WSL。結果を Catalog へ import |
| LiDAR 点の semantic | **SALT GUI** | WSL。`SALT/bin/SALT` |
| カメラと点群の突き合わせ | **Xtreme1** | `:8190`（Autoware の compose。Garage `:9000` と別。Xtreme1 の MinIO は `:8193`） |

Xtreme1 の YOLOR は箱検出です。Freespace マスクには使いません。

---

## 1. 画像クラス / bbox（毎回の主経路）

1. ingest する。カメラが Label Studio の `UGV-obstacle-cls` に入る。
2. http://127.0.0.1:8081 に `admin@ugv.local` / `label_change_me` で入る。
3. `POST /datasets/build` のとき Catalog が export して `annotation` に戻す。

未ラベルのまま `obstacle_cls` / `detection` の版切りは失敗します。

---

## 2. Freespace（SAM）

```powershell
# Catalog から JPEG を data/annotate/<mission>/inbox へ
python -m ugv_mlops.cli annotate-init M-20260831-001
# または POST /annotate/init
```

Autoware_e2e 側（WSL、conda `SALT`）:

```bash
python tools/annotate.py segment M-20260831-001
# work/*_overlay.jpg を見て classes.txt に drivable: 1, 4
python tools/annotate.py export M-20260831-001
```

戻す:

```powershell
python -m ugv_mlops.cli annotate-import-masks M-20260831-001
```

---

## 3. LiDAR（SALT）

1. ポータル工程3でツール **SALT**、ミッションを選び「SALT 用に点群を出す」。
2. `C:\Users\xeno1\Autoware_e2e\docs\reference\SALT_SETUP_WSL.md` で GUI を起動する。
3. `data/annotate/<mission_id>/lidar` の `.pcd` / `.bin` を読み、クラス色で塗る。
4. 書き出した jsonl（各行 `sample_id` + `label_json`）を `data/annotate/<mission_id>/export.jsonl` に置き、工程3の「結果を Catalog に戻す」。

```powershell
python -m ugv_mlops.cli annotate-import-jsonl data/annotate/M-AVBAG-001/export.jsonl --tool salt
```

点群原本は bag（Garage `raw`）。ingest の lidar はキーフレームメタです。

---

## 4. 確認（Xtreme1）

ポータル工程3でツール **Xtreme1** を選ぶと、画像と点群を `data/annotate/<mission_id>/` に出し、http://127.0.0.1:8190 を開きます。

Autoware_e2e で:

```powershell
cd C:\Users\xeno1\Autoware_e2e\Xtreme1
docker compose up -d
```

http://127.0.0.1:8190 。初回は画面の **Sign up** でアカウントを作ります（メール形式、英字+数字 8 文字以上）。MinIO コンソール http://127.0.0.1:8194 は `admin` / `1tQB970y`（本スタックの Garage `:9000` とは別）。

状態確認:

```powershell
python -m ugv_mlops.cli annotate-status
# GET http://localhost:8000/annotate
```

`.env` の `AUTOWARE_E2E_ROOT` が SALT / SAM のパスです。
