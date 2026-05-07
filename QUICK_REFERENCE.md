# OKVIS2-X 快速参考表

## 📁 数据集目录结构速查

### 标准EuRoC格式（必需）
```
dataset/
├── imu0/data.csv                    ← 必需：IMU数据
├── cam0/                            ← 必需：第一个相机
│   ├── data.csv                     ← 时间戳,文件名
│   └── data/                        ← 图像文件夹
├── cam1/                            ← 可选：第二个相机
│   ├── data.csv
│   └── data/
├── cam2/ ... cam4/                  ← 可选：更多相机（最多5个）
└── depth0/, depth1/...             ← 可选：深度图像
```

### CSV格式示例
```csv
# imu0/data.csv
#timestamp [ns],w_RS_S_x [rad s^-1],w_RS_S_y [rad s^-1],w_RS_S_z [rad s^-1],a_RS_S_x [m s^-2],a_RS_S_y [m s^-2],a_RS_S_z [m s^-2]
1403636579763555584,0.123,-0.045,0.089,-0.123,0.456,10.145

# cam0/data.csv
#timestamp [ns],filename
1403636579763555584, 1403636579763555584.png
1403636579813555584, 1403636579813555584.png
```

---

## 🎥 相机配置快速查询

### 鱼眼相机配置（Equidistant）
```yaml
- T_SC: [4x4_extrinsics_matrix]
  image_dimension: [720, 540]
  distortion_type: equidistant              # ← 鱼眼
  distortion_coefficients: [-0.036, -0.005, 0.002, -0.001]  # [k1, k2, k3, k4]
  focal_length: [350.37, 350.46]
  principal_point: [367.59, 253.84]
  cam_model: pinhole
  camera_type: gray                         # or gray+depth
  slam_use: okvis
```

### 标准相机配置（RadialTangential）
```yaml
- T_SC: [4x4_extrinsics_matrix]
  image_dimension: [752, 480]
  distortion_type: radialtangential         # ← 标准
  distortion_coefficients: [-0.28, 0.07, 0.00001, 0.00002]  # [k1, k2, p1, p2]
  focal_length: [458.65, 457.29]
  principal_point: [367.21, 248.37]
  cam_model: pinhole
  camera_type: gray+depth
  slam_use: okvis
```

### 全向相机配置（EUCM）
```yaml
- T_SC: [4x4_extrinsics_matrix]
  image_dimension: [640, 480]
  cam_model: eucm
  eucm_parameters: [0.5, 1.5]               # [alpha ∈ [0,1], beta > 0]
  focal_length: [400, 400]
  principal_point: [320, 240]
  camera_type: gray
  slam_use: okvis
```

---

## 🔧 camera_parameters 配置

### 完整示例（5相机鱼眼）
```yaml
camera_parameters:
  timestamp_tolerance: 0.005                # 相机时间同步容差 [s]
  sync_cameras: [0, 1, 2, 3, 4]             # 需要同步的相机ID
  image_delay: 0.0018                       # 图像延迟修正 [s]
  online_calibration:
    do_extrinsics: true                     # 在线标定外参
    do_extrinsics_final_ba: true
    sigma_r: 0.001                          # 位置先验 [m]
    sigma_alpha: 0.005                      # 旋转先验 [rad]
    sigma_r_final_ba: 0.003
    sigma_alpha_final_ba: 0.016
  deep_stereo_indices: [1, 0]               # 立体对相机ID [左, 右]
  fov_scale: 0.9                            # 鱼眼矫正缩放因子
```

---

## 📊 Distortion_Type 对照表

| distortion_type | 参数数量 | 参数说明 | 使用场景 | cam_model |
|----------------|---------|--------|--------|----------|
| `equidistant` | 4 | k1, k2, k3, k4 | 鱼眼相机、广角 | pinhole |
| `radialtangential` | 4 | k1, k2, p1, p2 | 标准相机、单目 | pinhole |
| `plumb_bob` | 4 | 同上 | OpenCV标准 | pinhole |
| `radialtangential8` | 8 | k1-k6, p1, p2 | 高精度标定 | pinhole |
| `plumb_bob8` | 8 | 同上 | 8参数精细 | pinhole |
| N/A (EUCM) | 用eucm_parameters | alpha, beta | 全向相机 | eucm |

---

## 🎯 多相机配置对照表

| 特性 | Hilti22(5鱼眼) | VBR(2标准) | EuRoC(2标准) | GVINS(2+GPS) |
|------|---|---|---|---|
| **相机数** | 5 | 2 | 2 | 2 |
| **相机类型** | equidistant | radialtangential | radialtangential | radialtangential |
| **深度支持** | ✅ 相机1 | ✅ 相机0 | ✅ 相机0 | ❌ |
| **配置文件** | `config/hilti22/okvis2.yaml` | `config/vbr/okvis2.yaml` | `config/euroc/okvis2.yaml` | `config/gvins/okvis2.yaml` |
| **同步相机** | [0,1,2,3,4] | [0,1] | [0,1] | [0,1] |
| **立体对** | [1,0] | [0,1] | [0,1] | [0,1] |
| **矫正缩放** | 0.9 | 1.0 | 1.0 | 1.0 |

---

## 📖 配置示例文件位置

```
config/
├── hilti22/
│   ├── okvis2.yaml                  ← 5相机鱼眼 (推荐参考)
│   ├── okvis2-lidar.yaml            ← +LiDAR版本
│   └── okvis2-postcalib.yaml        ← 后处理标定
├── vbr/
│   └── okvis2.yaml                  ← 2相机标准
├── euroc/
│   └── okvis2.yaml                  ← EuRoC双目标准
├── gvins/
│   └── okvis2.yaml                  ← 2相机+GNSS
└── rsD455/
    └── okvis2.yaml                  ← RealSense D455
```

---

## ✅ 配置检查清单

### 创建新的鱼眼多相机配置
- [ ] 设置 `distortion_type: equidistant`
- [ ] 配置4个 distortion_coefficients
- [ ] 填写每个相机的 T_SC (4x4变换矩阵)
- [ ] 设置 `sync_cameras: [0, 1, 2, ...]`
- [ ] 设置 `deep_stereo_indices: [i, j]` 用于密集映射
- [ ] 设置 `fov_scale: 0.9` (对于鱼眼)
- [ ] 启用 `mapping_rectification: true`
- [ ] 设置 `online_calibration.do_extrinsics: true`
- [ ] 验证 IMU 数据在 `imu0/data.csv`
- [ ] 验证相机图像在 `cam*/data/` 目录

### 创建新的标准相机多相机配置
- [ ] 设置 `distortion_type: radialtangential`
- [ ] 配置4个 distortion_coefficients
- [ ] 填写每个相机的 T_SC
- [ ] 设置 `sync_cameras: [0, 1]`
- [ ] 设置 `deep_stereo_indices: [0, 1]`
- [ ] 设置 `fov_scale: 1.0` (标准相机)
- [ ] 验证图像格式和尺寸

---

## 🔍 主要源代码文件

### 数据集读取
```
okvis_multisensor_processing/
├── include/okvis/DatasetReader.hpp
├── src/DatasetReader.cpp               ← 核心实现
├── src/DatasetWriter.cpp               ← 数据集生成
└── src/...DatasetReader.cpp            ← 其他格式
```

### 相机模型与配置
```
okvis_cv/include/okvis/cameras/
├── EquidistantDistortion.hpp           ← 鱼眼畸变
├── RadialTangentialDistortion.hpp      ← 标准畸变
├── RadialTangentialDistortion8.hpp     ← 8参数畸变
├── EucmCamera.hpp                      ← 全向相机
└── NCameraSystem.hpp                   ← 多相机管理

okvis_common/src/
└── ViParametersReader.cpp              ← 配置解析
```

---

## 🚀 实用命令

### 验证数据集结构
```bash
# 检查必需的 CSV 文件
find dataset -name "data.csv" | sort

# 验证图像数量
find dataset/cam*/data -type f | wc -l
find dataset/depth*/data -type f | wc -l

# 检查 IMU 数据行数
wc -l dataset/imu0/data.csv
```

### 从代码查询相机支持
```bash
# 查找所有支持的 distortion_type
grep -r "distortion_type" config/ | grep -o "distortion_type:.*" | sort -u

# 查找多相机配置
grep -l "cam[3-4]" config/*/*.yaml

# 查找鱼眼相机配置
grep -l "equidistant" config/*/*.yaml
```

---

## 📞 问题排查

### "no imu file found"
- ✅ 确保 `imu0/data.csv` 存在且不为空
- ✅ 第一行必须是注释行（`#timestamp ...`）

### "no images found for camera"
- ✅ 确保 `cam*/data.csv` 存在
- ✅ 确保 `cam*/data/` 目录包含图像文件
- ✅ 图像文件名必须与 CSV 中的文件名匹配

### 相机时间不同步
- 📝 调整 `timestamp_tolerance` (默认 0.005s)
- 📝 检查 `image_delay` 参数
- 📝 验证 `sync_cameras` 列表正确

### 深度图像问题
- ✅ 深度图必须在 `depth*/data/` 目录
- ✅ 16位整数深度会被乘以0.001转换为浮点
- ✅ 设置 `camera_type: gray+depth` 或 `rgb+depth`

