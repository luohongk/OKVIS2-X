#!/usr/bin/env bash
#
# 运行「一个 EGO 设备」采集的多组 session。
#
# 顶部变量指定：设备名(DEVICE)、config 目录名(CONFIG_NAME)、并行度(MAX_PARALLEL)。
# 脚本自动枚举 INPUT_ROOT/DEVICE 下的所有 session，用 run_mcap_vio.py 逐个跑，
# 受 MAX_PARALLEL 控制同时运行数量（避免 OOM）。
#
# 用法:
#   ./run_one.sh                      # 用下面的默认变量
#   DEVICE=EGO4 CONFIG_NAME=EGO4 ./run_one.sh
#   DEVICE=EGO2 CONFIG_NAME=EGO2_MINGLEI MAX_PARALLEL=2 ./run_one.sh
#
set -uo pipefail

# ========================= 可配置变量 =========================
DEVICE="${DEVICE:-EGO2}"                       # 设备名 (INPUT_ROOT 下的子目录)
CONFIG_NAME="${CONFIG_NAME:-EGO2_MINGLEI}"     # config 目录名 (CONFIG_ROOT 下的子目录)
MAX_PARALLEL="${MAX_PARALLEL:-2}"              # 同时运行的 session 数 (提取很吃内存, 建议 2-3)
CAM_WORKERS="${CAM_WORKERS:-4}"                # 每个 session 的相机解码线程数 (最多 4)

INPUT_ROOT="${INPUT_ROOT:-/home/conanluo/okvis_data/batch_mcap_vio}"
EXTRA_ARGS="${EXTRA_ARGS:-}"                   # 透传给 run_mcap_vio.py 的额外参数, 如 "--skip-existing"
# =============================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_PY="${REPO_ROOT}/mcap_vio_run/run_mcap_vio.py"
CONFIG="${REPO_ROOT}/config/myfisheye4/${CONFIG_NAME}/okvis2_eucm.yaml"
DEVICE_DIR="${INPUT_ROOT}/${DEVICE}"
EXTRACT_PYTHON="${EXTRACT_PYTHON:-/usr/bin/python3}"   # 必须是装了 av/cv2/rosbags 的解释器

# ---- 前置检查 ----
[[ -f "${RUN_PY}" ]]      || { echo "[ERROR] 找不到 run_mcap_vio.py: ${RUN_PY}" >&2; exit 1; }
[[ -f "${CONFIG}" ]]      || { echo "[ERROR] 找不到 config: ${CONFIG}" >&2; exit 1; }
[[ -d "${DEVICE_DIR}" ]]  || { echo "[ERROR] 找不到设备目录: ${DEVICE_DIR}" >&2; exit 1; }

# ---- 枚举 session (含 cam0/imu 的子目录) ----
mapfile -t SESSIONS < <(
  for d in "${DEVICE_DIR}"/*/; do
    [[ -d "${d}cam0" && -d "${d}imu" ]] && basename "$d"
  done | sort
)
[[ ${#SESSIONS[@]} -gt 0 ]] || { echo "[ERROR] ${DEVICE_DIR} 下没有有效 session" >&2; exit 1; }

echo "======================================================================"
echo "device      : ${DEVICE}"
echo "config      : ${CONFIG}"
echo "input       : ${DEVICE_DIR}"
echo "sessions    : ${#SESSIONS[@]} -> ${SESSIONS[*]}"
echo "parallel    : ${MAX_PARALLEL}  cam_workers=${CAM_WORKERS}"
echo "python      : ${EXTRACT_PYTHON}"
echo "======================================================================"

# ---- 受控并行执行 ----
declare -A PID2NAME=()
declare -a FAILED=()

wait_one() {
  # 等任意一个后台任务结束, 记录失败
  local pid rc name
  wait -n -p pid
  rc=$?
  name="${PID2NAME[$pid]:-unknown}"
  unset 'PID2NAME[$pid]'
  if [[ $rc -ne 0 ]]; then
    echo "[FAIL ] ${DEVICE}/${name} rc=${rc}"
    FAILED+=("${name}")
  else
    echo "[OK   ] ${DEVICE}/${name}"
  fi
}

for s in "${SESSIONS[@]}"; do
  # 达到并行上限则先等一个结束
  while [[ ${#PID2NAME[@]} -ge ${MAX_PARALLEL} ]]; do
    wait_one
  done

  echo "[START] ${DEVICE}/${s}"
  python3 "${RUN_PY}" \
    --python "${EXTRACT_PYTHON}" \
    --session-dir "${DEVICE_DIR}/${s}" \
    --config "${CONFIG}" \
    --cam-workers "${CAM_WORKERS}" \
    ${EXTRA_ARGS} &
  PID2NAME[$!]="${s}"
done

# 等剩余任务
while [[ ${#PID2NAME[@]} -gt 0 ]]; do
  wait_one
done

echo "======================================================================"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "[DONE ] ${DEVICE}: 全部 ${#SESSIONS[@]} 个 session 成功"
  exit 0
else
  echo "[DONE ] ${DEVICE}: ${#FAILED[@]}/${#SESSIONS[@]} 个失败 -> ${FAILED[*]}"
  exit 1
fi
