from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import Settings


@dataclass(frozen=True)
class RunnerResult:
    exit_code: int
    runner_status: str | None
    interrupted: bool = False
    error_summary: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.runner_status == "SUCCESS" and not self.interrupted


def build_runner_command(
    settings: Settings,
    task: Mapping[str, Any],
    *,
    runner_script: Path | None = None,
) -> list[str]:
    task_root = Path(task["task_root"])
    command = [
        str(settings.runner_python),
        "-u",
        str(runner_script or settings.runner_script),
        "--python",
        str(settings.extract_python),
        "--session-dir",
        str(settings.data_root / str(task["device"]) / str(task["sequence"])),
        "--config",
        str(task_root / "selected-config.yaml"),
        "--euroc-root",
        str(task_root / "euroc"),
        "--output-dir",
        str(task_root / "output"),
        "--okvis-bin",
        str(settings.okvis_binary),
        "--extract-script",
        str(settings.extract_script),
        "--cam-workers",
        str(task["cam_workers"]),
    ]
    if bool(task["keep_images"]):
        command.append("--keep-images")
    return command


class Runner:
    def __init__(self, settings: Settings, *, runner_script: Path | None = None) -> None:
        self.settings = settings
        self.runner_script = runner_script
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen[str]] = {}
        self._interrupted: dict[str, str] = {}
        self._terminating = False

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def run(
        self,
        task: Mapping[str, Any],
        on_stage: Callable[[str], None],
        cancel_event: threading.Event,
    ) -> RunnerResult:
        task_id = str(task["id"])
        task_root = Path(task["task_root"])
        task_root.mkdir(parents=True, exist_ok=True)
        log_path = task_root / "runner.log"
        command = build_runner_command(
            self.settings,
            task,
            runner_script=self.runner_script,
        )
        recent_lines: deque[str] = deque(maxlen=20)
        process: subprocess.Popen[str] | None = None
        execution_error: str | None = None
        exit_code = 127

        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                with self._lock:
                    if self._terminating or cancel_event.is_set():
                        return RunnerResult(
                            exit_code=-15,
                            runner_status=None,
                            interrupted=True,
                            error_summary=(
                                "runner is shutting down"
                                if self._terminating
                                else "task cancellation requested"
                            ),
                        )
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        errors="replace",
                        bufsize=1,
                        start_new_session=True,
                        shell=False,
                    )
                    self._active[task_id] = process

                try:
                    assert process.stdout is not None
                    for line in process.stdout:
                        log_file.write(line)
                        log_file.flush()
                        stripped = line.rstrip()
                        if stripped:
                            recent_lines.append(stripped)
                        if "[EXTRACT] start" in line:
                            on_stage("extracting")
                        elif "[OKVIS] start" in line:
                            on_stage("vio")
                    exit_code = process.wait()
                except Exception as exc:
                    execution_error = f"runner execution error: {exc}"
                    self._terminate_process(process)
                    exit_code = process.wait()
        except OSError as exc:
            execution_error = f"could not start runner: {exc}"
        finally:
            with self._lock:
                self._active.pop(task_id, None)
                interruption_summary = self._interrupted.pop(task_id, None)
                interrupted = interruption_summary is not None

        status_path = task_root / "output" / str(task["sequence"]) / "status.txt"
        runner_status: str | None = None
        if status_path.is_file():
            runner_status = status_path.read_text(errors="replace").strip() or None

        error_summary: str | None = execution_error
        if interrupted:
            error_summary = interruption_summary
        elif error_summary is None and exit_code != 0:
            error_summary = recent_lines[-1] if recent_lines else f"runner exited with code {exit_code}"
        elif error_summary is None and runner_status != "SUCCESS":
            error_summary = "runner exited successfully but status file is missing or not SUCCESS"

        return RunnerResult(
            exit_code=exit_code,
            runner_status=runner_status,
            interrupted=interrupted,
            error_summary=error_summary,
        )

    def _terminate_process(
        self,
        process: subprocess.Popen[str],
        *,
        task_id: str | None = None,
    ) -> None:
        if process.poll() is not None:
            return
        process_group: int | None = None
        try:
            process_group = os.getpgid(process.pid)
            os.killpg(process_group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=self.settings.terminate_grace_sec)
            return
        except subprocess.TimeoutExpired:
            pass
        if process_group is not None:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            process.wait(timeout=self.settings.terminate_grace_sec)
        except subprocess.TimeoutExpired as exc:
            target = task_id or str(process.pid)
            raise TimeoutError(
                f"task {target} process group did not exit after SIGKILL"
            ) from exc

    def terminate_task(self, task_id: str) -> None:
        with self._lock:
            process = self._active.get(task_id)
            if process is None:
                return
            self._interrupted[task_id] = "task cancellation requested"
        self._terminate_process(process, task_id=task_id)

    def terminate_all(self) -> None:
        with self._lock:
            self._terminating = True
            active = list(self._active.items())
            for task_id, _ in active:
                self._interrupted[task_id] = "task interrupted during shutdown"

        process_groups: list[int] = []
        for _, process in active:
            if process.poll() is not None:
                continue
            try:
                process_group = os.getpgid(process.pid)
                process_groups.append(process_group)
                os.killpg(process_group, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue

        deadline = time.monotonic() + self.settings.terminate_grace_sec
        while process_groups and time.monotonic() < deadline:
            remaining: list[int] = []
            for process_group in process_groups:
                try:
                    os.killpg(process_group, 0)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    continue
                remaining.append(process_group)
            process_groups = remaining
            if process_groups:
                time.sleep(0.02)

        for process_group in process_groups:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
