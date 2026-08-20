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

from ego_web.db import Database
from ego_web.runner import Runner, RunnerResult, build_runner_command
from ego_web.scheduler import Scheduler
from ego_web.settings import Settings


def make_settings(tmp_path: Path, *, max_concurrency: int = 2) -> Settings:
    repo_root = tmp_path / "repo"
    return Settings(
        data_root=tmp_path / "data",
        config_root=tmp_path / "configs",
        repo_root=repo_root,
        runtime_root=tmp_path / "runtime",
        runner_python=Path(sys.executable),
        extract_python=Path(sys.executable),
        max_concurrency=max_concurrency,
        terminate_grace_sec=0.5,
    )


def task_details(
    tmp_path: Path,
    sequence: str = "sequence",
    *,
    keep_images: bool = False,
) -> dict[str, Any]:
    task_root = tmp_path / "runtime" / "tasks" / "device" / sequence / "config" / f"task-{sequence}"
    task_root.mkdir(parents=True)
    (task_root / "selected-config.yaml").write_text("config")
    return {
        "id": f"task-{sequence}",
        "device": "device",
        "sequence": sequence,
        "task_root": str(task_root),
        "cam_workers": 3,
        "keep_images": int(keep_images),
    }


def fake_runner_settings(tmp_path: Path) -> Settings:
    settings = make_settings(tmp_path)
    object.__setattr__(settings, "repo_root", Path("/root/OKVIS2-X"))
    return settings


def test_build_runner_command_uses_only_approved_argv(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    task = task_details(tmp_path, keep_images=True)

    command = build_runner_command(settings, task)

    assert command == [
        str(settings.runner_python),
        "-u",
        str(settings.runner_script),
        "--python",
        str(settings.extract_python),
        "--session-dir",
        str(settings.data_root / "device" / "sequence"),
        "--config",
        str(Path(task["task_root"]) / "selected-config.yaml"),
        "--euroc-root",
        str(Path(task["task_root"]) / "euroc"),
        "--output-dir",
        str(Path(task["task_root"]) / "output"),
        "--okvis-bin",
        str(settings.okvis_binary),
        "--extract-script",
        str(settings.extract_script),
        "--cam-workers",
        "3",
        "--keep-images",
    ]
    assert "--quiet" not in command


def test_runner_streams_log_reports_stages_and_requires_success_status(tmp_path: Path) -> None:
    settings = fake_runner_settings(tmp_path)
    task = task_details(tmp_path)
    runner = Runner(settings, runner_script=Path(__file__).with_name("fake_runner.py"))
    stages: list[str] = []

    result = runner.run(task, stages.append)

    assert result == RunnerResult(exit_code=0, runner_status="SUCCESS", interrupted=False)
    assert stages == ["extracting", "vio"]
    log = (Path(task["task_root"]) / "runner.log").read_text()
    assert "[EXTRACT] start" in log
    assert "[O] optimization complete" in log

    missing = task_details(tmp_path, "missing-status")
    missing_result = runner.run(missing, lambda stage: None)
    assert missing_result.exit_code == 0
    assert missing_result.runner_status is None
    assert missing_result.succeeded is False
    assert "status file" in (missing_result.error_summary or "")


def test_runner_terminates_the_whole_process_group(tmp_path: Path) -> None:
    settings = fake_runner_settings(tmp_path)
    task = task_details(tmp_path, "slow")
    runner = Runner(settings, runner_script=Path(__file__).with_name("fake_runner.py"))
    result_holder: list[RunnerResult] = []
    started = threading.Event()

    thread = threading.Thread(
        target=lambda: result_holder.append(
            runner.run(task, lambda stage: started.set() if stage == "extracting" else None)
        )
    )
    thread.start()
    assert started.wait(3)
    child_pid_file = Path(task["task_root"]) / "output" / "slow" / "child.pid"
    deadline = time.monotonic() + 3
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text())

    runner.terminate_all()
    thread.join(3)

    assert not thread.is_alive()
    assert result_holder[0].interrupted is True
    deadline = time.monotonic() + 3
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{child_pid}").exists()


def create_database_with_tasks(tmp_path: Path, sequences: list[str]) -> Database:
    database = Database(tmp_path / "platform.sqlite3")
    database.initialize()
    database.create_task_group(
        {
            "id": "group",
            "device": "device",
            "config_rel": "config.yaml",
            "config_sha256": "a" * 64,
            "cam_workers": 4,
            "keep_images": False,
            "created_at": "2026-08-06T10:00:00Z",
        },
        [
            {
                "id": f"task-{sequence}",
                "group_id": "group",
                "sequence": sequence,
                "status": "queued",
                "created_at": f"2026-08-06T10:00:{index:02d}Z",
                "task_root": str(
                    tmp_path / "tasks" / sequence / f"task-{sequence}"
                ),
            }
            for index, sequence in enumerate(sequences)
        ],
    )
    return database


class ControlledRunner:
    def __init__(self, delay: float = 0.03) -> None:
        self.delay = delay
        self.lock = threading.Lock()
        self.order: list[str] = []
        self.active = 0
        self.max_active = 0
        self.interrupted = threading.Event()

    def run(self, task: dict[str, Any], on_stage: Callable[[str], None]) -> RunnerResult:
        with self.lock:
            self.order.append(task["sequence"])
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            on_stage("extracting")
            time.sleep(self.delay)
            if self.interrupted.is_set():
                return RunnerResult(-15, None, interrupted=True)
            on_stage("vio")
            time.sleep(self.delay)
            if task["sequence"] == "fail":
                return RunnerResult(7, "FAIL_OKVIS", error_summary="fake failure")
            return RunnerResult(0, "SUCCESS")
        finally:
            with self.lock:
                self.active -= 1

    def terminate_all(self) -> None:
        self.interrupted.set()


def test_scheduler_is_fifo_bounded_and_isolates_failures(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["one", "fail", "three", "four"])
    runner = ControlledRunner()
    scheduler = Scheduler(
        database,
        runner,
        max_concurrency=2,
        now_factory=lambda: "2026-08-06T11:00:00Z",
    )

    scheduler.start()
    scheduler.enqueue(["task-one", "task-fail", "task-three", "task-four"])
    assert scheduler.wait_until_idle(5)
    scheduler.stop()

    assert runner.order == ["one", "fail", "three", "four"]
    assert runner.max_active == 2
    assert database.get_task("task-one")["status"] == "succeeded"  # type: ignore[index]
    assert database.get_task("task-fail")["status"] == "failed"  # type: ignore[index]
    assert database.get_task("task-three")["status"] == "succeeded"  # type: ignore[index]
    assert database.get_task("task-four")["status"] == "succeeded"  # type: ignore[index]
    failed = database.get_task("task-fail")
    assert failed is not None
    assert failed["exit_code"] == 7
    assert failed["runner_status"] == "FAIL_OKVIS"
    assert failed["error_summary"] == "fake failure"


def test_scheduler_shutdown_interrupts_running_and_queued_tasks(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["one", "two", "three"])
    runner = ControlledRunner(delay=1)
    scheduler = Scheduler(
        database,
        runner,
        max_concurrency=1,
        now_factory=lambda: "2026-08-06T11:00:00Z",
    )

    scheduler.start()
    scheduler.enqueue(["task-one", "task-two", "task-three"])
    deadline = time.monotonic() + 3
    while database.get_task("task-one")["status"] == "queued" and time.monotonic() < deadline:  # type: ignore[index]
        time.sleep(0.01)
    scheduler.stop()

    assert scheduler.accepting_tasks is False
    assert database.get_task("task-one")["status"] == "interrupted"  # type: ignore[index]
    assert database.get_task("task-two")["status"] == "interrupted"  # type: ignore[index]
    assert database.get_task("task-three")["status"] == "interrupted"  # type: ignore[index]
    with pytest.raises(RuntimeError, match="not accepting"):
        scheduler.enqueue(["task-one"])


def test_runner_refuses_new_processes_after_termination_begins(tmp_path: Path) -> None:
    settings = fake_runner_settings(tmp_path)
    task = task_details(tmp_path, "slow")
    runner = Runner(settings, runner_script=Path(__file__).with_name("fake_runner.py"))

    runner.terminate_all()
    started_at = time.monotonic()
    result = runner.run(task, lambda stage: None)

    assert time.monotonic() - started_at < 1
    assert result.interrupted is True
    assert not (Path(task["task_root"]) / "output" / "slow" / "child.pid").exists()


def test_runner_terminates_spawned_process_when_stage_callback_fails(tmp_path: Path) -> None:
    settings = fake_runner_settings(tmp_path)
    task = task_details(tmp_path, "slow")
    runner = Runner(settings, runner_script=Path(__file__).with_name("fake_runner.py"))

    result = runner.run(task, lambda stage: (_ for _ in ()).throw(RuntimeError("callback failed")))

    assert result.succeeded is False
    assert "callback failed" in (result.error_summary or "")
    assert runner.active_count == 0


def test_concurrent_enqueue_publishes_tickets_atomically_in_fifo_order(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["one", "two"])
    scheduler = Scheduler(database, ControlledRunner(), max_concurrency=1)
    scheduler.accepting_tasks = True
    original_put = scheduler._queue.put
    first_put_started = threading.Event()
    release_first_put = threading.Event()

    def blocking_put(item: tuple[int, str] | None) -> None:
        if item == (0, "task-one"):
            first_put_started.set()
            assert release_first_put.wait(3)
        original_put(item)

    scheduler._queue.put = blocking_put  # type: ignore[method-assign]
    first = threading.Thread(target=lambda: scheduler.enqueue(["task-one"]))
    second = threading.Thread(target=lambda: scheduler.enqueue(["task-two"]))
    first.start()
    assert first_put_started.wait(3)
    second.start()
    time.sleep(0.05)
    assert second.is_alive()
    release_first_put.set()
    first.join(3)
    second.join(3)

    assert scheduler._queue.get_nowait() == (0, "task-one")
    assert scheduler._queue.get_nowait() == (1, "task-two")


def test_enqueue_racing_with_stop_cannot_publish_after_shutdown(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["one"])
    runner = ControlledRunner()
    scheduler = Scheduler(database, runner, max_concurrency=1)
    scheduler.start()
    original_get_task = database.get_task
    validation_started = threading.Event()
    release_validation = threading.Event()

    def blocked_get_task(task_id: str) -> dict[str, Any] | None:
        validation_started.set()
        assert release_validation.wait(3)
        return original_get_task(task_id)

    database.get_task = blocked_get_task  # type: ignore[method-assign]
    errors: list[Exception] = []
    enqueue_thread = threading.Thread(
        target=lambda: _capture_error(errors, lambda: scheduler.enqueue(["task-one"]))
    )
    enqueue_thread.start()
    assert validation_started.wait(3)
    stop_thread = threading.Thread(target=scheduler.stop)
    stop_thread.start()
    stop_thread.join(3)
    assert not stop_thread.is_alive()
    release_validation.set()
    enqueue_thread.join(3)

    assert errors and isinstance(errors[0], RuntimeError)
    assert database.get_task("task-one")["status"] == "interrupted"  # type: ignore[index]
    assert scheduler.wait_until_idle(0.1)


def _capture_error(errors: list[Exception], action: Callable[[], None]) -> None:
    try:
        action()
    except Exception as exc:
        errors.append(exc)


def test_prelaunch_database_error_does_not_poison_later_fifo_tasks(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["one", "two"])
    runner = ControlledRunner(delay=0.01)
    scheduler = Scheduler(database, runner, max_concurrency=2)
    original_get_details = database.get_task_details
    failed_once = False

    def flaky_get_details(task_id: str) -> dict[str, Any] | None:
        nonlocal failed_once
        if task_id == "task-one" and not failed_once:
            failed_once = True
            raise sqlite3.OperationalError("temporary read failure")
        return original_get_details(task_id)

    database.get_task_details = flaky_get_details  # type: ignore[method-assign]
    scheduler.start()
    scheduler.enqueue(["task-one", "task-two"])

    assert scheduler.wait_until_idle(3)
    scheduler.stop()
    assert database.get_task("task-one")["status"] == "failed"  # type: ignore[index]
    assert database.get_task("task-two")["status"] == "succeeded"  # type: ignore[index]


def test_scheduler_is_one_shot_after_stop(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["one"])
    scheduler = Scheduler(database, ControlledRunner(), max_concurrency=1)
    scheduler.start()
    scheduler.stop()

    with pytest.raises(RuntimeError, match="cannot be restarted"):
        scheduler.start()


def test_scheduler_recovery_marks_old_unfinished_tasks_interrupted(tmp_path: Path) -> None:
    database = create_database_with_tasks(tmp_path, ["queued", "extracting", "done"])
    database.transition_task("task-extracting", "extracting")
    database.transition_task("task-done", "extracting")
    database.transition_task("task-done", "vio")
    database.transition_task("task-done", "succeeded")
    scheduler = Scheduler(
        database,
        ControlledRunner(),
        max_concurrency=1,
        now_factory=lambda: "2026-08-06T11:00:00Z",
    )

    changed = scheduler.recover_unfinished()

    assert changed == 2
    assert database.get_task("task-queued")["status"] == "interrupted"  # type: ignore[index]
    assert database.get_task("task-extracting")["status"] == "interrupted"  # type: ignore[index]
    assert database.get_task("task-done")["status"] == "succeeded"  # type: ignore[index]
