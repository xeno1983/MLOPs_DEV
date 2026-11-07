# 操作手順書（画面つき）

| 成果物 | 説明 |
|--------|------|
| [UGV_MLOps_データ取込からデプロイ手順.pptx](UGV_MLOps_データ取込からデプロイ手順.pptx) | ポータル 11 工程の操作手順 |
| `screens/` | PPT に載せるキャプチャ（任意で差し替え） |

工程の契約・スキップ規則は [../pipeline-stages.md](../pipeline-stages.md)。文章だけの手順は [../usage.md](../usage.md)。新規 PC 構築は [../setup-windows.md](../setup-windows.md)。

## 再生成

```powershell
cd C:\Users\xeno1\MLOPS
python scripts/build-procedure-pptx.py
```

キャプチャを取り直すときは `screens/` の PNG を更新してから上を実行する。ファイル名は `scripts/build-procedure-pptx.py` 内の参照に合わせる。
