"""GPU ホストワーカー: compose の外（Windows ホスト）で動き、runner=host のジョブだけを拾う。

AutoE2E の切出し・微調整は CUDA とホスト上の Autoware_e2e / auto_e2e が要るので、
CPU 専用の docker worker では回せない。Catalog / Postgres / Garage / MLflow は
compose が公開しているポート（localhost）越しに使う。起動は launcher/MLOPS_gpu_worker.bat。
"""
from __future__ import annotations

import sys

from ugv_mlops.jobs import loop
from ugv_mlops.settings import settings

if __name__ == "__main__":
    # Windows のコンソールは cp932 のことがあり、MLflow の絵文字出力で落ちる。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(f"host worker: db={settings.catalog_database_url.split('@')[-1]} s3={settings.minio_endpoint} "
          f"mlflow={settings.mlflow_tracking_uri} autoware_e2e={settings.autoware_e2e_root}", flush=True)
    try:
        loop("host", keep_going=True)
    except KeyboardInterrupt:
        sys.exit(0)
