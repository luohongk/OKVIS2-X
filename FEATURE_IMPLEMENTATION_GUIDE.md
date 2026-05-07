# OKVIS2-X Feature Detection & Matching - Quick Reference Guide

## TL;DR

✅ **SuperPoint + LightGlue are FULLY implemented and ENABLED in the EuRoC config**

The system uses **SuperPoint (ONNX)** for feature extraction and **LightGlue (ONNX)** for matching, with BRISK as fallback.

---

## Key Files to Understand

### Configuration
- **`config/euroc/okvis2.yaml`** (Lines 81-87)
  - `use_dl_features: true` → Enables SuperPoint+LightGlue
  - `dl_extractor_path` → Path to SuperPoint ONNX model (5.1 MB)
  - `dl_matcher_path` → Path to LightGlue ONNX model (44 MB)
  - `dl_match_threshold: 0.7` → Confidence threshold for matches

### Core Implementation
- **`okvis_frontend/src/Frontend.cpp`**
  - `detectAndDescribe()` (line 276) → Detection logic
  - `initialiseDlFeatures()` → ONNX runtime setup
  - `isDLDescriptor()` (line 110) → Runtime descriptor type detection
  - Matching logic uses descriptor type to choose algorithm

- **`okvis_frontend/include/okvis/dl_features/DLFeatureExtractor.hpp`**
  - `DLFeatureExtractor` class → SuperPoint inference
  - `DLFeatureMatcher` class → LightGlue inference

### Parameters
- **`okvis_common/include/okvis/Parameters.hpp`** (Line 111+)
  - `struct FrontendParameters` defines all config options
  - `use_dl_features`, `dl_extractor_path`, `dl_matcher_path`, etc.

### Build Configuration
- **`okvis_frontend/CMakeLists.txt`** (Line 21+)
  - `if(USE_DL_FEATURES)` block
  - Links against ONNX Runtime
  - Default path: `/home/lhk/ThirdParty/onnxruntime-linux-x64-gpu-1.16.3`

---

## Feature Extraction Pipeline

```
Image Input
   ↓
[Super Point ONNX Model]
   ├─ Input: [1,1,H,W] float32 (normalized [0,1])
   └─ Outputs:
      ├─ Keypoints: [1,N,2] int64
      ├─ Scores: [1,N] float32
      └─ Descriptors: [1,256,N] float32
   ↓
Convert to cv::KeyPoint + cv::Mat
   ↓
Store in Frame (CV_32F format)
```

## Feature Matching Pipeline

### Stereo Matching (within MultiFrame)
```
Frame 0 (SuperPoint features)  +  Frame 1 (SuperPoint features)
                ↓
        [LightGlue ONNX Model]
        - Normalize keypoints
        - Input: kpts0, kpts1, desc0, desc1
        - Output: Match pairs + Confidence scores
                ↓
        Filter by threshold (0.7)
```

### Map Matching (Landmark Association)
```
Current Frame Features  +  Landmark Descriptors
        ↓
[Check Descriptor Type]
        ├─ If CV_32F → Use LightGlue matcher
        └─ If CV_8U → Use Hamming distance (BRISK)
```

---

## Important Configuration Parameters

| Parameter | Config Value | Purpose |
|-----------|--------------|---------|
| `use_dl_features` | `true` | Enable SuperPoint+LightGlue |
| `dl_extractor_type` | `superpoint` | Feature detector (only option) |
| `dl_extractor_path` | `/home/lhk/workspace/OKVIS2-X/weight/superpoint.onnx` | SuperPoint model location |
| `dl_matcher_path` | `/home/lhk/workspace/OKVIS2-X/weight/superpoint_lightglue_fused_cpu.onnx` | LightGlue model (CPU version) |
| `dl_match_threshold` | `0.7` | LightGlue confidence [0,1] |
| `dl_image_size` | `512` | Resize longest edge (pixels) |
| `dl_use_gpu` | `false` | CPU inference for EuRoC config |

---

## BRISK Fallback Parameters (Still Available)

| Parameter | Config Value | Purpose |
|-----------|--------------|---------|
| `detection_threshold` | `38.0` | Harris corner uniformity radius |
| `absolute_threshold` | `150.0` | Harris corner threshold |
| `matching_threshold` | `60.0` | BRISK Hamming distance threshold |
| `octaves` | `0` | Single-scale (0) vs multi-scale |
| `max_num_keypoints` | `700` | Max keypoints per image |

---

## Available ONNX Models

Location: `/home/lhk/workspace/OKVIS2-X/weight/`

| Model | Size | Purpose |
|-------|------|---------|
| `superpoint.onnx` | 5.1 MB | Feature extractor (256-D) |
| `superpoint_lightglue_fused_cpu.onnx` | 44 MB | Matcher for CPU (current config) |
| `superpoint_lightglue_fused.onnx` | 44 MB | Matcher for GPU |
| `disk.onnx` | 4.3 MB | Alternative extractor (128-D) |
| `disk_lightglue_fused.onnx` | 44 MB | DISK+LightGlue matcher |

**Note:** To switch to DISK or GPU versions, modify config and set `dl_use_gpu: true` if needed.

---

## How Feature Type Detection Works

```cpp
// At runtime, the system checks descriptor type
bool isDLDescriptor(const MultiFramePtr& mf, size_t cam) {
  if (mf->numKeypoints(cam) == 0) return false;
  return mf->descriptorType(cam) == CV_32F;  // True = SuperPoint, False = BRISK
}

// Then selects appropriate matching:
if (isDLDescriptor(...)) {
  dlMatcher_->match(...);  // Use LightGlue
} else {
  briskMatch(...);  // Use Hamming distance
}
```

This allows mixing BRISK and SuperPoint features in the same SLAM session!

---

## Thread Safety

DL inference is protected by mutex to handle multi-camera systems:

```cpp
std::mutex dlMutex_;  // Protects dlExtractor_ / dlMatcher_

// Usage in detectAndDescribe():
{
  std::lock_guard<std::mutex> dlLock(dlMutex_);
  dlExtractor_->extract(img, pts, scores, descs);
}
```

---

## GPU Support

To enable GPU inference:

1. **Build with CUDA:**
   ```bash
   cmake .. -DUSE_GPU=ON -DOnnxRuntime_DIR=/path/to/gpu/onnxruntime
   ```

2. **Update config:**
   ```yaml
   frontend_parameters:
     dl_use_gpu: true
     dl_matcher_path: "/path/to/superpoint_lightglue_fused.onnx"
   ```

**Requirements:**
- CUDA 11+ and cuDNN 8
- ONNX Runtime built with CUDA support

---

## Descriptor Metrics

### BRISK (Handcrafted)
- **Type:** Binary (uint8)
- **Distance:** Hamming distance (PopcntofXORed)
- **Range:** [0, ∞] (typically 0-64)
- **Threshold:** `matching_threshold` (60.0)

### SuperPoint (Deep Learning)
- **Type:** Float32 (L2-normalized)
- **Distance:** Cosine distance (1 - dot_product)
- **Range:** [0, 2] (0 = identical, 2 = opposite)
- **Threshold:** `dl_match_threshold` (0.7)

---

## RANSAC and Pose Estimation

Both BRISK and SuperPoint features use the same RANSAC pipelines:

- **3D-2D RANSAC** (`runRansac3d2d()`) → Pose initialization
- **2D-2D RANSAC** (`runRansac2d2d()`) → Stereo matching with rotation-only fallback
- **Landmark Verification** → Triangulation and 3D initialization

RANSAC algorithm is implemented in `opengv/` (external dependency).

---

## Enable/Disable DL Features

To toggle between SuperPoint and BRISK:

**Enable SuperPoint (Current):**
```yaml
frontend_parameters:
  use_dl_features: true
  dl_extractor_path: "/home/lhk/workspace/OKVIS2-X/weight/superpoint.onnx"
  dl_matcher_path: "/home/lhk/workspace/OKVIS2-X/weight/superpoint_lightglue_fused_cpu.onnx"
```

**Disable SuperPoint (Fallback to BRISK):**
```yaml
frontend_parameters:
  use_dl_features: false
  # BRISK params will be used instead
```

---

## What Gets Stored in Keyframes

For each keyframe, the system stores:

1. **Keypoints** (cv::KeyPoint)
   - Position (x, y)
   - Size (response/score)
   - Angle (-1 for non-oriented)

2. **Descriptors** (cv::Mat)
   - SuperPoint: CV_32F, shape [N, 256]
   - BRISK: CV_8U, shape [N, 48]

3. **Metadata**
   - Camera index
   - Timestamp
   - Pose (estimated during SLAM)

4. **Associated Landmarks**
   - 3D world positions
   - Keypoint-landmark associations
   - Reprojection errors

---

## Performance Notes

**SuperPoint (DL):**
- ✅ More distinctive features
- ✅ Better in challenging lighting/viewpoint changes
- ✅ GPU acceleration available
- ❌ ~256 descriptors (larger memory footprint)
- ❌ Inference latency (but typically <50ms per image)

**BRISK (Handcrafted):**
- ✅ Fast, pure CPU
- ✅ Smaller descriptors (~48 bytes)
- ✅ No model dependency
- ❌ Less robust to viewpoint/illumination changes
- ❌ Fewer distinctive keypoints

---

## Debugging

Enable verbose logging in `Frontend.cpp`:
- Line 298: `dlExtractor_->extract()` logs extraction status
- Line 409: `dlMatcher_->match()` logs match counts
- `isDLDescriptor()` can be called to check feature type

Check descriptor type at runtime:
```cpp
LOG(INFO) << "Descriptor type: " << (isDLDescriptor(multiFrame, cam) ? "DL" : "BRISK");
```

---

## Related Components

- **DBoW2** → Place recognition (uses BRISK descriptors for vocabulary)
- **OpenGV** → RANSAC and pose estimation
- **Ceres Solver** → Nonlinear optimization
- **ONNX Runtime** → DL model inference
- **PyTorch** → Optional CNN for dynamic content filtering

---

## Quick Start

1. **Check if DL features are enabled:**
   ```bash
   grep -A2 "use_dl_features" config/euroc/okvis2.yaml
   ```

2. **Verify ONNX models exist:**
   ```bash
   ls -lh weight/*.onnx
   ```

3. **Run with SuperPoint+LightGlue:**
   ```bash
   ./okvis2x_app_synchronous config/euroc/okvis2.yaml euroc_dataset/
   ```

4. **Switch to BRISK (disable DL):**
   - Edit config, set `use_dl_features: false`
   - Rebuild if needed (USE_DL_FEATURES=OFF)

---

## Summary

| Aspect | Status |
|--------|--------|
| SuperPoint | ✅ Implemented & Enabled |
| LightGlue | ✅ Implemented & Enabled |
| BRISK | ✅ Fallback available |
| Configuration | ✅ YAML-driven |
| GPU Support | ✅ Optional (CUDA) |
| Model Files | ✅ Available (5 models) |
| ONNX Runtime | ✅ Integrated (1.16.3) |
| Thread Safety | ✅ Mutex protected |

**Current Configuration:** SuperPoint+LightGlue (CPU mode) enabled by default ✅

