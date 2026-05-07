# OKVIS2-X Feature Detection and Matching Analysis

## Executive Summary

OKVIS2-X uses a **dual-path approach** for feature detection and matching:

1. **Primary Path (when enabled):** SuperPoint + LightGlue via ONNX Runtime
2. **Fallback/Baseline Path:** BRISK (traditional handcrafted features)

The system can dynamically switch between these methods, with SuperPoint+LightGlue as the preferred option when deep-learning models are available.

---

## 1. Current Frontend Configuration (EuRoC Config)

**File:** `/home/lhk/workspace/OKVIS2-X/config/euroc/okvis2.yaml`

### Deep Learning Features Configuration (Lines 81-87)
```yaml
frontend_parameters:
    ...
    use_dl_features: true                          # Enable SuperPoint + LightGlue
    dl_extractor_type: superpoint                  # Only superpoint supported
    dl_extractor_path: "/home/lhk/workspace/OKVIS2-X/weight/superpoint.onnx"
    dl_matcher_path: "/home/lhk/workspace/OKVIS2-X/weight/superpoint_lightglue_fused_cpu.onnx"
    dl_match_threshold: 0.7                        # LightGlue confidence threshold [0,1]
    dl_image_size: 512                             # Resize longest edge to this
    dl_use_gpu: false                              # CPU inference for this config
```

### BRISK Configuration (Lines 72-80 - Still Present as Fallback)
```yaml
frontend_parameters:
    detection_threshold: 38.0                      # Uniformity radius in pixels
    absolute_threshold: 150.0                      # Harris corner threshold
    matching_threshold: 60.0                       # BRISK descriptor threshold
    octaves: 0                                     # Single-scale at highest resolution
    max_num_keypoints: 700                         # Maximum keypoints per image
    parallelise_detection: true                    # Parallel detect & describe
    num_matching_threads: 4                        # Thread count for matching
```

---

## 2. Feature Pipeline Architecture

### 2.1 Detection and Description Flow

**File:** `okvis_frontend/src/Frontend.cpp` - Function `detectAndDescribe()`

```
Input: Camera frame
   |
   v
[DL Features Enabled?]
   |
   +---YES---> [SuperPoint ONNX Inference]
   |           - Resize image to dl_image_size (512px longest edge)
   |           - Normalize to [0,1] float32
   |           - Extract keypoints, scores, 256-D descriptors
   |           - Return: float32 descriptors (CV_32F)
   |           - Store as cv::Mat in Frame
   |
   +---NO----> [BRISK Fallback]
               - Harris corner detection
               - BRISK feature descriptor extraction
               - Return: uint8 descriptors (CV_8U)
               - Store as cv::Mat in Frame

Output: Frame with keypoints, scores, and descriptors
```

**Key Code Section** (lines 276-330 in Frontend.cpp):
- Tries SuperPoint extraction first if `dlFeaturesInitialised_` is true
- Falls through to BRISK if extraction returns 0 keypoints
- Converts SuperPoint float keypoints to cv::KeyPoint format
- Stores descriptors in original CV_32F format (not quantized)

### 2.2 Matching Strategy

The system uses **runtime descriptor type detection**:

```cpp
inline bool isDLDescriptor(const MultiFramePtr& mf, size_t cam) {
  if (mf->numKeypoints(cam) == 0) return false;
  // BRISK descriptors stored as CV_8U; DL (SuperPoint/DISK) stored as CV_32F
  return mf->descriptorType(cam) == CV_32F;
}
```

**Matching Metrics:**
- **BRISK → BRISK:** Hamming distance (uint8 binary descriptors)
  ```cpp
  double briskDescDist(const uchar* a, const uchar* b) {
    return static_cast<double>(brisk::Hamming::PopcntofXORed(a, b, 3));
  }
  ```

- **SuperPoint → SuperPoint:** Cosine distance (float32 normalized descriptors)
  ```cpp
  inline double dlDescDist(const float* a, const float* b, int D) {
    double dot = 0.0;
    for (int i = 0; i < D; ++i) 
      dot += static_cast<double>(a[i]) * static_cast<double>(b[i]);
    return 1.0 - dot;  // Returns [0, 2], lower = more similar
  }
  ```

**Threshold Selection:**
- BRISK matches: `matching_threshold` (60.0 in config)
- LightGlue matches: `dl_match_threshold` (0.7 in config)

---

## 3. Deep Learning Components

### 3.1 SuperPoint Extractor

**Class:** `okvis::dl::DLFeatureExtractor`
**File:** `okvis_frontend/include/okvis/dl_features/DLFeatureExtractor.hpp`

**ONNX Model I/O:**
```
Input:
  [0] "image"   shape [1,1,H,W]   float32  (normalized [0,1])

Outputs:
  [0] "keypoints"   shape [1,N,2]    int64     (pixel coordinates)
  [1] "scores"      shape [1,N]      float32   (confidence scores)
  [2] "descriptors" shape [1,256,N]  float32   (256-D L2-normalized)
```

**Preprocessing:**
- Image resize to longest edge = `dl_image_size` (512px default)
- Grayscale conversion (if needed)
- Normalization to [0,1] float32
- Maintains aspect ratio with padding if needed

### 3.2 LightGlue Matcher

**Class:** `okvis::dl::DLFeatureMatcher`
**File:** `okvis_frontend/include/okvis/dl_features/DLFeatureExtractor.hpp`

**Fused ONNX Model I/O:**
```
Inputs:
  [0] "kpts0"   [1,N,2]   float32  (normalized keypoints of frame 0)
  [1] "kpts1"   [1,M,2]   float32  (normalized keypoints of frame 1)
  [2] "desc0"   [1,N,256] float32  (descriptors of frame 0)
  [3] "desc1"   [1,M,256] float32  (descriptors of frame 1)

Outputs:
  [0] "matches0"        [1,K,2]   int32/int64  ((idx0, idx1) pairs)
  [1] "matching_scores0" [1,K]    float32      (confidence scores)
```

**Keypoint Normalization:**
```cpp
kpt_norm = (kpt - [w/2, h/2]) / max(w, h) * 2
```
Range: [-2, 2] (normalized image coordinates)

---

## 4. Build Configuration

### 4.1 CMake Options

**File:** `CMakeLists.txt` (root)

```cmake
option(USE_NN "Use keypoint classification as part of okvis, requires torch" ON)
option(USE_GPU "Use keypoint classification with GPU inference, requires torch with Cuda" OFF)
```

**File:** `okvis_frontend/CMakeLists.txt`

```cmake
# Deep-learning feature extraction (SuperPoint + LightGlue)
if(USE_DL_FEATURES)
  find_path(OnnxRuntime_INCLUDE_DIR
    NAMES onnxruntime_cxx_api.h
    PATHS "${OnnxRuntime_DIR}/include"
    REQUIRED)
  find_library(OnnxRuntime_LIBRARY
    NAMES onnxruntime
    PATHS "${OnnxRuntime_DIR}/lib"
    REQUIRED)
endif()
```

### 4.2 Current ONNX Runtime Setup

**Location:** `/home/lhk/ThirdParty/onnxruntime-linux-x64-gpu-1.16.3`

**Available Models:** `/home/lhk/workspace/OKVIS2-X/weight/`
- `superpoint.onnx` (5.1 MB) - Feature extractor
- `superpoint_lightglue_fused_cpu.onnx` (44 MB) - Fused matcher (CPU version)
- `superpoint_lightglue_fused.onnx` (44 MB) - Fused matcher (GPU version)
- `disk.onnx` (4.3 MB) - Alternative extractor (DISK features)
- `disk_lightglue_fused.onnx` (44 MB) - DISK+LightGlue matcher

### 4.3 Compilation Flags

When `USE_DL_FEATURES` is enabled:
```cmake
target_compile_definitions(okvis_frontend PUBLIC OKVIS_USE_DL_FEATURES)
if(USE_GPU)
  target_compile_definitions(okvis_frontend PUBLIC OKVIS_USE_GPU)
endif()
```

The code conditionally compiles based on `#ifdef OKVIS_USE_DL_FEATURES`

---

## 5. Feature Parameters (okvis::FrontendParameters)

**File:** `okvis_common/include/okvis/Parameters.hpp`

```cpp
struct FrontendParameters {
  // Traditional BRISK parameters
  double detection_threshold;           // Uniformity radius [pixels]
  double absolute_threshold;            // Harris corner threshold (noise floor)
  double matching_threshold;            // BRISK Hamming distance threshold
  int octaves;                          // Multi-scale octaves (0 = single-scale)
  int max_num_keypoints;                // Max keypoints per image
  double keyframe_overlap;              // Keyframe insertion threshold
  bool use_cnn;                         // CNN-based filtering (sky/dynamic content)
  bool parallelise_detection;           // Parallel detect & describe
  int num_matching_threads;             // Thread count for matching

  // Deep-learning feature parameters
  bool use_dl_features = false;         // Enable SuperPoint + LightGlue
  std::string dl_extractor_type = "";   // "superpoint" or "disk"
  std::string dl_extractor_path = "";   // Path to extractor ONNX
  std::string dl_matcher_path = "";     // Path to LightGlue ONNX
  double dl_match_threshold = 0.7;      // LightGlue confidence [0,1]
  int dl_image_size = 512;              // Resize longest edge
  bool dl_use_gpu = false;              // GPU inference flag
};
```

---

## 6. Initialization Sequence

**File:** `okvis_frontend/src/Frontend.cpp`

### 6.1 Constructor
```cpp
Frontend::Frontend(size_t numCameras, std::string dBowVocDir)
    : ...
      briskDetectionThreshold_(40.0),
      briskMatchingThreshold_(60.0),
      ...
{
  initialiseBriskFeatureDetectors();  // Always initializes BRISK
  // Load CNN networks (if OKVIS_USE_NN enabled)
}
```

### 6.2 DL Features Initialization
```cpp
void Frontend::initialiseDlFeatures(const okvis::ViParameters& params) {
  if (dlFeaturesInitialised_) return;
  const auto& fp = params.frontend;
  if (!fp.use_dl_features) return;
  
  if (fp.dl_extractor_path.empty()) { ... }
  
  // Create ONNX Runtime sessions
  dlExtractor_ = std::make_unique<dl::DLFeatureExtractor>(
      fp.dl_extractor_path,
      fp.dl_use_gpu,
      0,  // device_id
      fp.dl_image_size);
  
  dlMatcher_ = std::make_unique<dl::DLFeatureMatcher>(
      fp.dl_matcher_path,
      fp.dl_match_threshold,
      fp.dl_use_gpu,
      0);
  
  dlFeaturesInitialised_ = true;
}
```

Called from `dataAssociationAndInitialization()` on first use.

---

## 7. Matching Operations

### 7.1 Stereo Matching (Within MultiFrame)
**File:** `okvis_frontend/src/Frontend.cpp` - Function `matchStereo()`

Uses LightGlue when available:
```cpp
if (dlModeLC && oldFrameDL && dlMatcher_) {
  dlMatcher_->match(newKpts, oldKpts, newDesc, oldDesc,
                    newImg.rows, newImg.cols,
                    oldImg.rows, oldImg.cols,
                    stereoMatches, matchScores);
}
```

### 7.2 Map Matching (Landmark Association)
**File:** `okvis_frontend/src/Frontend.cpp` - Function `matchToMap()`

Two matching strategies based on descriptor type:
- **DL path:** Uses LightGlue on landmark descriptors
- **BRISK path:** Brute-force Hamming distance matching

### 7.3 Loop Closure Detection
**File:** `okvis_frontend/src/Frontend.cpp` - Function `verifyRecognisedPlace()`

Uses DBoW (Bag-of-Words) with BRISK vocabularies for place recognition,
followed by geometric verification with either DL or BRISK matching.

---

## 8. Descriptor Storage and Format

The Frame/MultiFrame structure stores descriptors as OpenCV cv::Mat:

```cpp
// BRISK: CV_8U (uint8)
// DL (SuperPoint/DISK): CV_32F (float32)

inline bool isDLDescriptor(const MultiFramePtr& mf, size_t cam) {
  if (mf->numKeypoints(cam) == 0) return false;
  return mf->descriptorType(cam) == CV_32F;
}
```

This allows seamless coexistence of both descriptor types in the same frame.

---

## 9. GPU Support

### 9.1 ONNX Runtime CUDA Provider

When `dl_use_gpu: true` and built with `USE_GPU=ON`:

```cpp
OrtCUDAProviderOptions cudaOptions{};
cudaOptions.device_id = deviceId;
sessionOptions_.AppendExecutionProvider_CUDA(cudaOptions);
```

**Requirements:**
- CUDA 11+ and cuDNN 8
- ONNX Runtime built with CUDA support
- GPU model weights (superpoint_lightglue_fused.onnx)

### 9.2 Thread Safety

DL inference is protected by mutex:
```cpp
std::mutex dlMutex_;  // Protects dlExtractor_ / dlMatcher_

// Usage:
{
  std::lock_guard<std::mutex> dlLock(dlMutex_);
  dlExtractor_->extract(img, pts, scores, descs);
}
```

---

## 10. Key Findings Summary

| Aspect | Answer |
|--------|--------|
| **SuperPoint+LightGlue Implemented?** | ✅ YES - Full implementation via ONNX Runtime |
| **Currently Enabled?** | ✅ YES - `use_dl_features: true` in EuRoC config |
| **Primary Frontend** | **SuperPoint** (when enabled) |
| **Fallback Frontend** | BRISK (traditional handcrafted) |
| **Matching Method (DL)** | **LightGlue** (fused ONNX model) |
| **Matching Method (BRISK)** | Brute-force Hamming distance |
| **Descriptor Dimension** | 256 (SuperPoint), 128 (DISK option) |
| **Model Format** | ONNX Runtime |
| **Backend** | CPU or CUDA (configurable) |
| **Model Weights Available** | ✅ YES - 5 ONNX models in `/home/lhk/workspace/OKVIS2-X/weight/` |
| **Build Dependencies** | ONNX Runtime 1.16.3 (GPU version) |

---

## 11. Alternative Detectors Available

The system supports switching detectors via configuration:

1. **SuperPoint** (currently configured)
   - 256-D float descriptors
   - Trained on multiple datasets
   - 5.1 MB model size

2. **DISK** (alternative, not currently configured)
   - 128-D float descriptors
   - Disk-based keypoint detection
   - 4.3 MB model size
   - Set `dl_extractor_type: disk` to use

Both support LightGlue for matching (via fused models).

---

## 12. Code Organization

```
okvis_frontend/
├── include/
│   ├── okvis/
│   │   ├── Frontend.hpp                    # Main frontend class
│   │   └── dl_features/
│   │       └── DLFeatureExtractor.hpp      # SuperPoint + LightGlue
│   └── (BRISK, DBoW2, OpenGV includes)
├── src/
│   ├── Frontend.cpp                        # Core implementation
│   ├── dl_features/
│   │   └── DLFeatureExtractor.cpp          # ONNX inference logic
│   └── (Feature detector, matcher, RANSAC)
└── CMakeLists.txt                          # Build configuration
```

---

## 13. Configuration Loading

**File:** `okvis_common/src/Parameters.cpp` (not shown, but handles YAML parsing)

The YAML config file is parsed to populate `okvis::ViParameters::frontend`:
- `use_dl_features` → `fp.use_dl_features`
- `dl_extractor_path` → `fp.dl_extractor_path`
- `dl_matcher_path` → `fp.dl_matcher_path`
- etc.

---

## Conclusion

OKVIS2-X implements **a production-ready dual-path feature extraction and matching system** with:

1. **Primary Path:** SuperPoint (ONNX) for feature detection + LightGlue (ONNX) for matching
2. **Fallback Path:** BRISK for traditional descriptor-based SLAM
3. **Runtime Adaptation:** Automatically chooses matching strategy based on descriptor type
4. **GPU Support:** Optional CUDA acceleration for inference
5. **Configuration-Driven:** Easy enable/disable and parameter tuning via YAML configs

The implementation demonstrates modern best practices in visual SLAM with deep-learning integration while maintaining backward compatibility with traditional handcrafted features.
