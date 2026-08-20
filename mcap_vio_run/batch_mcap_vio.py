#!/usr/bin/env python3
"""
Batch runner for EGO2/EGO4 MCAP VIO sessions.

Input layout:
  /home/conanluo/okvis_data/batch_mcap_vio/
    EGO2/<session>/{cam0,cam1,cam2,cam3,imu}
    EGO4/<session>/{cam0,cam1,cam2,cam3,imu}

Config layout:
  config/myfisheye4/EGO2/okvis2_eucm.yaml
  config/myfisheye4/EGO4/okvis2_eucm.yaml
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_ROOT = Path("/home/conanluo/okvis_data/batch_mcap_vio")
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config" / "myfisheye4"
DEFAULT_EUROC_ROOT = REPO_ROOT / "data" / "mcap_vio_batch"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output" / "mcap_vio_batch"
DEFAULT_EXTRACT_SCRIPT = REPO_ROOT / "mcap_vio_run" / "extract_mcap_for_okvis.py"
DEFAULT_OKVIS_BIN = REPO_ROOT / "build" / "okvis_app_synchronous"
DEFAULT_DEVICES = ("EGO2", "EGO4")
SESSION_REQUIRED_DIRS = ("cam0", "cam1", "cam2", "cam3", "imu")
CAM_NAMES = ("cam0", "cam1", "cam2", "cam3")

EXTRACT_DEPS = ("av", "cv2", "rosbags", "numpy")

_PRINT_LOCK = threading.Lock()


class Progress:
    """Thread-safe progress tracker for the batch."""

    def __init__(self, total: int, quiet: bool):
        self.total = total
        self.quiet = quiet
        self.lock = threading.Lock()
        self.done = 0
        self.extracting = 0
        self.running_okvis = 0
        self.succeeded = 0
        self.failed = 0
        self.active: dict[str, tuple[str, Path, float]] = {}

    def _emit(self) -> None:
        bar_done = self.succeeded + self.failed
        line = (
            f"[PROGRESS] {self.done}/{self.total} done "
            f"(ok={self.succeeded} fail={self.failed}) | "
            f"extracting={self.extracting} okvis={self.running_okvis}"
        )
        with _PRINT_LOCK:
            print(line, flush=True)

    def stage(self, delta_extract: int = 0, delta_okvis: int = 0) -> None:
        with self.lock:
            self.extracting += delta_extract
            self.running_okvis += delta_okvis
            self._emit()

    def start_work(self, name: str, stage: str, log_file: Path) -> None:
        with self.lock:
            self.active[name] = (stage, log_file, time.time())

    def stop_work(self, name: str) -> None:
        with self.lock:
            self.active.pop(name, None)

    def snapshot(self) -> tuple[int, int, int, int, int, dict[str, tuple[str, Path, float]]]:
        with self.lock:
            return (
                self.done,
                self.succeeded,
                self.failed,
                self.extracting,
                self.running_okvis,
                dict(self.active),
            )

    def finish(self, status: str) -> None:
        with self.lock:
            self.done += 1
            if status in {"SUCCESS", "SKIPPED", "EXTRACT_ONLY"}:
                self.succeeded += 1
            else:
                self.failed += 1
            self._emit()


def check_extract_deps(python: str) -> tuple[bool, str]:
    """Verify the target python can import the modules the extractor needs."""
    code = (
        "import importlib.util, sys\n"
        f"mods = {list(EXTRACT_DEPS)!r}\n"
        "missing = [m for m in mods if importlib.util.find_spec(m) is None]\n"
        "sys.stdout.write(','.join(missing))\n"
    )
    try:
        proc = subprocess.run(
            [python, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not launch {python}: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "unknown error").strip()
    missing = [m for m in proc.stdout.strip().split(",") if m]
    if missing:
        return False, f"missing modules: {', '.join(missing)}"
    return True, ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch run MCAP VIO for EGO2/EGO4")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT))
    parser.add_argument("--config-root", default=str(DEFAULT_CONFIG_ROOT))
    parser.add_argument("--euroc-root", default=str(DEFAULT_EUROC_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--extract-script", default=str(DEFAULT_EXTRACT_SCRIPT))
    parser.add_argument("--okvis-bin", default=str(DEFAULT_OKVIS_BIN))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--devices", nargs="+", default=list(DEFAULT_DEVICES))
    parser.add_argument("--cam-workers", type=int, default=4)
    parser.add_argument("--extract-jobs", type=int, default=2)
    parser.add_argument("--okvis-jobs", type=int, default=4)
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--keep-images", action="store_true")
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=30.0,
        help="seconds between heartbeat progress summaries; use 0 to disable",
    )
    return parser.parse_args()


def is_session_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_dir() for name in SESSION_REQUIRED_DIRS)


def discover_sessions(input_root: Path, devices: list[str]) -> list[tuple[str, Path]]:
    sessions: list[tuple[str, Path]] = []
    for device in devices:
        device_dir = input_root / device
        if not device_dir.exists():
            raise FileNotFoundError(f"device directory does not exist: {device_dir}")
        for session_dir in sorted(device_dir.iterdir()):
            if is_session_dir(session_dir):
                sessions.append((device, session_dir))
    return sessions


def status_for(output_root: Path, device: str, session: str) -> str:
    status_file = output_root / device / session / "status.txt"
    if not status_file.exists():
        return "MISSING"
    return status_file.read_text().strip() or "EMPTY"


def write_status(output_dir: Path, status: str) -> None:
    (output_dir / "status.txt").write_text(status + "\n")


def log_tail(log_file: Path, lines: int = 15) -> str:
    if not log_file.exists():
        return ""
    try:
        content = log_file.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(f"    {ln}" for ln in content[-lines:])


def tail_lines(log_file: Path, lines: int = 80, max_bytes: int = 65536) -> list[str]:
    if not log_file.exists():
        return []
    try:
        with open(log_file, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-lines:]


def latest_log_status(stage: str, log_file: Path) -> str:
    lines = tail_lines(log_file)
    if not lines:
        return "log pending"
    if stage == "okvis":
        for line in reversed(lines):
            match = re.search(r"Progress:\s*([0-9]+)%", line)
            if match:
                return f"Progress {match.group(1)}%"
    for line in reversed(lines):
        stripped = line.strip()
        if stripped and not set(stripped) <= {"="}:
            return stripped
    return "log pending"


def heartbeat_loop(progress: Progress, start_time: float, interval: float, stop_event: threading.Event) -> None:
    while not stop_event.wait(interval):
        done, succeeded, failed, extracting, running_okvis, active = progress.snapshot()
        elapsed = time.time() - start_time
        with _PRINT_LOCK:
            print(
                f"[HEARTBEAT] elapsed={elapsed:.0f}s "
                f"done={done}/{progress.total} ok={succeeded} fail={failed} "
                f"extracting={extracting} okvis={running_okvis}",
                flush=True,
            )
            for name, (stage, log_file, stage_start) in sorted(active.items()):
                stage_elapsed = time.time() - stage_start
                print(
                    f"[HEARTBEAT]   {name} {stage} {stage_elapsed:.0f}s | "
                    f"{latest_log_status(stage, log_file)}",
                    flush=True,
                )


def count_csv_rows(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with open(csv_path, "r", errors="replace") as f:
        return max(0, sum(1 for _line in f) - 1)


def is_extracted(euroc_dir: Path) -> bool:
    if count_csv_rows(euroc_dir / "imu0" / "data.csv") <= 0:
        return False
    for cam in CAM_NAMES:
        csv_rows = count_csv_rows(euroc_dir / cam / "data.csv")
        png_count = sum(1 for _p in (euroc_dir / cam / "data").glob("*.png"))
        if csv_rows <= 0 or png_count != csv_rows:
            return False
    return True


def delete_images(euroc_dir: Path) -> int:
    total = 0
    for cam in CAM_NAMES:
        data_dir = euroc_dir / cam / "data"
        if not data_dir.exists():
            continue
        for png in data_dir.glob("*.png"):
            png.unlink()
            total += 1
    return total


def stream_subprocess(cmd: list[str], log_file: Path, prefix: str, quiet: bool) -> int:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        text=True,
        errors="replace",
    )
    with open(log_file, "w") as lf:
        assert proc.stdout is not None
        for line in proc.stdout:
            lf.write(line)
            lf.flush()
            if not quiet:
                with _PRINT_LOCK:
                    sys.stdout.write(f"[{prefix}] {line}")
                    sys.stdout.flush()
    proc.wait()
    return proc.returncode


def config_for(args: argparse.Namespace, device: str) -> Path:
    config = Path(args.config_root) / device / "okvis2_eucm.yaml"
    if not config.exists():
        raise FileNotFoundError(f"missing config for {device}: {config}")
    return config


def run_extract(args: argparse.Namespace, session_dir: Path, euroc_dir: Path, log_file: Path, prefix: str) -> int:
    cmd = [
        args.python,
        "-u",
        args.extract_script,
        str(session_dir),
        str(euroc_dir),
        "--cam-workers",
        str(args.cam_workers),
    ]
    return stream_subprocess(cmd, log_file, prefix, args.quiet)


def run_okvis(args: argparse.Namespace, config: Path, euroc_dir: Path, results_dir: Path, log_file: Path, prefix: str) -> int:
    cmd = [args.okvis_bin, str(config), str(euroc_dir), str(results_dir)]
    return stream_subprocess(cmd, log_file, prefix, args.quiet)


def process_one(
    args: argparse.Namespace,
    device: str,
    session_dir: Path,
    extract_sem: threading.Semaphore,
    okvis_sem: threading.Semaphore,
    progress: Progress,
) -> tuple[str, str, str]:
    session = session_dir.name
    config = config_for(args, device)
    euroc_dir = Path(args.euroc_root) / device / session
    output_dir = Path(args.output_root) / device / session
    results_dir = output_dir / "results"
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    if args.skip_existing and status_for(Path(args.output_root), device, session) == "SUCCESS":
        with _PRINT_LOCK:
            print(f"[SKIP ] {device}/{session} already SUCCESS")
        progress.finish("SKIPPED")
        return device, session, "SKIPPED"

    name = f"{device}/{session}"
    with _PRINT_LOCK:
        print(f"[QUEUE] {name} config={config}")

    extract_ok = False
    if args.skip_extract or is_extracted(euroc_dir):
        extract_ok = is_extracted(euroc_dir)
        with _PRINT_LOCK:
            print(f"[EXTRACT SKIP] {name} extracted={extract_ok}")
    if not extract_ok:
        with extract_sem:
            progress.stage(delta_extract=1)
            progress.start_work(name, "extract", logs_dir / "extract.log")
            with _PRINT_LOCK:
                print(f"[EXTRACT START] {name}")
            rc = run_extract(args, session_dir, euroc_dir, logs_dir / "extract.log", f"E {device}-{session}")
            progress.stop_work(name)
            progress.stage(delta_extract=-1)
            if rc != 0:
                write_status(output_dir, "FAIL_EXTRACT")
                tail = log_tail(logs_dir / "extract.log")
                with _PRINT_LOCK:
                    print(f"[EXTRACT FAIL ] {name} exit={rc}")
                    if tail:
                        print(f"[EXTRACT FAIL ] {name} last log lines:\n{tail}")
                progress.finish("FAIL_EXTRACT")
                return device, session, "FAIL_EXTRACT"
            with _PRINT_LOCK:
                print(f"[EXTRACT DONE ] {name}")

    if args.extract_only:
        write_status(output_dir, "EXTRACT_ONLY")
        progress.finish("EXTRACT_ONLY")
        return device, session, "EXTRACT_ONLY"

    with okvis_sem:
        progress.stage(delta_okvis=1)
        progress.start_work(name, "okvis", logs_dir / "okvis.log")
        with _PRINT_LOCK:
            print(f"[OKVIS START  ] {name}")
        rc = run_okvis(args, config, euroc_dir, results_dir, logs_dir / "okvis.log", f"O {device}-{session}")
        progress.stop_work(name)
        progress.stage(delta_okvis=-1)
        if rc != 0:
            write_status(output_dir, "FAIL_OKVIS")
            if not args.keep_images:
                removed = delete_images(euroc_dir)
                with _PRINT_LOCK:
                    print(f"[CLEANUP     ] {name} removed {removed} images after failure")
            tail = log_tail(logs_dir / "okvis.log")
            with _PRINT_LOCK:
                print(f"[OKVIS FAIL  ] {name} exit={rc}")
                if tail:
                    print(f"[OKVIS FAIL  ] {name} last log lines:\n{tail}")
            progress.finish("FAIL_OKVIS")
            return device, session, "FAIL_OKVIS"

    write_status(output_dir, "SUCCESS")
    if not args.keep_images:
        removed = delete_images(euroc_dir)
        with _PRINT_LOCK:
            print(f"[CLEANUP     ] {name} removed {removed} images")

    csv_count = len(list(results_dir.glob("*.csv")))
    with _PRINT_LOCK:
        print(f"[SUCCESS     ] {name} results_csv={csv_count}")
    progress.finish("SUCCESS")
    return device, session, "SUCCESS"


def main() -> int:
    args = parse_args()
    input_root = Path(args.input_root)
    output_root = Path(args.output_root)

    sessions = discover_sessions(input_root, args.devices)
    if not sessions:
        print(f"[ERROR] no valid sessions found under {input_root}", file=sys.stderr)
        return 1

    print("=" * 70)
    print(f"input : {input_root}")
    print(f"output: {output_root}")
    print(f"euroc : {args.euroc_root}")
    print(f"python: {args.python}")
    print(f"jobs  : extract={args.extract_jobs} okvis={args.okvis_jobs} cam_workers={args.cam_workers}")
    print(f"count : {len(sessions)} sessions")
    for device, session_dir in sessions:
        print(f"  - {device}/{session_dir.name}")
    print("=" * 70)

    if not args.skip_extract:
        deps_ok, deps_msg = check_extract_deps(args.python)
        if not deps_ok:
            print(
                f"[ERROR] extractor python '{args.python}' cannot run: {deps_msg}\n"
                f"        the extractor needs: {', '.join(EXTRACT_DEPS)}\n"
                f"        fix: pass --python /usr/bin/python3 (or a python that has these installed),\n"
                f"        or 'conda deactivate' / install the modules into the active env.",
                file=sys.stderr,
            )
            return 1

    start = time.time()
    results: list[tuple[str, str, str]] = []
    extract_sem = threading.Semaphore(max(1, args.extract_jobs))
    okvis_sem = threading.Semaphore(max(1, args.okvis_jobs))
    max_workers = max(1, args.extract_jobs + args.okvis_jobs)
    progress = Progress(len(sessions), args.quiet)
    stop_heartbeat = threading.Event()
    heartbeat: threading.Thread | None = None
    if args.progress_interval > 0:
        heartbeat = threading.Thread(
            target=heartbeat_loop,
            args=(progress, start, args.progress_interval, stop_heartbeat),
            daemon=True,
        )
        heartbeat.start()

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(process_one, args, device, session_dir, extract_sem, okvis_sem, progress)
                for device, session_dir in sessions
            ]
            for future in as_completed(futures):
                results.append(future.result())
    finally:
        stop_heartbeat.set()
        if heartbeat is not None:
            heartbeat.join(timeout=1)

    elapsed = time.time() - start
    print("=" * 70)
    print(f"batch done in {elapsed:.1f}s")
    ok = sum(1 for _d, _s, st in results if st in {"SUCCESS", "SKIPPED", "EXTRACT_ONLY"})
    print(f"summary: {ok}/{len(results)} ok, {len(results) - ok} failed")
    for device, session, status in sorted(results):
        print(f"{device}/{session}: {status}")
    print("=" * 70)

    return 0 if all(status in {"SUCCESS", "SKIPPED", "EXTRACT_ONLY"} for _device, _session, status in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
