# OKVIS2-X 项目分析总结

## 1. DatasetReader 实现及数据集目录结构

### 1.1 DatasetReader 的作用
- **文件位置**: `/okvis_multisensor_processing/include/okvis/DatasetReader.hpp` 和 `.cpp`
- **主要功能**: 充当虚拟VI传感器，从磁盘读取数据集并流式传输给处理管道

### 1.2 期望的数据集目录结构

```
dataset_root/
├── imu0/
│   └── data.csv                  # IMU数据文件
├── cam0/
│   ├── data.csv                  # 时间戳和文件名映射
│   └── data/                     # 图像文件目录
│       ├── [timestamp].png
│       └── [timestamp].png
├── cam1/
│   ├── data.csv
│   └── data/
├── cam2/
│   ├── data.csv
│   └── data/
├── cam3/
│   ├── data.csv
│   └── data/
├── cam4/  (可选，支持5个相机)
│   ├── data.csv
│   └── data/
├── depth0/  (可选，用于深度相机)
│   ├── data.csv
│   └── data/
│       ├── [timestamp].tif
│       └── [timestamp].tif
├── rgb0/   (如果是RGB相机，用 rgb0 替代 cam0)
│   ├── data.csv
│   └── data/
│       ├── [timestamp].jpg
│       └── [timestamp].jpg
└── gps0/   (可选，GPS数据)
    ├── data.csv (笛卡尔坐标)
    ├── data_raw.csv (大地坐标)
    └── gnss.csv (Leica格式)
```

### 1.3 CSV 文件格式

**imu0/data.csv:**
```
#timestamp [ns],w_RS_S_x [rad s^-1],w_RS_S_y [rad s^-1],w_RS_S_z [rad s^-1],a_RS_S_x [m s^-2],a_RS_S_y [m s^-2],a_RS_S_z [m s^-2]
1403636579763555584,0.123,-0.045,0.089,-0.123,0.456,10.145
```

**cam0/data.csv 或 rgb0/data.csv:**
```
#timestamp [ns],filename
1403636579763555584, 1403636579763555584.png
1403636579813555584, 1403636579813555584.png
```

**深度 depth0/data.csv:**
```
#timestamp [ns],filename
1403636579763555584, 1403636579763555584.tif
1403636579813555584, 1403636579813555584.tif
```

### 1.4 关键特性
- **多相机支持**: 支持 cam0, cam1, cam2, cam3, cam4... 等多个灰度相机，或 rgb0, rgb1... 彩色相机
- **图像格式**:
  - 灰度图: `.png` 格式，强制读取为灰度
  - RGB图: `.jpg` 格式
- **时间戳**: 使用纳秒（nanoseconds）精度，存储为 uint64_t
- **深度支持**: 可选的深度图像，存储为 `.tif` 格式
  - 浮点32深度图 (CV_32FC1): 按原样保存
  - 16位整数深度: 转换为 float，乘以 0.001 因子
- **GPS支持**: 三种GPS格式支持
  - `cartesian`: 笛卡尔坐标 (x, y, z)
  - `geodetic`: 大地坐标 (lat, lon, alt, h_err, v_err)
  - `geodetic-leica`: Leica扩展格式

---

## 2. 鱼眼相机相关配置

### 2.1 支持的鱼眼配置

所有在项目中搜索到的包含 "fisheye", "equidistant", "kannala" 关键词的配置文件都使用 **equidistant** 作为 distortion_type：

| 配置文件 | 相机数量 | 相机类型 | 说明 |
|--------|--------|--------|------|
| `config/hilti22/okvis2.yaml` | 5 | equidistant fisheye | **多相机鱼眼示例** |
| `config/hilti22/okvis2-lidar.yaml` | 5 | equidistant fisheye | 带LiDAR的版本 |
| `config/hilti22/okvis2-postcalib.yaml` | 5 | equidistant fisheye | 后处理标定版本 |
| `config/vbr/okvis2.yaml` | 2 | radialtangential | (标准径向切向) |
| `config/euroc/okvis2.yaml` | 2 | radialtangential | EuRoC标准双目 |
| `config/gvins/okvis2.yaml` | 2 | radialtangential | GNSS+VI |
| `config/replica/okvis2.yaml` | 无 | radialtangential | 虚拟环境 |
| `config/rsD455/okvis2.yaml` | 无 | equidistant | RealSense D455 |

### 2.2 Hilti22 五相机鱼眼配置（推荐参考）

```yaml
%YAML:1.2
cameras:
  - {T_SC: [extrinsics_4x4_matrix...],
     image_dimension: [720, 540],
     distortion_coefficients: [-0.0364618, -0.0054448, 0.0027542, -0.0011093],
     distortion_type: equidistant,  # ← 鱼眼型 (equidistant/fisheye)
     focal_length: [350.3728, 350.4644],
     principal_point: [367.5982, 253.8475],
     cam_model: pinhole,
     camera_type: gray,
     mapping_rectification: true,
     slam_use: okvis}

  - {T_SC: [...], distortion_type: equidistant, ...}  # Camera 1
  - {T_SC: [...], distortion_type: equidistant, ...}  # Camera 2
  - {T_SC: [...], distortion_type: equidistant, ...}  # Camera 3
  - {T_SC: [...], distortion_type: equidistant, ...}  # Camera 4

camera_parameters:
  timestamp_tolerance: 0.005
  sync_cameras: [0, 1, 2, 3, 4]     # 同步所有5个相机
  image_delay: 0.0018
  online_calibration:
    do_extrinsics: true              # 在线标定外参
    do_extrinsics_final_ba: true
    sigma_r: 0.001                   # 位置先验标准差 [m]
    sigma_alpha: 0.005               # 旋转先验标准差 [rad]
  deep_stereo_indices: [1, 0]        # 用于密集深度估计的立体相机对
  fov_scale: 0.9                     # ← 鱼眼立体矫正的焦距缩放因子
```

### 2.3 鱼眼相机的关键参数

| 参数 | 说明 | Hilti22 值 |
|------|------|-----------|
| `distortion_type` | 畸变类型 | `equidistant` |
| `distortion_coefficients` | 4个畸变系数 [k1, k2, k3, k4] | [-0.036, -0.005, 0.002, -0.001] |
| `focal_length` | 焦距 [f_x, f_y] | [350.37, 350.46] |
| `principal_point` | 主点 [c_x, c_y] | [367.59, 253.84] |
| `image_dimension` | 图像尺寸 [width, height] | [720, 540] |
| `fov_scale` | 立体矫正焦距缩放 | 0.9 |

---

## 3. 多相机（4+相机）配置示例

### 3.1 多相机配置目录

所有支持多相机的配置在这三个目录：
- **`config/hilti22/`** - **5个鱼眼相机** ← 推荐参考
- **`config/vbr/`** - 2个径向切向相机
- **`config/gvins/`** - 2个径向切向相机 + GPS

### 3.2 Hilti22 的相机部分完整配置

```yaml
cameras:
  - {T_SC:
       [0.007659110000000,   0.003215660000000,   0.999965500000000,   0.053090490000000,
        0.999928820000000,   0.009123650000000,  -0.007688160000000,   0.045903280000000,
        -0.009148060000000,   0.999953210000000,  -0.003145550000000,  -0.014702850000000,
        0.,                  0.,                  0.,                  1.  ],
     image_dimension: [720, 540],
     distortion_coefficients: [-0.036461810007375715, -0.005444851637229522, 0.0027544255985531532, -0.0011093007039524076],
     distortion_type: equidistant,
     focal_length: [350.3728036482598, 350.4644910214914],
     principal_point: [367.5982315054545, 253.84756624017055],
     cam_model: pinhole,
     camera_type: gray,
     mapping_rectification: true,
     slam_use: okvis}

  - {T_SC: [0.002718210000000, 0.001492590000000, 0.999995190000000, 0.052228070000000, ...],
     image_dimension: [720, 540],
     distortion_coefficients: [-0.03757458639343802, -0.005391152610508747, 0.0024181795504577054, -0.0008408335480924147],
     distortion_type: equidistant,
     focal_length: [351.64657493358027, 351.79425435313976],
     principal_point: [347.615033118946, 270.724275256626],
     cam_model: pinhole,
     camera_type: gray+depth,         # ← 相机2有深度图
     mapping: true,
     mapping_rectification: true,
     slam_use: okvis}

  - {T_SC: [0.999989894765666, 0.001410259205189, -0.004268260673128, 0.016020483659020, ...],
     image_dimension: [720, 540],
     distortion_coefficients: [-0.03797509, -0.00631309, 0.00478156, -0.00181366],
     distortion_type: equidistant,
     focal_length: [349.65420714, 349.69004533],
     principal_point: [375.16391931, 268.63008188],
     cam_model: pinhole,
     camera_type: gray,
     slam_use: okvis}

  - {T_SC: [-0.999849667750467, 0.014662362254630, 0.009254920099213, -0.003018021388404, ...],
     image_dimension: [720, 540],
     distortion_coefficients: [-0.0408695, 0.0111725, -0.01325958, 0.00386262],
     distortion_type: equidistant,
     focal_length: [350.95815742, 351.29060578],
     principal_point: [364.37064004, 266.19201052],
     cam_model: pinhole,
     camera_type: gray,
     slam_use: okvis}

  - {T_SC: [0.999976071553276, 0.006730119448312, -0.001599477019339, -0.004560426796944, ...],
     image_dimension: [720, 540],
     distortion_coefficients: [-0.03496239, -0.00947668, 0.00592657, -0.00186744],
     distortion_type: equidistant,
     focal_length: [349.97099061, 350.18639289],
     principal_point: [342.73031692, 259.72588616],
     cam_model: pinhole,
     camera_type: gray,
     slam_use: okvis}

camera_parameters:
  timestamp_tolerance: 0.005        # 相机时间同步容差
  sync_cameras: [0, 1, 2, 3, 4]     # 所有5个相机同步
  image_delay: 0.0018
  online_calibration:
    do_extrinsics: true
    do_extrinsics_final_ba: true
    sigma_r: 0.001
    sigma_alpha: 0.005
  deep_stereo_indices: [1, 0]       # Camera1 和 Camera0 作为立体对
  fov_scale: 0.9                    # 鱼眼立体矫正缩放
```

### 3.3 VBR 的双相机配置（径向切向）

```yaml
cameras:
  - T_SC: [0.0119141292438965, -0.000945065440359438, 0.99992857763726, 0.0879708589133336, ...]
    image_dimension: [1388, 700]
    distortion_coefficients: [-0.10572495, 0.2426826, -0.00141098, -0.00577569]
    distortion_type: radialtangential    # ← 标准径向切向畸变
    focal_length: [1282.79698152, 1283.56137502]
    principal_point: [683.48271639, 345.04730772]
    cam_model: pinhole
    camera_type: gray+depth
    mapping: true
    mapping_rectification: true
    slam_use: okvis

  - T_SC: [0.0522904883845883, 0.00752371052953171, 0.998603574299817, 0.092607244896494, ...]
    image_dimension: [1388, 700]
    distortion_coefficients: [-0.11189064, 0.27022719, -0.0011909, -0.00119663]
    distortion_type: radialtangential
    focal_length: [1286.97877645, 1287.17303603]
    principal_point: [687.96374444, 345.34980449]
    cam_model: pinhole
    camera_type: gray
    mapping: false
    mapping_rectification: true
    slam_use: okvis

camera_parameters:
  timestamp_tolerance: 0.005
  sync_cameras: [0, 1]
  image_delay: 0.0
  deep_stereo_indices: [0, 1]
  fov_scale: 1.0
```

---

## 4. Distortion_Type 支持的类型

### 4.1 完整列表

基于 `okvis_common/src/ViParametersReader.cpp` 的代码分析，支持以下 distortion_type：

| distortion_type | 相机模型 | 参数数量 | 说明 |
|-----------------|--------|--------|------|
| `equidistant` | Pinhole + EquidistantDistortion | 4 | **鱼眼/等距畸变模型** |
| `radialtangential` | Pinhole + RadialTangentialDistortion | 4 | 标准径向切向畸变 (OpenCV 标准) |
| `plumb_bob` | Pinhole + RadialTangentialDistortion | 4 | 径向切向的别名 |
| `radialtangential8` | Pinhole + RadialTangentialDistortion8 | 8 | 扩展的8参数径向切向畸变 |
| `plumb_bob8` | Pinhole + RadialTangentialDistortion8 | 8 | 8参数的别名 |

**额外**: 相机模型 `eucm` 时使用 `eucm_parameters` 而不是 `distortion_type`

### 4.2 畸变参数说明

**Equidistant (鱼眼) - 4参数:**
```
distortion_coefficients: [k1, k2, k3, k4]
- k1, k2: 主要畸变系数（径向）
- k3, k4: 切向畸变系数
```

**RadialTangential - 4参数:**
```
distortion_coefficients: [k1, k2, p1, p2]
- k1, k2: 径向畸变系数（r²和r⁴项）
- p1, p2: 切向畸变系数
```

**RadialTangentialDistortion8 - 8参数:**
```
distortion_coefficients: [k1, k2, p1, p2, k3, k4, k5, k6]
- k1-k6: 6个径向畸变系数
- p1-p2: 切向畸变系数
```

### 4.3 相机模型支持

从代码中确认的完整相机模型支持：

```cpp
// 从 ViParametersReader.cpp 的 getCalibrationViaConfig 函数：
if((std::string)((*it)["cam_model"]) == "pinhole") {
  // 支持所有 distortion_type
}
else if ((std::string)((*it)["cam_model"]) == "eucm") {
  // Enhanced Unified Camera Model - 用 eucm_parameters: [alpha, beta]
}
```

### 4.4 EUCM 配置示例

```yaml
- cam_model: eucm
  T_SC: [...]
  image_dimension: [width, height]
  focal_length: [f_u, f_v]
  principal_point: [c_u, c_v]
  eucm_parameters: [alpha, beta]  # alpha: [0,1], beta: > 0
  camera_type: gray
  slam_use: okvis
```

### 4.5 代码支持的完整链

从 `ViParametersReader.cpp` L135-220：

```cpp
// Line 135: 处理 equidistant
else if (strcmp(calibrations[i].distortionType.c_str(), "equidistant") == 0) {
  // 创建 PinholeCamera<EquidistantDistortion>
  // 加载 EquidistantDistortion 类
}
// Line 161: 处理 radialtangential 和 plumb_bob
else if (strcmp(calibrations[i].distortionType.c_str(), "radialtangential") == 0
         || strcmp(calibrations[i].distortionType.c_str(), "plumb_bob") == 0) {
  // 创建 PinholeCamera<RadialTangentialDistortion>
}
// Line 188: 处理 radialtangential8 和 plumb_bob8
else if (strcmp(calibrations[i].distortionType.c_str(), "radialtangential8") == 0
         || strcmp(calibrations[i].distortionType.c_str(), "plumb_bob8") == 0) {
  // 创建 PinholeCamera<RadialTangentialDistortion8>
}
else {
  LOG(ERROR) << "unrecognized distortion type " << calibrations[i].distortionType;
}
```

---

## 5. 多相机支持能力总结

### 5.1 架构特点
- **无硬编码上限**: 使用 `std::vector` 存储所有相机
- **已测试**: Hilti22 数据集使用 **5 个鱼眼相机**
- **灵活同步**: `sync_cameras` 数组指定哪些相机需要时间同步

### 5.2 关键支持类

| 类 | 文件 | 功能 |
|----|------|------|
| `NCameraSystem` | `okvis_cv/include/okvis/cameras/NCameraSystem.hpp` | 多相机管理器，存储所有 T_SC、相机几何、重叠图 |
| `FrameNoncentralAbsoluteAdapter` | 基于 OpenGV | 支持非中心相机的 3D2D RANSAC |
| `Frontend` | `okvis_frontend/src/Frontend.cpp` | 跨相机特征匹配 |
| `EquidistantDistortion` | `okvis_cv/include/okvis/cameras/EquidistantDistortion.hpp` | 鱼眼畸变模型 |
| `EucmCamera` | `okvis_cv/include/okvis/cameras/EucmCamera.hpp` | 全向相机模型 |

### 5.3 特性支持矩阵

| 特性 | 支持 | 说明 |
|------|------|------|
| 4+ 个相机 | ✅ | 无限制 |
| 鱼眼相机 | ✅ | equidistant 型 |
| 非中心几何 | ✅ | 任意 T_SC 位置 |
| 任意方向 | ✅ | 支持 90° 分离 |
| 在线标定 | ✅ | 运行时修正外参 |
| 立体矫正 | ✅ | 支持鱼眼专用缩放 |

---

## 6. 快速参考

### 项目关键文件

```
OKVIS2-X/
├── okvis_multisensor_processing/
│   ├── include/okvis/DatasetReader.hpp      ← 数据集读取器接口
│   ├── src/DatasetReader.cpp                ← 实现
│   ├── src/DatasetWriter.cpp                ← 数据集生成
│   └── src/...DatasetReader.cpp             ← 其他格式读取器
├── okvis_cv/include/okvis/cameras/
│   ├── EquidistantDistortion.hpp            ← 鱼眼畸变模型
│   ├── EucmCamera.hpp                       ← 全向相机
│   ├── RadialTangentialDistortion.hpp       ← 标准畸变
│   └── NCameraSystem.hpp                    ← 多相机管理
├── okvis_common/src/
│   └── ViParametersReader.cpp               ← 配置文件解析
├── config/
│   ├── hilti22/okvis2.yaml                  ← **5相机鱼眼参考**
│   ├── vbr/okvis2.yaml
│   ├── euroc/okvis2.yaml
│   └── gvins/okvis2.yaml
└── OKVIS2_CIRCULAR_FISHEYE_ANALYSIS.md      ← 详细分析文档
```

### 配置检查清单

☑️ **对于鱼眼多相机系统**:
1. 设置 `distortion_type: equidistant`
2. 配置 4 个 distortion_coefficients
3. 设置 `sync_cameras: [0, 1, 2, 3]` （或更多）
4. 设置 `deep_stereo_indices: [i, j]` 用于密集映射
5. 设置 `fov_scale: 0.9` （对于鱼眼）
6. 启用 `mapping_rectification: true`

