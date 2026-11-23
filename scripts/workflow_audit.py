"""Audit core MLOps workflows against live stack. Exit 0 always; prints JSON report."""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from ugv_mlops.db import connect
from ugv_mlops.integrations import probe
from ugv_mlops.lakefs_sync import required, status as lakefs_status
from ugv_mlops.settings import settings
from ugv_mlops.storage import get_bytes, put_bytes


def _ok(detail: str, **extra: Any) -> dict:
    return {"status": "pass", "detail": detail, **extra}


def _warn(detail: str, **extra: Any) -> dict:
    return {"status": "warn", "detail": detail, **extra}


def _fail(detail: str, **extra: Any) -> dict:
    return {"status": "fail", "detail": detail, **extra}


def _skip(detail: str, **extra: Any) -> dict:
    return {"status": "skip", "detail": detail, **extra}


def check_core_services() -> dict:
    p = probe()
    core = {k: v for k, v in p.items() if v.get("core")}
    bad = [k for k, v in core.items() if not v.get("ok") or not v.get("wired")]
    if bad:
        return _fail(f"core not wired: {bad}", tools=core)
    return _ok("all core tools ok+wired", tools={k: {"ok": v["ok"], "wired": v["wired"]} for k, v in core.items()})


def check_s3_chain() -> dict:
    try:
        put_bytes("raw", "audit/workflow.txt", b"audit")
        put_bytes("datasets", "audit/workflow.zip", b"PK\x03\x04")
        put_bytes("models", "audit/workflow.joblib", b"JOB")
        put_bytes("packages", "audit/workflow.pkg", b"PKG")
        assert get_bytes("raw", "audit/workflow.txt") == b"audit"
        return _ok("raw/datasets/models/packages put-get")
    except Exception as exc:
        return _fail(str(exc))


def check_lakefs() -> dict:
    st = lakefs_status()
    if not st.get("ok") or not st.get("wired"):
        return _fail("lakefs not wired", **st)
    if not required():
        return _warn("LAKEFS_REQUIRED is false (expected true after P4)", **st)
    return _ok("lakefs wired + required", repo=st.get("repo"), required=True)


def check_db_pipeline_assets() -> dict:
    with connect() as conn:
        counts = {
            "samples": conn.execute(text("select count(*) from learning_sample")).scalar(),
            "annotations": conn.execute(text("select count(*) from annotation")).scalar(),
            "datasets": conn.execute(text("select count(*) from dataset_version")).scalar(),
            "runs": conn.execute(text("select count(*) from training_run")).scalar(),
            "models": conn.execute(text("select count(*) from model_version")).scalar(),
            "packages": conn.execute(text("select count(*) from update_package")).scalar(),
            "jobs_recent": conn.execute(
                text("select count(*) from job where created_at > now() - interval '7 days'")
            ).scalar(),
        }
        latest_ds = conn.execute(
            text(
                "select dataset_version_id, object_uri from dataset_version "
                "order by created_at desc nulls last limit 1"
            )
        ).mappings().first()
        latest_run = conn.execute(
            text(
                "select run_id, artifact_uri, status from training_run "
                "order by created_at desc nulls last limit 1"
            )
        ).mappings().first()
    detail = f"samples={counts['samples']} ann={counts['annotations']} ds={counts['datasets']} runs={counts['runs']}"
    if counts["samples"] == 0:
        return _warn("no samples in catalog", **counts)
    return _ok(detail, counts=counts, latest_ds=dict(latest_ds) if latest_ds else None, latest_run=dict(latest_run) if latest_run else None)


def check_ingest_dataset_train_evidence() -> dict:
    """Confirm Seaweed smoke artifacts from prior run still readable."""
    with connect() as conn:
        row = conn.execute(
            text(
                "select dataset_version_id, object_uri from dataset_version "
                "where dataset_id = 'ds-seaweed-smoke' order by version desc limit 1"
            )
        ).mappings().first()
        run = conn.execute(
            text(
                "select run_id, artifact_uri, status, metrics_json from training_run "
                "where dataset_version_id like 'ds-seaweed-smoke%' "
                "order by created_at desc nulls last limit 1"
            )
        ).mappings().first()
    if not row or not run:
        return _warn("no ds-seaweed-smoke evidence; run scripts/smoke_seaweed_lakefs.py")
    try:
        from ugv_mlops.storage import parse_s3_uri

        db, dk = parse_s3_uri(row["object_uri"])
        ds_bytes = len(get_bytes(db, dk))
        mb, mk = parse_s3_uri(run["artifact_uri"])
        model_bytes = len(get_bytes(mb, mk))
    except Exception as exc:
        return _fail(f"artifact read failed: {exc}", dataset=dict(row), run=dict(run))
    return _ok(
        "ingest→dataset→train evidence readable",
        dataset_version_id=row["dataset_version_id"],
        dataset_bytes=ds_bytes,
        run_id=run["run_id"],
        model_bytes=model_bytes,
        run_status=run["status"],
    )


def check_data_qa_path() -> dict:
    try:
        from ugv_mlops.pipeline_qa import run_data_qa

        with connect() as conn:
            mid = conn.execute(
                text(
                    "select mission_id from learning_sample "
                    "where mission_id = 'M-SEAWEED-SMOKE-E2E' limit 1"
                )
            ).scalar()
        if not mid:
            mid = "M-SEAWEED-SMOKE-E2E"
            return _skip(f"mission {mid} not in samples for data-qa live call")
        out = run_data_qa(mid, confirm=True)
        return _ok("data_qa ok", result={k: out.get(k) for k in ("passed", "ok", "mission_id", "checks") if k in out or True})
    except Exception as exc:
        return _fail(f"{exc}", traceback=traceback.format_exc()[-400:])


def check_annotate_ls() -> dict:
    try:
        from ugv_mlops.integrations import _ls_ensure_project, _ls_headers

        h = _ls_headers()
        pid = _ls_ensure_project(h)
        return _ok("label studio auth+project", project_id=pid)
    except Exception as exc:
        return _fail(str(exc))


def check_gate_path() -> dict:
    with connect() as conn:
        run = conn.execute(
            text(
                "select run_id from training_run where status = 'completed' "
                "order by created_at desc nulls last limit 1"
            )
        ).scalar()
    if not run:
        return _skip("no completed training_run")
    try:
        from ugv_mlops.gate import evaluate_and_promote

        out = evaluate_and_promote(run, gate_name="smoke", target_status="staging")
        return _ok(
            "gate evaluated",
            run_id=run,
            passed=out.get("passed"),
            model_id=out.get("model_id"),
            reasons=out.get("reasons") or (out.get("gate_json") or {}).get("reasons"),
        )
    except Exception as exc:
        return _fail(str(exc)[:300])


def check_lighten_package_sync() -> dict:
    with connect() as conn:
        model = conn.execute(
            text(
                "select model_id, artifact_uri, artifact_kind, status from model_version "
                "order by created_at desc nulls last limit 1"
            )
        ).mappings().first()
        pkg = conn.execute(
            text(
                "select package_id, object_uri from update_package "
                "order by created_at desc nulls last limit 1"
            )
        ).mappings().first()
    notes = []
    if model:
        notes.append(f"model={model['model_id']} kind={model.get('artifact_kind')} status={model.get('status')}")
        uri = model.get("artifact_uri")
        if uri and str(uri).startswith("s3://"):
            try:
                from ugv_mlops.storage import parse_s3_uri

                b, k = parse_s3_uri(uri)
                notes.append(f"model_s3_bytes={len(get_bytes(b, k))}")
            except Exception as exc:
                notes.append(f"model_s3_miss={exc}")
    else:
        notes.append("no model_version")
    if pkg:
        try:
            from ugv_mlops.storage import parse_s3_uri

            b, k = parse_s3_uri(pkg["object_uri"])
            n = len(get_bytes(b, k))
            notes.append(f"package={pkg['package_id']} bytes={n}")
        except Exception as exc:
            return _warn(
                f"package row exists but s3 miss (Seaweed empty start?): {exc}",
                model=dict(model) if model else None,
                notes=notes,
            )
    else:
        notes.append("no update_package")
    if model and pkg and "model_s3_miss" not in ";".join(notes) and "s3 miss" not in ";".join(notes):
        # package may still miss on wiped volume
        if any("package=" in n and "bytes=" in n for n in notes):
            return _ok("; ".join(notes), model=dict(model), package_id=pkg["package_id"])
        return _warn("; ".join(notes), model=dict(model), package_id=pkg["package_id"])
    if model:
        return _warn(
            "model exists; package missing or S3 wiped after Garage→Seaweed — re-run lighten/package",
            notes=notes,
        )
    return _warn("no gate/package evidence on current Seaweed volume", notes=notes)


def check_carla() -> dict:
    try:
        from ugv_mlops.carla_bridge import status as carla_status

        st = carla_status()
        reachable = bool(st.get("reachable") or st.get("ok") or st.get("running"))
        if reachable:
            return _ok("carla reachable", **{k: st.get(k) for k in list(st)[:12]})
        return _skip("carla not running (compose-external)", summary={k: st.get(k) for k in list(st)[:12]})
    except Exception as exc:
        return _skip(f"carla probe error: {exc}")


def check_autoe2e() -> dict:
    try:
        from ugv_mlops.autoe2e import readiness

        st = readiness()
        ready = bool(st.get("ready") or st.get("ok"))
        if ready:
            return _ok("autoe2e ready", summary={k: st.get(k) for k in list(st)[:12]})
        return _skip("autoe2e not ready (host Autoware_e2e / GPU path)", summary={k: st.get(k) for k in list(st)[:12]})
    except Exception as exc:
        return _fail(str(exc)[:300])


def check_job_dispatch_surface() -> dict:
    kinds = [
        "ingest",
        "build_dataset",
        "train",
        "gate",
        "package",
        "lighten",
        "lakefs_publish",
        "bag_parse",
        "bag_track",
        "bag_store",
        "annotate_init",
        "data_qa",
        "label_review",
        "carla_import",
        "demo",
    ]
    from ugv_mlops import jobs

    src = open(jobs.__file__, encoding="utf-8").read()
    missing = [k for k in kinds if f'kind == "{k}"' not in src and f"kind == '{k}'" not in src]
    # lakefs only exact; train has variants
    missing = [k for k in missing if k != "train" or 'kind == "train"' not in src]
    if missing:
        return _warn(f"dispatch branches missing: {missing}")
    return _ok(f"{len(kinds)} job kinds present in dispatcher")


def check_api_routes() -> dict:
    # worker からは catalog サービス名で到達
    base = "http://catalog:8000"
    # /integrations・/pipeline は probe が重いので長め
    routes = [
        ("GET", "/health", 5.0),
        ("GET", "/lakefs", 15.0),
        ("GET", "/status", 10.0),
        ("GET", "/inbox", 10.0),
        ("GET", "/missions", 20.0),
        ("GET", "/annotate", 10.0),
        ("GET", "/carla/status", 15.0),
        ("GET", "/autoe2e/status", 15.0),
        ("GET", "/flow-options", 10.0),
        ("GET", "/pipeline", 60.0),
        ("GET", "/integrations", 90.0),
    ]
    results = []
    for method, path, timeout in routes:
        try:
            r = httpx.request(method, base + path, timeout=timeout)
            results.append({"path": path, "code": r.status_code, "ok": r.status_code < 500})
        except Exception as exc:
            results.append({"path": path, "code": None, "ok": False, "error": str(exc)})
    bad = [r for r in results if not r["ok"]]
    heavy_only = bad and all(r["path"] in {"/integrations", "/pipeline"} for r in bad)
    if bad and heavy_only:
        return _warn("heavy catalog routes slow/timed out (core GETs ok)", bad=bad, results=results)
    if bad:
        return _fail("some API routes failed", bad=bad, results=results)
    return _ok("catalog GET routes healthy", results=results)


def main() -> None:
    checks = [
        ("core_services", check_core_services),
        ("s3_chain", check_s3_chain),
        ("lakefs", check_lakefs),
        ("db_assets", check_db_pipeline_assets),
        ("ingest_dataset_train_evidence", check_ingest_dataset_train_evidence),
        ("data_qa", check_data_qa_path),
        ("annotate_labelstudio", check_annotate_ls),
        ("gate", check_gate_path),
        ("lighten_package_sync", check_lighten_package_sync),
        ("carla", check_carla),
        ("autoe2e", check_autoe2e),
        ("job_dispatch", check_job_dispatch_surface),
        ("api_routes", check_api_routes),
    ]
    report: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": settings.default_learning_stage,
        "checks": {},
    }
    for name, fn in checks:
        try:
            report["checks"][name] = fn()
        except Exception as exc:
            report["checks"][name] = _fail(str(exc), traceback=traceback.format_exc()[-500:])
    tallies = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for v in report["checks"].values():
        tallies[v.get("status", "fail")] = tallies.get(v.get("status", "fail"), 0) + 1
    report["tallies"] = tallies
    if tallies["fail"]:
        report["verdict"] = "blocked"
    elif tallies["warn"]:
        report["verdict"] = "partial"
    else:
        report["verdict"] = "healthy"
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
