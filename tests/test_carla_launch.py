"""CARLA launch helpers（実 UE4 は起動しない）。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_db = sys.modules.setdefault("ugv_mlops.db", types.ModuleType("ugv_mlops.db"))
_db.audit = lambda *a, **k: None  # type: ignore
_db.connect = lambda: None  # type: ignore
_db.ensure_schema = lambda: None  # type: ignore

import pytest

from ugv_mlops import carla_launch as cl
from ugv_mlops.settings import settings


def test_runner_for_carla_jobs_is_host():
    # jobs 全体の import は DB/sqlalchemy 依存があるため、契約をソースで固定
    src = Path(__file__).resolve().parents[1] / "src" / "ugv_mlops" / "jobs.py"
    text = src.read_text(encoding="utf-8")
    assert 'if kind in {"carla_launch", "carla_stop"}:' in text
    assert 'return "host"' in text.split('if kind in {"carla_launch", "carla_stop"}:')[1].split("\n")[1]


def test_normalize_components_stack():
    assert cl._normalize_components(["stack"]) == ["server", "mining_sim"]
    assert cl._normalize_components(["carla"]) == ["server"]


def test_launch_dry_run_without_carla_root(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "carla_root", "")
    monkeypatch.setattr(settings, "work_dir", str(tmp_path / "work"))
    monkeypatch.delenv("CARLA_ROOT", raising=False)
    # よくあるパス探索を無効化
    monkeypatch.setattr(cl, "resolve_carla_root", lambda: None)
    out = cl.launch(["server"], dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["planned"][0]["component"] == "server"
    assert out["planned"][0].get("missing_root") is True


def test_launch_rejected_in_container(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cl, "in_container", lambda: True)
    monkeypatch.setattr(settings, "work_dir", str(tmp_path / "work"))
    with pytest.raises(RuntimeError, match="コンテナ"):
        cl.launch(["server"], dry_run=False)


def test_probe_carla_returns_dict():
    p = cl.probe_carla(host="127.0.0.1", port=1, timeout=0.05)
    assert "listening" in p
    assert p["port"] == 1


def test_looks_like_carla_root(tmp_path: Path):
    assert cl._looks_like_carla_root(tmp_path) is False
    (tmp_path / "CarlaUE4.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    assert cl._looks_like_carla_root(tmp_path) is True
