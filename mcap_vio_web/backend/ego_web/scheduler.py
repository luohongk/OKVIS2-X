from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any, Protocol

from .db import Database, InvalidStatusTransition, TERMINAL_STATUSES
from .runner import RunnerResult


class TaskRunner(Protocol):
    def run(
        self,
        task: dict[str, Any],
        on_stage: Callable[[str], None],
        cancel_event: threading.Event,
    ) -> RunnerResult: ...

    def terminate_task(self, task_id: str) -> None: ...

    def terminate_all(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Scheduler:
    def __init__(
        self,
        database: Database,
        runner: TaskRunner,
        *,
        max_concurrency: int,
        now_factory: Callable[[], str] = _utc_now,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.database = database
        self.runner = runner
        self.max_concurrency = max_concurrency
        self.now_factory = now_factory
        self._queue: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._state_lock = threading.Lock()
        self._lifecycle_condition = threading.Condition(self._state_lock)
        self._launch_condition = threading.Condition()
        self._next_ticket = 0
        self._ticket_counter = 0
        self._running_ids: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        self._stopping = threading.Event()
        self._started_once = False
        self.accepting_tasks = False

    def recover_unfinished(self) -> int:
        return self.database.mark_unfinished_interrupted(self.now_factory())

    def start(self) -> None:
        with self._state_lock:
            if self._workers:
                return
            if self._started_once:
                raise RuntimeError("scheduler cannot be restarted after stop")
            self._started_once = True
            self._stopping.clear()
            self.accepting_tasks = True
            self._workers = [
                threading.Thread(
                    target=self._worker,
                    name=f"ego-task-worker-{index + 1}",
                    daemon=True,
                )
                for index in range(self.max_concurrency)
            ]
            workers = list(self._workers)
        for worker in workers:
            worker.start()

    def enqueue(self, task_ids: Iterable[str]) -> None:
        with self._state_lock:
            if not self.accepting_tasks:
                raise RuntimeError("scheduler is not accepting tasks")
        validated: list[str] = []
        for task_id in task_ids:
            task = self.database.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task["status"] != "queued":
                raise ValueError(f"task {task_id} is not queued")
            validated.append(task_id)

        with self._state_lock:
            if not self.accepting_tasks:
                stopped = True
            else:
                stopped = False
                with self._launch_condition:
                    for task_id in validated:
                        self._cancel_events.setdefault(task_id, threading.Event())
                        self._queue.put((self._ticket_counter, task_id))
                        self._ticket_counter += 1
        if stopped:
            for task_id in validated:
                task = self.database.get_task(task_id)
                if task is not None and task["status"] == "queued":
                    self.database.transition_task(
                        task_id,
                        "interrupted",
                        finished_at=self.now_factory(),
                        error_summary="task rejected during scheduler shutdown",
                    )
            raise RuntimeError("scheduler is not accepting tasks")

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True

    def cancel_and_wait(self, task_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        with self._state_lock:
            cancel_event = self._cancel_events.setdefault(task_id, threading.Event())
            cancel_event.set()
            wait_for_running = task_id in self._running_ids

        task = self.database.get_task(task_id)
        if task is not None and task["status"] not in TERMINAL_STATUSES:
            try:
                self.database.transition_task(
                    task_id,
                    "interrupted",
                    finished_at=self.now_factory(),
                    error_summary="task cancellation requested",
                )
            except InvalidStatusTransition:
                pass

        self.runner.terminate_task(task_id)
        if not wait_for_running:
            return

        with self._lifecycle_condition:
            while task_id in self._running_ids:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"task {task_id} did not stop before timeout")
                self._lifecycle_condition.wait(remaining)

    def stop(self) -> None:
        with self._state_lock:
            if not self._workers:
                self.accepting_tasks = False
                return
            self.accepting_tasks = False
            self._stopping.set()
            for cancel_event in self._cancel_events.values():
                cancel_event.set()
            workers = list(self._workers)
        with self._launch_condition:
            self._launch_condition.notify_all()

        self._interrupt_queued_tasks()
        self.runner.terminate_all()
        for _ in workers:
            self._queue.put(None)
        for worker in workers:
            worker.join()
        with self._state_lock:
            self._workers = []
            self._running_ids.clear()

    def runtime_summary(self) -> dict[str, int | bool]:
        with self._state_lock:
            running = len(self._running_ids)
            accepting = self.accepting_tasks
        queued = len(self.database.list_tasks(status="queued", limit=500))
        return {
            "max_concurrency": self.max_concurrency,
            "running": running,
            "queued": queued,
            "accepting_tasks": accepting,
        }

    def _interrupt_queued_tasks(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                if item is not None:
                    _, task_id = item
                    task = self.database.get_task(task_id)
                    if task is not None and task["status"] == "queued":
                        self.database.transition_task(
                            task_id,
                            "interrupted",
                            finished_at=self.now_factory(),
                            error_summary="task interrupted before start",
                        )
            finally:
                self._queue.task_done()

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            ticket, task_id = item
            try:
                self._execute(ticket, task_id)
            except Exception as exc:
                try:
                    self._record_exception(task_id, exc)
                except Exception:
                    pass
                self._retire_ticket(ticket)
            finally:
                self._queue.task_done()

    def _execute(self, ticket: int, task_id: str) -> None:
        running_registered = False
        try:
            if not self._wait_for_launch_turn(ticket):
                task = self.database.get_task(task_id)
                if task is not None and task["status"] not in TERMINAL_STATUSES:
                    self.database.transition_task(
                        task_id,
                        "interrupted",
                        finished_at=self.now_factory(),
                        error_summary="task interrupted before launch",
                    )
                return

            task = self.database.get_task_details(task_id)
            if task is None or task["status"] != "queued":
                return
            with self._state_lock:
                cancel_event = self._cancel_events.setdefault(task_id, threading.Event())
                if cancel_event.is_set():
                    return
                self._running_ids.add(task_id)
                running_registered = True
            self.database.transition_task(
                task_id,
                "extracting",
                started_at=self.now_factory(),
            )
            task["status"] = "extracting"

            launch_released = False

            def on_stage(stage: str) -> None:
                nonlocal launch_released
                if not launch_released:
                    self._retire_ticket(ticket)
                    launch_released = True
                self._record_stage(task_id, stage)

            result = self.runner.run(task, on_stage, cancel_event)
            self._record_result(task_id, result)
        except Exception as exc:
            self._record_exception(task_id, exc)
        finally:
            self._retire_ticket(ticket)
            with self._lifecycle_condition:
                if running_registered:
                    self._running_ids.discard(task_id)
                    self._lifecycle_condition.notify_all()
                self._cancel_events.pop(task_id, None)

    def _wait_for_launch_turn(self, ticket: int) -> bool:
        with self._launch_condition:
            while ticket != self._next_ticket and not self._stopping.is_set():
                self._launch_condition.wait()
            return not self._stopping.is_set()

    def _retire_ticket(self, ticket: int) -> None:
        with self._launch_condition:
            if ticket == self._next_ticket:
                self._next_ticket += 1
                self._launch_condition.notify_all()

    def _record_stage(self, task_id: str, stage: str) -> None:
        if stage == "extracting":
            return
        if stage != "vio":
            return
        task = self.database.get_task(task_id)
        if task is not None and task["status"] == "extracting":
            self.database.transition_task(task_id, "vio")

    def _record_result(self, task_id: str, result: RunnerResult) -> None:
        task = self.database.get_task(task_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        finished_at = self.now_factory()
        if result.interrupted or self._stopping.is_set():
            self.database.transition_task(
                task_id,
                "interrupted",
                finished_at=finished_at,
                exit_code=result.exit_code,
                runner_status=result.runner_status,
                error_summary=result.error_summary or "task interrupted during shutdown",
            )
            return
        if result.succeeded:
            if task["status"] == "extracting":
                self.database.transition_task(task_id, "vio")
            self.database.transition_task(
                task_id,
                "succeeded",
                finished_at=finished_at,
                exit_code=result.exit_code,
                runner_status=result.runner_status,
            )
            return
        self.database.transition_task(
            task_id,
            "failed",
            finished_at=finished_at,
            exit_code=result.exit_code,
            runner_status=result.runner_status,
            error_summary=result.error_summary,
        )

    def _record_exception(self, task_id: str, exc: Exception) -> None:
        task = self.database.get_task(task_id)
        if task is None or task["status"] in TERMINAL_STATUSES:
            return
        status = "interrupted" if self._stopping.is_set() else "failed"
        try:
            self.database.transition_task(
                task_id,
                status,
                finished_at=self.now_factory(),
                error_summary=f"task execution error: {exc}",
            )
        except InvalidStatusTransition:
            pass
