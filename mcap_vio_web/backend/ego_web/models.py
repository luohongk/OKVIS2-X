from pydantic import BaseModel, ConfigDict


class DeviceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    valid_sequence_count: int


class SequenceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    complete: bool
    duration_sec: float | None
    platform_name: str | None
    info_warning: str | None
    sensor_mcap_counts: dict[str, int]


class ConfigInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
