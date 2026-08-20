from __future__ import annotations

import csv
import errno
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ego_web.db import Database
from ego_web.main import create_app
from ego_web.results import (
    ResultError,
    list_artifacts,
    parse_trajectory,
    resolve_artifact,
)
from ego_web.runner import RunnerResult
from ego_web.settings import Settings


class IdleRunner:
    def run(self, task: dict[str, Any], on_stage: Callable[[str], None]) -> RunnerResult:
        return RunnerResult(0, "SUCCESS")

    def terminate_all(self) -> None:
        pass


def make_settings(tmp_path: Path) -> Settings:
    data_root = tmp_path / "data"
    config_root = tmp_path / "configs"
    data_root.mkdir()
    config_root.mkdir()
    return Settings(
        data_root=data_root,
        config_root=config_root,
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path / "runtime",
        runner_python=Path(sys.executable),
        extract_python=Path(sys.executable),
        max_concurrency=1,
    )


def make_task(
    tmp_path: Path,
    *,
    status: str = "succeeded",
) -> tuple[Database, dict[str, Any]]:
    database = Database(tmp_path / "platform.sqlite3")
    database.initialize()
    task_root = tmp_path / "tasks" / "task-1"
    task_root.mkdir(parents=True)
    (task_root / "selected-config.yaml").write_text("config")
    database.create_task_group(
        {
            "id": "group",
            "device": "0805",
            "config_rel": "EGO2/okvis.yaml",
            "config_sha256": "a" * 64,
            "cam_workers": 4,
            "keep_images": False,
            "created_at": "2026-08-06T12:00:00Z",
        },
        [
            {
                "id": "task-1",
                "group_id": "group",
                "sequence": "sequence",
                "status": "queued",
                "created_at": "2026-08-06T12:00:00Z",
                "task_root": str(task_root),
            }
        ],
    )
    if status != "queued":
        database.transition_task("task-1", "extracting")
    if status in {"vio", "succeeded", "failed"}:
        database.transition_task("task-1", "vio")
    if status in {"succeeded", "failed"}:
        database.transition_task("task-1", status)
    if status == "interrupted":
        database.transition_task("task-1", "interrupted")
    task = database.get_task_details("task-1")
    assert task is not None
    return database, task


def write_trajectory(task: dict[str, Any], content: str) -> Path:
    result_dir = (
        Path(task["task_root"])
        / "output"
        / str(task["sequence"])
        / "results"
    )
    result_dir.mkdir(parents=True)
    trajectory = result_dir / "okvis2-slam-calib-final_trajectory.csv"
    trajectory.write_text(content)
    return trajectory


def test_parse_trajectory_strips_headers_and_computes_statistics(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text(
        "timestamp, p_WS_W_x, p_WS_W_y, p_WS_W_z\n"
        "1000000000, 0, 0, 0\n"
        "2000000000, 3, 0, 0\n"
        "3000000000, 3, 4, 0\n"
    )

    parsed = parse_trajectory(trajectory)

    assert parsed == {
        "timestamp_start": 1000000000,
        "timestamp_end": 3000000000,
        "duration_sec": 2.0,
        "point_count": 3,
        "points": [[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [3.0, 4.0, 0.0]],
        "bbox": {"min": [0.0, 0.0, 0.0], "max": [3.0, 4.0, 0.0]},
        "path_length_m": 7.0,
        "displacement_m": 5.0,
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "empty"),
        ("timestamp,p_WS_W_x,p_WS_W_y\n1,0,0\n", "missing columns"),
        (
            "timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n1,not-a-number,0,0\n",
            "invalid numeric",
        ),
        (
            "timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n1,nan,0,0\n",
            "non-finite",
        ),
    ],
)
def test_parse_trajectory_rejects_invalid_files(
    tmp_path: Path, content: str, message: str
) -> None:
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text(content)

    with pytest.raises(ResultError, match=message):
        parse_trajectory(trajectory)


def test_parse_trajectory_rejects_non_monotonic_timestamps_and_overflowing_stats(
    tmp_path: Path,
) -> None:
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text(
        "timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n"
        "2,1e308,0,0\n"
        "1,-1e308,0,0\n"
    )

    with pytest.raises(ResultError, match="timestamp"):
        parse_trajectory(trajectory)

    trajectory.write_text(
        "timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n"
        "1,1e308,0,0\n"
        "2,-1e308,0,0\n"
    )
    with pytest.raises(ResultError, match="non-finite statistics"):
        parse_trajectory(trajectory)


def test_parse_trajectory_wraps_encoding_and_csv_failures(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_bytes(b"timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n1,\xff,0,0\n")

    with pytest.raises(ResultError, match="invalid trajectory file"):
        parse_trajectory(trajectory)


def test_parse_trajectory_classifies_open_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.csv"
    with pytest.raises(ResultError) as missing_error:
        parse_trajectory(missing)
    assert missing_error.value.code == "trajectory_missing"

    denied = tmp_path / "denied.csv"
    denied.write_text("unused")
    original_open = Path.open

    def fail_open(self: Path, *args: object, **kwargs: object):
        if self == denied:
            raise PermissionError(errno.EACCES, "permission denied", str(self))
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    with pytest.raises(ResultError) as denied_error:
        parse_trajectory(denied)
    assert denied_error.value.code == "trajectory_invalid"
    assert "invalid trajectory file" in str(denied_error.value)


def test_parse_trajectory_wraps_csv_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text("header\n")

    class BrokenReader:
        def __iter__(self):
            return self

        def __next__(self):
            raise csv.Error("broken csv")

    monkeypatch.setattr(csv, "reader", lambda stream: BrokenReader())
    with pytest.raises(ResultError, match="invalid trajectory file"):
        parse_trajectory(trajectory)


def test_parse_trajectory_enforces_point_limit(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.csv"
    trajectory.write_text(
        "timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n"
        "1,0,0,0\n"
        "2,1,0,0\n"
    )

    with pytest.raises(ResultError, match="point limit"):
        parse_trajectory(trajectory, max_points=1)


def test_list_artifacts_includes_only_allowed_regular_files(tmp_path: Path) -> None:
    _, task = make_task(tmp_path)
    task_root = Path(task["task_root"])
    (task_root / "runner.log").write_text("runner")
    output = task_root / "output" / "sequence"
    (output / "logs").mkdir(parents=True)
    (output / "results").mkdir()
    (output / "status.txt").write_text("SUCCESS")
    (output / "logs" / "extract.log").write_text("extract")
    (output / "results" / "map.g2o").write_text("map")
    (task_root / "euroc" / "cam0").mkdir(parents=True)
    (task_root / "euroc" / "cam0" / "image.png").write_bytes(b"image")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (output / "results" / "secret-link").symlink_to(outside)

    artifacts = list_artifacts(task)

    assert [artifact["path"] for artifact in artifacts] == [
        "selected-config.yaml",
        "runner.log",
        "output/sequence/status.txt",
        "output/sequence/logs/extract.log",
        "output/sequence/results/map.g2o",
    ]
    assert all(artifact["size"] >= 0 for artifact in artifacts)


def test_resolve_artifact_rejects_intermediate_symlink_even_inside_task_root(
    tmp_path: Path,
) -> None:
    _, task = make_task(tmp_path)
    task_root = Path(task["task_root"])
    hidden = task_root / "hidden"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("secret")
    results = task_root / "output" / "sequence" / "results"
    results.mkdir(parents=True)
    (results / "link").symlink_to(hidden, target_is_directory=True)

    with pytest.raises(ResultError, match="symbolic"):
        resolve_artifact(task, "output/sequence/results/link/secret.txt")


@pytest.mark.parametrize(
    "artifact_path",
    [
        "../secret.txt",
        "/etc/passwd",
        "output/sequence/results/../../../../secret.txt",
        "euroc/cam0/image.png",
        "output/sequence",
        "output/sequence/results/secret-link",
    ],
)
def test_resolve_artifact_rejects_unsafe_or_disallowed_paths(
    tmp_path: Path, artifact_path: str
) -> None:
    _, task = make_task(tmp_path)
    task_root = Path(task["task_root"])
    output = task_root / "output" / "sequence" / "results"
    output.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (output / "secret-link").symlink_to(outside)
    (task_root / "euroc" / "cam0").mkdir(parents=True)
    (task_root / "euroc" / "cam0" / "image.png").write_bytes(b"image")

    with pytest.raises(ResultError):
        resolve_artifact(task, artifact_path)


def test_results_api_handles_status_trajectory_and_streaming_download(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, runner=IdleRunner())

    with TestClient(app) as client:
        database = app.state.database
        task_root = tmp_path / "api-task"
        task_root.mkdir()
        database.create_task_group(
            {
                "id": "group",
                "device": "0805",
                "config_rel": "config.yaml",
                "config_sha256": "a" * 64,
                "cam_workers": 4,
                "keep_images": False,
                "created_at": "2026-08-06T12:00:00Z",
            },
            [
                {
                    "id": "task",
                    "group_id": "group",
                    "sequence": "sequence",
                    "status": "queued",
                    "created_at": "2026-08-06T12:00:00Z",
                    "task_root": str(task_root),
                }
            ],
        )
        not_ready = client.get("/api/v1/tasks/task/trajectory")
        database.transition_task("task", "extracting")
        database.transition_task("task", "vio")
        database.transition_task("task", "succeeded")
        task = database.get_task_details("task")
        assert task is not None
        trajectory = write_trajectory(
            task,
            "timestamp,p_WS_W_x,p_WS_W_y,p_WS_W_z\n1,0,0,0\n2,1,0,0\n",
        )
        (task_root / "runner.log").write_text("runner")
        trajectory_response = client.get("/api/v1/tasks/task/trajectory")
        artifacts_response = client.get("/api/v1/tasks/task/artifacts")
        download = client.get(
            "/api/v1/tasks/task/artifacts/output/sequence/results/"
            "okvis2-slam-calib-final_trajectory.csv"
        )
        traversal = client.get("/api/v1/tasks/task/artifacts/../selected-config.yaml")

    assert not_ready.status_code == 409
    assert trajectory_response.status_code == 200
    assert trajectory_response.json()["point_count"] == 2
    assert artifacts_response.status_code == 200
    assert any(item["path"].endswith("final_trajectory.csv") for item in artifacts_response.json()["items"])
    assert download.status_code == 200
    assert download.content == trajectory.read_bytes()
    assert "content-length" in download.headers
    assert traversal.status_code in {400, 404}


def test_results_api_reports_missing_and_invalid_trajectory(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, runner=IdleRunner())

    with TestClient(app) as client:
        database = app.state.database
        task_root = tmp_path / "api-task"
        task_root.mkdir()
        database.create_task_group(
            {
                "id": "group",
                "device": "0805",
                "config_rel": "config.yaml",
                "config_sha256": "a" * 64,
                "cam_workers": 4,
                "keep_images": False,
                "created_at": "2026-08-06T12:00:00Z",
            },
            [
                {
                    "id": "task",
                    "group_id": "group",
                    "sequence": "sequence",
                    "status": "queued",
                    "created_at": "2026-08-06T12:00:00Z",
                    "task_root": str(task_root),
                }
            ],
        )
        database.transition_task("task", "extracting")
        database.transition_task("task", "vio")
        database.transition_task("task", "succeeded")
        missing = client.get("/api/v1/tasks/task/trajectory")
        task = database.get_task_details("task")
        assert task is not None
        write_trajectory(task, "timestamp,x\n1,0\n")
        invalid = client.get("/api/v1/tasks/task/trajectory")

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "trajectory_missing"
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "trajectory_invalid"
