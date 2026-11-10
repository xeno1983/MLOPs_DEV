"""CARLA bridge helpers without Docker."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

# Stub DB so import does not need Postgres
if "ugv_mlops.db" not in sys.modules:
    sys.modules["ugv_mlops.db"] = types.ModuleType("ugv_mlops.db")
_db = sys.modules["ugv_mlops.db"]
_db.audit = lambda *a, **k: None  # type: ignore
_db.connect = lambda: None  # type: ignore
_db.ensure_schema = lambda: None  # type: ignore

from ugv_mlops.carla_bridge import (  # noqa: E402
    _assert_allowed,
    _looks_like_bag,
    _sanitize_mission_id,
    record_command_hint,
)
from ugv_mlops.settings import settings  # noqa: E402


def test_sanitize_mission_id():
    assert "mine" in _sanitize_mission_id("mine_001")
    assert "/" not in _sanitize_mission_id("../x")


def test_looks_like_bag(tmp_path: Path):
    assert _looks_like_bag(tmp_path) is False
    (tmp_path / "metadata.yaml").write_text("x")
    (tmp_path / "a.db3").write_bytes(b"db")
    assert _looks_like_bag(tmp_path) is True


def test_assert_allowed_rejects_work_path(tmp_path: Path, monkeypatch):
    root = tmp_path / "aw"
    root.mkdir()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    sneaky = work / "secret.bin"
    sneaky.write_bytes(b"x")
    monkeypatch.setattr(settings, "inbox_dir", str(inbox))
    monkeypatch.setattr(settings, "work_dir", str(work))
    import pytest

    with pytest.raises(PermissionError):
        _assert_allowed(sneaky, root)


def test_record_command_hint():
    h = record_command_hint("unit_test_run")
    assert "ros2 bag record" in h["command"]
    assert "unit_test_run" in h["output_dir"]
