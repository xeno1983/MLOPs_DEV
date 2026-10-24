# エアギャップ（閉鎖ネットワーク）

学習パイプラインはクラウドを呼びません。必要なのは **事前に集めた Docker イメージ** だけです。

## 接続できる側

```powershell
.\scripts\airgap-export.ps1
```

出力: `data/airgap/images.tar`（ベースイメージ + catalog / worker / mlflow のビルド済み）。リポジトリ本体も一緒に運びます。

## 閉鎖側

```powershell
.\scripts\airgap-import.ps1
```

中身は `docker load` のあと:

```powershell
docker compose -f docker-compose.yml -f docker-compose.offline.yml up -d --no-build --pull never
```

これで Hub にも PyPI にも出ません。

使わないこと:

- `docker compose pull`
- `docker compose up --build`
- コンテナ内での `pip install`

Python ライブラリを足すときは接続側で wheel を集め、閉鎖側でイメージを作り直してから tar を再搬入します。

## 外に出ないようにしてあるもの

- LakeFS の利用統計・版チェック
- Grafana のレポート／更新確認
- Gitea の更新チェッカー（offline mode）
- Label Studio の analytics フラグ

OS や Docker Desktop 自体の更新確認は別問題です。ホストのプロキシ／ファイアウォールで外向きを落としてください。
