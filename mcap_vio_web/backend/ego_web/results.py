from __future__ import annotations

import csv
import math
import stat
from pathlib import Path, PurePosixPath
from typing import Any


class ResultError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_artifact") -> None:
        super().__init__(message)
        self.code = code


TRAJECTORY_NAME = "okvis2-slam-calib-final_trajectory.csv"
REQUIRED_COLUMNS = ("timestamp", "p_WS_W_x", "p_WS_W_y", "p_WS_W_z")


def parse_trajectory(path: Path, *, max_points: int = 200_000) -> dict[str, Any]:
    try:
        stream = path.open("r", encoding="utf-8", newline="")
    except FileNotFoundError as exc:
        raise ResultError("trajectory file is missing", code="trajectory_missing") from exc
    except OSError as exc:
        raise ResultError("invalid trajectory file", code="trajectory_invalid") from exc

    with stream:
        reader = csv.reader(stream)
        try:
            raw_header = next(reader)
        except StopIteration as exc:
            raise ResultError("trajectory file is empty", code="trajectory_invalid") from exc
        except (UnicodeError, csv.Error, OSError) as exc:
            raise ResultError("invalid trajectory file", code="trajectory_invalid") from exc
        header = [column.strip() for column in raw_header]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            raise ResultError(
                f"trajectory is missing columns: {', '.join(missing)}",
                code="trajectory_invalid",
            )
        indices = {column: header.index(column) for column in REQUIRED_COLUMNS}

        points: list[list[float]] = []
        first_timestamp: int | None = None
        previous_timestamp: int | None = None
        last_timestamp: int | None = None
        path_length = 0.0
        previous: list[float] | None = None
        minima = [math.inf, math.inf, math.inf]
        maxima = [-math.inf, -math.inf, -math.inf]
        try:
            for line_number, row in enumerate(reader, start=2):
                if not row or not any(value.strip() for value in row):
                    continue
                try:
                    timestamp = int(row[indices["timestamp"]].strip())
                    point = [
                        float(row[indices["p_WS_W_x"]].strip()),
                        float(row[indices["p_WS_W_y"]].strip()),
                        float(row[indices["p_WS_W_z"]].strip()),
                    ]
                except (ValueError, IndexError) as exc:
                    raise ResultError(
                        f"invalid numeric trajectory data at line {line_number}",
                        code="trajectory_invalid",
                    ) from exc
                if not all(math.isfinite(value) for value in point):
                    raise ResultError(
                        f"non-finite trajectory coordinate at line {line_number}",
                        code="trajectory_invalid",
                    )
                if previous_timestamp is not None and timestamp < previous_timestamp:
                    raise ResultError(
                        f"trajectory timestamp decreases at line {line_number}",
                        code="trajectory_invalid",
                    )
                if len(points) >= max_points:
                    raise ResultError(
                        f"trajectory exceeds point limit of {max_points}",
                        code="trajectory_invalid",
                    )
                if previous is not None:
                    segment = math.dist(previous, point)
                    path_length += segment
                    if not math.isfinite(path_length):
                        raise ResultError(
                            "trajectory has non-finite statistics",
                            code="trajectory_invalid",
                        )
                previous = point
                points.append(point)
                if first_timestamp is None:
                    first_timestamp = timestamp
                previous_timestamp = timestamp
                last_timestamp = timestamp
                for axis, value in enumerate(point):
                    minima[axis] = min(minima[axis], value)
                    maxima[axis] = max(maxima[axis], value)
        except (UnicodeError, csv.Error, OSError) as exc:
            raise ResultError("invalid trajectory file", code="trajectory_invalid") from exc

    if not points or first_timestamp is None or last_timestamp is None:
        raise ResultError("trajectory file is empty", code="trajectory_invalid")
    try:
        duration_sec = (last_timestamp - first_timestamp) / 1_000_000_000
        displacement = math.dist(points[0], points[-1])
    except (OverflowError, ValueError) as exc:
        raise ResultError("trajectory has invalid statistics", code="trajectory_invalid") from exc
    if not all(math.isfinite(value) for value in (duration_sec, path_length, displacement)):
        raise ResultError("trajectory has non-finite statistics", code="trajectory_invalid")
    return {
        "timestamp_start": first_timestamp,
        "timestamp_end": last_timestamp,
        "duration_sec": duration_sec,
        "point_count": len(points),
        "points": points,
        "bbox": {"min": minima, "max": maxima},
        "path_length_m": path_length,
        "displacement_m": displacement,
    }


def _task_root(task: dict[str, Any]) -> Path:
    root = Path(task["task_root"])
    if root.is_symlink():
        raise ResultError("task root cannot be a symbolic link")
    return root.resolve()


def _allowed_relative_path(task: dict[str, Any], relative: PurePosixPath) -> bool:
    parts = relative.parts
    if parts in {("selected-config.yaml",), ("runner.log",)}:
        return True
    prefix = ("output", str(task["sequence"]))
    if parts == prefix + ("status.txt",):
        return True
    return len(parts) > len(prefix) + 1 and parts[: len(prefix)] == prefix and parts[len(prefix)] in {
        "logs",
        "results",
    }


def resolve_artifact(task: dict[str, Any], artifact_path: str) -> Path:
    relative = PurePosixPath(artifact_path)
    if (
        not artifact_path
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != artifact_path
        or not _allowed_relative_path(task, relative)
    ):
        raise ResultError("artifact path is not allowed")
    root = _task_root(task)
    candidate = root
    for component in relative.parts:
        candidate = candidate / component
        if candidate.is_symlink():
            raise ResultError("artifact symbolic links are not allowed")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        file_stat = resolved.stat()
    except (OSError, ValueError) as exc:
        raise ResultError("artifact is missing or outside task root") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ResultError("artifact must be a regular file")
    return resolved


def list_artifacts(task: dict[str, Any]) -> list[dict[str, Any]]:
    root = _task_root(task)
    candidates: list[Path] = [root / "selected-config.yaml", root / "runner.log"]
    output = root / "output" / str(task["sequence"])
    candidates.append(output / "status.txt")
    for directory in (output / "logs", output / "results"):
        if directory.is_dir() and not directory.is_symlink():
            candidates.extend(sorted(directory.rglob("*")))

    artifacts: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            relative = candidate.relative_to(root).as_posix()
            resolved = resolve_artifact(task, relative)
            size = resolved.stat().st_size
        except (ResultError, OSError):
            continue
        artifacts.append({"path": relative, "name": resolved.name, "size": size})
    order = {
        "selected-config.yaml": 0,
        "runner.log": 1,
        f"output/{task['sequence']}/status.txt": 2,
    }
    return sorted(artifacts, key=lambda item: (order.get(item["path"], 3), item["path"]))


def trajectory_path(task: dict[str, Any]) -> Path:
    relative = (
        f"output/{task['sequence']}/results/{TRAJECTORY_NAME}"
    )
    try:
        return resolve_artifact(task, relative)
    except ResultError as exc:
        raise ResultError("trajectory file is missing", code="trajectory_missing") from exc
