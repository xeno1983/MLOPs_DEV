"""Unit tests for lighten / data QA helpers (no Docker / DB required)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# Stub DB/storage/settings so importing lighten does not need Postgres.
for name in ("ugv_mlops.db", "ugv_mlops.storage", "ugv_mlops.hashing", "ugv_mlops.stages"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

sys.modules["ugv_mlops.db"].audit = lambda *a, **k: None  # type: ignore
sys.modules["ugv_mlops.db"].connect = lambda: None  # type: ignore
sys.modules["ugv_mlops.hashing"].sha256_bytes = lambda b: "0" * 64  # type: ignore
sys.modules["ugv_mlops.stages"].allowed_artifacts = lambda stage, policy=None: {  # type: ignore
    "onnx",
    "adapter",
    "distilled_student",
    "joblib",
    "full_weights",
}
sys.modules["ugv_mlops.stages"].assert_artifact_allowed = lambda *a, **k: None  # type: ignore
sys.modules["ugv_mlops.storage"].get_bytes = lambda *a, **k: b""  # type: ignore
sys.modules["ugv_mlops.storage"].put_bytes = lambda *a, **k: "s3://x"  # type: ignore
sys.modules["ugv_mlops.storage"].parse_s3_uri = lambda u: ("b", "k")  # type: ignore

from ugv_mlops.lighten import _convert  # noqa: E402


def _load_looks_like_bag():
    """Load _looks_like_bag without importing pipeline_qa (needs settings/db)."""
    src = Path(__file__).resolve().parents[1] / "src" / "ugv_mlops" / "pipeline_qa.py"
    text = src.read_text(encoding="utf-8")
    start = text.index("def _looks_like_bag")
    end = text.index("\ndef count_audit")
    ns: dict = {"Path": Path}
    exec(text[start:end], ns)
    return ns["_looks_like_bag"]


_looks_like_bag = _load_looks_like_bag()


def test_convert_adapter_rejects_huge():
    huge = b"x" * (8_388_608 + 1)
    with pytest.raises(PermissionError):
        _convert(huge, "joblib", "m.joblib", "adapter")


def test_convert_adapter_repacks_small_joblib():
    raw = b"tiny-joblib"
    out, kind, detail = _convert(raw, "joblib", "m.joblib", "adapter")
    assert kind == "adapter"
    assert out == raw
    assert "adapter" in detail


def test_convert_onnx_unknown_kind_raises():
    with pytest.raises(TypeError):
        _convert(b"nope", "calibration", "c.bin", "onnx")


def test_adapter_does_not_satisfy_onnx():
    with pytest.raises(TypeError):
        _convert(b"adapter-bytes", "adapter", "a.adapter", "onnx")


def test_looks_like_bag_images(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"x")
    assert _looks_like_bag(tmp_path) is True


def test_looks_like_bag_empty(tmp_path):
    assert _looks_like_bag(tmp_path) is False


def test_looks_like_bag_rosbag(tmp_path):
    (tmp_path / "metadata.yaml").write_text("rosbag2_bagfile_information: {}\n")
    (tmp_path / "data.db3").write_bytes(b"db")
    assert _looks_like_bag(tmp_path) is True
