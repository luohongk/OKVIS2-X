from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ego_web.main import create_app
from ego_web.runner import RunnerResult
from ego_web.settings import Settings


class ImmediateRunner:
    def __init__(self) -> None:
        self.sequences: list[str] = []
        self.terminate_called = False

    def run(self, task: dict[str, Any], on_stage: Callable[[str], None]) -> RunnerResult:
        self.sequences.append(task["sequence"])
        on_stage("extracting")
        on_stage("vio")
        return RunnerResult(0, "SUCCESS")

    def terminate_all(self) -> None:
        self.terminate_called = True


def make_settings(tmp_path: Path, *, healthy: bool = True) -> Settings:
    repo_root = tmp_path / "repo"
    data_root = tmp_path / "data"
    config_root = tmp_path / "configs"
    data_root.mkdir()
    config_root.mkdir()
    if healthy:
        runner_script = repo_root / "mcap_vio_run" / "run_mcap_vio.py"
        extract_script = repo_root / "mcap_vio_run" / "extract_mcap_for_okvis.py"
        okvis_binary = repo_root / "build" / "okvis_app_synchronous"
        runner_script.parent.mkdir(parents=True)
        extract_script.write_text("# extractor")
        runner_script.write_text("# runner")
        okvis_binary.parent.mkdir(parents=True)
        okvis_binary.write_text("binary")
        okvis_binary.chmod(0o755)
    return Settings(
        data_root=data_root,
        config_root=config_root,
        repo_root=repo_root,
        runtime_root=tmp_path / "runtime",
        runner_python=Path(sys.executable),
        extract_python=Path(sys.executable),
        max_concurrency=2,
    )


def create_valid_catalog(
    settings: Settings,
    make_session: Callable[[Path, str, str], Path],
) -> None:
    make_session(settings.data_root, "0805", "seq-a")
    make_session(settings.data_root, "0805", "seq-b")
    config = settings.config_root / "0805" / "okvis.yaml"
    config.parent.mkdir()
    config.write_text("config")


def test_catalog_endpoints_return_devices_sequences_and_configs(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        devices = client.get("/api/v1/devices")
        sequences = client.get("/api/v1/devices/0805/sequences")
        configs = client.get("/api/v1/configs")
        missing = client.get("/api/v1/devices/missing/sequences")

    assert devices.status_code == 200
    assert devices.json() == {
        "items": [{"name": "0805", "valid_sequence_count": 2}]
    }
    assert sequences.status_code == 200
    assert [item["name"] for item in sequences.json()["items"]] == ["seq-a", "seq-b"]
    assert configs.json() == {
        "items": [{"id": "0805/okvis.yaml", "label": "0805 / okvis.yaml"}]
    }
    assert missing.status_code == 404


def test_task_group_submission_is_strict_and_enqueues_all_tasks(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    runner = ImmediateRunner()
    app = create_app(settings=settings, runner=runner)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": ["seq-a", "seq-b"],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 3, "keep_images": True},
            },
        )
        invalid_extra = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": ["seq-a"],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 4, "keep_images": False},
                "extra_args": "--skip-existing",
            },
        )
        duplicate = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": ["seq-a", "seq-a"],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 4, "keep_images": False},
            },
        )

        assert response.status_code == 201
        payload = response.json()
        assert payload["group_id"]
        assert len(payload["task_ids"]) == 2
        group = client.get(f"/api/v1/task-groups/{payload['group_id']}")
        assert group.status_code == 200
        assert group.json()["task_count"] == 2
        assert group.json()["config_rel"] == "0805/okvis.yaml"
        deadline = time.monotonic() + 2
        while len(runner.sequences) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sorted(runner.sequences) == ["seq-a", "seq-b"]

    assert invalid_extra.status_code == 422
    assert duplicate.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {
            "device": "0805",
            "sequences": ["missing"],
            "config_id": "0805/okvis.yaml",
            "options": {"cam_workers": 4, "keep_images": False},
        },
        {
            "device": "0805",
            "sequences": ["seq-a"],
            "config_id": "../outside.yaml",
            "options": {"cam_workers": 4, "keep_images": False},
        },
    ],
)
def test_invalid_task_submission_creates_nothing(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    payload: dict[str, Any],
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        response = client.post("/api/v1/task-groups", json=payload)
        tasks = client.get("/api/v1/tasks")
        groups = client.get("/api/v1/task-groups")

    assert response.status_code == 422
    assert tasks.json()["items"] == []
    assert groups.json()["items"] == []


def test_task_queries_combine_filters_and_runtime_summary(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        service = app.state.task_service
        first = service.create_task_group(
            device="0805",
            sequences=["seq-a"],
            config_id="0805/okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )
        second = service.create_task_group(
            device="0805",
            sequences=["seq-b"],
            config_id="0805/okvis.yaml",
            cam_workers=2,
            keep_images=False,
        )
        app.state.database.transition_task(first.task_ids[0], "extracting")
        filtered = client.get(
            "/api/v1/tasks",
            params={
                "device": "0805",
                "sequence": "seq-a",
                "config_id": "0805/okvis.yaml",
                "status": "extracting",
                "group_id": first.group_id,
                "limit": 10,
                "offset": 0,
            },
        )
        detail = client.get(f"/api/v1/tasks/{first.task_ids[0]}")
        missing = client.get("/api/v1/tasks/missing")
        runtime = client.get("/api/v1/runtime")
        groups = client.get("/api/v1/task-groups", params={"limit": 1, "offset": 1})

        assert filtered.status_code == 200
        assert [item["id"] for item in filtered.json()["items"]] == first.task_ids
        assert detail.json()["device"] == "0805"
        assert missing.status_code == 404
        assert runtime.json() == {
            "max_concurrency": 2,
            "running": 0,
            "queued": 1,
            "accepting_tasks": True,
        }
        assert groups.status_code == 200
        assert len(groups.json()["items"]) == 1
        assert second.group_id != first.group_id


def test_task_responses_do_not_expose_server_filesystem_paths(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        created = app.state.task_service.create_task_group(
            device="0805",
            sequences=["seq-a"],
            config_id="0805/okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )
        task = client.get(f"/api/v1/tasks/{created.task_ids[0]}").json()
        tasks = client.get("/api/v1/tasks").json()["items"]
        group = client.get(f"/api/v1/task-groups/{created.group_id}").json()

    assert "task_root" not in task
    assert all("task_root" not in item for item in tasks)
    assert all("task_root" not in item for item in group["tasks"])


def test_enqueue_rejection_does_not_leave_queued_tasks(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        app.state.scheduler.accepting_tasks = False
        response = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": ["seq-a"],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 4, "keep_images": False},
            },
        )
        tasks = client.get("/api/v1/tasks").json()["items"]

    assert response.status_code == 503
    assert len(tasks) == 1
    assert tasks[0]["status"] == "interrupted"


def test_task_group_limits_sequences_to_detail_response_capacity(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": [f"seq-{index}" for index in range(501)],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 4, "keep_images": False},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "too_long"


def test_filesystem_failure_is_reported_as_service_error(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        monkeypatch.setattr(
            app.state.task_service,
            "create_task_group",
            lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        response = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": ["seq-a"],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 4, "keep_images": False},
            },
        )

    assert response.status_code == 503


def test_sqlite_failure_is_reported_as_service_error(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        monkeypatch.setattr(
            app.state.database,
            "create_task_group",
            lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError("database locked")),
        )
        response = client.post(
            "/api/v1/task-groups",
            json={
                "device": "0805",
                "sequences": ["seq-a"],
                "config_id": "0805/okvis.yaml",
                "options": {"cam_workers": 4, "keep_images": False},
            },
        )

    assert response.status_code == 503


def test_health_reports_prerequisites_without_modifying_them(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, healthy=False)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "checks": {
            "data_root": True,
            "config_root": True,
            "runner_script": False,
            "extract_script": False,
            "runner_python": True,
            "extract_python": True,
            "okvis_binary": False,
        },
    }
    assert not settings.runner_script.exists()
    assert not settings.okvis_binary.exists()


def test_lifespan_starts_and_stops_scheduler(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runner = ImmediateRunner()
    app = create_app(settings=settings, runner=runner)

    with TestClient(app) as client:
        assert client.get("/api/v1/runtime").json()["accepting_tasks"] is True

    assert runner.terminate_called is True
    assert app.state.scheduler.accepting_tasks is False


def test_lifespan_recovers_unfinished_tasks_before_accepting_requests(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    app = create_app(settings=settings, runner=ImmediateRunner())
    app.state.database.initialize()
    created = app.state.task_service.create_task_group(
        device="0805",
        sequences=["seq-a"],
        config_id="0805/okvis.yaml",
        cam_workers=4,
        keep_images=False,
    )

    with TestClient(app) as client:
        response = client.get(f"/api/v1/tasks/{created.task_ids[0]}")

    assert response.json()["status"] == "interrupted"
