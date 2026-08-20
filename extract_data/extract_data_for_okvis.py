#!/usr/bin/env python3
"""
extract_data_for_okvis.py  ── 多线程加速版
从 ROS1 .bag 文件提取四路鱼眼相机（CompressedImage）+ IMU，
保存为 EuRoC 格式供 OKVis2 使用。

【加速原理】
  主线程  : 顺序读 bag → 解析 chunk → 提交 raw bytes 到线程池
  工作线程 : cv2.imdecode（JPEG→BGR）+ cv2.imwrite（BGR→PNG）
             OpenCV C 扩展自动释放 GIL，多线程真正并行
  有界信号量: 限制内存中待处理帧数，防止内存暴涨

【兼容性】
  完全绕开末尾索引区，bag 录制中断/索引损坏也可正常提取。
  无需 ROS 环境，仅依赖: pip install opencv-python numpy lz4

bag 话题映射：
    /fisheye/left/image_raw/compressed   → cam0
    /fisheye/right/image_raw/compressed  → cam1
    /fisheye/bleft/image_raw/compressed  → cam2
    /fisheye/bright/image_raw/compressed → cam3
    /imu_data_raw                        → imu0

输出 EuRoC 目录结构：
    <SAVE_ROOT>/
    ├── cam0/data.csv  +  cam0/data/<ts_ns>.png
    ├── cam1/data.csv  +  cam1/data/<ts_ns>.png
    ├── cam2/data.csv  +  cam2/data/<ts_ns>.png
    ├── cam3/data.csv  +  cam3/data/<ts_ns>.png
    └── imu0/data.csv
"""

import sys
import csv
import io
import struct
import lz4.frame
import bz2
import threading
import time
import numpy as np
import cv2
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

# ══════════════════════════════════════════════
#  【配置：只改这里，或通过命令行参数覆盖】
# ══════════════════════════════════════════════
# bag 话题 → EuRoC 相机目录名
CAM_TOPICS = {
    "/fisheye/left/image_raw/compressed":   "cam0",
    "/fisheye/right/image_raw/compressed":  "cam1",
    "/fisheye/bleft/image_raw/compressed":  "cam2",
    "/fisheye/bright/image_raw/compressed": "cam3",
}
IMU_TOPIC = "/imu_data_raw"

# 最多导出帧数（None = 全部）
MAX_FRAMES = None

# 线程池大小：图像解码+写盘线程数
# 机器有 384 核，配额 128 核 → 单 bag 提取设大一点（IO/解码并行收益高）
# 但要考虑：批量并发 N 个 bag 时实际总线程 = N × THREAD_WORKERS
# 默认 32：单 bag 跑就吃满，2 bag 并发也只用 64 核
THREAD_WORKERS = int(__import__("os").environ.get("EXTRACT_THREADS", 32))

# 内存中最多保留的待处理帧数（超过则主线程阻塞等待）
# 每帧约占 200KB（压缩）+ 6MB（解码后），256 帧 ≈ 1.5GB 峰值（机器有 2.2T 内存）
MAX_INFLIGHT = int(__import__("os").environ.get("EXTRACT_INFLIGHT", 256))
# ══════════════════════════════════════════════

# ── 命令行参数解析（优先于硬编码默认值）─────────────
# 用法：python extract_data_for_okvis.py <bag_path> <save_root>
if len(sys.argv) >= 3:
    BAG_PATH  = sys.argv[1]
    SAVE_ROOT = sys.argv[2]
elif len(sys.argv) == 2:
    BAG_PATH  = sys.argv[1]
    SAVE_ROOT = str(Path(sys.argv[1]).parent / Path(sys.argv[1]).stem)
else:
    # 默认硬编码路径（仅在不传参数时生效）
    BAG_PATH  = "/home/lhk/data/get_okvis_data_from_mybag/20260520-164736.bag"
    SAVE_ROOT = "/home/lhk/data/get_okvis_data_from_mybag/okvis_202605201647"


# ─────────────────────────────────────────────
#  底层 ROS1 bag 解析（不依赖 rosbags）
# ─────────────────────────────────────────────

OP_CHUNK      = 0x05
OP_CONNECTION = 0x07
OP_MSGDATA    = 0x02


def _parse_header_fields(raw: bytes) -> dict:
    """将 bag record header 解析为 {key: bytes} 字典。"""
    fields: dict[str, bytes] = {}
    p = 0
    while p < len(raw):
        if p + 4 > len(raw):
            break
        fl = struct.unpack_from("<I", raw, p)[0]; p += 4
        if p + fl > len(raw):
            break
        kv = raw[p: p + fl]; p += fl
        eq = kv.find(b"=")
        if eq < 0:
            continue
        fields[kv[:eq].decode("ascii", "replace")] = kv[eq + 1:]
    return fields


def _read_one_record(f) -> tuple[dict | None, bytes | None]:
    """
    从文件/BytesIO 读取一条 ROS1 bag 记录。
    末尾截断时返回 (None, None)（索引损坏的 bag 末尾可能不完整）。
    """
    raw = f.read(4)
    if len(raw) < 4:
        return None, None
    hl = struct.unpack_from("<I", raw)[0]
    hdr_raw = f.read(hl)
    if len(hdr_raw) < hl:
        return None, None
    raw2 = f.read(4)
    if len(raw2) < 4:
        return None, None
    dl = struct.unpack_from("<I", raw2)[0]
    data = f.read(dl)
    return _parse_header_fields(hdr_raw), data


# ─────────────────────────────────────────────
#  IMU 消息解码（sensor_msgs/Imu ROS1 CDR）
# ─────────────────────────────────────────────

def _decode_imu(data: bytes) -> tuple | None:
    """
    Layout: seq(4) sec(4) nsec(4) fid_len(4) fid(N)
            orientation(4×8=32) orient_cov(9×8=72)
            angular_velocity(3×8=24) ang_cov(9×8=72)
            linear_acceleration(3×8=24)
    """
    try:
        p = 4                                       # skip seq
        sec  = struct.unpack_from("<I", data, p)[0]; p += 4
        nsec = struct.unpack_from("<I", data, p)[0]; p += 4
        fid_len = struct.unpack_from("<I", data, p)[0]; p += 4 + fid_len
        p += 32 + 72                                # orientation + cov
        wx, wy, wz = struct.unpack_from("<3d", data, p); p += 24 + 72
        ax, ay, az = struct.unpack_from("<3d", data, p)
        return sec * 1_000_000_000 + nsec, wx, wy, wz, ax, ay, az
    except struct.error:
        return None


# ─────────────────────────────────────────────
#  【工作线程任务】解码 CompressedImage + 写 PNG
# ─────────────────────────────────────────────

def _decode_and_save(
    raw_bytes: bytes,
    save_dir: Path,
    ts_bag_ns: int,
) -> tuple[int, str] | None:
    """
    线程池内执行（GIL 在 OpenCV C 代码中释放，真并行）：
      1. 解析 sensor_msgs/CompressedImage header → 取出 ts_ns
      2. cv2.imdecode：JPEG → BGR ndarray
      3. cv2.imwrite：BGR → PNG 写盘
    返回 (ts_ns, filename) 或 None（失败）。
    """
    try:
        p = 4                                       # skip seq
        sec     = struct.unpack_from("<I", raw_bytes, p)[0]; p += 4
        nsec    = struct.unpack_from("<I", raw_bytes, p)[0]; p += 4
        fid_len = struct.unpack_from("<I", raw_bytes, p)[0]; p += 4 + fid_len
        fmt_len = struct.unpack_from("<I", raw_bytes, p)[0]; p += 4 + fmt_len
        img_len = struct.unpack_from("<I", raw_bytes, p)[0]; p += 4
        img_bytes = raw_bytes[p: p + img_len]

        # 优先用 bag 录制时间（墙钟，cam/imu 同一时钟，内部一致）
        # header stamp 由驱动写入，本数据集中偏差约 77 天且 cam/imu 不一致，不可用
        ts_ns = ts_bag_ns
        if ts_ns == 0:
            ts_ns = sec * 1_000_000_000 + nsec

        buf = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)   # 灰度解码（OKVIS2 强制 IMREAD_GRAYSCALE）
        if img is None:
            return None
        img = cv2.resize(img, (960, 600))               # 降半分辨率：1920×1088 → 960×600

        fname = f"{ts_ns}.png"
        cv2.imwrite(str(save_dir / fname), img)
        return ts_ns, fname
    except Exception:
        return None


# ─────────────────────────────────────────────
#  主流程
# ─────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("  ROS1 bag → EuRoC 多线程加速版（OKVis2）")
    print(f"  线程数={THREAD_WORKERS}  最大并发帧={MAX_INFLIGHT}")
    print("  顺序扫描 CHUNK，索引损坏也可正常提取")
    print("=" * 70)

    bag_path  = Path(BAG_PATH)
    save_root = Path(SAVE_ROOT)

    if not bag_path.exists():
        print(f"[ERROR] bag 文件不存在：{bag_path}")
        sys.exit(1)

    # ── 提前建立所有输出目录 ─────────────────────────────────
    # 必须在提交线程任务前完成，否则工作线程写盘会 FileNotFoundError
    for cam_name in CAM_TOPICS.values():
        (save_root / cam_name / "data").mkdir(parents=True, exist_ok=True)
    (save_root / "imu0").mkdir(parents=True, exist_ok=True)
    print(f"\n输出目录：{save_root}\n")

    all_topics = set(CAM_TOPICS.keys()) | {IMU_TOPIC}
    cam_rows: dict[str, list] = {n: [] for n in CAM_TOPICS.values()}
    imu_rows: list = []
    conn_map: dict[int, str] = {}

    bag_size   = bag_path.stat().st_size
    chunk_idx  = 0
    submitted  = 0      # 已提交到线程池的图像任务数
    imu_count  = 0
    skip_count = 0
    t0         = time.time()

    # future → cam_name，用于收集结果
    future_map: dict[Future, str] = {}

    # 有界信号量：限制同时在内存中的待处理帧数
    # 工作线程任务完成时释放，主线程提交前获取
    sema = threading.BoundedSemaphore(MAX_INFLIGHT)

    def _on_done(fut: Future) -> None:
        """Future 完成回调：释放信号量槽位。"""
        sema.release()

    # ── 进度条 ───────────────────────────────────────────────
    # tqdm 在终端显示精美进度条；非终端（pipe/log）时每秒最多刷新一次，避免刷屏
    is_tty = sys.stderr.isatty()
    pbar = None
    if _HAS_TQDM:
        pbar = tqdm(
            total=bag_size, unit="B", unit_scale=True, unit_divisor=1024,
            desc=f"[{bag_path.stem}] extract",
            dynamic_ncols=True,
            mininterval=1.0 if not is_tty else 0.3,
            file=sys.stderr,
            ascii=not is_tty,           # 非 tty 用 ASCII，避免乱码
        )
    last_log = 0.0

    with ThreadPoolExecutor(max_workers=THREAD_WORKERS) as executor:

        with open(bag_path, "rb") as f:
            # ── 跳过 BAGHEADER 记录 ──────────────────────────
            f.readline()                                         # "#ROSBAG V2.0\n"
            raw = f.read(4); hl = struct.unpack_from("<I", raw)[0]; f.read(hl)
            raw = f.read(4); dl = struct.unpack_from("<I", raw)[0]; f.seek(dl, 1)

            # ── 顺序扫描顶层记录 ─────────────────────────────
            while True:
                pos = f.tell()
                if pos >= bag_size:
                    break

                fields, data = _read_one_record(f)
                if fields is None:
                    break

                op_raw = fields.get("op", b"\xff")
                if not op_raw or op_raw[0] != OP_CHUNK:
                    continue   # 跳过 IDXDATA / CHUNK_INFO

                chunk_idx += 1

                # ── 解压 chunk ───────────────────────────────
                comp = fields.get("compression", b"none").decode("ascii", "replace").strip()
                try:
                    if comp == "lz4":
                        chunk_data = lz4.frame.decompress(data)
                    elif comp == "bz2":
                        chunk_data = bz2.decompress(data)
                    else:
                        chunk_data = data
                except Exception as e:
                    print(f"\n[WARN] chunk#{chunk_idx} 解压失败（{e}），跳过")
                    skip_count += 1
                    continue

                # ── 解析 chunk 内部记录 ──────────────────────
                bio = io.BytesIO(chunk_data)
                while True:
                    cf, cd = _read_one_record(bio)
                    if cf is None:
                        break

                    iop_raw = cf.get("op", b"\xff")
                    if not iop_raw:
                        continue
                    iop = iop_raw[0]

                    # CONNECTION → 更新 conn_id 到 topic 映射
                    if iop == OP_CONNECTION:
                        conn_raw = cf.get("conn", b"")
                        if len(conn_raw) >= 4:
                            cid   = struct.unpack_from("<I", conn_raw)[0]
                            topic = cf.get("topic", b"").decode("utf-8", "replace")
                            conn_map[cid] = topic

                    # MSGDATA → 分流处理
                    elif iop == OP_MSGDATA:
                        conn_raw = cf.get("conn", b"")
                        if len(conn_raw) < 4:
                            continue
                        cid   = struct.unpack_from("<I", conn_raw)[0]
                        topic = conn_map.get(cid, "")
                        if topic not in all_topics:
                            continue

                        # bag 时间戳（备用）
                        ts_raw = cf.get("time", b"")
                        ts_bag_ns = 0
                        if len(ts_raw) == 8:
                            ts_bag_ns = (
                                struct.unpack_from("<I", ts_raw, 0)[0] * 1_000_000_000
                                + struct.unpack_from("<I", ts_raw, 4)[0]
                            )

                        # ── 相机：提交线程池 ──────────────────
                        if topic in CAM_TOPICS:
                            cam_name = CAM_TOPICS[topic]
                            if MAX_FRAMES is not None and len(cam_rows[cam_name]) >= MAX_FRAMES:
                                continue

                            # 获取信号量槽位（满时阻塞，控制内存）
                            sema.acquire()

                            save_dir = save_root / cam_name / "data"
                            # cd 是 bytes，提交时已复制，线程安全
                            fut = executor.submit(_decode_and_save, cd, save_dir, ts_bag_ns)
                            fut.add_done_callback(_on_done)
                            future_map[fut] = cam_name
                            submitted += 1

                        # ── IMU：主线程同步处理（极快，不值得异步）──
                        elif topic == IMU_TOPIC:
                            result = _decode_imu(cd)
                            if result is None:
                                skip_count += 1
                            else:
                                # 优先用 bag 录制时间（与相机同一时钟）
                                ts_ns = ts_bag_ns if ts_bag_ns != 0 else result[0]
                                imu_rows.append((ts_ns,) + result[1:])
                                imu_count += 1

                # ── 进度更新 ─────────────────────────────────
                if pbar is not None:
                    pbar.update(pos - pbar.n)
                    pbar.set_postfix_str(
                        f"chunk={chunk_idx} cam={submitted} imu={imu_count} skip={skip_count}",
                        refresh=False,
                    )
                else:
                    # 非 tqdm 退路：每 2 秒一行
                    now = time.time()
                    if now - last_log > 2.0:
                        pct     = pos / bag_size * 100
                        speed   = pos / (now - t0) / 1e6 if now > t0 else 0
                        print(
                            f"  [{bag_path.stem}] {pct:5.1f}%  chunk={chunk_idx}  "
                            f"cam={submitted}  imu={imu_count}  skip={skip_count}  "
                            f"{speed:5.1f}MB/s",
                            flush=True,
                        )
                        last_log = now

        if pbar is not None:
            pbar.update(bag_size - pbar.n)
            pbar.close()

        # ── executor.__exit__ 会等待所有任务完成 ────────────
        print(f"\n  等待剩余 {sum(1 for f in future_map if not f.done())} 个写盘任务完成...", flush=True)

    # ── 收集所有图像结果 ─────────────────────────────────────
    print("  收集结果...")
    cam_count = 0
    for fut, cam_name in future_map.items():
        res = fut.result()          # 已完成，不阻塞
        if res is not None:
            cam_rows[cam_name].append(res)
            cam_count += 1
        else:
            skip_count += 1

    elapsed = time.time() - t0
    print(
        f"  总耗时 {elapsed:.1f}s  "
        f"cam={cam_count}  imu={imu_count}  skip={skip_count}"
    )

    # ── 写相机 CSV（按时间戳升序）────────────────────────────
    print("\n写 CSV 文件...")
    for cam_name, rows in cam_rows.items():
        rows.sort(key=lambda x: x[0])
        csv_path = save_root / cam_name / "data.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["#timestamp [ns]", "filename"])
            for ts_ns, img_name in rows:
                writer.writerow([str(ts_ns), img_name])
        print(f"  [{cam_name}] ✅  {len(rows):5d} 帧 → {csv_path}")

    # ── 写 IMU CSV ────────────────────────────────────────────
    imu_rows.sort(key=lambda x: x[0])
    csv_path = save_root / "imu0" / "data.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "#timestamp [ns]",
            "w_RS_S_x [rad s^-1]", "w_RS_S_y [rad s^-1]", "w_RS_S_z [rad s^-1]",
            "a_RS_S_x [m s^-2]",   "a_RS_S_y [m s^-2]",   "a_RS_S_z [m s^-2]",
        ])
        for row in imu_rows:
            writer.writerow([str(row[0])] + [f"{v:.10f}" for v in row[1:]])
    print(f"  [imu0 ] ✅  {len(imu_rows):5d} 条 → {csv_path}")

    # ── 完成总结 ─────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("导出完成！EuRoC 目录结构：")
    print(f"   {save_root}/")
    topic_cam = {v: k for k, v in CAM_TOPICS.items()}
    for i in range(4):
        cname  = f"cam{i}"
        frames = len(cam_rows.get(cname, []))
        topic  = topic_cam.get(cname, "")
        print(f"   ├── {cname}/  ({frames} 帧)  ← {topic}")
    print(f"   └── imu0/  ({len(imu_rows)} 条)  ← {IMU_TOPIC}")
    print(f"   总耗时：{elapsed:.1f} s  ({cam_count/(elapsed+1e-9):.1f} 帧/s)")
    print("=" * 70)


if __name__ == "__main__":
    main()
