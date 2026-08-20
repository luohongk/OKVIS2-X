from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .db import Database
from .discovery import resolve_config, resolve_sequence
from .settings import Settings


@dataclass(frozen=True)
class CreatedTaskGroup:
    group_id: str
    task_ids: list[str]


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _config_key(config_id: str) -> str:
    stem = Path(config_id).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-") or "config"
    safe_stem = safe_stem[:80]
    path_hash = hashlib.sha256(config_id.encode("utf-8")).hexdigest()[:8]
    return f"{safe_stem}-{path_hash}"


class TaskService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        id_factory: Callable[[], str] = _new_id,
        now_factory: Callable[[], str] = _utc_now,
    ) -> None:
        self.database = database
        self.settings = settings
        self.id_factory = id_factory
        self.now_factory = now_factory

    def create_task_group(
        self,
        *,
        device: str,
        sequences: list[str],
        config_id: str,
        cam_workers: int,
        keep_images: bool,
    ) -> CreatedTaskGroup:
        if not sequences:
            raise ValueError("at least one sequence is required")
        if len(sequences) != len(set(sequences)):
            raise ValueError("duplicate sequences are not allowed")
        if not 1 <= cam_workers <= 4:
            raise ValueError("cam_workers must be between 1 and 4")

        for sequence in sequences:
            resolve_sequence(self.settings.data_root, device, sequence)
        config_path = resolve_config(self.settings.config_root, config_id)
        canonical_config_id = config_path.relative_to(
            self.settings.config_root.resolve()
        ).as_posix()
        config_bytes = config_path.read_bytes()
        config_sha256 = hashlib.sha256(config_bytes).hexdigest()
        config_key = _config_key(canonical_config_id)

        group_id = self.id_factory()
        created_at = self.now_factory()
        task_ids: list[str] = []
        task_records: list[dict[str, object]] = []
        created_roots: list[Path] = []
        tasks_root = self.settings.runtime_root / "tasks"

        try:
            for sequence in sequences:
                task_id = self.id_factory()
                task_parent = self._prepare_task_parent(
                    tasks_root, device, sequence, config_key
                )
                task_root = task_parent / task_id
                task_root.mkdir(exist_ok=False)
                created_roots.append(task_root)
                (task_root / "selected-config.yaml").write_bytes(config_bytes)
                task_ids.append(task_id)
                task_records.append(
                    {
                        "id": task_id,
                        "group_id": group_id,
                        "sequence": sequence,
                        "status": "queued",
                        "created_at": created_at,
                        "task_root": str(task_root),
                    }
                )

            self.database.create_task_group(
                {
                    "id": group_id,
                    "device": device,
                    "config_rel": canonical_config_id,
                    "config_sha256": config_sha256,
                    "cam_workers": cam_workers,
                    "keep_images": keep_images,
                    "created_at": created_at,
                },
                task_records,
            )
        except Exception:
            self._remove_created_roots(created_roots)
            raise

        return CreatedTaskGroup(group_id=group_id, task_ids=task_ids)

    def _prepare_task_parent(
        self,
        tasks_root: Path,
        device: str,
        sequence: str,
        config_key: str,
    ) -> Path:
        runtime_root = self.settings.runtime_root
        if runtime_root.is_symlink():
            raise ValueError("runtime root cannot be a symbolic link")
        runtime_root.mkdir(parents=True, exist_ok=True)
        resolved_runtime = runtime_root.resolve()

        current = runtime_root
        for component in ("tasks", device, sequence, config_key):
            current = current / component
            if current.is_symlink():
                raise ValueError("task path cannot contain a symbolic link")
            current.mkdir(exist_ok=True)
            if not current.is_dir() or current.is_symlink():
                raise ValueError("task path must contain only real directories")
            try:
                current.resolve().relative_to(resolved_runtime)
            except ValueError as exc:
                raise ValueError("task path escapes runtime root") from exc
        return current

    @staticmethod
    def _remove_created_roots(created_roots: list[Path]) -> None:
        for task_root in reversed(created_roots):
            shutil.rmtree(task_root, ignore_errors=True)
