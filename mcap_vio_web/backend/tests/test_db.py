from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ego_web.db import Database, InvalidStatusTransition


def group_record(group_id: str = "group-1", **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": group_id,
        "device": "0805",
        "config_rel": "0805_VIO_EGO2/okvis2_eucm.yaml",
        "config_sha256": "a" * 64,
        "cam_workers": 4,
        "keep_images": False,
        "created_at": "2026-08-06T08:00:00Z",
    }
    record.update(overrides)
    return record


def task_record(
    task_id: str,
    sequence: str,
    group_id: str = "group-1",
    **overrides: object,
) -> dict[str, object]:
    record: dict[str, object] = {
        "id": task_id,
        "group_id": group_id,
        "sequence": sequence,
        "status": "queued",
        "created_at": "2026-08-06T08:00:00Z",
        "task_root": f"/runtime/{task_id}",
    }
    record.update(overrides)
    return record


def test_initialize_creates_versioned_wal_database(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")

    db.initialize()

    with db.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"task_groups", "tasks"}.issubset(tables)


def test_initialize_rejects_newer_or_unknown_existing_schema(tmp_path: Path) -> None:
    newer_path = tmp_path / "newer.sqlite3"
    with sqlite3.connect(newer_path) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(RuntimeError, match="database schema version"):
        Database(newer_path).initialize()
    with sqlite3.connect(newer_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2

    unknown_path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(unknown_path) as connection:
        connection.execute("CREATE TABLE legacy (id INTEGER)")
    with pytest.raises(RuntimeError, match="unknown unversioned database"):
        Database(unknown_path).initialize()


def test_create_group_inserts_group_and_tasks_atomically(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()

    db.create_task_group(
        group_record(),
        [task_record("task-1", "seq-1"), task_record("task-2", "seq-2")],
    )

    group = db.get_task_group("group-1")
    assert group is not None
    assert group["device"] == "0805"
    assert group["task_count"] == 2
    assert group["aggregate_status"] == "queued"
    assert [item["id"] for item in db.list_tasks(group_id="group-1")] == [
        "task-1",
        "task-2",
    ]


def test_create_group_rejects_empty_or_cross_group_tasks(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()

    with pytest.raises(ValueError, match="at least one task"):
        db.create_task_group(group_record(), [])
    with pytest.raises(ValueError, match="same group"):
        db.create_task_group(
            group_record(),
            [task_record("task-1", "seq-1", group_id="another-group")],
        )

    assert db.get_task_group("group-1") is None
    assert db.list_tasks() == []


def test_create_group_rolls_back_everything_when_a_task_is_invalid(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()
    tasks = [
        task_record("task-1", "seq-1", task_root="/runtime/shared"),
        task_record("task-2", "seq-2", task_root="/runtime/shared"),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        db.create_task_group(group_record(), tasks)

    assert db.get_task_group("group-1") is None
    assert db.list_tasks(group_id="group-1") == []


def test_transition_task_enforces_state_machine_and_records_result(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()
    db.create_task_group(group_record(), [task_record("task-1", "seq-1")])

    db.transition_task(
        "task-1",
        "extracting",
        started_at="2026-08-06T08:01:00Z",
    )
    db.transition_task("task-1", "vio")
    db.transition_task(
        "task-1",
        "succeeded",
        finished_at="2026-08-06T08:09:00Z",
        exit_code=0,
        runner_status="SUCCESS",
    )

    task = db.get_task("task-1")
    assert task is not None
    assert task["status"] == "succeeded"
    assert task["started_at"] == "2026-08-06T08:01:00Z"
    assert task["finished_at"] == "2026-08-06T08:09:00Z"
    assert task["exit_code"] == 0
    assert task["runner_status"] == "SUCCESS"
    with pytest.raises(InvalidStatusTransition):
        db.transition_task("task-1", "vio")


def test_task_group_status_is_aggregated_from_children(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()
    db.create_task_group(
        group_record(),
        [task_record("task-1", "seq-1"), task_record("task-2", "seq-2")],
    )

    db.transition_task("task-1", "extracting")
    assert db.get_task_group("group-1")["aggregate_status"] == "running"  # type: ignore[index]
    db.transition_task("task-1", "vio")
    db.transition_task("task-1", "succeeded")
    db.transition_task("task-2", "extracting")
    db.transition_task("task-2", "vio")
    db.transition_task("task-2", "succeeded")
    assert db.get_task_group("group-1")["aggregate_status"] == "succeeded"  # type: ignore[index]

    db.create_task_group(group_record("group-2"), [task_record("task-3", "seq-3", "group-2")])
    db.transition_task("task-3", "extracting")
    db.transition_task("task-3", "failed", error_summary="extract failed")
    assert db.get_task_group("group-2")["aggregate_status"] == "completed_with_errors"  # type: ignore[index]


def test_list_tasks_combines_filters_and_uses_stable_pagination(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()
    db.create_task_group(
        group_record("group-1", device="0805"),
        [
            task_record("task-1", "seq-a", created_at="2026-08-06T08:00:00Z"),
            task_record("task-2", "seq-b", created_at="2026-08-06T08:01:00Z"),
        ],
    )
    db.create_task_group(
        group_record("group-2", device="0804", config_rel="other/config.yaml"),
        [task_record("task-3", "seq-a", "group-2", created_at="2026-08-06T08:02:00Z")],
    )
    db.transition_task("task-2", "extracting")

    assert [task["id"] for task in db.list_tasks(device="0805", status="queued")] == [
        "task-1"
    ]
    assert [task["id"] for task in db.list_tasks(sequence="seq-a")] == [
        "task-3",
        "task-1",
    ]
    assert [task["id"] for task in db.list_tasks(config_rel="other/config.yaml")] == [
        "task-3"
    ]
    assert [task["id"] for task in db.list_tasks(limit=1, offset=1)] == ["task-2"]


def test_mark_unfinished_interrupted_preserves_terminal_tasks(tmp_path: Path) -> None:
    db = Database(tmp_path / "platform.sqlite3")
    db.initialize()
    db.create_task_group(
        group_record(),
        [task_record("task-1", "seq-1"), task_record("task-2", "seq-2")],
    )
    db.transition_task("task-2", "extracting")
    db.transition_task("task-2", "failed")

    changed = db.mark_unfinished_interrupted("2026-08-06T09:00:00Z")

    assert changed == 1
    assert db.get_task("task-1")["status"] == "interrupted"  # type: ignore[index]
    assert db.get_task("task-1")["finished_at"] == "2026-08-06T09:00:00Z"  # type: ignore[index]
    assert db.get_task("task-2")["status"] == "failed"  # type: ignore[index]
