#!/usr/bin/env bash
#
# 运行「多个 EGO 设备」采集的多组 session。
#
# 顶部 PAIRS 数组给出「设备名:config目录名」配对, 逐个调用 run_one.sh。
# 每个设备内部由 run_one.sh 做受控并行; 设备之间串行执行。
#
# 用法:
#   ./run_batch.sh
#   MAX_PARALLEL=3 ./run_batch.sh
#
set -uo pipefail

# ========================= 可配置变量 =========================
# 「设备名:config目录名」配对, 每行一个。
PAIRS=(
  "EGO2:EGO2_MINGLEI"
  "EGO4:EGO4"
)

MAX_PARALLEL="${MAX_PARALLEL:-2}"     # 传给 run_one.sh 的并行度
CAM_WORKERS="${CAM_WORKERS:-4}"
EXTRA_ARGS="${EXTRA_ARGS:-}"          # 透传, 如 "--skip-existing"
# =============================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ONE="${REPO_ROOT}/mcap_vio_run/run_one.sh"

[[ -f "${RUN_ONE}" ]] || { echo "[ERROR] 找不到 run_one.sh: ${RUN_ONE}" >&2; exit 1; }

echo "######################################################################"
echo "# batch: ${#PAIRS[@]} 个设备"
for p in "${PAIRS[@]}"; do echo "#   ${p%%:*}  ->  config/${p##*:}"; done
echo "######################################################################"

declare -a FAILED_DEVICES=()

for pair in "${PAIRS[@]}"; do
  device="${pair%%:*}"
  config_name="${pair##*:}"
  echo
  echo ">>>>>>>>>>>>>>>>>>>> ${device} (config=${config_name}) >>>>>>>>>>>>>>>>>>>>"

  DEVICE="${device}" \
  CONFIG_NAME="${config_name}" \
  MAX_PARALLEL="${MAX_PARALLEL}" \
  CAM_WORKERS="${CAM_WORKERS}" \
  EXTRA_ARGS="${EXTRA_ARGS}" \
  bash "${RUN_ONE}"

  rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "<<<<<<<<<<<<<<<<<<<< ${device} 有失败 (rc=${rc}) <<<<<<<<<<<<<<<<<<<<"
    FAILED_DEVICES+=("${device}")
  else
    echo "<<<<<<<<<<<<<<<<<<<< ${device} 全部成功 <<<<<<<<<<<<<<<<<<<<"
  fi
done

echo
echo "######################################################################"
if [[ ${#FAILED_DEVICES[@]} -eq 0 ]]; then
  echo "# batch 完成: 全部设备成功"
  exit 0
else
  echo "# batch 完成: 以下设备有失败 -> ${FAILED_DEVICES[*]}"
  exit 1
fi
