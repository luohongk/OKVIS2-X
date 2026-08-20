# MCAP VIO run

这里的脚本用于把 rosbag2/mcap 的四路 H.264 相机和 IMU 数据转成 OKVIS 需要的 EuRoC 目录，并调用 OKVIS 保存结果。

## 脚本一览

| 脚本 | 用途 |
|------|------|
| `extract_mcap_for_okvis.py` | 单个 session：mcap → EuRoC 目录（被其他脚本调用） |
| `run_mcap_vio.py`           | 单个 session：抽取 + 跑 OKVIS |
| `run_one.sh`                | **一个 EGO 设备**的多组 session（受控并行） |
| `run_batch.sh`              | **多个 EGO 设备**的多组 session（设备间串行、设备内受控并行） |
| `batch_mcap_vio.py`         | Python 版批量流水线（信号量并发 + 进度显示 + 依赖预检） |

## 重要前提：抽取用的 Python

抽取需要 `av`、`cv2`、`rosbags`、`numpy`。conda `(base)` 环境通常缺这些，会导致抽取瞬间失败。
**统一用 `/usr/bin/python3` 做抽取**（所有脚本已默认，或通过参数指定）：

- `run_mcap_vio.py` / `batch_mcap_vio.py`：加 `--python /usr/bin/python3`
- `run_one.sh` / `run_batch.sh`：已默认 `EXTRACT_PYTHON=/usr/bin/python3`

## 单个 session

```bash
cd /root/OKVIS2-X
python3 mcap_vio_run/run_mcap_vio.py \
  --python /usr/bin/python3 \
  --session-dir /home/conanluo/okvis_data/batch_mcap_vio/EGO2/20260727-163811 \
  --config config/myfisheye4/EGO2_MINGLEI/okvis2_eucm.yaml
```

输出：

```text
data/mcap_vio/20260727-163811/          # EuRoC 中间数据
output/mcap_vio/20260727-163811/
  results/                              # OKVIS 输出（轨迹 csv）
  logs/extract.log
  logs/okvis.log
  status.txt
```

常用参数：

```bash
# 只抽取，不跑 OKVIS
python3 mcap_vio_run/run_mcap_vio.py ... --extract-only

# EuRoC 已存在时直接跑 OKVIS
python3 mcap_vio_run/run_mcap_vio.py ... --skip-extract

# OKVIS 后保留中间 PNG（默认跑完会删）
python3 mcap_vio_run/run_mcap_vio.py ... --keep-images
```

## 一个设备的多组数据：run_one.sh

跑某个 EGO 设备下的全部 session。顶部变量指定设备名、config 目录名、并行度：

```bash
# 用脚本内默认变量（DEVICE=EGO2, CONFIG_NAME=EGO2_MINGLEI, MAX_PARALLEL=2）
bash mcap_vio_run/run_one.sh

# 通过环境变量覆盖
DEVICE=EGO4 CONFIG_NAME=EGO4 MAX_PARALLEL=2 bash mcap_vio_run/run_one.sh

# 透传额外参数给 run_mcap_vio.py
EXTRA_ARGS="--skip-existing" bash mcap_vio_run/run_one.sh
```

可配置变量：`DEVICE`、`CONFIG_NAME`、`MAX_PARALLEL`、`CAM_WORKERS`、`INPUT_ROOT`、`EXTRACT_PYTHON`、`EXTRA_ARGS`。
config 路径按约定拼为 `config/myfisheye4/<CONFIG_NAME>/okvis2_eucm.yaml`。

## 多个设备的多组数据：run_batch.sh

顶部 `PAIRS` 数组给出「设备名:config目录名」配对，逐设备调用 `run_one.sh`：

```bash
# PAIRS 默认: EGO2:EGO2_MINGLEI, EGO4:EGO4（在脚本顶部编辑）
bash mcap_vio_run/run_batch.sh

MAX_PARALLEL=2 bash mcap_vio_run/run_batch.sh
```

## 并行度与内存注意

抽取阶段每路相机会启动 H.264 解码器，非常吃内存。同时跑太多 session/OKVIS 会触发容器 cgroup 的 **OOM kill**（曾用 `--extract-jobs 24` 导致整批被杀）。经验值：

- **`MAX_PARALLEL=2`** 已验证稳定；追求最稳可设 `1`（纯串行）。
- 单个 OKVIS 跑完一个 session 约 6~7 分钟，且资源占用高。
- 不建议再用大并发（如 24）。

## batch_mcap_vio.py（Python 批量流水线）

多个 session 流水线并发：可同时抽取；某 session 抽取完立即进入 OKVIS；OKVIS 完成后删除该 session 的中间 PNG，只保留 CSV 和结果。含依赖预检（缺 av/rosbags 会提前报错）和进度显示。

```bash
python3 mcap_vio_run/batch_mcap_vio.py --python /usr/bin/python3 \
  --skip-existing --extract-jobs 2 --okvis-jobs 4 --cam-workers 4

# 指定其他输入根目录 / 设备
python3 mcap_vio_run/batch_mcap_vio.py --python /usr/bin/python3 \
  --input-root /home/conanluo/okvis_data --devices 0729_VIO_EGO2 0729_VIO_EGO4 \
  --skip-existing --extract-jobs 2 --okvis-jobs 4
```

python3 mcap_vio_run/run_mcap_vio.py \
    --python /usr/bin/python3 \
    --session-dir /home/conanluo/okvis_data/0803_VIO_EGO2/20260803-112714 \
    --config config/myfisheye4/0803_VIO_EGO2/okvis2_eucm.yaml


INPUT_ROOT=/home/conanluo/okvis_data \
DEVICE=0813_EGO0_collect  \
CONFIG_NAME=EGO0 \
MAX_PARALLEL=8 \
bash mcap_vio_run/run_one.sh


批量输出按设备分层：

```text
data/mcap_vio_batch/EGO2/<session>/
data/mcap_vio_batch/EGO4/<session>/
output/mcap_vio_batch/EGO2/<session>/
output/mcap_vio_batch/EGO4/<session>/
```
