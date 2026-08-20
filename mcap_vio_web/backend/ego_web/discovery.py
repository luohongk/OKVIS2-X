from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ConfigInfo, DeviceInfo, SequenceInfo

SENSOR_NAMES = ("cam0", "cam1", "cam2", "cam3", "imu")
CONFIG_SUFFIXES = {".yaml", ".yml"}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_direct_child(root: Path, name: str) -> Path:
    relative = Path(name)
    if (
        not name
        or relative.is_absolute()
        or len(relative.parts) != 1
        or relative.as_posix() != name
        or name in {".", ".."}
    ):
        raise ValueError("path must use a canonical direct-child name")
    resolved_root = root.resolve()
    candidate = resolved_root / relative
    if candidate.is_symlink():
        raise ValueError("symbolic-link identifiers are not allowed")
    resolved = candidate.resolve()
    if not _is_within(resolved, resolved_root) or resolved.parent != resolved_root:
        raise ValueError("path escapes root")
    return resolved


def _sensor_mcap_counts(session: Path) -> dict[str, int] | None:
    counts: dict[str, int] = {}
    session_root = session.resolve()
    for sensor in SENSOR_NAMES:
        sensor_dir = session / sensor
        if not sensor_dir.is_dir() or sensor_dir.is_symlink():
            return None
        resolved_sensor = sensor_dir.resolve()
        if not _is_within(resolved_sensor, session_root):
            return None
        count = sum(
            1
            for item in sensor_dir.iterdir()
            if item.suffix.lower() == ".mcap" and item.is_file() and not item.is_symlink()
        )
        if count == 0:
            return None
        counts[sensor] = count
    return counts


def _read_info(session: Path) -> tuple[float | None, str | None, str | None]:
    info_path = session / f"{session.name}.info"
    if not info_path.is_file() or info_path.is_symlink():
        return None, None, "missing .info file"
    try:
        payload: Any = json.loads(info_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("root is not an object")
        duration = payload.get("time_duration_sec")
        if duration is not None and not isinstance(duration, (int, float)):
            raise ValueError("time_duration_sec is not numeric")
        collect_info = payload.get("collect_info")
        platform_name = collect_info.get("platform_name") if isinstance(collect_info, dict) else None
        if platform_name is not None and not isinstance(platform_name, str):
            raise ValueError("platform_name is not text")
        return float(duration) if duration is not None else None, platform_name, None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, None, f"invalid .info file: {exc}"


def discover_sequences(data_root: Path, device_name: str) -> list[SequenceInfo]:
    device = _resolve_direct_child(data_root, device_name)
    if not device.is_dir():
        raise ValueError(f"unknown device: {device_name}")

    sequences: list[SequenceInfo] = []
    for session in sorted(device.iterdir(), key=lambda item: item.name):
        if not session.is_dir() or session.is_symlink():
            continue
        counts = _sensor_mcap_counts(session)
        if counts is None:
            continue
        duration, platform_name, warning = _read_info(session)
        sequences.append(
            SequenceInfo(
                name=session.name,
                complete=(session / ".complete").is_file(),
                duration_sec=duration,
                platform_name=platform_name,
                info_warning=warning,
                sensor_mcap_counts=counts,
            )
        )
    return sequences


def discover_devices(data_root: Path) -> list[DeviceInfo]:
    resolved_root = data_root.resolve()
    if not resolved_root.is_dir():
        return []

    devices: list[DeviceInfo] = []
    for device in sorted(resolved_root.iterdir(), key=lambda item: item.name):
        if not device.is_dir() or device.is_symlink():
            continue
        sequences = discover_sequences(resolved_root, device.name)
        if sequences:
            devices.append(DeviceInfo(name=device.name, valid_sequence_count=len(sequences)))
    return devices


def resolve_sequence(data_root: Path, device_name: str, sequence_name: str) -> Path:
    device = _resolve_direct_child(data_root, device_name)
    sequence = _resolve_direct_child(device, sequence_name)
    if not sequence.is_dir() or sequence.is_symlink() or _sensor_mcap_counts(sequence) is None:
        raise ValueError("unknown or invalid sequence")
    return sequence


def discover_configs(config_root: Path) -> list[ConfigInfo]:
    resolved_root = config_root.resolve()
    if not resolved_root.is_dir():
        return []

    configs: list[ConfigInfo] = []
    for candidate in resolved_root.rglob("*"):
        if (
            candidate.suffix.lower() not in CONFIG_SUFFIXES
            or not candidate.is_file()
            or candidate.is_symlink()
        ):
            continue
        resolved = candidate.resolve()
        if not _is_within(resolved, resolved_root):
            continue
        relative = candidate.relative_to(resolved_root)
        config_id = relative.as_posix()
        configs.append(ConfigInfo(id=config_id, label=" / ".join(relative.parts)))
    return sorted(configs, key=lambda item: item.id)


def resolve_config(config_root: Path, config_id: str) -> Path:
    relative = Path(config_id)
    if (
        not config_id
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != config_id
    ):
        raise ValueError("invalid or non-canonical config path")
    resolved_root = config_root.resolve()
    candidate = resolved_root / relative
    if candidate.is_symlink():
        raise ValueError("symbolic-link configs are not allowed")
    resolved = candidate.resolve()
    if (
        not _is_within(resolved, resolved_root)
        or resolved.suffix.lower() not in CONFIG_SUFFIXES
        or not resolved.is_file()
    ):
        raise ValueError("unknown config")
    return resolved
