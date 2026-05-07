# SuperVINS: SuperPoint + LightGlue - Quick Reference

## Architecture Overview

```
Image Input
    ↓
[Extractor_DPL] → SuperPoint ONNX Model → Keypoints + Descriptors
    ↓
[Matcher_DPL]   → LightGlue ONNX Model  → Matches (indices pairs)
    ↓
[RANSAC]        → Fundamental Matrix    → Geometric Verification
    ↓
Output: Tracked Features or Loop Closure Constraints
```

---

## Class Hierarchy

### Core Classes (extractor_matcher_dpl.h/cpp)

#### `Extractor_DPL`
```cpp
// Initialization
void initialize(std::string extractorPath, int extractor_type_)
  - Loads ONNX SuperPoint model
  - Configures CUDA GPU
  - Supports: SUPERPOINT (256-dim) or DISK (128-dim)

// API
cv::Mat pre_process(const cv::Mat &Image, float &scale)
  - Resize to 512x512 (or 1024x2048)
  - Normalize to [0, 1]
  - RGB→Grayscale for SuperPoint

std::pair<std::vector<cv::Point2f>, float *> extract_featurepoints(const cv::Mat &image)
  - Input: Preprocessed CV_32F image
  - Returns:
    - vector<Point2f>: N keypoints (in preprocessed image coords)
    - float*: Raw descriptor buffer (N * descriptor_size floats)
```

**Output Format:**
```
Keypoints: cv::Point2f (x, y) - pixel coordinates in 512x512 resized image
Descriptors: float* buffer
  - Layout: [kpt0_dim0, kpt0_dim1, ..., kpt0_dim255, kpt1_dim0, ...]
  - For N keypoints: total size = N * 256 floats
  - Accessed as: desc[i * 256 + j] for i-th keypoint, j-th dimension
```

#### `Matcher_DPL`
```cpp
// Initialization
void initialize(std::string matcherPath, int extractor_type_, float matchThresh_)
  - Loads ONNX LightGlue model
  - Sets match confidence threshold (default 0.5)
  - Must match extractor type (SUPERPOINT or DISK)

// API
std::vector<cv::Point2f> pre_process(std::vector<cv::Point2f> kpts, int h, int w)
  - Normalize keypoints to [-1, 1] range
  - center = (w/2, h/2), scale = max(w,h)/2
  - normalized_kpt = (kpt - center) / scale

std::vector<std::pair<int, int>> match_featurepoints(
    std::vector<cv::Point2f> kpts0,      // Previous frame (normalized)
    std::vector<cv::Point2f> kpts1,      // Current frame (normalized)
    float *desc0,                        // Previous frame descriptors
    float *desc1)                        // Current frame descriptors
  - Returns: vector of (idx_prev, idx_curr) pairs
  - Only includes matches with confidence > matchThresh
```

**Input Format:**
```
kpts0, kpts1: Normalized to [-1, 1]
desc0, desc1: Raw float buffers
  - Input shape expected: [1, N, descriptor_size]
  - For SuperPoint: descriptor_size = 256
  - For DISK: descriptor_size = 128
```

---

### Integration Layer (feature_tracker_dpl.h/cpp)

#### `FeatureTrackerDPL`
```cpp
// Initialization
void initializeExtractorMatcher(
    int extractor_type_, 
    string &extractor_weight_path,       // Path to ONNX file
    string &matcher_weight_path,         // Path to ONNX file
    float matcher_threshold = 0.5)

// Feature Storage
struct FeaturePoint {
    cv::Point2f keypoint;                // Pixel coordinates
    std::vector<float> descriptor;       // 256-dim vector (SuperPoint)
};

std::vector<pair<cv::Point2f, vector<float>>> cur_dplpts_descriptors;
std::vector<pair<cv::Point2f, vector<float>>> prev_dplpts_descriptors;

// Main tracking loop
map<int, vector<pair<int, Eigen::Matrix<double, 7, 1>>>> 
trackImage_dpl(double _cur_time, const cv::Mat &_img, const cv::Mat &_img1 = cv::Mat())
  - Extracts features from current frame
  - Matches with previous frame (LightGlue)
  - Applies RANSAC verification
  - Returns: tracked features with velocities

// Core methods
void extract_features_dpl(
    cv::Mat img, 
    vector<cv::Point2f> &pts,
    vector<pair<cv::Point2f, vector<float>>> &dplpts_descriptors)

void match_features_dpl(
    cv::Mat prev_img_, cv::Mat cur_img_,
    vector<pair<cv::Point2f, vector<float>>> &prev_dplpts_descriptors_,
    vector<pair<cv::Point2f, vector<float>>> &cur_dplpts_descriptors_,
    vector<pair<int, int>> &result_matches,
    double &ransacReprojThreshold)
```

---

## Data Flow: Frame-to-Frame Tracking

```
Current Image (cv::Mat)
    ↓
[Extractor_DPL.pre_process]
    ├─ Resize: original → 512x512
    ├─ Normalize: [0,255] → [0,1]
    └─ For SuperPoint: RGB → Grayscale
    ↓
[Extractor_DPL.extract_featurepoints]
    ├─ ONNX inference
    ├─ Returns: ~400-800 keypoints + 256-dim descriptors
    └─ Keypoints in resized image space
    ↓
Rescale keypoints back to original image coordinates
    ↓
Store: vector<pair<Point2f, vector<float>>> cur_dplpts_descriptors

═══════════════════════════════════════════════

Previous Frame (already stored)
    + Current Frame (newly extracted)
    ↓
[Matcher_DPL.pre_process]
    └─ Normalize keypoints: pixel → [-1, 1]
    ↓
Prepare descriptor arrays:
    ├─ float prev_descriptors[N_prev * 256]
    └─ float cur_descriptors[N_cur * 256]
    ↓
[Matcher_DPL.match_featurepoints]
    ├─ ONNX inference (LightGlue network)
    ├─ Returns: match indices + confidence scores
    └─ Filters by matchThresh (default 0.5)
    ↓
Extract matched points
    ├─ points1: Previous frame coordinates (normalized)
    └─ points2: Current frame coordinates (normalized)
    ↓
[cv::findFundamentalMat with FM_RANSAC]
    ├─ Threshold: ransacReprojThreshold (typically 0.05-0.06)
    ├─ Confidence: 0.99
    └─ Output: inlier mask
    ↓
Keep only inlier matches
    ↓
Output: vector<pair<int, int>> result_matches
    ├─ Each pair: (index_in_prev_frame, index_in_cur_frame)
    └─ Geometrically verified by RANSAC
```

---

## Data Flow: Loop Closure Detection

```
New Keyframe created with:
    ├─ VIO-estimated pose
    ├─ Triangulated 3D points
    ├─ VIO-tracked 2D points
    ├─ BRIEF descriptors (from VIO points)
    └─ **SuperPoint descriptors (NEW)**
    ↓
[Vocabulary BoW lookup (DBoW3)]
    └─ Candidate loop frames identified
    ↓
For each candidate old_kf:
    ├─ [searchByBRIEFDes]
    │   ├─ Match current frame's 2D points with old frame's BRIEF descriptors
    │   ├─ Using Hamming distance threshold (< 80 bits)
    │   └─ Output: ~20-50 initial matches
    │
    └─ [Geometric verification: RANSAC + PnP]
        ├─ Input: 2D matches from above
        ├─ 3D points: triangulated from VIO features
        ├─ [cv::solvePnPRansac]
        │   ├─ RANSAC iterations: 100
        │   ├─ Pixel error threshold: 10.0 / 460.0
        │   ├─ Confidence: 0.99
        │   └─ Output: inlier mask, camera pose (old frame)
        │
        ├─ Reduce to inliers (typically 18+ required by MIN_LOOP_NUM)
        │
        └─ Compute relative pose:
            ├─ relative_t = R_old^T * (T_vio_cur - T_pnp_old)
            ├─ relative_q = R_old^T * R_vio_cur
            └─ relative_yaw = yaw_cur - yaw_old
    ↓
Loop validation:
    ├─ abs(relative_yaw) < 30° ✓
    ├─ relative_t.norm() < 20m ✓
    └─ If both pass → Loop closure detected!
    ↓
Store loop constraint:
    loop_info << [t_x, t_y, t_z, q_w, q_x, q_y, q_z, yaw]
    ↓
[Pose Graph Optimization (4-DoF or 6-DoF)]
    └─ Corrects drift in trajectory
```

---

## Key Constants & Parameters

```cpp
// From parameters.h
enum ExtractorType {
    SUPERPOINT = 0,  // 256-dimensional descriptors
    DISK = 1         // 128-dimensional descriptors
};

// Feature extraction
IMAGE_SIZE_DPL = 512                    // Target resize dimension

// Matching
MATCHER_THRESHOLD = 0.5                 // LightGlue confidence threshold
ransacReprojThreshold = 0.05-0.06       // RANSAC pixel error threshold

// Loop closure
MIN_LOOP_NUM = 18                       // Min matches for valid loop closure
PnP RANSAC iterations = 100
PnP RANSAC confidence = 0.99
Relative pose validation:
  - Yaw diff < 30°
  - Translation < 20m
```

---

## File Structure Quick Map

```
supervins_estimator/src/featureTracker/
├── extractor_matcher_dpl.h/cpp         # Extractor_DPL, Matcher_DPL classes
├── feature_tracker_dpl.h/cpp           # FeatureTrackerDPL integration
├── transform_dpl.h/cpp                 # Helpers: NormalizeImage, NormalizeKeypoints, ResizeImage
├── ort_include/                        # ONNX Runtime C++ API headers
└── (feature_tracker.h/cpp)             # Original tracker (for reference)

supervins_loop_fusion/src/
├── keyframe.h/cpp                      # KeyFrame class with SuperPointDescriptors
├── pose_graph.h/cpp                    # PoseGraph with BoW vocabulary
└── (... loop closure backend ...)

supervins_estimator/src/estimator/
└── parameters.h                        # Configuration constants
```

---

## Memory Layout Examples

### Descriptor Buffer (Extraction)
```
N = 500 keypoints
descriptor_size = 256

Buffer: float desc[500 * 256] = 128,000 floats

Access pattern:
  desc[0..255]     = descriptors for keypoint 0
  desc[256..511]   = descriptors for keypoint 1
  ...
  desc[127744..127999] = descriptors for keypoint 499

For keypoint i, dimension j:
  value = desc[i * 256 + j]
```

### Keypoint Vector
```
N = 500 keypoints in 512x512 preprocessed image

keypoints[0] = cv::Point2f(128.5, 256.3)    // In 512x512 space
keypoints[1] = cv::Point2f(301.2, 10.8)
...

After rescaling to original image:
  original_x = (preprocessed_x + 0.5) / scale - 0.5
  where scale ≈ 512 / max(original_height, original_width)
```

---

## Integration Checklist

When integrating into another codebase:

- [ ] Link against ONNX Runtime library
- [ ] Include ONNX Runtime headers (onnxruntime_cxx_api.h)
- [ ] Have SuperPoint and LightGlue ONNX model files
- [ ] Handle image preprocessing (normalization, resize, channel conversion)
- [ ] Store descriptor buffers persistently for matching
- [ ] Implement RANSAC verification after matching
- [ ] For loop closure: have 3D point structure + camera calibration
- [ ] Consider GPU acceleration setup (CUDA) vs CPU-only

---

## Typical Performance

```
Extraction:  ~30-50ms per frame (512x512 image, GPU)
Matching:    ~10-20ms for 500+ keypoint pairs (GPU)
RANSAC:      ~5-10ms per frame-pair
Total:       ~50-80ms per frame (real-time capable)
```

