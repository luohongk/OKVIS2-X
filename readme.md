# OKVIS2-X EGO 数据处理项目交接文档

## 快速导航

- [1. 项目概览](#1-项目概览)
- [2. 交接范围与备份](#2-交接范围与备份)
- [3. 运行环境与依赖](#3-运行环境与依赖)
  - [3.1 OKVIS 编译](#31-okvis-编译)
  - [3.2 MCAP 抽取 Python 环境](#32-mcap-抽取-python-环境)
  - [3.3 Web 前后端依赖](#33-web-前后端依赖)
- [4. 原始数据格式](#4-原始数据格式)
  - [4.1 运行前检查](#41-运行前检查)
- [5. 设备标定与配置](#5-设备标定与配置)
  - [5.1 配置目录](#51-配置目录)
  - [5.2 从 Kalibr 结果生成 EUCM 配置](#52-从-kalibr-结果生成-eucm-配置)
- [6. 命令行运行](#6-命令行运行)
  - [6.1 单个序列](#61-单个序列)
  - [6.2 单个设备的全部序列](#62-单个设备的全部序列)
  - [6.3 多设备批处理](#63-多设备批处理)
  - [6.4 结果检查](#64-结果检查)
- [7. Web 平台](#7-web-平台)
  - [7.1 服务端口](#71-服务端口)
  - [7.2 启动后端](#72-启动后端)
  - [7.3 启动前端](#73-启动前端)
  - [7.4 启动检查](#74-启动检查)
  - [7.5 环境变量](#75-环境变量)
  - [7.6 页面使用流程](#76-页面使用流程)
- [8. 相对上游的改动](#8-相对上游的改动)

## 1. 项目概览

以 [ethz-mrl/OKVIS2-X](https://github.com/ethz-mrl/OKVIS2-X) 为基础，针对 EGO 设备采集数据增加了完整的离线 VIO 流水线和 Web 操作平台。

当前主要处理链路如下：

```text
EGO 原始数据
  └─ 每个序列包含 cam0、cam1、cam2、cam3、imu 五路 rosbag2/MCAP
       ↓ mcap_vio_run/extract_mcap_for_okvis.py
EuRoC 格式中间数据
  └─ 4 路灰度 PNG、相机 data.csv、imu0/data.csv
       ↓ build/okvis_app_synchronous + 对应设备标定配置
OKVIS VIO/SLAM 结果
  └─ 轨迹 CSV、运行日志和状态文件
       ↓ mcap_vio_web
网页任务管理、实时日志、三维轨迹查看和结果下载
```

## 2. 交接范围与备份

| 内容 | 当前服务器路径 | 说明 |
| --- | --- | --- |
| 项目仓库 | `/root/OKVIS2-X` | 源码、脚本、网页和配置 |
| 原始数据根目录 | `/home/conanluo/okvis_data` | Web 默认扫描目录 |
| OKVIS 可执行文件 | `/root/OKVIS2-X/build/okvis_app_synchronous` | CLI 和 Web 都调用它 |
| 设备配置目录 | `/root/OKVIS2-X/config/myfisheye4` | 每台设备应使用自己的 YAML |
| CLI 中间数据 | `/root/OKVIS2-X/data/mcap_vio*` | 默认运行结束后删除 PNG，仅保留 CSV 目录结构 |
| CLI 结果 | `/root/OKVIS2-X/output/mcap_vio*` | 轨迹、日志和状态 |
| Web 运行数据 | `/root/OKVIS2-X/output/ego_annotation` | SQLite、任务快照、中间数据、日志和结果 |
| 上游说明 | `/root/OKVIS2-X/README.md` | OKVIS2-X 原始编译和算法说明 |

需要优先备份的内容是：

1. `config/myfisheye4/` 中已经验证过的设备标定配置；
2. `/home/conanluo/okvis_data/` 中采集的原始数据；
3. `output/ego_annotation/` 中仍需保留的任务结果和 `platform.sqlite3`；

## 3. 运行环境与依赖

当前部署已核对的环境如下：

| 组件 | 当前值 |
| --- | --- |
| 操作系统 | Ubuntu 22.04.5 LTS，x86_64 |
| CMake | 3.22.1 |
| GCC | 11.4.0 |
| 系统 Python | 3.10.12 |
| Node.js | 20.20.2，位于 `mcap_vio_web/.node/bin` |
| OKVIS 构建类型 | Release |
| 构建开关 | `BUILD_APPS=ON`、`BUILD_ROS2=OFF`、`USE_NN=OFF`、`USE_DL_FEATURES=OFF`、`HAVE_LIBREALSENSE=OFF` |

当前 `/usr/bin/python3` 已能导入 `av`、`cv2`、`numpy` 和 `rosbags`。这些模块是 MCAP 抽取的必要依赖。

### 3.1 OKVIS 编译

自行参考：https://github.com/ethz-mrl/OKVIS2-X

### 3.2 MCAP 抽取 Python 环境

抽取脚本需要 `av`、`cv2`、`numpy` 和 `rosbags`。当前服务器统一使用 `/usr/bin/python3`。先检查：

```bash
/usr/bin/python3 -c "import av, cv2, numpy, rosbags; print('extract dependencies: OK')"
```

如果新服务器的系统 Python 没有这些模块，建议创建独立虚拟环境，并在运行时显式指定它：

```bash
cd /root/OKVIS2-X
python3 -m venv mcap_vio_run/.venv
mcap_vio_run/.venv/bin/pip install -U pip
mcap_vio_run/.venv/bin/pip install av opencv-python-headless numpy rosbags
```

后续 CLI 使用 `--python /root/OKVIS2-X/mcap_vio_run/.venv/bin/python`，Web 使用 `EGO_WEB_EXTRACT_PYTHON=/root/OKVIS2-X/mcap_vio_run/.venv/bin/python`。

注意：进入 Conda `base` 后，命令行中的 `python3` 可能不是系统 Python。抽取任务“瞬间失败”时，首先确认实际使用的解释器，而不是只检查当前终端能否 import。

### 3.3 Web 前后端依赖

已有服务器可以直接使用仓库内现成的 `.venv` 和 `.node`。新服务器可按下面方式重建。

后端：

```bash
cd /root/OKVIS2-X/mcap_vio_web/backend
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
```

前端需要 Node.js 20 和 npm：

```bash
cd /root/OKVIS2-X/mcap_vio_web/frontend
npm ci
npm run build
```

仓库内的 `.venv`、`.node` 和 `node_modules` 是服务器运行环境，不应依赖 Git 帮助迁移；交接到新机器时应重新安装。

## 4. 原始数据格式

Web 和批处理脚本预期以下目录层级：

```text
/home/conanluo/okvis_data/
└── <设备名>/
    └── <序列名>/
        ├── cam0/
        │   ├── metadata.yaml
        │   └── *.mcap
        ├── cam1/
        │   └── *.mcap
        ├── cam2/
        │   └── *.mcap
        ├── cam3/
        │   └── *.mcap
        ├── imu/
        │   └── *.mcap
        ├── <序列名>.info       # 可选；Web 用于显示时长和平台信息
        └── .complete           # 可选；Web 用于显示采集完成标记
```

有效数据必须满足：

- `cam0` 至 `cam3` 都至少包含一个 `sensor_msgs/msg/CompressedImage` 连接，消息 `format` 包含 `h264`；
- `imu` 至少包含一个 `sensor_msgs/msg/Imu` 连接；
- 每个传感器目录至少有一个 `.mcap` 文件；
- 目录不能用符号链接代替；Web 出于安全原因会忽略符号链接；
- 相机和 IMU 最终使用 rosbag2/MCAP 的记录时间戳，不使用消息 Header 时间戳。

抽取脚本默认把相机画面转换为 `960 × 600` 灰度 PNG，因此使用的 OKVIS 配置也必须与这个分辨率及其内参一致。

### 4.1 运行前检查

```bash
cd /root/OKVIS2-X
find /home/conanluo/okvis_data/0813_EGO0_collect/<序列名> \
  -maxdepth 2 -type f -name '*.mcap' -print
test -f config/myfisheye4/EGO0/okvis2_eucm.yaml
test -x build/okvis_app_synchronous
df -h /root/OKVIS2-X /home/conanluo/okvis_data
```

长序列在抽取阶段会生成大量 PNG。即使默认运行结束后会删除 PNG，也必须在开始前确认磁盘有足够临时空间。

## 5. 设备标定与配置

### 5.1 配置目录

每个物理设备应有独立配置：

```text
config/myfisheye4/
├── ECA/okvis2_eucm.yaml
├── EGO0/okvis2_eucm.yaml
├── EGO2/okvis2_eucm.yaml
├── EGO4/okvis2_eucm.yaml
└── EGO5/okvis2_eucm.yaml
```

CLI 的 `CONFIG_NAME` 是上面的目录名，不一定等于原始数据目录的 `DEVICE`。例如 `DEVICE=0813_EGO0_collect` 对应 `CONFIG_NAME=EGO0`。

严禁仅因为文件名相似就跨设备复用配置。配置中包含 4 路相机内参、相机与 IMU 外参、时间偏移和 IMU 噪声参数，用错会直接造成初始化失败、轨迹漂移或轨迹方向异常。

### 5.2 从 Kalibr 结果生成 EUCM 配置

转换工具位于 `config/myfisheye4/from_kalibr_result_to_okvis2_config/gen_okvis2_config_eucm.py`。它需要：

- `kalibr_input-camchain-imucam.yaml`：至少包含 cam0 的 `T_cam_imu` 和 `timeshift_cam_imu`；
- `kalibr_input-camchain.yaml`：4 路相机的 EUCM 内参和相邻相机外参 `T_cn_cnm1`；
- `imu.yaml`：IMU 噪声密度和随机游走参数。

不要直接依赖脚本内部当前写死的 `EUCM_DIR`。为新设备生成配置时，显式传入文件路径：

```bash
cd /root/OKVIS2-X
python3 config/myfisheye4/from_kalibr_result_to_okvis2_config/gen_okvis2_config_eucm.py \
  --imucam /path/to/kalibr_input-camchain-imucam.yaml \
  --camchain /path/to/kalibr_input-camchain.yaml \
  --imu /path/to/imu.yaml \
  --scale 0.5 \
  -o config/myfisheye4/<新设备>/okvis2_eucm.yaml
```

先用 `-h` 核对参数名。如果 Kalibr 原始分辨率为 `1920 × 1200`，当前抽取尺寸 `960 × 600` 对应 `--scale 0.5`。

生成后的人工核对项：

1. 恰好有 cam0～cam3 四个相机块；
2. `image_dimension` 是 `[960, 600]`；
3. `cam_model` 是 `eucm`，并存在 `eucm_parameters`；
4. `T_SC = inv(T_cam_imu)` 的方向没有弄反；
5. `image_delay = -timeshift_cam_imu`；
6. OpenCV YAML 中布尔值使用 `0/1`，不要写 `true/false`；
7. 用一个短序列试跑并人工查看三维轨迹后，再投入批量运行。

Web 创建任务时会把当时选中的配置复制为任务目录内的 `selected-config.yaml`，因此后来修改源配置不会改变旧任务，便于追溯。

## 6. 命令行运行

所有命令都建议先进入仓库根目录：

```bash
cd /root/OKVIS2-X
```

### 6.1 单个序列

```bash
python3 mcap_vio_run/run_mcap_vio.py \
  --python /usr/bin/python3 \
  --session-dir /home/conanluo/okvis_data/0813_EGO0_collect/<序列名> \
  --config config/myfisheye4/EGO0/okvis2_eucm.yaml
```

执行顺序为：抽取 → OKVIS → 写状态 → 删除中间 PNG。默认输出：

```text
data/mcap_vio/<序列名>/
├── cam0/data.csv
├── cam1/data.csv
├── cam2/data.csv
├── cam3/data.csv
└── imu0/data.csv

output/mcap_vio/<序列名>/
├── results/                   # OKVIS 轨迹 CSV
├── logs/extract.log
├── logs/okvis.log
└── status.txt
```

常用参数：

| 参数 | 含义 |
| --- | --- |
| `--extract-only` | 只抽取 EuRoC 数据，不运行 OKVIS |
| `--skip-extract` | 已有完整 EuRoC 数据时跳过抽取；数据不完整仍会重新抽取 |
| `--skip-existing` | `status.txt` 已为 `SUCCESS` 时跳过整个序列 |
| `--keep-images` | OKVIS 完成后保留中间 PNG，磁盘占用会很大 |
| `--cam-workers 1..4` | 单个序列并行解码的相机路数 |
| `--quiet` | 不向终端打印子进程日志，日志文件仍保留 |
| `--euroc-root` / `--output-dir` | 自定义中间数据和结果根目录 |

`status.txt` 的主要值：`SUCCESS`、`FAIL_EXTRACT`、`FAIL_OKVIS`、`EXTRACT_ONLY`。

### 6.2 单个设备的全部序列

```bash
INPUT_ROOT=/home/conanluo/okvis_data \
DEVICE=ECA_0819 \
CONFIG_NAME=ECA \
MAX_PARALLEL=2 \
CAM_WORKERS=4 \
EXTRACT_PYTHON=/usr/bin/python3 \
EXTRA_ARGS="--skip-existing" \
bash mcap_vio_run/run_one.sh
```

| 变量 | 含义 |
| --- | --- |
| `INPUT_ROOT` | 设备目录的上一级目录 |
| `DEVICE` | `INPUT_ROOT` 下的原始数据目录名 |
| `CONFIG_NAME` | `config/myfisheye4` 下的配置目录名 |
| `MAX_PARALLEL` | 同时运行的 **序列数**，不是单个算法的线程数 |
| `CAM_WORKERS` | 每个序列同时解码的相机路数，最大 4 |
| `EXTRACT_PYTHON` | 安装了抽取依赖的 Python |
| `EXTRA_ARGS` | 原样传给 `run_mcap_vio.py` 的附加参数 |

`MAX_PARALLEL=2` 是当前有成功经验的保守值；内存紧张时使用 `1`。不要未经压力测试就使用 `8`、`24` 等大并发，H.264 解码和 OKVIS 同时运行可能触发 OOM kill。

注意：`run_one.sh` 默认仍把结果写到 `data/mcap_vio/<序列名>` 和 `output/mcap_vio/<序列名>`，路径中不含设备名。如果不同设备可能出现相同序列名，应改用下一节的 Python 批处理，避免互相覆盖。

### 6.3 多设备批处理

推荐使用会按设备分层保存结果的 Python 批处理：

```bash
python3 mcap_vio_run/batch_mcap_vio.py \
  --python /usr/bin/python3 \
  --input-root /home/conanluo/okvis_data \
  --devices EGO2 EGO4 \
  --skip-existing \
  --extract-jobs 2 \
  --okvis-jobs 2 \
  --cam-workers 4
```

配置按 `config/myfisheye4/<设备名>/okvis2_eucm.yaml` 查找，输出按设备隔离：

```text
data/mcap_vio_batch/<设备名>/<序列名>/
output/mcap_vio_batch/<设备名>/<序列名>/
```

`run_batch.sh` 也可使用，但设备与配置的对应关系写死在脚本顶部 `PAIRS` 数组中；新增设备前必须编辑并复查。日常交接更推荐参数明确的 `batch_mcap_vio.py`。

### 6.4 结果检查

“进程返回 0”只说明程序执行完成，交付结果前还应检查：

```bash
cat output/mcap_vio/<序列名>/status.txt
tail -n 50 output/mcap_vio/<序列名>/logs/okvis.log
find output/mcap_vio/<序列名>/results -maxdepth 1 -type f -name '*.csv' -ls
```

网页三维轨迹当前固定读取 `okvis2-slam-calib-final_trajectory.csv`。对应配置通常需要启用回环和最终 BA（例如 `do_loop_closures: 1`、`do_final_ba: 1`）。如果 OKVIS 成功但网页提示轨迹缺失，先检查结果目录实际文件名和上述配置。

人工验收至少关注：轨迹是否有足够点数、时间是否单调、运动方向是否合理、是否存在突然飞点、路径长度与采集场景是否大致一致。

## 7. Web 平台

### 7.1 服务端口

| 服务 | 端口 | 用途 |
| --- | --- | --- |
| FastAPI 后端 | `8000` | `/api/v1/*` API；`/docs` 为 Swagger |
| Vite 前端 | `5173` | 用户实际访问的网页 |

当前前端开发服务器通过代理把 `/api` 转发到 `127.0.0.1:8000`。不要把旧文档中的 `8080` 当作当前网页端口。

### 7.2 启动后端

终端 1：

```bash
cd /root/OKVIS2-X
EGO_WEB_MAX_CONCURRENCY=1 \
/root/OKVIS2-X/mcap_vio_web/backend/.venv/bin/uvicorn \
  ego_web.main:app \
  --app-dir /root/OKVIS2-X/mcap_vio_web/backend \
  --host 0.0.0.0 \
  --port 8000
```

首次交接或资源不确定时保持并发为 1；确认内存余量后再逐步增加。

### 7.3 启动前端

终端 2：

```bash
cd /root/OKVIS2-X
PATH="/root/OKVIS2-X/mcap_vio_web/.node/bin:$PATH" \
npm --prefix /root/OKVIS2-X/mcap_vio_web/frontend \
  run dev -- --host 0.0.0.0 --port 5173
```

本机访问 `http://127.0.0.1:5173`，远程访问 `http://<服务器 IP>:5173`。云服务器或容器环境需要额外开放、映射或代理 5173 端口。8000 端口只需供前端代理访问时，不建议直接暴露到公网。

### 7.4 启动检查

```bash
curl -s http://127.0.0.1:8000/api/v1/health
curl -s http://127.0.0.1:8000/api/v1/devices
curl -I http://127.0.0.1:5173/
```

`health` 应返回 `status: ok`。如果是 `degraded`，响应里的 `checks` 会指出缺失的数据目录、脚本、Python 或 OKVIS 可执行文件。

### 7.5 环境变量

后端使用 `EGO_WEB_` 前缀读取配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EGO_WEB_DATA_ROOT` | `/home/conanluo/okvis_data` | 原始数据根目录 |
| `EGO_WEB_CONFIG_ROOT` | `/root/OKVIS2-X/config/myfisheye4` | 配置根目录，递归发现 `.yaml/.yml` |
| `EGO_WEB_REPO_ROOT` | `/root/OKVIS2-X` | 仓库根目录 |
| `EGO_WEB_RUNTIME_ROOT` | `/root/OKVIS2-X/output/ego_annotation` | 数据库和任务产物根目录 |
| `EGO_WEB_MAX_CONCURRENCY` | `8` | 同时运行的任务数；当前启动命令覆盖为 1 |
| `EGO_WEB_CAM_WORKERS` | `4` | 默认相机解码并发，界面可为任务选择 1～4 |
| `EGO_WEB_RUNNER_PYTHON` | `/usr/bin/python3` | 启动流水线脚本的 Python |
| `EGO_WEB_EXTRACT_PYTHON` | `/usr/bin/python3` | 真正执行 MCAP 抽取的 Python |
| `EGO_WEB_TERMINATE_GRACE_SEC` | `10` | 删除运行中任务时等待进程退出的秒数 |

修改数据目录或迁移仓库时，应通过这些环境变量覆盖路径，不要批量替换源码中的默认字符串。

### 7.6 页面使用流程

1. 打开“数据运行”；
2. 选择设备，平台只显示具有完整 `cam0..3 + imu` MCAP 结构的序列；
3. 选择一个或多个序列；
4. 选择与物理设备匹配的配置文件；
5. 选择相机解码线程，通常保持 4；
6. 非调试场景不要勾选“保留抽取图片”；
7. 提交任务后到“任务中心”查看排队、抽取、VIO、成功或失败状态及实时日志；
8. 成功后进入结果页查看三维轨迹、统计信息并下载产物。

任务数据库位于 `output/ego_annotation/platform.sqlite3`，任务文件位于：

```text
output/ego_annotation/tasks/<设备>/<序列>/<配置标识>/<任务 UUID>/
```

## 8. 相对上游的改动

围绕 EGO 四目鱼眼设备补齐了工程化数据链路：新增四路 H.264 相机与 IMU 的 rosbag2/MCAP 解码、EuRoC 格式转换、单序列及多设备受控并行运行、日志/状态管理和中间图片清理；新增各 EGO 设备的 EUCM 标定配置与 Kalibr 转换工具；新增 FastAPI + React 任务平台，可发现数据、选择配置、调度任务、查看实时日志与三维轨迹、下载或删除产物。
