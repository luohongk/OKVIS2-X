from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from ego_web.db import Database
from ego_web.settings import Settings
from ego_web.task_service import TaskService


def make_settings(tmp_path: Path) -> Settings:
    repo_root = tmp_path / "repo"
    return Settings(
        data_root=tmp_path / "data",
        config_root=tmp_path / "configs",
        repo_root=repo_root,
        runtime_root=tmp_path / "runtime",
    )


def sequential_ids(*values: str) -> Callable[[], str]:
    identifiers: Iterator[str] = iter(values)
    return lambda: next(identifiers)


def test_create_group_builds_isolated_tasks_and_config_snapshots(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "0805", "seq-a")
    make_session(settings.data_root, "0805", "seq-b")
    config = settings.config_root / "0805_VIO_EGO2" / "okvis2_eucm.yaml"
    config.parent.mkdir(parents=True)
    config_bytes = b"%YAML:1.2\ncameras: []\n"
    config.write_bytes(config_bytes)
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(
        db,
        settings,
        id_factory=sequential_ids("group-1", "task-1", "task-2"),
        now_factory=lambda: "2026-08-06T10:00:00Z",
    )

    created = service.create_task_group(
        device="0805",
        sequences=["seq-a", "seq-b"],
        config_id="0805_VIO_EGO2/okvis2_eucm.yaml",
        cam_workers=3,
        keep_images=True,
    )

    assert created.group_id == "group-1"
    assert created.task_ids == ["task-1", "task-2"]
    group = db.get_task_group("group-1")
    assert group is not None
    assert group["config_rel"] == "0805_VIO_EGO2/okvis2_eucm.yaml"
    assert group["config_sha256"] == hashlib.sha256(config_bytes).hexdigest()
    assert group["cam_workers"] == 3
    assert group["keep_images"] == 1

    tasks = db.list_tasks(group_id="group-1")
    config_key = "okvis2_eucm-" + hashlib.sha256(
        b"0805_VIO_EGO2/okvis2_eucm.yaml"
    ).hexdigest()[:8]
    assert {task["sequence"] for task in tasks} == {"seq-a", "seq-b"}
    assert {task["id"] for task in tasks} == {"task-1", "task-2"}
    for task in tasks:
        task_root = Path(task["task_root"])
        assert task_root.parts[-5:-1] == (
            "tasks",
            "0805",
            task["sequence"],
            config_key,
        )
        assert task_root.name == task["id"]
        assert (task_root / "selected-config.yaml").read_bytes() == config_bytes
        assert not (task_root / "euroc").exists()
        assert not (task_root / "output").exists()


def test_config_snapshot_is_unchanged_after_source_config_changes(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "seq")
    config = settings.config_root / "device" / "okvis.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("original")
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(
        db,
        settings,
        id_factory=sequential_ids("group", "task"),
        now_factory=lambda: "2026-08-06T10:00:00Z",
    )

    service.create_task_group(
        device="EGO2",
        sequences=["seq"],
        config_id="device/okvis.yaml",
        cam_workers=4,
        keep_images=False,
    )
    config.write_text("changed")

    task = db.get_task("task")
    assert task is not None
    assert (Path(task["task_root"]) / "selected-config.yaml").read_text() == "original"


@pytest.mark.parametrize("sequences", [[], ["seq", "seq"]])
def test_empty_or_duplicate_sequences_create_nothing(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    sequences: list[str],
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "seq")
    config = settings.config_root / "okvis.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("config")
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(db, settings)

    with pytest.raises(ValueError):
        service.create_task_group(
            device="EGO2",
            sequences=sequences,
            config_id="okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )

    assert db.list_tasks() == []
    assert not (settings.runtime_root / "tasks").exists()


@pytest.mark.parametrize(
    ("sequences", "config_id"),
    [(["missing"], "okvis.yaml"), (["valid"], "missing.yaml")],
)
def test_invalid_input_is_fully_validated_before_writing(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    sequences: list[str],
    config_id: str,
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "valid")
    config = settings.config_root / "okvis.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("config")
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(db, settings)

    with pytest.raises(ValueError):
        service.create_task_group(
            device="EGO2",
            sequences=sequences,
            config_id=config_id,
            cam_workers=4,
            keep_images=False,
        )

    assert db.list_tasks() == []
    assert not (settings.runtime_root / "tasks").exists()


def test_path_aliases_are_rejected_before_writing(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "seq")
    config = settings.config_root / "okvis.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("config")
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(db, settings)

    with pytest.raises(ValueError):
        service.create_task_group(
            device="EGO2",
            sequences=["seq", "./seq"],
            config_id="okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )
    with pytest.raises(ValueError):
        service.create_task_group(
            device="EGO2",
            sequences=["seq"],
            config_id="./okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )

    assert db.list_tasks() == []
    assert not (settings.runtime_root / "tasks").exists()


def test_runtime_symlink_cannot_redirect_task_files_outside_root(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "seq")
    config = settings.config_root / "okvis.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("config")
    outside = tmp_path / "outside"
    outside.mkdir()
    settings.runtime_root.mkdir(parents=True)
    (settings.runtime_root / "tasks").symlink_to(outside, target_is_directory=True)
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(db, settings)

    with pytest.raises(ValueError, match="symbolic link"):
        service.create_task_group(
            device="EGO2",
            sequences=["seq"],
            config_id="okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )

    assert list(outside.iterdir()) == []
    assert db.list_tasks() == []


def test_long_config_name_produces_bounded_directory_component(
    tmp_path: Path, make_session: Callable[[Path, str, str], Path]
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "seq")
    config_id = f"{'x' * 247}.yaml"
    config = settings.config_root / config_id
    config.parent.mkdir(parents=True)
    config.write_text("config")
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(
        db,
        settings,
        id_factory=sequential_ids("group", "task"),
        now_factory=lambda: "2026-08-06T10:00:00Z",
    )

    service.create_task_group(
        device="EGO2",
        sequences=["seq"],
        config_id=config_id,
        cam_workers=4,
        keep_images=False,
    )

    task = db.get_task("task")
    assert task is not None
    assert len(Path(task["task_root"]).parent.name) <= 96


def test_database_failure_removes_only_new_task_directories(
    tmp_path: Path,
    make_session: Callable[[Path, str, str], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    make_session(settings.data_root, "EGO2", "seq")
    config = settings.config_root / "okvis.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("config")
    preserved = settings.runtime_root / "tasks" / "existing" / "keep.txt"
    preserved.parent.mkdir(parents=True)
    preserved.write_text("keep")
    config_key = "okvis-" + hashlib.sha256(b"okvis.yaml").hexdigest()[:8]
    preexisting_empty_parent = (
        settings.runtime_root / "tasks" / "EGO2" / "seq" / config_key
    )
    preexisting_empty_parent.mkdir(parents=True)
    db = Database(settings.database_path)
    db.initialize()
    service = TaskService(
        db,
        settings,
        id_factory=sequential_ids("group", "task"),
        now_factory=lambda: "2026-08-06T10:00:00Z",
    )

    def fail_insert(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(db, "create_task_group", fail_insert)

    with pytest.raises(sqlite3.OperationalError):
        service.create_task_group(
            device="EGO2",
            sequences=["seq"],
            config_id="okvis.yaml",
            cam_workers=4,
            keep_images=False,
        )

    assert preserved.read_text() == "keep"
    assert preexisting_empty_parent.is_dir()
    assert list(preexisting_empty_parent.iterdir()) == []
