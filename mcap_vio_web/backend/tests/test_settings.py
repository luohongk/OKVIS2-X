from pathlib import Path

import pytest
from pydantic import ValidationError

from ego_web.settings import Settings


def test_settings_defaults_match_workstation(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "EGO_WEB_DATA_ROOT",
        "EGO_WEB_CONFIG_ROOT",
        "EGO_WEB_REPO_ROOT",
        "EGO_WEB_RUNTIME_ROOT",
        "EGO_WEB_MAX_CONCURRENCY",
        "EGO_WEB_CAM_WORKERS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.data_root == Path("/home/conanluo/okvis_data")
    assert settings.config_root == Path("/root/OKVIS2-X/config/myfisheye4")
    assert settings.repo_root == Path("/root/OKVIS2-X")
    assert settings.runtime_root == Path("/root/OKVIS2-X/output/ego_annotation")
    assert settings.max_concurrency == 8
    assert settings.cam_workers == 4
    assert settings.runner_script == Path("/root/OKVIS2-X/mcap_vio_run/run_mcap_vio.py")
    assert settings.okvis_binary == Path("/root/OKVIS2-X/build/okvis_app_synchronous")


def test_settings_accept_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EGO_WEB_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("EGO_WEB_CONFIG_ROOT", str(tmp_path / "configs"))
    monkeypatch.setenv("EGO_WEB_REPO_ROOT", str(tmp_path / "repo"))
    monkeypatch.setenv("EGO_WEB_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("EGO_WEB_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("EGO_WEB_CAM_WORKERS", "2")

    settings = Settings()

    assert settings.data_root == tmp_path / "data"
    assert settings.config_root == tmp_path / "configs"
    assert settings.repo_root == tmp_path / "repo"
    assert settings.runtime_root == tmp_path / "runtime"
    assert settings.max_concurrency == 3
    assert settings.cam_workers == 2
    assert settings.runner_script == tmp_path / "repo/mcap_vio_run/run_mcap_vio.py"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EGO_WEB_MAX_CONCURRENCY", "0"),
        ("EGO_WEB_CAM_WORKERS", "0"),
        ("EGO_WEB_CAM_WORKERS", "5"),
    ],
)
def test_settings_reject_invalid_worker_counts(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()
