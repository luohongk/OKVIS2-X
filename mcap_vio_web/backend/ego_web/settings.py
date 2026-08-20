from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EGO_WEB_", extra="ignore")

    data_root: Path = Path("/home/conanluo/okvis_data")
    config_root: Path = Path("/root/OKVIS2-X/config/myfisheye4")
    repo_root: Path = Path("/root/OKVIS2-X")
    runtime_root: Path = Path("/root/OKVIS2-X/output/ego_annotation")
    max_concurrency: int = Field(default=8, ge=1)
    cam_workers: int = Field(default=4, ge=1, le=4)
    runner_python: Path = Path("/usr/bin/python3")
    extract_python: Path = Path("/usr/bin/python3")
    terminate_grace_sec: float = Field(default=10.0, gt=0)
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    @computed_field
    @property
    def runner_script(self) -> Path:
        return self.repo_root / "mcap_vio_run" / "run_mcap_vio.py"

    @computed_field
    @property
    def okvis_binary(self) -> Path:
        return self.repo_root / "build" / "okvis_app_synchronous"

    @computed_field
    @property
    def extract_script(self) -> Path:
        return self.repo_root / "mcap_vio_run" / "extract_mcap_for_okvis.py"

    @computed_field
    @property
    def database_path(self) -> Path:
        return self.runtime_root / "platform.sqlite3"

    @computed_field
    @property
    def frontend_dist(self) -> Path:
        return self.repo_root / "mcap_vio_web" / "frontend" / "dist"
