"""P4: LakeFS fail-closed の既定と required() の整合。"""

from pydantic_settings import SettingsConfigDict

from ugv_mlops.lakefs_sync import required
from ugv_mlops.settings import Settings


class _Isolated(Settings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")


def test_settings_lakefs_required_default_true():
    assert _Isolated().lakefs_required is True


def test_required_follows_settings(monkeypatch):
    soft = _Isolated(lakefs_required=False)
    monkeypatch.setattr("ugv_mlops.lakefs_sync.settings", soft)
    assert required() is False

    hard = _Isolated(lakefs_required=True)
    monkeypatch.setattr("ugv_mlops.lakefs_sync.settings", hard)
    assert required() is True
