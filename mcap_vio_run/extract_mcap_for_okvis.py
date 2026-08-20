#!/usr/bin/env python3
"""
Extract one rosbag2/mcap recording directory to the EuRoC layout used by OKVIS.

Expected input layout:
  <session>/
    cam0/cam0_0.mcap
    cam1/cam1_0.mcap
    cam2/cam2_0.mcap
    cam3/cam3_0.mcap
    imu/imu_0.mcap

Camera messages are sensor_msgs/msg/CompressedImage with format=h264.
IMU messages are sensor_msgs/msg/Imu. Timestamps written to CSV use the
rosbag2 record timestamp from the MCAP reader, matching the existing bag
extraction policy in extract_data/extract_data_for_okvis.py.
"""

from __future__ import annotations

import argparse
import csv
import struct
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import av
import cv2
import numpy as np
from rosbags.highlevel import AnyReader


CAM_NAMES = ("cam0", "cam1", "cam2", "cam3")
IMU_DIR_NAME = "imu"
OKVIS_IMU_DIR_NAME = "imu0"
PROGRESS_UPDATE_EVERY_MESSAGES = 25


class CameraProgressReporter:
    """Emit compact, throttled progress shared by all camera workers."""

    def __init__(self, totals: dict[str, int], interval: float) -> None:
        self.totals = totals
        self.interval = interval
        self.started_at = time.monotonic()
        self.last_report_at = self.started_at
        self.processed = {cam: 0 for cam in totals}
        self.frames = {cam: 0 for cam in totals}
        self.lock = threading.Lock()

    def update(self, cam_name: str, processed: int, frames: int) -> None:
        now = time.monotonic()
        with self.lock:
            self.processed[cam_name] = processed
            self.frames[cam_name] = frames
            if now - self.last_report_at < self.interval:
                return
            self.last_report_at = now
            line = self._format_line(now)
        print(line, flush=True)

    def report(self) -> None:
        now = time.monotonic()
        with self.lock:
            self.last_report_at = now
            line = self._format_line(now)
        print(line, flush=True)

    def _format_line(self, now: float) -> str:
        processed = sum(
            min(self.processed[cam], self.totals[cam])
            for cam in self.totals
        )
        total = sum(self.totals.values())
        frames = sum(self.frames.values())
        elapsed = max(0.0, now - self.started_at)
        rate = frames / elapsed if elapsed > 0 else 0.0
        percent = min(100.0, processed * 100.0 / total) if total else 0.0
        cameras = " ".join(
            f"{cam}={min(self.processed[cam], self.totals[cam])}/{self.totals[cam]}"
            for cam in CAM_NAMES
        )
        return (
            f"[PROGRESS] {percent:5.1f}% messages={processed}/{total} "
            f"frames={frames} elapsed={elapsed:.1f}s rate={rate:.1f} frame/s | {cameras}"
        )


class CdrReader:
    """Small CDR reader for the fixed ROS2 message types used here."""

    def __init__(self, data: bytes):
        self.data = data
        self.base = 4
        self.pos = 4  # skip CDR encapsulation header

    def align(self, size: int) -> None:
        rel = self.pos - self.base
        self.pos = self.base + ((rel + size - 1) // size) * size

    def int32(self) -> int:
        self.align(4)
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def uint32(self) -> int:
        self.align(4)
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def float64(self) -> float:
        self.align(8)
        value = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return value

    def string(self) -> str:
        length = self.uint32()
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        self.align(4)
        return raw.rstrip(b"\x00").decode("utf-8", "replace")

    def uint8_sequence(self) -> bytes:
        length = self.uint32()
        raw = self.data[self.pos : self.pos + length]
        self.pos += length
        return raw


def parse_header(reader: CdrReader) -> tuple[int, int, str]:
    sec = reader.int32()
    nsec = reader.uint32()
    frame_id = reader.string()
    return sec, nsec, frame_id


def parse_compressed_image(raw: bytes) -> tuple[str, bytes]:
    reader = CdrReader(raw)
    parse_header(reader)
    fmt = reader.string().lower()
    data = reader.uint8_sequence()
    return fmt, data


def parse_imu(raw: bytes) -> tuple[float, float, float, float, float, float]:
    reader = CdrReader(raw)
    parse_header(reader)
    for _ in range(4):
        reader.float64()  # orientation
    for _ in range(9):
        reader.float64()  # orientation covariance
    wx = reader.float64()
    wy = reader.float64()
    wz = reader.float64()
    for _ in range(9):
        reader.float64()  # angular velocity covariance
    ax = reader.float64()
    ay = reader.float64()
    az = reader.float64()
    return wx, wy, wz, ax, ay, az


def open_reader(input_dir: Path) -> AnyReader:
    reader = AnyReader([input_dir])
    reader.open()
    return reader


def camera_message_count(session_dir: Path, cam_name: str) -> int:
    reader = open_reader(session_dir / cam_name)
    try:
        connections = [
            conn
            for conn in reader.connections
            if conn.msgtype == "sensor_msgs/msg/CompressedImage"
        ]
        if not connections:
            raise RuntimeError(f"no CompressedImage topic found in {session_dir / cam_name}")
        return sum(conn.msgcount for conn in connections)
    finally:
        reader.close()


def save_frame(frame: av.VideoFrame, timestamp_ns: int, output_dir: Path, size: tuple[int, int]) -> str:
    image = frame.to_ndarray(format="gray")
    if image.shape[1] != size[0] or image.shape[0] != size[1]:
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    filename = f"{timestamp_ns}.png"
    ok = cv2.imwrite(str(output_dir / filename), image)
    if not ok:
        raise RuntimeError(f"failed to write image: {output_dir / filename}")
    return filename


def extract_camera(
    session_dir: Path,
    save_root: Path,
    cam_name: str,
    size: tuple[int, int],
    max_frames: int | None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> tuple[str, int, int]:
    input_dir = session_dir / cam_name
    output_dir = save_root / cam_name / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    reader = open_reader(input_dir)
    codec = av.CodecContext.create("h264", "r")
    pending_timestamps: deque[int] = deque()
    rows: list[tuple[int, str]] = []
    message_count = 0
    skipped = 0

    try:
        connections = [
            conn
            for conn in reader.connections
            if conn.msgtype == "sensor_msgs/msg/CompressedImage"
        ]
        if not connections:
            raise RuntimeError(f"no CompressedImage topic found in {input_dir}")

        for conn, timestamp_ns, raw in reader.messages(connections=connections):
            if max_frames is not None and len(rows) >= max_frames:
                break
            message_count += 1
            fmt, packet_bytes = parse_compressed_image(raw)
            if "h264" not in fmt:
                skipped += 1
                if on_progress is not None:
                    on_progress(cam_name, message_count, len(rows))
                continue

            pending_timestamps.append(timestamp_ns)
            for packet in codec.parse(packet_bytes):
                for frame in codec.decode(packet):
                    ts = pending_timestamps.popleft() if pending_timestamps else timestamp_ns
                    filename = save_frame(frame, ts, output_dir, size)
                    rows.append((ts, filename))
                    if max_frames is not None and len(rows) >= max_frames:
                        break
                if max_frames is not None and len(rows) >= max_frames:
                    break

            if (
                on_progress is not None
                and (
                    message_count % PROGRESS_UPDATE_EVERY_MESSAGES == 0
                    or (max_frames is not None and len(rows) >= max_frames)
                )
            ):
                on_progress(cam_name, message_count, len(rows))

        if max_frames is None:
            for packet in codec.parse(b""):
                for frame in codec.decode(packet):
                    if not pending_timestamps:
                        break
                    ts = pending_timestamps.popleft()
                    filename = save_frame(frame, ts, output_dir, size)
                    rows.append((ts, filename))
            for frame in codec.decode(None):
                if not pending_timestamps:
                    break
                ts = pending_timestamps.popleft()
                filename = save_frame(frame, ts, output_dir, size)
                rows.append((ts, filename))
    finally:
        reader.close()

    rows.sort(key=lambda item: item[0])
    csv_path = save_root / cam_name / "data.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["#timestamp [ns]", "filename"])
        for ts, filename in rows:
            writer.writerow([str(ts), filename])

    if on_progress is not None:
        on_progress(cam_name, message_count, len(rows))

    return cam_name, len(rows), skipped + len(pending_timestamps)


def extract_imu(session_dir: Path, save_root: Path) -> int:
    input_dir = session_dir / IMU_DIR_NAME
    output_dir = save_root / OKVIS_IMU_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[int, float, float, float, float, float, float]] = []

    reader = open_reader(input_dir)
    try:
        connections = [
            conn for conn in reader.connections if conn.msgtype == "sensor_msgs/msg/Imu"
        ]
        if not connections:
            raise RuntimeError(f"no Imu topic found in {input_dir}")

        for _conn, timestamp_ns, raw in reader.messages(connections=connections):
            wx, wy, wz, ax, ay, az = parse_imu(raw)
            rows.append((timestamp_ns, wx, wy, wz, ax, ay, az))
    finally:
        reader.close()

    rows.sort(key=lambda item: item[0])
    csv_path = output_dir / "data.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "#timestamp [ns]",
                "w_RS_S_x [rad s^-1]",
                "w_RS_S_y [rad s^-1]",
                "w_RS_S_z [rad s^-1]",
                "a_RS_S_x [m s^-2]",
                "a_RS_S_y [m s^-2]",
                "a_RS_S_z [m s^-2]",
            ]
        )
        for row in rows:
            writer.writerow([str(row[0])] + [f"{value:.10f}" for value in row[1:]])

    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract mcap camera/IMU data for OKVIS")
    parser.add_argument("session_dir", help="rosbag2/mcap session directory")
    parser.add_argument("save_root", help="EuRoC output directory")
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument("--cam-workers", type=int, default=4)
    parser.add_argument("--max-frames", type=int, default=None, help="debug limit per camera")
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=5.0,
        help="seconds between camera extraction progress lines",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_dir = Path(args.session_dir)
    save_root = Path(args.save_root)
    size = (args.width, args.height)

    if not session_dir.exists():
        print(f"[ERROR] session directory does not exist: {session_dir}", file=sys.stderr)
        return 1

    missing = [name for name in (*CAM_NAMES, IMU_DIR_NAME) if not (session_dir / name).exists()]
    if missing:
        print(f"[ERROR] missing input directories: {', '.join(missing)}", file=sys.stderr)
        return 1
    if args.progress_interval <= 0:
        print("[ERROR] --progress-interval must be greater than zero", file=sys.stderr)
        return 1

    print("=" * 70)
    print("  MCAP -> EuRoC extractor for OKVIS")
    print(f"  input : {session_dir}")
    print(f"  output: {save_root}")
    print(f"  image : {args.width}x{args.height}")
    print("=" * 70)

    save_root.mkdir(parents=True, exist_ok=True)
    start_time = time.time()

    imu_count = extract_imu(session_dir, save_root)
    print(f"[imu0] {imu_count} rows", flush=True)

    workers = max(1, min(args.cam_workers, len(CAM_NAMES)))
    camera_totals = {
        cam: camera_message_count(session_dir, cam)
        for cam in CAM_NAMES
    }
    if args.max_frames is not None:
        camera_totals = {
            cam: min(total, args.max_frames)
            for cam, total in camera_totals.items()
        }
    print(
        "[CAMERAS] "
        + " ".join(f"{cam}={camera_totals[cam]}" for cam in CAM_NAMES)
        + f" total={sum(camera_totals.values())} workers={workers}",
        flush=True,
    )
    progress = CameraProgressReporter(camera_totals, args.progress_interval)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                extract_camera,
                session_dir,
                save_root,
                cam,
                size,
                args.max_frames,
                progress.update,
            )
            for cam in CAM_NAMES
        ]
        for future in as_completed(futures):
            cam_name, frame_count, skipped = future.result()
            print(
                f"[{cam_name}] {frame_count} frames, skipped_or_pending={skipped}",
                flush=True,
            )

    progress.report()

    elapsed = time.time() - start_time
    print("=" * 70)
    print(f"Done: {save_root} ({elapsed:.1f}s)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
