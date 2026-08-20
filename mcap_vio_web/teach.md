# EGO 设备数据标注平台使用说明

当前版本已完成后端任务 API，暂时通过 FastAPI 的 Swagger 页面操作。可视化网页、实时日志和 3D 轨迹页面将在后续任务中开发。

## 1. 启动服务

在 OKVIS 工作站执行：

```bash
EGO_WEB_MAX_CONCURRENCY=1 \
/root/OKVIS2-X/mcap_vio_web/backend/.venv/bin/uvicorn \
ego_web.main:app \
--app-dir /root/OKVIS2-X/mcap_vio_web/backend \
--host 0.0.0.0 \
--port 8080
```

首次运行建议将并发数设为 `1`。确认内存稳定后可改为 `2`：

```bash
EGO_WEB_MAX_CONCURRENCY=2 \
/root/OKVIS2-X/mcap_vio_web/backend/.venv/bin/uvicorn \
ego_web.main:app \
--app-dir /root/OKVIS2-X/mcap_vio_web/backend \
--host 0.0.0.0 \
--port 8000
```

不要使用多个 Uvicorn worker。本平台的任务调度器按单服务进程设计。

停止服务时在启动服务的终端按 `Ctrl+C`。正在排队或运行的任务会被标记为 `interrupted`。

## 2. 打开操作页面

服务器本机访问：

```text
http://127.0.0.1:8000/docs
```

局域网其他电脑访问：

```text
http://<服务器IP>:8000/docs
```

在服务器上查看 IP：

```bash
hostname -I
```

打开 `/docs` 后，点击接口，再点击 **Try it out** 即可填写参数并执行。

## 3. 检查运行环境

执行：

```http
GET /api/v1/health
```

正常情况下应返回：

```json
{
  "status": "ok",
  "checks": {
    "data_root": true,
    "config_root": true,
    "runner_script": true,
    "extract_script": true,
    "runner_python": true,
    "extract_python": true,
    "okvis_binary": true
  }
}
```

如果 `status` 是 `degraded`，请检查值为 `false` 的项目：

- `data_root`：数据目录 `/home/conanluo/okvis_data`
- `config_root`：配置目录 `/root/OKVIS2-X/config/myfisheye4`
- `runner_script`：`mcap_vio_run/run_mcap_vio.py`
- `extract_script`：`mcap_vio_run/extract_mcap_for_okvis.py`
- `runner_python`、`extract_python`：任务使用的 Python
- `okvis_binary`：`build/okvis_app_synchronous`

## 4. 查看设备

执行：

```http
GET /api/v1/devices
```

平台只显示至少包含一个有效 MCAP 序列的一级目录。示例：

```json
{
  "items": [
    {
      "name": "0805",
      "valid_sequence_count": 14
    }
  ]
}
```

## 5. 查看设备下的序列

将设备名放入接口路径，例如：

```http
GET /api/v1/devices/0805/sequences
```

有效序列必须同时包含：

```text
cam0/*.mcap
cam1/*.mcap
cam2/*.mcap
cam3/*.mcap
imu/*.mcap
```

接口还会返回 `.complete`、采集时长、设备名称和各传感器 MCAP 文件数量。

## 6. 查看可用配置

执行：

```http
GET /api/v1/configs
```

示例响应：

```json
{
  "items": [
    {
      "id": "EGO2/okvis2_eucm.yaml",
      "label": "EGO2 / okvis2_eucm.yaml"
    }
  ]
}
```

提交任务时必须使用接口返回的 `id`，不能提交任意绝对路径。

## 7. 提交位姿任务

执行：

```http
POST /api/v1/task-groups
```

单序列示例：

```json
{
  "device": "0805",
  "sequences": [
    "20260805-113804"
  ],
  "config_id": "EGO2/okvis2_eucm.yaml",
  "options": {
    "cam_workers": 4,
    "keep_images": false
  }
}
```

多序列示例：

```json
{
  "device": "0805",
  "sequences": [
    "20260805-113804",
    "20260805-114500"
  ],
  "config_id": "EGO2/okvis2_eucm.yaml",
  "options": {
    "cam_workers": 4,
    "keep_images": false
  }
}
```

参数说明：

- `device`：`GET /devices` 返回的设备名称
- `sequences`：该设备下一个或多个序列名称，最多 500 个
- `config_id`：`GET /configs` 返回的配置 ID
- `cam_workers`：单任务相机解码线程数，只能为 `1` 到 `4`
- `keep_images`：是否保留抽取后的 PNG；通常设为 `false` 以节省磁盘

成功后返回任务组 ID 和任务 ID：

```json
{
  "group_id": "任务组ID",
  "task_ids": [
    "任务ID"
  ]
}
```

## 8. 查看运行状态

查看全局运行情况：

```http
GET /api/v1/runtime
```

示例：

```json
{
  "max_concurrency": 1,
  "running": 1,
  "queued": 2,
  "accepting_tasks": true
}
```

查看任务组：

```http
GET /api/v1/task-groups
GET /api/v1/task-groups/{group_id}
```

查看任务：

```http
GET /api/v1/tasks
GET /api/v1/tasks/{task_id}
```

任务状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待执行 |
| `extracting` | 正在从 MCAP 抽取 EuRoC 数据 |
| `vio` | 正在运行 OKVIS 位姿计算 |
| `succeeded` | 运行成功 |
| `failed` | 抽取或 OKVIS 运行失败 |
| `interrupted` | 服务停止或任务被中断 |

任务列表支持过滤，例如：

```text
/api/v1/tasks?device=0805
/api/v1/tasks?status=failed
/api/v1/tasks?sequence=20260805-113804
/api/v1/tasks?config_id=EGO2/okvis2_eucm.yaml
/api/v1/tasks?group_id=<任务组ID>
```

## 9. 查看日志和结果文件

每个任务拥有独立目录：

```text
/root/OKVIS2-X/output/ego_annotation/tasks/
  <device>/<sequence>/<config-key>/<task-id>/
```

目录内容：

```text
selected-config.yaml
runner.log
euroc/<sequence>/
output/<sequence>/
  status.txt
  logs/extract.log
  logs/okvis.log
  results/
```

常用查看命令：

```bash
# 查看任务总日志
tail -f /root/OKVIS2-X/output/ego_annotation/tasks/<设备>/<序列>/<配置>/<任务ID>/runner.log

# 查看最终状态
cat /root/OKVIS2-X/output/ego_annotation/tasks/<设备>/<序列>/<配置>/<任务ID>/output/<序列>/status.txt

# 查看结果文件
ls /root/OKVIS2-X/output/ego_annotation/tasks/<设备>/<序列>/<配置>/<任务ID>/output/<序列>/results
```

成功状态应为：

```text
SUCCESS
```

最终位姿轨迹通常是：

```text
okvis2-slam-calib-final_trajectory.csv
```

## 10. 命令行调用示例

除了 `/docs`，也可以使用 `curl`。

查看设备：

```bash
curl http://127.0.0.1:8000/api/v1/devices
```

提交任务：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/task-groups \
  -H 'Content-Type: application/json' \
  -d '{
    "device": "0805",
    "sequences": ["20260805-113804"],
    "config_id": "EGO2/okvis2_eucm.yaml",
    "options": {
      "cam_workers": 4,
      "keep_images": false
    }
  }'
```

查看所有任务：

```bash
curl http://127.0.0.1:8000/api/v1/tasks
```

## 11. 常见问题

### 健康检查显示 degraded

根据 `checks` 中为 `false` 的字段检查路径、权限和可执行文件。

### 提交返回 422

通常是以下原因：

- 设备或序列不存在
- 序列缺少某个传感器 MCAP
- 配置 ID 不是 `/configs` 返回的值
- 序列重复
- `cam_workers` 不在 1 到 4 之间
- 请求中包含平台不支持的额外字段

### 提交返回 503

通常表示任务调度器正在关闭、数据库不可用、磁盘不可写或任务目录创建失败。

### 任务 failed

查看：

```text
runner.log
output/<sequence>/logs/extract.log
output/<sequence>/logs/okvis.log
output/<sequence>/status.txt
```

### 内存不足或进程被系统杀死

降低全局并发数后重启服务：

```bash
EGO_WEB_MAX_CONCURRENCY=1 ...
```

MCAP 解码和 OKVIS 都比较占内存，建议从并发 `1` 开始验证。