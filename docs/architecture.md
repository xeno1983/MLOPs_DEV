# アーキテクチャ

全体の設計（データフローとツール連携）は **[design.md](design.md)** を正とする。四脚 PJ に合わせた一連の流れは **[workflow.md](workflow.md)**。本ファイルは短い参照用である。

## 位置づけ

UAIS のデータ層のうち、本リポジトリが担うのは **層3 学習DB と MLOps 閉ループ** です。

| 層 | 系統 | 本環境での扱い |
|----|------|----------------|
| 0 エッジ | 車載 DB・推論・送信キュー | 更新パッケージの適用先。差分同期の受信側 |
| 1 一次 | 管制の生受信 | ミッションフォルダ／bag 相当として inbox へ |
| 2 二次 | COP／DSS の判断ラベル | annotation として学習DBへ合流 |
| 3 学習 | 隔離セグメント | Catalog + Garage + MLflow |

多段階学習:

| 段 | 場所 | 本スタック |
|----|------|------------|
| S0 | 車載・推論固定 | パッケージ適用後の状態 |
| S1 | 車載適応 | 許容アーティファクトが小さい。`artifact_policy.yaml` |
| S2 | 戦域集約 | 任意。署名付き差分の交換 |
| S3 | 前線拠点 | **デフォルトの学習場所**（本 compose） |
| S4 | 後方 | 同一スタックを複製し `DEFAULT_LEARNING_STAGE=S4` |

## データ面

オブジェクトは **Garage**（S3 互換）のバケットで分離します。

- `raw` — ミッション生データ（ROS2 bag、展開 JPEG、IMU/GNSS/LiDAR/action 系列）
- `curated` — スクラブ・間引き後
- `datasets` — 版付き zip（train/val/test）
- `models` — 学習成果物
- `packages` — 車載向け署名付き更新
- `mlflow` — 実験アーティファクト
- `lakefs` — LakeFS ブロックストア（Garage バケット）

カタログ（PostgreSQL `catalog`）がサンプル ID、アノテーション、データセット版、学習 run、ゲート結果、同期トランザクション、**デプロイ済みモデルに紐づく課題**、監査ログを持ちます。スキーマは `schemas/01_catalog.sql` と `schemas/02_issues.sql` です。既存ボリュームには Catalog 起動時の `ensure_schema()` が課題表を足します。

## 処理の流れ

1. **Ingest** — ROS2 bag またはミッションフォルダを Garage `raw` へ。カメラは JPEG、他センサは jsonl、元 bag は `rosbag` として保管。SHA-256 で重複排除。
2. **Annotate（必須・四脚に無い段）** — 知覚は Label Studio または `labels.jsonl`。未ラベルはデータセットに入らない。E2E だけ操作系列が教師。
3. **Build dataset** — 非機密またはスクラブ済みのみ。知覚は ImageFolder、E2E は時刻同期 `records.jsonl`。
4. **Train** — ワーカーが分類器または E2E ベースラインを学習し MLflow に記録。成果物は `models`。
5. **Gate** — 知覚は精度・FPR、E2E は MAE。任務モード・段ホワイトリスト。Ed25519 でマニフェスト署名。
6. **Package** — 段のサイズ上限内の zip。失敗時のロールバック ID を残す余地あり。
7. **Sync** — チャンク送信、オフセット再開、ack / rollback。D-DIL を模擬。
8. **Issues** — ゲート通過／梱包済みモデルに課題を起票し、open → in_progress → resolved まで追跡。

## 連携

- **Label Studio** → 書き出し JSON を `annotation` に戻す（operations 参照）。
- **MLflow** → run_id を `training_run` に保存し、ゲートと相互参照。
- **DVC** → ミッションとデータセット zip のポインタ。実体は Garage `dvc`。学習ジョブが見る正は Catalog `dataset_version`。
- **Label Studio / SALT / Xtreme1** → アノテ分担は [annotation.md](annotation.md)。
- **LakeFS / Gitea / Grafana** → 任意。`docker compose --profile full`。

ROS2 bag / MCAP は Catalog が `rosbags` で展開します（ホストに ROS は不要）。Windows オンプレでも inbox のフォルダ契約は同じです。
