from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ego_web.db import Database
from ego_web.events import log_file_size, read_utf8_increment, stream_task_events
from ego_web.main import create_app
from ego_web.runner import RunnerResult
from ego_web.settings import Settings


class ImmediateRunner:
    def run(
        self,
        task: dict[str, Any],
        on_stage: Callable[[str], None],
        cancel_event: threading.Event,
    ) -> RunnerResult:
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


def create_task(tmp_path: Path, *, status: str = "queued") -> tuple[Database, dict[str, object]]:
    database = Database(tmp_path / "platform.sqlite3")
    database.initialize()
    task_root = tmp_path / "tasks" / "task-1"
    task_root.mkdir(parents=True)
    database.create_task_group(
        {
            "id": "group-1",
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
                "group_id": "group-1",
                "sequence": "sequence",
                "status": "queued",
                "created_at": "2026-08-06T12:00:00Z",
                "task_root": str(task_root),
            }
        ],
    )
    if status == "extracting":
        database.transition_task("task-1", "extracting")
    elif status == "vio":
        database.transition_task("task-1", "extracting")
        database.transition_task("task-1", "vio")
    elif status in {"succeeded", "failed"}:
        database.transition_task("task-1", "extracting")
        database.transition_task("task-1", "vio")
        database.transition_task("task-1", status)
    elif status == "interrupted":
        database.transition_task("task-1", "interrupted")
    task = database.get_task_details("task-1")
    assert task is not None
    return database, task


def parse_events(chunks: list[str]) -> list[tuple[str, dict[str, object], str | None]]:
    parsed: list[tuple[str, dict[str, object], str | None]] = []
    for chunk in chunks:
        if chunk.startswith(":"):
            continue
        fields: dict[str, str] = {}
        for line in chunk.strip().splitlines():
            key, value = line.split(":", 1)
            fields[key] = value.lstrip()
        parsed.append((fields["event"], json.loads(fields["data"]), fields.get("id")))
    return parsed


def test_terminal_stream_sends_snapshot_log_status_and_terminal(tmp_path: Path) -> None:
    database, task = create_task(tmp_path, status="succeeded")
    log_bytes = "extract start\n中文日志\n".encode()
    (Path(task["task_root"]) / "runner.log").write_bytes(log_bytes)

    events = parse_events(
        list(
            stream_task_events(
                database,
                "task-1",
                offset=0,
                poll_interval=0,
                heartbeat_interval=10,
            )
        )
    )

    assert [event[0] for event in events] == ["snapshot", "log", "terminal"]
    assert "task_root" not in events[0][1]["task"]
    assert events[1][1] == {"offset": len(log_bytes), "text": "extract start\n中文日志\n"}
    assert events[1][2] == str(len(log_bytes))
    assert events[2][1]["status"] == "succeeded"


def test_last_offset_replays_only_new_log_bytes(tmp_path: Path) -> None:
    database, task = create_task(tmp_path, status="failed")
    old = "old line\n".encode()
    new = "new line\n".encode()
    (Path(task["task_root"]) / "runner.log").write_bytes(old + new)

    events = parse_events(
        list(stream_task_events(database, "task-1", offset=len(old), poll_interval=0))
    )
    log_event = next(event for event in events if event[0] == "log")

    assert log_event[1] == {"offset": len(old + new), "text": "new line\n"}
    assert log_event[2] == str(len(old + new))


def test_utf8_reader_does_not_advance_past_incomplete_character(tmp_path: Path) -> None:
    log = tmp_path / "runner.log"
    encoded = "中".encode()
    log.write_bytes(encoded[:2])

    text, offset = read_utf8_increment(log, 0)
    assert text == ""
    assert offset == 0

    with log.open("ab") as stream:
        stream.write(encoded[2:])
    text, offset = read_utf8_increment(log, offset)
    assert text == "中"
    assert offset == len(encoded)


def test_terminal_stream_consumes_incomplete_utf8_at_final_eof(tmp_path: Path) -> None:
    database, task = create_task(tmp_path, status="failed")
    log = Path(task["task_root"]) / "runner.log"
    log.write_bytes(b"valid\n\xe4\xb8")

    events = parse_events(list(stream_task_events(database, "task-1", poll_interval=0)))

    log_events = [event for event in events if event[0] == "log"]
    assert log_events[-1][1]["offset"] == log.stat().st_size
    assert "".join(str(event[1]["text"]) for event in log_events) == "valid\n�"
    assert events[-1][0] == "terminal"


def test_live_stream_resets_offset_when_log_is_truncated(tmp_path: Path) -> None:
    database, task = create_task(tmp_path)
    log = Path(task["task_root"]) / "runner.log"
    log.write_text("old content")
    old_size = log.stat().st_size
    stream = stream_task_events(
        database,
        "task-1",
        offset=old_size,
        poll_interval=0,
        heartbeat_interval=0,
    )
    next(stream)
    next(stream)
    log.write_text("new")
    database.transition_task("task-1", "interrupted")

    events = parse_events(list(stream))
    log_event = next(event for event in events if event[0] == "log")

    assert log_event[1] == {"offset": 3, "text": "new", "reset": True}
    assert events[-1][0] == "terminal"


def test_live_stream_emits_heartbeat_status_change_and_terminal(tmp_path: Path) -> None:
    database, _ = create_task(tmp_path)
    stream = stream_task_events(
        database,
        "task-1",
        offset=0,
        poll_interval=0,
        heartbeat_interval=0,
    )

    snapshot = next(stream)
    heartbeat = next(stream)
    database.transition_task("task-1", "interrupted")
    remaining = list(stream)
    events = parse_events([snapshot, *remaining])

    assert "event: snapshot" in snapshot
    assert heartbeat == ": heartbeat\n\n"
    assert [event[0] for event in events] == ["snapshot", "status", "terminal"]
    assert events[1][1]["status"] == "interrupted"


def test_log_file_size_tolerates_file_disappearing(
    tmp_path: Path, monkeypatch
) -> None:
    log = tmp_path / "runner.log"
    log.write_text("abc")
    monkeypatch.setattr(Path, "stat", lambda self: (_ for _ in ()).throw(FileNotFoundError()))

    assert log_file_size(log) == 0


def test_events_endpoint_validates_task_and_offsets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, runner=ImmediateRunner())

    with TestClient(app) as client:
        app.state.database.create_task_group(
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
                    "task_root": str(tmp_path / "task"),
                }
            ],
        )
        (tmp_path / "task").mkdir()
        (tmp_path / "task" / "runner.log").write_text("abc")
        missing = client.get("/api/v1/tasks/missing/events")
        invalid_header = client.get(
            "/api/v1/tasks/task/events", headers={"Last-Event-ID": "invalid"}
        )
        too_large = client.get("/api/v1/tasks/task/events?offset=4")
        negative = client.get("/api/v1/tasks/task/events?offset=-1")
        non_numeric = client.get("/api/v1/tasks/task/events?offset=abc")

    assert missing.status_code == 404
    assert invalid_header.status_code == 400
    assert too_large.status_code == 400
    assert negative.status_code == 400
    assert non_numeric.status_code == 400
