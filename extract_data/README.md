# OKVIS2 批量处理流水线

从 ROS bag 自动提取多相机 + IMU 数据并批量运行 OKVIS2-X SLAM。

支持 **多 bag 流水线并发**：某个 bag 提取完后立即触发 OKVIS2，与此同时下一个 bag 已在提取中。
OKVIS 跑完后自动删除中间图片，仅保留 SLAM 结果，节省磁盘。

---

## 目录

- [一、快速开始](#一快速开始)
- [二、文件总览](#二文件总览)
- [三、目录结构](#三目录结构)
- [四、命令行参数](#四命令行参数)
- [五、并发与性能调优](#五并发与性能调优)
- [六、配置文件参数](#六配置文件参数)
- [七、断点续跑 / 错误恢复](#七断点续跑--错误恢复)
- [八、常见问题](#八常见问题)

---

## 一、快速开始

### 1. 一次性安装依赖

```bash
# 系统 / 环境正在使用的 Python（conda base 或 /usr/bin/python3 都要装）
python3 -m pip install lz4 opencv-python numpy tqdm
```

> 提取脚本依赖 `lz4`（解压 ROS bag chunk）+ `opencv-python`（解码 JPEG）+ `tqdm`（进度条）。
> 如果你启动时提示 `ModuleNotFoundError: No module named 'lz4'`，说明执行流水线的那个 Python 解释器缺包，重装即可。

### 2. 默认运行（处理 `/home/tione/notebook/dataset/lhk/okvis_rosbag/` 下所有 bag）

```bash
cd /root/OKVIS2-X
python3 extract_data/batch_pipeline.py
```

### 3. 推荐参数

```bash
python3 extract_data/batch_pipeline.py --extract-jobs 2 --okvis-jobs 6
```

输出会实时打印每个子进程的日志（带 `[E bagname]` / `[O bagname]` 前缀），同时完整写入日志文件。

---

## 二、文件总览

| 文件 | 作用 |
|------|------|
| `extract_data/extract_data_for_okvis.py` | **单个 bag** → EuRoC 格式（多线程解码 + tqdm 进度条） |
| `extract_data/batch_pipeline.py` | **批量流水线**：扫描 bag 目录，提取 + OKVIS 流水线并发 |
| `config/myfisheye4/okvis2_eucm.yaml` | OKVIS2 配置（含线程参数） |
| `build/okvis_app_synchronous` | OKVIS2 可执行程序（你已编译） |

---

## 三、目录结构

### 1. 输入：ROS bag

```
/home/tione/notebook/dataset/lhk/okvis_rosbag/
├── 20260520-164736.bag
└── 20260520-165452.bag
```

### 2. 中间产物：EuRoC 格式（OKVIS 完成后自动删除图片，保留 csv）

```
/root/OKVIS2-X/data/
└── 20260520-164736/
    ├── cam0/  data/*.png  data.csv     ← 鱼眼前左
    ├── cam1/  data/*.png  data.csv     ← 鱼眼前右
    ├── cam2/  data/*.png  data.csv     ← 鱼眼后左
    ├── cam3/  data/*.png  data.csv     ← 鱼眼后右
    └── imu0/  data.csv                  ← IMU
```

### 3. 输出：OKVIS 结果（永久保留）

```
/root/OKVIS2-X/output/batch/
└── 20260520-164736/
    ├── results/                          ← OKVIS 全部输出
    │   ├── okvis2-slam_trajectory.csv
    │   ├── okvis2-slam-final-ba_trajectory.csv
    │   ├── okvis2-slam-final_map.csv
    │   ├── okvis2-slam-final_map.g2o
    │   └── ...
    ├── logs/
    │   ├── extract.log                   ← 提取过程完整日志
    │   └── okvis.log                     ← OKVIS 运行完整日志
    └── status.txt                        ← SUCCESS / FAIL_EXTRACT / FAIL_OKVIS
```

---

## 四、命令行参数

```bash
python3 extract_data/batch_pipeline.py [选项]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--bag-dir`        | `/home/tione/notebook/dataset/lhk/okvis_rosbag` | bag 文件所在目录 |
| `--euroc-root`     | `/root/OKVIS2-X/data`                            | 中间 EuRoC 数据根目录 |
| `--output-dir`     | `/root/OKVIS2-X/output/batch`                    | OKVIS 结果根目录 |
| `--okvis-bin`      | `/root/OKVIS2-X/build/okvis_app_synchronous`     | OKVIS 可执行程序 |
| `--config`         | `/root/OKVIS2-X/config/myfisheye4/okvis2_eucm.yaml` | OKVIS 配置 yaml |
| `--extract-script` | `/root/OKVIS2-X/extract_data/extract_data_for_okvis.py` | 单 bag 提取脚本 |
| `--extract-jobs`   | `2`                                              | 同时提取的 bag 数 |
| `--okvis-jobs`     | `4`                                              | 同时运行的 OKVIS 进程数 |
| `--skip-existing`  | _off_                                            | 跳过已 SUCCESS 的 bag（断点续跑） |
| `--keep-images`    | _off_                                            | 保留中间 PNG 图片 |
| `--quiet`          | _off_                                            | 不在终端打印子进程输出（仅写日志） |
| `--python`         | 当前解释器                                        | 提取脚本的 Python 解释器 |

### 提取脚本可用环境变量

```bash
EXTRACT_THREADS=64 EXTRACT_INFLIGHT=512 python3 extract_data/batch_pipeline.py --extract-jobs 20 --okvis-jobs 20
```

| 变量 | 默认 | 说明 |
|------|------|------|
| `EXTRACT_THREADS`  | `32`  | 单个提取进程内部的线程池大小 |
| `EXTRACT_INFLIGHT` | `256` | 内存中最多缓存多少帧（每帧约 6MB） |

---

## 五、并发与性能调优

### 资源情况
- CPU：384 物理核，cgroup 配额 **128 核**
- 内存：约 2.2 TiB（极其充裕）
- 磁盘 IO 通常是 **提取阶段的真瓶颈**

### 推荐参数表

| 场景 | extract-jobs | okvis-jobs | EXTRACT_THREADS | 总核数估算 |
|------|--------------|------------|------------------|------------|
| 默认（保守）          | 2 | 4 | 32 | ≤ 128 |
| 推荐                  | 2 | 6 | 32 | ≤ 128 |
| 磁盘 IO 充裕、bag 多   | 4 | 6 | 32 | ≤ 224（峰值） |
| 想吃满 CPU 跑 OKVIS    | 1 | 8 | 32 | ≤ 192 |

> 流水线本质：提取（IO 密集）和 OKVIS（CPU 密集）是错峰的，所以两个池可以同时存在不抢核。

### 经验
- **OKVIS 单进程线程数（yaml 里）建议 16**，再大 Ceres BA 反而因调度变慢。
- **批量并发数（extract/okvis-jobs）** 比 **单进程线程数** 更值得拉高 —— 进程间无锁、缓存命中更好。
- 提取脚本是磁盘 IO 主导，extract-jobs 设 4 以上常常因互相抢 IO 反而慢。

---

## 六、配置文件参数

`config/myfisheye4/okvis2_eucm.yaml` 中影响速度的关键项：

```yaml
frontend_parameters:
  parallelise_detection: 1       # 多相机并行特征检测
  num_matching_threads: 16       # 特征匹配线程数

estimator_parameters:
  enforce_realtime: 1            # 强制实时（丢帧保速度），关掉精度更好
  realtime_num_threads: 16       # 前端 BA 线程数
  full_graph_num_threads: 16     # 全局 BA / final BA 线程数（最耗时）
  do_loop_closures: 1            # 闭环
  do_final_ba: 1                 # 跑完一遍后再做一次完整 BA（获得 *-final-ba_*.csv）
```

修改后无需重新编译，直接重跑流水线即可。

---

## 七、断点续跑 / 错误恢复

### 全部跑一次
```bash
python3 extract_data/batch_pipeline.py
```

### 中途中断后续跑（跳过已成功的）
```bash
python3 extract_data/batch_pipeline.py --skip-existing
```
判定逻辑：`output/batch/<bag>/status.txt` 内容为 `SUCCESS` 就跳过。

### 只重跑失败的
```bash
# 删掉失败 bag 的 status.txt，再续跑
rm /root/OKVIS2-X/output/batch/<bag_name>/status.txt
python3 extract_data/batch_pipeline.py --skip-existing
```

### 失败排查
```bash
# 提取失败
cat /root/OKVIS2-X/output/batch/<bag_name>/logs/extract.log

# OKVIS 失败
cat /root/OKVIS2-X/output/batch/<bag_name>/logs/okvis.log
```

### 状态汇总
```bash
# 看每个 bag 的最终状态
for f in /root/OKVIS2-X/output/batch/*/status.txt; do
  echo "$(basename $(dirname $f)): $(cat $f)"
done
```

---

## 八、常见问题

### Q1：`ModuleNotFoundError: No module named 'lz4'`
执行流水线的 Python 解释器缺依赖。检查并安装：
```bash
which python3            # 看实际用的是哪个 Python
python3 -m pip install lz4 opencv-python numpy tqdm
```

### Q2：所有 bag 都瞬间 FAIL_EXTRACT
日志通常是 `import` 错误。同 Q1，先看：
```bash
cat /root/OKVIS2-X/output/batch/<bag>/logs/extract.log
```

### Q3：想保留图片用于检查
```bash
python3 extract_data/batch_pipeline.py --keep-images
```

### Q4：想只跑提取、不跑 OKVIS（或反之）
最简单：
```bash
# 只跑单个 bag 的提取
python3 extract_data/extract_data_for_okvis.py /path/to.bag /output/dir

# 只跑单个 EuRoC 目录的 OKVIS
./build/okvis_app_synchronous \
    config/myfisheye4/okvis2_eucm.yaml \
    /root/OKVIS2-X/data/<bagname> \
    /root/OKVIS2-X/output/batch/<bagname>/results
```

### Q5：想后台跑、不占终端
```bash
nohup python3 extract_data/batch_pipeline.py --quiet > pipeline.log 2>&1 &
tail -f pipeline.log
```

### Q6：提取很慢
- 检查 bag 是否在 NFS / 慢盘上 → 拷到本地 SSD 再跑
- 试 `EXTRACT_THREADS=64`：`EXTRACT_THREADS=64 python3 extract_data/batch_pipeline.py`
- 提取阶段 `tqdm` 显示 MB/s，正常 SSD 至少 80–150 MB/s；若长期 < 30 MB/s 就是 IO 瓶颈

### Q7：OKVIS 输出文件含义
- `okvis2-slam_trajectory.csv`：实时 SLAM 轨迹（时间戳 + 位姿）
- `okvis2-slam-final_trajectory.csv`：跑完后修正的轨迹
- `okvis2-slam-final-ba_trajectory.csv`：最终全局 BA 后的轨迹（**精度最高**）
- `okvis2-slam-final_map.csv` / `.g2o`：地图点 + 位姿图

EuRoC 时间戳单位为纳秒。轨迹格式：`timestamp, p_x, p_y, p_z, q_x, q_y, q_z, q_w` 等（具体看文件首行表头）。
