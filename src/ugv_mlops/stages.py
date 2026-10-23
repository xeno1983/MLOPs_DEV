from __future__ import annotations

from typing import Any

import yaml
from pathlib import Path

from ugv_mlops.settings import settings


def load_policy(path: str | None = None) -> dict[str, Any]:
    policy_path = Path(path or settings.artifact_policy_path)
    with policy_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def allowed_artifacts(stage: str, policy: dict[str, Any] | None = None) -> set[str]:
    policy = policy or load_policy()
    stage_cfg = policy.get("stages", {}).get(stage, {})
    return set(stage_cfg.get("allowed_artifacts") or [])


def max_bytes(stage: str, policy: dict[str, Any] | None = None) -> int:
    policy = policy or load_policy()
    return int(policy.get("stages", {}).get(stage, {}).get("max_bytes") or 0)


def gate_thresholds(gate_name: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    return dict(policy.get("gates", {}).get(gate_name, {}))


def assert_artifact_allowed(stage: str, kind: str, size_bytes: int) -> None:
    allowed = allowed_artifacts(stage)
    if kind not in allowed:
        raise PermissionError(
            f"artifact kind '{kind}' is not allowed at stage {stage}; allowed={sorted(allowed)}"
        )
    limit = max_bytes(stage)
    if limit and size_bytes > limit:
        raise PermissionError(
            f"artifact size {size_bytes} exceeds stage {stage} limit {limit}"
        )
