#!/usr/bin/env python3
"""
Run one MCAP session through extraction and OKVIS.

The output layout mirrors extract_data/batch_pipeline.py:
  output/mcap_vio/<session>/
    results/
    logs/extract.log
    logs/okvis.log
    status.txt
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = "/home/conanluo/okvis_data/optitrack/EGO2/0727_1/20260727-163811"
DEFAULT_EUROC_ROOT = REPO_ROOT / "data" / "mcap_vio"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "mcap_vio"
DEFAULT_OKVIS_BIN = REPO_ROOT / "build" / "okvis_app_synchronous"
DEFAULT_CONFIG = REPO_ROOT / "config" / "myfisheye4" / "okvis2_eucm.yaml"
DEFAULT_EXTRACT_SCRIPT = REPO_ROOT / "mcap_vio_run" / "extract_mcap_for_okvis.py"
CAM_NAMES = ("cam0", "cam1", "cam2", "cam3")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract MCAP data and run OKVIS")
    parser.add_argument("--session-dir", default=DEFAULT_SESSION)
    parser.add_argument("--euroc-root", default=str(DEFAULT_EUROC_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--okvis-bin", default=str(DEFAULT_OKVIS_BIN))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--extract-script", default=str(DEFAULT_EXTRACT_SCRIPT))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--cam-workers", type=int, default=4)
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--keep-images", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--extract-only", action="store_true")
    return parser.parse_args()


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
                sys.stdout.write(f"[{prefix}] {line}")
                sys.stdout.flush()
    proc.wait()
    return proc.returncode


def is_extracted(euroc_dir: Path) -> bool:
    imu_csv = euroc_dir / "imu0" / "data.csv"
    if not imu_csv.exists():
        return False
    for cam in CAM_NAMES:
        if not (euroc_dir / cam / "data.csv").exists():
            return False
        if not any((euroc_dir / cam / "data").glob("*.png")):
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


def write_status(output_root: Path, status: str) -> None:
    (output_root / "status.txt").write_text(status + "\n")


def main() -> int:
    args = parse_args()
    session_dir = Path(args.session_dir)
    name = session_dir.name
    euroc_dir = Path(args.euroc_root) / name
    output_root = Path(args.output_dir) / name
    results_dir = output_root / "results"
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    status_file = output_root / "status.txt"
    if args.skip_existing and status_file.exists() and status_file.read_text().strip() == "SUCCESS":
        print(f"[SKIP] {name} already has SUCCESS status")
        return 0

    required = [
        (session_dir, "session directory"),
        (Path(args.extract_script), "extract script"),
        (Path(args.okvis_bin), "OKVIS binary"),
        (Path(args.config), "OKVIS config"),
    ]
    for path, label in required:
        if not path.exists():
            print(f"[ERROR] missing {label}: {path}", file=sys.stderr)
            return 1

    print("=" * 70)
    print(f"session : {session_dir}")
    print(f"euroc   : {euroc_dir}")
    print(f"results : {results_dir}")
    print("=" * 70)

    if args.skip_extract and is_extracted(euroc_dir):
        print("[EXTRACT] skipped; EuRoC data already exists")
    else:
        extract_cmd = [
            args.python,
            "-u",
            args.extract_script,
            str(session_dir),
            str(euroc_dir),
            "--cam-workers",
            str(args.cam_workers),
        ]
        print("[EXTRACT] start")
        rc = stream_subprocess(extract_cmd, logs_dir / "extract.log", "E", args.quiet)
        if rc != 0:
            write_status(output_root, "FAIL_EXTRACT")
            print(f"[EXTRACT] failed: exit={rc}, log={logs_dir / 'extract.log'}")
            return rc
        print("[EXTRACT] done")

    if args.extract_only:
        write_status(output_root, "EXTRACT_ONLY")
        print("[OKVIS] skipped by --extract-only")
        return 0

    okvis_cmd = [args.okvis_bin, args.config, str(euroc_dir), str(results_dir)]
    print("[OKVIS] start")
    rc = stream_subprocess(okvis_cmd, logs_dir / "okvis.log", "O", args.quiet)
    if rc != 0:
        write_status(output_root, "FAIL_OKVIS")
        print(f"[OKVIS] failed: exit={rc}, log={logs_dir / 'okvis.log'}")
        if not args.keep_images:
            removed = delete_images(euroc_dir)
            print(f"[CLEANUP] removed {removed} images")
        return rc

    write_status(output_root, "SUCCESS")
    print("[OKVIS] done")
    if not args.keep_images:
        removed = delete_images(euroc_dir)
        print(f"[CLEANUP] removed {removed} images")

    csv_count = len(list(results_dir.glob("*.csv")))
    print(f"[DONE] {name}: {csv_count} result csv files in {results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
