#!/usr/bin/env python3
"""
batch_pipeline.py — 批量流水线：提取 bag → 立即跑 OKVIS2，多 bag 并发

输出目录结构（每个 bag 独立文件夹）
------------------------------------
  output/batch/
  └── 20260520-164736/
      ├── results/          ← OKVIS 原始输出（所有 .csv / .g2o / .png 等）
      ├── logs/
      │   ├── extract.log   ← 提取脚本 stdout/stderr
      │   └── okvis.log     ← OKVIS 进程 stdout/stderr
      └── status.txt        ← 本 bag 的最终状态（SUCCESS / FAIL_EXTRACT / FAIL_OKVIS）

临时提取目录（OKVIS 跑完后自动清理图片）
-----------------------------------------
  euroc_root/20260520-164736/
      cam0/data/*.png  ← OKVIS 完成后删除
      cam1/data/*.png  ←
      cam2/data/*.png  ←
      cam3/data/*.png  ←
      cam0/data.csv    ← 保留（时间戳索引，体积极小）
      imu0/data.csv    ← 保留

并发控制
--------
  --extract-jobs : 同时提取的 bag 数（磁盘 IO 瓶颈，建议 1~3）
  --okvis-jobs   : 同时运行的 OKVIS 进程数（CPU 密集，按核数调）
  两者用独立信号量控制，互不干扰。

用法示例
--------
  python3 batch_pipeline.py                         # 默认参数
  python3 batch_pipeline.py --extract-jobs 2 --okvis-jobs 6
  python3 batch_pipeline.py --skip-existing         # 断点续跑
  python3 batch_pipeline.py --keep-images           # 不删除图片
"""

import argparse
import shutil
import subprocess
import sys
import threading
import time
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ══════════════════════════════════════════════
#  默认配置（可通过命令行覆盖）
# ══════════════════════════════════════════════
DEFAULT_BAG_DIR    = "/home/conanluo/okvis_data/0629"
DEFAULT_EUROC_ROOT = "/root/OKVIS2-X/data"
DEFAULT_OUTPUT_DIR = "/root/OKVIS2-X/output/batch"
DEFAULT_OKVIS_BIN  = "/root/OKVIS2-X/build/okvis_app_synchronous"
DEFAULT_CONFIG     = "/root/OKVIS2-X/config/myfisheye4/okvis2_eucm.yaml"
DEFAULT_SCRIPT     = "/root/OKVIS2-X/extract_data/extract_data_for_okvis.py"
CAM_DIRS           = ["cam0", "cam1", "cam2", "cam3"]   # 提取后的相机目录名
# ══════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def parse_args():
    p = argparse.ArgumentParser(description="OKVIS2 批量流水线")
    p.add_argument("--bag-dir",        default=DEFAULT_BAG_DIR)
    p.add_argument("--euroc-root",     default=DEFAULT_EUROC_ROOT,
                   help="提取的 EuRoC 中间数据根目录")
    p.add_argument("--output-dir",     default=DEFAULT_OUTPUT_DIR,
                   help="OKVIS 结果根目录，每个 bag 在此下建子文件夹")
    p.add_argument("--okvis-bin",      default=DEFAULT_OKVIS_BIN)
    p.add_argument("--config",         default=DEFAULT_CONFIG)
    p.add_argument("--extract-script", default=DEFAULT_SCRIPT)
    p.add_argument("--extract-jobs",   type=int, default=2,
                   help="同时提取的 bag 数（建议 1~3，受磁盘 IO 限制）")
    p.add_argument("--okvis-jobs",     type=int, default=4,
                   help="同时运行的 OKVIS 进程数（按 CPU 核数调整）")
    p.add_argument("--skip-existing",  action="store_true",
                   help="跳过 output 下已有 SUCCESS 标记的 bag")
    p.add_argument("--keep-images",    action="store_true",
                   help="OKVIS 完成后不删除提取的图片")
    p.add_argument("--quiet",          action="store_true",
                   help="不在终端打印子进程实时输出（仅写日志文件）")
    p.add_argument("--python",         default=sys.executable,
                   help="Python 解释器路径")
    return p.parse_args()


# ── 子进程实时输出工具 ─────────────────────────────────────────────────────
# 全局打印锁，避免多个并发子进程的输出在终端互相穿插混乱
_print_lock = threading.Lock()

def _stream_subprocess(cmd: list, log_file: Path, prefix: str, quiet: bool) -> int:
    """
    启动子进程，实时把 stdout/stderr 转发到：
      1) 日志文件（完整保存）
      2) 终端（带 [prefix] 前缀，多进程并发时用全局锁串行打印）

    返回进程退出码。
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, text=True, errors="replace",
    )
    with open(log_file, "w") as lf:
        for line in proc.stdout:
            lf.write(line)
            lf.flush()
            if not quiet:
                with _print_lock:
                    sys.stdout.write(f"[{prefix}] {line}")
                    sys.stdout.flush()
    proc.wait()
    return proc.returncode


# ── 工具函数 ──────────────────────────────────────────────────────────────

def write_status(bag_out: Path, status: str):
    """向 status.txt 写入最终状态。"""
    (bag_out / "status.txt").write_text(status + "\n")


def is_done(bag_out: Path) -> bool:
    """判断该 bag 是否已成功完成（status.txt 内容为 SUCCESS）。"""
    f = bag_out / "status.txt"
    return f.exists() and f.read_text().strip() == "SUCCESS"


def is_extracted(euroc_dir: Path) -> bool:
    """cam0 有 PNG 且 imu0 有 CSV → 认为提取已完成。"""
    cam0_data = euroc_dir / "cam0" / "data"
    imu_csv   = euroc_dir / "imu0" / "data.csv"
    if not (cam0_data.exists() and imu_csv.exists()):
        return False
    return any(cam0_data.glob("*.png"))


def delete_images(euroc_dir: Path):
    """删除所有相机目录下的 data/*.png，保留 data.csv。"""
    total = 0
    for cam in CAM_DIRS:
        data_dir = euroc_dir / cam / "data"
        if not data_dir.exists():
            continue
        pngs = list(data_dir.glob("*.png"))
        for f in pngs:
            f.unlink()
        total += len(pngs)
    if total:
        log.info(f"[CLEANUP        ] {euroc_dir.name}  已删除 {total} 张图片")


# ── 核心步骤 ──────────────────────────────────────────────────────────────

def run_extract(bag: Path, euroc_dir: Path, log_dir: Path,
                python: str, script: str, quiet: bool) -> bool:
    log_file = log_dir / "extract.log"
    log.info(f"[EXTRACT  START ] {bag.name}")
    euroc_dir.mkdir(parents=True, exist_ok=True)
    rc = _stream_subprocess(
        [python, "-u", script, str(bag), str(euroc_dir)],
        log_file, prefix=f"E {bag.stem}", quiet=quiet,
    )
    if rc == 0:
        log.info(f"[EXTRACT  DONE  ] {bag.name}")
        return True
    log.error(f"[EXTRACT  FAIL  ] {bag.name}  exit={rc}  log={log_file}")
    return False


def run_okvis(name: str, euroc_dir: Path, results_dir: Path, log_dir: Path,
              okvis_bin: str, config: str, quiet: bool) -> bool:
    log_file = log_dir / "okvis.log"
    log.info(f"[OKVIS    START ] {name}")
    results_dir.mkdir(parents=True, exist_ok=True)
    rc = _stream_subprocess(
        [okvis_bin, config, str(euroc_dir), str(results_dir)],
        log_file, prefix=f"O {name}", quiet=quiet,
    )
    if rc == 0:
        log.info(f"[OKVIS    DONE  ] {name}")
        return True
    log.error(f"[OKVIS    FAIL  ] {name}  exit={rc}  log={log_file}")
    return False


# ── 每个 bag 的完整流水线 ──────────────────────────────────────────────────

def process_one(bag: Path, args,
                extract_sem: threading.Semaphore,
                okvis_sem: threading.Semaphore) -> tuple[str, str]:
    """
    返回 (bag_name, status)
    status ∈ {"SUCCESS", "FAIL_EXTRACT", "FAIL_OKVIS", "SKIPPED"}
    """
    name        = bag.stem
    euroc_dir   = Path(args.euroc_root) / name
    bag_out     = Path(args.output_dir) / name   # 该 bag 的结果根目录
    results_dir = bag_out / "results"             # OKVIS 输出子目录
    log_dir     = bag_out / "logs"                # 日志子目录

    # 建好输出目录结构
    log_dir.mkdir(parents=True, exist_ok=True)

    # 断点续跑：已成功则跳过
    if args.skip_existing and is_done(bag_out):
        log.info(f"[SKIP           ] {name}  (已有 SUCCESS 标记)")
        return name, "SKIPPED"

    # ── Step 1: 提取（带信号量限流）────────────────────────────
    if args.skip_existing and is_extracted(euroc_dir):
        log.info(f"[EXTRACT  SKIP  ] {name}  (EuRoC 数据已存在)")
        extract_ok = True
    else:
        with extract_sem:
            extract_ok = run_extract(bag, euroc_dir, log_dir,
                                     args.python, args.extract_script, args.quiet)

    if not extract_ok:
        write_status(bag_out, "FAIL_EXTRACT")
        return name, "FAIL_EXTRACT"

    # ── Step 2: OKVIS（带信号量限流，提取完立即触发）───────────
    with okvis_sem:
        okvis_ok = run_okvis(name, euroc_dir, results_dir, log_dir,
                             args.okvis_bin, args.config, args.quiet)

    if not okvis_ok:
        write_status(bag_out, "FAIL_OKVIS")
        # OKVIS 失败也清图（结果已无效，节省空间）
        if not args.keep_images:
            delete_images(euroc_dir)
        return name, "FAIL_OKVIS"

    # ── Step 3: 清理图片（OKVIS 成功后）────────────────────────
    if not args.keep_images:
        delete_images(euroc_dir)

    write_status(bag_out, "SUCCESS")
    return name, "SUCCESS"


# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    # 前置检查
    for attr, label in [
        ("okvis_bin",      "OKVIS 二进制"),
        ("config",         "配置文件"),
        ("extract_script", "提取脚本"),
    ]:
        p = Path(getattr(args, attr))
        if not p.exists():
            log.error(f"找不到{label}：{p}")
            sys.exit(1)

    bags = sorted(Path(args.bag_dir).glob("*.bag"))
    if not bags:
        log.error(f"在 {args.bag_dir} 下未找到任何 .bag 文件")
        sys.exit(1)

    Path(args.euroc_root).mkdir(parents=True, exist_ok=True)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    log.info("=" * 62)
    log.info(f"  共 {len(bags)} 个 bag  |  extract_jobs={args.extract_jobs}  okvis_jobs={args.okvis_jobs}")
    log.info(f"  EuRoC 中间目录  : {args.euroc_root}")
    log.info(f"  结果输出目录    : {args.output_dir}")
    log.info(f"  完成后删除图片  : {'否' if args.keep_images else '是'}")
    log.info("=" * 62)
    for b in bags:
        log.info(f"  {b.name}")

    extract_sem = threading.Semaphore(args.extract_jobs)
    okvis_sem   = threading.Semaphore(args.okvis_jobs)
    max_workers = args.extract_jobs + args.okvis_jobs   # 上限足够即可

    results = {}
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_one, bag, args, extract_sem, okvis_sem): bag
            for bag in bags
        }
        for fut in as_completed(futures):
            name, status = fut.result()
            results[name] = status

    elapsed = time.time() - t0

    # 汇总
    log.info("=" * 62)
    log.info(f"  批量处理完成  总耗时 {elapsed:.0f}s  ({elapsed/60:.1f} min)")
    counts = {}
    for s in results.values():
        counts[s] = counts.get(s, 0) + 1
    for s, n in sorted(counts.items()):
        log.info(f"  {s:<16}: {n}")
    log.info(f"  结果目录 → {args.output_dir}/")
    log.info("=" * 62)

    # 打印目录树预览
    log.info("输出结构预览：")
    for bag in bags:
        name    = bag.stem
        bag_out = Path(args.output_dir) / name
        status  = results.get(name, "?")
        csvs    = list((bag_out / "results").glob("*.csv")) if (bag_out / "results").exists() else []
        log.info(f"  {name}/  [{status}]  {len(csvs)} csv")
        if csvs:
            for c in sorted(csvs):
                log.info(f"    results/{c.name}")
        log.info(f"    logs/extract.log  logs/okvis.log")
        log.info(f"    status.txt")


if __name__ == "__main__":
    main()
