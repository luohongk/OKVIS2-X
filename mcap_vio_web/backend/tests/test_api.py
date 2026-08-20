from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ego_web.main import create_app
from ego_web.runner import RunnerResult
from ego_web.settings import Settings
from ego_web.task_service import UnsafeTaskRootError


class ImmediateRunner:
    def __init__(self) -> None:
        self.sequences: list[str] = []
        self.terminated_task_ids: list[str] = []
        self.terminate_called = False

    def run(
        self,
        task: dict[str, Any],
        on_stage: Callable[[str], None],
        cancel_event: threading.Event,
    ) -> RunnerResult:
        self.sequences.append(task["sequence"])
        on_stage("extracting")
        on_stage("vio")
        return RunnerResult(0, "SUCCESS")

    def terminate_task(self, task_id: str) -> None:
        self.terminated_task_ids.append(task_id)

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


def test_delete_terminal_task_removes_task_and_last_group(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    create_valid_catalog(settings, make_session)
    runner = ImmediateRunner()
    app = create_app(settings=settings, runner=runner)

    with TestClient(app) as client:
        created = app.state.task_service.create_task_group(
            device="0805",
            sequences=["seq-a"],
            config_id="0805/okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )
        task_id = created.task_ids[0]
        task_root = Path(app.state.database.get_task_details(task_id)["task_root"])
        app.state.database.transition_task(task_id, "extracting")
        app.state.database.transition_task(task_id, "vio")
        app.state.database.transition_task(task_id, "succeeded")

        response = client.delete(f"/api/v1/tasks/{task_id}")
        missing = client.get(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 204
    assert response.content == b""
    assert missing.status_code == 404
    assert not task_root.exists()
    assert app.state.database.get_task_group(created.group_id) is None
    assert runner.terminated_task_ids == [task_id]


def test_delete_queued_task_cancels_before_deleting_in_api_order(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
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
        task_id = created.task_ids[0]
        task = app.state.database.get_task_details(task_id)
        calls: list[object] = []

        def get_task_details(requested_id: str) -> dict[str, Any] | None:
            calls.append(("get", requested_id))
            return task

        def cancel_and_wait(requested_id: str, timeout: float) -> None:
            calls.append(("cancel", requested_id, timeout))

        def delete_task(requested_task: dict[str, Any]) -> bool:
            calls.append(("delete", requested_task))
            return True

        monkeypatch.setattr(app.state.database, "get_task_details", get_task_details)
        monkeypatch.setattr(app.state.scheduler, "cancel_and_wait", cancel_and_wait)
        monkeypatch.setattr(app.state.task_service, "delete_task", delete_task)

        response = client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 204
    assert calls == [
        ("get", task_id),
        ("cancel", task_id, settings.terminate_grace_sec),
        ("delete", task),
    ]


def test_delete_missing_task_returns_404_without_cancelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(settings=make_settings(tmp_path), runner=ImmediateRunner())

    with TestClient(app) as client:
        cancel_called = False

        def cancel_and_wait(task_id: str, timeout: float) -> None:
            nonlocal cancel_called
            cancel_called = True

        monkeypatch.setattr(app.state.scheduler, "cancel_and_wait", cancel_and_wait)
        response = client.delete("/api/v1/tasks/missing")

    assert response.status_code == 404
    assert cancel_called is False


def test_delete_task_returns_409_for_unsafe_task_root(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
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
        monkeypatch.setattr(
            app.state.task_service,
            "delete_task",
            lambda task: (_ for _ in ()).throw(UnsafeTaskRootError("unsafe root")),
        )
        response = client.delete(f"/api/v1/tasks/{created.task_ids[0]}")

    assert response.status_code == 409


@pytest.mark.parametrize(
    "operation,error",
    [
        ("cancel", TimeoutError("still running")),
        ("delete", OSError("disk unavailable")),
        ("delete", sqlite3.OperationalError("database locked")),
    ],
)
def test_delete_task_reports_service_unavailability(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error: Exception,
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
        target = (
            app.state.scheduler if operation == "cancel" else app.state.task_service
        )
        method = "cancel_and_wait" if operation == "cancel" else "delete_task"
        monkeypatch.setattr(
            target,
            method,
            lambda *args: (_ for _ in ()).throw(error),
        )
        response = client.delete(f"/api/v1/tasks/{created.task_ids[0]}")

    assert response.status_code == 503


def test_delete_task_is_idempotent_when_concurrent_delete_wins(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
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
        task_id = created.task_ids[0]

        def concurrent_delete(_: str) -> None:
            app.state.database.delete_task(task_id)
            return None

        monkeypatch.setattr(app.state.database, "get_task", concurrent_delete)
        response = client.delete(f"/api/v1/tasks/{task_id}")

    assert response.status_code == 204
    assert response.content == b""


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
