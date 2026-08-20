import json
from collections.abc import Callable
from pathlib import Path

import pytest

from ego_web.discovery import (
    discover_configs,
    discover_devices,
    discover_sequences,
    resolve_config,
    resolve_sequence,
)


def test_devices_include_only_directories_with_valid_sessions(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    make_session(tmp_path, "0805", "20260805-113804")
    make_session(tmp_path, "0805", "20260805-114500")
    (tmp_path / "empty-device").mkdir()
    (tmp_path / "recording.bag").write_bytes(b"bag")
    nested = make_session(tmp_path / "wrapper", "nested-device", "nested-session")
    assert nested.exists()

    devices = discover_devices(tmp_path)

    assert [(item.name, item.valid_sequence_count) for item in devices] == [("0805", 2)]


def test_sequences_require_mcap_in_all_five_sensor_directories(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    valid = make_session(tmp_path, "EGO2", "valid")
    missing_sensor = make_session(tmp_path, "EGO2", "missing-sensor")
    (missing_sensor / "cam2" / "cam2_0.mcap").unlink()
    empty_sensor = make_session(tmp_path, "EGO2", "empty-sensor")
    (empty_sensor / "imu" / "imu_0.mcap").unlink()
    (empty_sensor / "imu" / "metadata.yaml").write_text("topic: imu\n")
    assert valid.exists()

    sequences = discover_sequences(tmp_path, "EGO2")

    assert [item.name for item in sequences] == ["valid"]
    assert sequences[0].sensor_mcap_counts == {
        "cam0": 1,
        "cam1": 1,
        "cam2": 1,
        "cam3": 1,
        "imu": 1,
    }


def test_sequence_metadata_and_completion_are_reported(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    session = make_session(tmp_path, "0805", "20260805-113804")
    (session / ".complete").touch()
    (session / "20260805-113804.info").write_text(
        json.dumps(
            {
                "time_duration_sec": 247,
                "collect_info": {"platform_name": "ego2"},
            }
        )
    )

    sequence = discover_sequences(tmp_path, "0805")[0]

    assert sequence.complete is True
    assert sequence.duration_sec == 247
    assert sequence.platform_name == "ego2"
    assert sequence.info_warning is None


@pytest.mark.parametrize("info_content", [None, "{not-json"])
def test_missing_or_invalid_info_does_not_hide_valid_sequence(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    info_content: str | None,
) -> None:
    session = make_session(tmp_path, "EGO2", "session")
    if info_content is not None:
        (session / "session.info").write_text(info_content)

    sequence = discover_sequences(tmp_path, "EGO2")[0]

    assert sequence.name == "session"
    assert sequence.duration_sec is None
    assert sequence.platform_name is None
    assert sequence.info_warning


def test_configs_are_recursive_sorted_yaml_files(tmp_path: Path) -> None:
    (tmp_path / "0805_VIO_EGO2").mkdir()
    (tmp_path / "0805_VIO_EGO2" / "okvis2_eucm.yaml").write_text("config")
    (tmp_path / "base.yml").write_text("base")
    (tmp_path / "notes.txt").write_text("ignore")

    configs = discover_configs(tmp_path)

    assert [item.id for item in configs] == [
        "0805_VIO_EGO2/okvis2_eucm.yaml",
        "base.yml",
    ]
    assert configs[0].label == "0805_VIO_EGO2 / okvis2_eucm.yaml"


def test_discovery_ignores_symlinks_that_escape_roots(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    config_root = tmp_path / "configs"
    outside = tmp_path / "outside"
    outside_session = outside / "device" / "session"
    for sensor in ("cam0", "cam1", "cam2", "cam3", "imu"):
        sensor_dir = outside_session / sensor
        sensor_dir.mkdir(parents=True, exist_ok=True)
        (sensor_dir / "part.mcap").write_bytes(b"mcap")
    outside_config = outside / "outside.yaml"
    outside_config.write_text("outside")
    data_root.mkdir()
    config_root.mkdir()
    (data_root / "escaped-device").symlink_to(outside / "device", target_is_directory=True)
    (config_root / "escaped.yaml").symlink_to(outside_config)

    assert discover_devices(data_root) == []
    assert discover_configs(config_root) == []


@pytest.mark.parametrize(
    "identifier",
    ["../outside.yaml", "/tmp/outside.yaml", "sub/../../outside.yaml", ""],
)
def test_config_resolution_rejects_untrusted_paths(tmp_path: Path, identifier: str) -> None:
    with pytest.raises(ValueError):
        resolve_config(tmp_path, identifier)


def test_config_resolution_returns_existing_yaml(tmp_path: Path) -> None:
    config = tmp_path / "device" / "okvis.yaml"
    config.parent.mkdir()
    config.write_text("config")

    assert resolve_config(tmp_path, "device/okvis.yaml") == config.resolve()


@pytest.mark.parametrize("device,sequence", [("../outside", "session"), ("device", "../outside")])
def test_sequence_resolution_rejects_path_traversal(
    tmp_path: Path, device: str, sequence: str
) -> None:
    with pytest.raises(ValueError):
        resolve_sequence(tmp_path, device, sequence)
