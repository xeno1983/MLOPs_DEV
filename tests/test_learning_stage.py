"""O7: train stage=smoke 等のゲート名を学習段として拒否する。"""

from __future__ import annotations

import pytest

from ugv_mlops.stages import LEARNING_STAGES, assert_learning_stage


def test_learning_stages_are_s0_s4() -> None:
    assert LEARNING_STAGES == {"S0", "S1", "S2", "S3", "S4"}


@pytest.mark.parametrize("stage", sorted(LEARNING_STAGES))
def test_assert_learning_stage_accepts(stage: str) -> None:
    assert assert_learning_stage(stage) == stage


@pytest.mark.parametrize("bad", ["smoke", "staging", "production", "S5", "s3", ""])
def test_assert_learning_stage_rejects_gate_names(bad: str) -> None:
    with pytest.raises(ValueError, match="train stage"):
        assert_learning_stage(bad)
