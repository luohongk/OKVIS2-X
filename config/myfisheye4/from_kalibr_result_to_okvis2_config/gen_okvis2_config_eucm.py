#!/usr/bin/env python3
"""
gen_okvis2_config_eucm.py — 从 EUCM Kalibr 标定结果直接生成 OKVis2 配置文件

默认输入文件（相对于脚本目录的 eucm/ 子目录）：
  eucm/kalibr_input-camchain-imucam.yaml ← cam0 的 T_cam_imu + 内参 + timeshift
  eucm/kalibr_input-camchain.yaml        ← 所有相机内参 & T_cn_cnm1
  eucm/imu.yaml                          ← IMU 噪声参数

外参计算逻辑：
  - cam0: T_SC = inv(T_cam_imu)  (来自 kalibr_input-camchain-imucam.yaml)
  - cam1~3: T_SC = inv(T_cn_cnm1 @ T_cam_imu_prev)  (链式推算)

用法：
  python gen_okvis2_config_eucm.py                           # 默认路径，输出到 stdout
  python gen_okvis2_config_eucm.py --scale 0.5 -o out.yaml  # 缩放 0.5 倍并写文件
  python gen_okvis2_config_eucm.py --scale 1.0 -o full.yaml # 原始分辨率
  python gen_okvis2_config_eucm.py -h                        # 帮助
"""

import sys
import os
import argparse
import numpy as np
import yaml

# ─────────────────────────────────────────────────────────────────────────────
#  默认路径（相对于本脚本所在目录）
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EUCM_DIR   = os.path.join(SCRIPT_DIR, "eucm_eca02")

DEFAULT_IMUCAM   = os.path.join(EUCM_DIR, "kalibr_input-camchain-imucam.yaml")
DEFAULT_CAMCHAIN = os.path.join(EUCM_DIR, "kalibr_input-camchain.yaml")
DEFAULT_IMU      = os.path.join(EUCM_DIR, "imu.yaml")

# 相机描述标签（仅用于注释）
CAM_LABELS = {
    "cam0": "front",
    "cam1": "right",
    "cam2": "back-left",
    "cam3": "back-right",
}


# ─────────────────────────────────────────────────────────────────────────────
#  基础工具
# ─────────────────────────────────────────────────────────────────────────────

def load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def invert_T(T: np.ndarray) -> np.ndarray:
    """4×4 刚体变换矩阵求逆（利用旋转矩阵正交性，精度优于 np.linalg.inv）"""
    R, t = T[:3, :3], T[:3, 3]
    Ti = np.eye(4)
    Ti[:3, :3] = R.T
    Ti[:3, 3]  = -R.T @ t
    return Ti


def rows_to_mat(rows) -> np.ndarray:
    return np.array(rows, dtype=float)


def fmt_matrix(flat16, indent=7):
    """将 16 元素列表格式化为 OKVis2 YAML 风格的 4×4 矩阵"""
    ind = " " * indent
    lines = []
    for r in range(4):
        vals = ", ".join(f"{v:.10f}" for v in flat16[r*4 : r*4+4])
        if r == 0:
            lines.append(f"{ind}[{vals},")
        elif r == 3:
            lines.append(f"{ind} {vals}],")
        else:
            lines.append(f"{ind} {vals},")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  外参计算
# ─────────────────────────────────────────────────────────────────────────────

def compute_T_SC(imucam: dict, camchain: dict):
    """
    计算所有相机的 T_SC（sensor-to-camera 坐标系变换，即 T_SC = inv(T_cam_imu)）。

    策略：
      1. 从 imucam 读取含 T_cam_imu 的相机（通常只有 cam0）
      2. 从 camchain 读取 T_cn_cnm1，链式推算其余相机
      3. 返回 {cam_key: T_SC_4x4}, timeshift_s
    """
    cam_sort = lambda k: int(k[3:]) if k[3:].isdigit() else 999

    # 收集所有 cam 键
    all_keys = set(k for k in imucam   if k.startswith("cam")) | \
               set(k for k in camchain if k.startswith("cam"))
    cams = sorted(all_keys, key=cam_sort)

    # 第一步：从 imucam 读取已知 T_cam_imu
    T_ci = {}
    for ck in cams:
        entry = imucam.get(ck, {})
        if "T_cam_imu" in entry:
            T_ci[ck] = rows_to_mat(entry["T_cam_imu"])
            print(f"  [{ck}] T_cam_imu 来自 imucam 文件")

    # 第二步：链式推算缺失相机（可能需要多轮）
    for _ in range(len(cams)):
        for ck in cams:
            if ck in T_ci:
                continue
            n = int(ck[3:])
            prev = f"cam{n - 1}"
            if prev not in T_ci:
                continue
            src = camchain.get(ck) or imucam.get(ck, {})
            if "T_cn_cnm1" not in src:
                print(f"  [WARN] {ck}: 无 T_cn_cnm1，无法推算外参，跳过", file=sys.stderr)
                continue
            T_cn_cnm1 = rows_to_mat(src["T_cn_cnm1"])
            T_ci[ck] = T_cn_cnm1 @ T_ci[prev]
            print(f"  [{ck}] T_cam_imu 由 {prev} + T_cn_cnm1 链式推算")

    # 求逆 → T_SC
    T_SC = {ck: invert_T(T) for ck, T in T_ci.items()}

    # timeshift（取 cam0）
    timeshift = 0.0
    cam0_entry = imucam.get("cam0", {})
    if "timeshift_cam_imu" in cam0_entry:
        timeshift = float(cam0_entry["timeshift_cam_imu"])
        print(f"  [timeshift_cam_imu] {timeshift:.9f} s  →  image_delay = {-timeshift:.9f} s")

    return T_SC, timeshift


# ─────────────────────────────────────────────────────────────────────────────
#  IMU 参数加载
# ─────────────────────────────────────────────────────────────────────────────

def load_imu_params(imu_path: str) -> dict:
    """读取 imu.yaml，返回 OKVis2 所需的 IMU 噪声参数字典"""
    defaults = {
        "sigma_g_c":  5.00e-3,
        "sigma_a_c":  1.00e-2,
        "sigma_gw_c": 4.00e-6,
        "sigma_aw_c": 2.00e-4,
        "a_max": 176.0,
        "g_max": 7.8,
        "g":     9.81007,
    }
    if not (imu_path and os.path.isfile(imu_path)):
        print(f"  [WARN] IMU 文件不存在 ({imu_path})，使用内置默认值", file=sys.stderr)
        return defaults

    data = load_yaml(imu_path)
    mapping = {
        "gyroscope_noise_density":    "sigma_g_c",
        "accelerometer_noise_density":"sigma_a_c",
        "gyroscope_random_walk":      "sigma_gw_c",
        "accelerometer_random_walk":  "sigma_aw_c",
    }
    for k_src, k_dst in mapping.items():
        if k_src in data:
            defaults[k_dst] = float(data[k_src])
    print(f"  [IMU] 读取噪声参数: σ_g={defaults['sigma_g_c']:.3e},"
          f" σ_a={defaults['sigma_a_c']:.3e},"
          f" σ_gw={defaults['sigma_gw_c']:.3e},"
          f" σ_aw={defaults['sigma_aw_c']:.3e}")
    return defaults


# ─────────────────────────────────────────────────────────────────────────────
#  相机配置块生成（EUCM）
# ─────────────────────────────────────────────────────────────────────────────

def build_cam_block(idx: int, ck: str, T_SC: np.ndarray,
                    intrinsics, resolution, scale: float) -> str:
    """生成单个相机的 YAML 块（EUCM 模型）"""
    alpha, beta, fx, fy, cx, cy = [float(v) for v in intrinsics]
    w_orig, h_orig = int(resolution[0]), int(resolution[1])

    w   = int(w_orig * scale)
    h   = int(h_orig * scale)
    fx_s = fx * scale
    fy_s = fy * scale
    cx_s = cx * scale
    cy_s = cy * scale

    flat = T_SC.flatten().tolist()
    label = CAM_LABELS.get(ck, ck)

    lines = [
        f"  # {ck} ({label})  EUCM: alpha={alpha:.10f}  beta={beta:.10f}",
        f"  - {{T_SC:",
        fmt_matrix(flat),
        f"     image_dimension: [{w}, {h}],",
        f"     distortion_coefficients: [0.0, 0.0, 0.0, 0.0],",
        f"     distortion_type: none,",
        f"     focal_length: [{fx_s:.8f}, {fy_s:.8f}],",
        f"     principal_point: [{cx_s:.8f}, {cy_s:.8f}],",
        f"     eucm_parameters: [{alpha:.10f}, {beta:.10f}],",
        f"     cam_model: eucm,",
        f"     camera_type: grayscale,",
    ]
    if idx == 0:
        lines.append(f"     mapping_rectification: 1,")
    lines.append(f"     slam_use: okvis}}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  完整 YAML 组装
# ─────────────────────────────────────────────────────────────────────────────

def assemble_config(cam_blocks: list, imu_p: dict,
                    image_delay: float, scale: float) -> str:
    n = len(cam_blocks)
    sync_idx = list(range(n))
    sg  = imu_p["sigma_g_c"]
    sa  = imu_p["sigma_a_c"]
    sgw = imu_p["sigma_gw_c"]
    saw = imu_p["sigma_aw_c"]

    L = []

    # ── 文件头
    L += [
        "%YAML:1.2",
        "# OKVis2 配置文件（由 gen_okvis2_config_eucm.py 自动生成）",
        "# 相机模型：EUCM（Extended Unified Camera Model，Kalibr 直出）",
        "# 外参约定：T_SC = inv(T_cam_imu)",
        f"# 图像缩放：×{scale}" if scale != 1.0 else "# 图像：原始分辨率（×1.0）",
        "# 注意：布尔值使用 0/1（OpenCV YAML 解析器不支持 true/false）",
        "",
    ]

    # ── cameras
    L.append("cameras:")
    for block in cam_blocks:
        L.append(block)
        L.append("")

    # ── camera_parameters
    L += [
        "camera_parameters:",
        "  timestamp_tolerance: 0.075",
        f"  sync_cameras: {sync_idx}",
        f"  image_delay: {image_delay:.9f}",
        "  online_calibration:",
        "    do_extrinsics: 1",
        "    do_extrinsics_final_ba: 1",
        "    sigma_r: 0.001",
        "    sigma_alpha: 0.005",
        "    sigma_r_final_ba: 0.003",
        "    sigma_alpha_final_ba: 0.016",
        "  fov_scale: 0.9",
        "",
    ]

    # ── imu_parameters
    L += [
        "imu_parameters:",
        "  use: 1",
        f"  a_max: {imu_p['a_max']}",
        f"  g_max: {imu_p['g_max']}",
        f"  sigma_g_c:  {sg:.4e}   # gyroscope_noise_density",
        f"  sigma_a_c:  {sa:.4e}   # accelerometer_noise_density",
        "  sigma_bg: 0.01",
        "  sigma_ba: 0.1",
        f"  sigma_gw_c: {sgw:.4e}   # gyroscope_random_walk",
        f"  sigma_aw_c: {saw:.4e}   # accelerometer_random_walk",
        f"  g: {imu_p['g']}",
        "  g0: [0.0, 0.0, 0.0]",
        "  a0: [0.0, 0.0, 0.0]",
        "  s_a: [1.0, 1.0, 1.0]",
        "  T_BS:",
        "    [1.0000000000, 0.0000000000, 0.0000000000, 0.0000000000,",
        "     0.0000000000, 1.0000000000, 0.0000000000, 0.0000000000,",
        "     0.0000000000, 0.0000000000, 1.0000000000, 0.0000000000,",
        "     0.0000000000, 0.0000000000, 0.0000000000, 1.0000000000]",
        "",
    ]

    # ── frontend_parameters
    L += [
        "frontend_parameters:",
        "  detection_threshold: 50.0",
        "  absolute_threshold: 20.0",
        "  matching_threshold: 60.0",
        "  octaves: 0",
        "  max_num_keypoints: 400",
        "  keyframe_overlap: 0.59",
        "  use_cnn: 0",
        "  parallelise_detection: 1",
        "  num_matching_threads: 8",
        "",
    ]

    # ── estimator_parameters
    L += [
        "estimator_parameters:",
        "  num_keyframes: 5",
        "  num_loop_closure_frames: 5",
        "  num_imu_frames: 3",
        "  do_loop_closures: 0",
        "  do_final_ba: 0",
        "  enforce_realtime: 1",
        "  realtime_min_iterations: 3",
        "  realtime_max_iterations: 10",
        "  realtime_time_limit: 0.035",
        "  realtime_num_threads: 8",
        "  full_graph_iterations: 15",
        "  full_graph_num_threads: 4",
        "  p_dbow: 0.4",
        "  drift_percentage_heuristic: 1.35",
        "",
    ]

    # ── output_parameters
    L += [
        "output_parameters:",
        "  display_topview: 1",
        "  display_matches: 1",
        "  display_overhead: 0",
        "  enable_submapping: 0",
        "",
    ]

    return "\n".join(L)


# ─────────────────────────────────────────────────────────────────────────────
#  主流程
# ─────────────────────────────────────────────────────────────────────────────

def generate(imucam_path: str, camchain_path: str, imu_path: str,
             scale: float, output_path: str):
    print(f"\n{'='*60}")
    print(f"  OKVis2 EUCM 配置生成器")
    print(f"  imucam  : {imucam_path}")
    print(f"  camchain: {camchain_path}")
    print(f"  imu     : {imu_path}")
    print(f"  缩放    : ×{scale}")
    print(f"{'='*60}")

    # ── 检查输入文件
    for path, name in [(imucam_path, "imucam"), (camchain_path, "camchain")]:
        if not os.path.isfile(path):
            print(f"[ERROR] {name} 文件不存在: {path}", file=sys.stderr)
            sys.exit(1)

    # ── 加载
    imucam   = load_yaml(imucam_path)
    camchain = load_yaml(camchain_path)
    imu_p    = load_imu_params(imu_path)

    # ── 外参计算
    print("\n[外参计算]")
    T_SC_all, timeshift = compute_T_SC(imucam, camchain)
    image_delay = -timeshift

    # ── 逐相机构建配置块
    print("\n[相机内参]")
    cam_sort = lambda k: int(k[3:]) if k[3:].isdigit() else 999
    cam_keys = sorted(T_SC_all.keys(), key=cam_sort)

    cam_blocks = []
    for idx, ck in enumerate(cam_keys):
        # 内参优先从 camchain 取（包含全部 4 个相机），其次 imucam
        src = camchain.get(ck) or imucam.get(ck, {})
        model = src.get("camera_model", "").lower()
        if model != "eucm":
            print(f"  [WARN] {ck}: camera_model='{model}'（非 eucm），跳过", file=sys.stderr)
            continue

        intrinsics = src["intrinsics"]   # [alpha, beta, fx, fy, cx, cy]
        resolution = src["resolution"]   # [w, h]
        alpha, beta, fx, fy, cx, cy = [float(v) for v in intrinsics]
        print(f"  [{ck}] α={alpha:.8f}  β={beta:.8f}  "
              f"fx={fx:.2f}  fy={fy:.2f}  cx={cx:.2f}  cy={cy:.2f}  "
              f"res={resolution[0]}×{resolution[1]}")

        block = build_cam_block(idx, ck, T_SC_all[ck], intrinsics, resolution, scale)
        cam_blocks.append(block)

    if not cam_blocks:
        print("[ERROR] 没有有效的 EUCM 相机，无法生成配置", file=sys.stderr)
        sys.exit(1)

    # ── 组装
    content = assemble_config(cam_blocks, imu_p, image_delay, scale)

    # ── 输出
    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n  ✓ 已写入: {output_path}")
    else:
        print("\n" + "─"*60)
        print(content)


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="从 EUCM Kalibr 标定结果生成 OKVis2 配置文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
默认输入文件（位于脚本目录的 eucm/ 子目录）：
  imucam  : {DEFAULT_IMUCAM}
  camchain: {DEFAULT_CAMCHAIN}
  imu     : {DEFAULT_IMU}

示例：
  # 使用默认路径，图像缩放 0.5（1920×1200 → 960×600），输出到文件
  python gen_okvis2_config_eucm.py --scale 0.5 -o okvis2_eucm.yaml

  # 原始分辨率，输出到 stdout
  python gen_okvis2_config_eucm.py --scale 1.0

  # 自定义输入文件
  python gen_okvis2_config_eucm.py \\
      --imucam   /path/to/kalibr_input-camchain-imucam.yaml \\
      --camchain /path/to/kalibr_input-camchain.yaml \\
      --imu      /path/to/imu.yaml \\
      --scale 0.5 -o okvis2_eucm.yaml
""",
    )
    parser.add_argument(
        "--imucam", default=DEFAULT_IMUCAM,
        help="Kalibr IMU-相机联合标定文件（含 T_cam_imu，通常只有 cam0）",
    )
    parser.add_argument(
        "--camchain", default=DEFAULT_CAMCHAIN,
        help="Kalibr 相机链文件（含所有相机内参 & T_cn_cnm1）",
    )
    parser.add_argument(
        "--imu", default=DEFAULT_IMU,
        help="IMU 噪声参数文件（imu.yaml）",
    )
    parser.add_argument(
        "--scale", type=float, default=0.5,
        help="图像及焦距/主点缩放比例（default: 0.5）",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="输出文件路径（不指定则打印到 stdout）",
    )

    args = parser.parse_args()
    generate(args.imucam, args.camchain, args.imu, args.scale, args.output)


if __name__ == "__main__":
    main()
