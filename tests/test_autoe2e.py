"""AutoE2E 連携のオフライン検査（DB / Garage / GPU なし）。 python tests/test_autoe2e.py で実行。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ugv_mlops import autoe2e, gate, jobs  # noqa: E402


def _ckpt_dir(tag_best: bool, base_ade: float | None, epochs: list[float]) -> Path:
    out = Path(tempfile.mkdtemp()) / "out"
    (out / "last").mkdir(parents=True)
    (out / "last" / "checkpoint.pt").write_bytes(b"x")
    if tag_best:
        (out / "best").mkdir()
        (out / "best" / "checkpoint.pt").write_bytes(b"x")
    hist = []
    if base_ade is not None:
        hist.append({"epoch": 0, "val": {"ade_6p4s_m": base_ade, "fde_6p4s_m": base_ade * 2}})
    for i, ade in enumerate(epochs, 1):
        hist.append({"epoch": i, "train": {"trajectory": 1.0, "total": 1.0}, "val": {"ade_6p4s_m": ade, "fde_6p4s_m": ade * 2}})
    (out / "history.json").write_text(json.dumps(hist))
    (out / "train_config.json").write_text(
        json.dumps({"train_samples": 40, "val_samples": 10, "constant_velocity_baseline": {"ade_6p4s_m": 3.0, "fde_6p4s_m": 7.0}})
    )
    return out


def test_collect_improved() -> None:
    got = autoe2e._collect(_ckpt_dir(True, 4.0, [3.5, 2.5, 2.8]))
    m = got["metrics"]
    assert got["tag"] == "best" and m["best_epoch"] == 2 and m["ade_6p4s_m"] == 2.5
    assert m["improved_over_parent"] is True and m["parent_ade_6p4s_m"] == 4.0
    assert m["n_test"] == 10 and m["cv_ade_6p4s_m"] == 3.0


def test_collect_not_improved_uses_last() -> None:
    got = autoe2e._collect(_ckpt_dir(False, 2.0, [2.5, 2.2]))
    m = got["metrics"]
    assert got["tag"] == "last" and m["best_epoch"] == 2 and m["improved_over_parent"] is False


def test_collect_skip_baseline() -> None:
    m = autoe2e._collect(_ckpt_dir(True, None, [2.5]))["metrics"]
    assert "improved_over_parent" not in m and "parent_ade_6p4s_m" not in m


def test_gate_reasons() -> None:
    staging = {"require_improved_over_parent": True, "max_ade_vs_cv_ratio": 1.0}
    good = autoe2e._collect(_ckpt_dir(True, 4.0, [2.5]))["metrics"]
    assert gate._autoe2e_reasons(good, staging) == []
    assert gate._autoe2e_reasons(good, {**staging, "max_ade_vs_cv_ratio": 0.8})  # 2.5 > 3.0*0.8
    worse = autoe2e._collect(_ckpt_dir(False, 2.0, [2.5]))["metrics"]
    assert any("親" in r for r in gate._autoe2e_reasons(worse, staging))
    nobase = autoe2e._collect(_ckpt_dir(True, None, [2.5]))["metrics"]
    assert any("比較" in r for r in gate._autoe2e_reasons(nobase, staging))
    assert gate._autoe2e_reasons(nobase, {**staging, "require_improved_over_parent": False}) == []
    assert gate._autoe2e_reasons(good, {"max_ade_6p4s_m": 2.0})


def test_runner_routing() -> None:
    assert jobs.runner_for("build_dataset", {"task": "autoe2e"}) == "host"
    assert jobs.runner_for("build_dataset", {"task": "e2e"}) == "docker"
    assert jobs.runner_for("gate", {"run_id": "x"}) == "docker"
    assert jobs.runner_for("demo", {}) == "docker"


def test_stage_guard() -> None:
    try:
        autoe2e.train_autoe2e("dv", "S1")
    except PermissionError:
        return
    raise AssertionError("S1 must be refused for full_weights")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
