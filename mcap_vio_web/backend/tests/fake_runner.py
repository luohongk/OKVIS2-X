#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python")
    parser.add_argument("--session-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--euroc-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cam-workers", required=True)
    parser.add_argument("--okvis-bin")
    parser.add_argument("--extract-script")
    parser.add_argument("--keep-images", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sequence = Path(args.session_dir).name
    output_root = Path(args.output_dir) / sequence
    output_root.mkdir(parents=True, exist_ok=True)

    print("[EXTRACT] start", flush=True)
    if sequence == "slow":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        (output_root / "child.pid").write_text(str(child.pid))
        time.sleep(30)
        return 0

    print("[E] extraction complete", flush=True)
    print("[OKVIS] start", flush=True)
    print("[O] optimization complete", flush=True)
    if sequence == "fail":
        (output_root / "status.txt").write_text("FAIL_OKVIS\n")
        return 7
    if sequence != "missing-status":
        (output_root / "status.txt").write_text("SUCCESS\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
