from collections.abc import Callable
from pathlib import Path

import pytest


SENSORS = ("cam0", "cam1", "cam2", "cam3", "imu")


@pytest.fixture
def make_session() -> Callable[[Path, str, str], Path]:
    def _make(data_root: Path, device: str, sequence: str) -> Path:
        session = data_root / device / sequence
        for sensor in SENSORS:
            sensor_dir = session / sensor
            sensor_dir.mkdir(parents=True, exist_ok=True)
            (sensor_dir / f"{sensor}_0.mcap").write_bytes(b"mcap")
        return session

    return _make
