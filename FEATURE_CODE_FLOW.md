# OKVIS2-X Feature Detection & Matching - Code Flow Diagram

## Overview

This document traces the exact code paths for feature detection, description, and matching in OKVIS2-X.

---

## Part 1: Initialization

### Step 1: Frontend Constructor

**File:** `okvis_frontend/src/Frontend.cpp:154-178`

```cpp
Frontend::Frontend(size_t numCameras, std::string dBowVocDir)
    : isInitialized_(false),
      numCameras_(numCameras),
      briskDetectionThreshold_(40.0),
      briskMatchingThreshold_(60.0),
      ...
{
  // Step 1: Always initialize BRISK detectors first
  initialiseBriskFeatureDetectors();
  
  // Step 2: Load CNN networks (if OKVIS_USE_NN)
  #ifdef OKVIS_USE_NN
    for (size_t i = 0; i < numCameras_; ++i) {
      networks_[i].reset(new Network(torch::jit::load(dBowVocDir+"/fast-scnn.pt", ...)));
    }
  #endif
}
```

**State after construction:**
- ✓ BRISK detectors initialized
- ✓ BRISK descriptors initialized
- ✓ CNN networks loaded (if OKVIS_USE_NN)
- ✗ DL features NOT initialized yet (lazy initialization)

### Step 2: First Data Association Call

**File:** `okvis_frontend/src/Frontend.cpp:???` (dataAssociationAndInitialization)

When `dataAssociationAndInitialization()` is first called:

```cpp
virtual bool dataAssociationAndInitialization(
    Estimator& estimator,
    const okvis::ViParameters & params,
    std::shared_ptr<okvis::MultiFrame> framesInOut,
    bool kfPrior,
    bool* asKeyframe) override final;
```

This calls:

```cpp
// Inside dataAssociationAndInitialization()
initialiseDlFeatures(params);  // LAZY INITIALIZATION
```

### Step 3: ONNX Runtime Initialization

**File:** `okvis_frontend/src/Frontend.cpp:204-230`

```cpp
void Frontend::initialiseDlFeatures(const okvis::ViParameters& params) {
  #ifdef OKVIS_USE_DL_FEATURES
  
  if (dlFeaturesInitialised_) return;  // Already done
  
  const auto& fp = params.frontend;
  if (!fp.use_dl_features) return;     // Feature disabled
  
  if (fp.dl_extractor_path.empty()) {
    LOG(WARNING) << "[DLFeatures] dl_extractor_path is empty. Skipping init.";
    return;
  }
  
  // Create ONNX Runtime session for SuperPoint
  dlExtractor_ = std::make_unique<dl::DLFeatureExtractor>(
      fp.dl_extractor_path,              // superpoint.onnx
      fp.dl_use_gpu,                     // false (CPU mode)
      0,                                 // device_id
      fp.dl_image_size);                 // 512
  
  // Create ONNX Runtime session for LightGlue (fused)
  dlMatcher_ = std::make_unique<dl::DLFeatureMatcher>(
      fp.dl_matcher_path,                // superpoint_lightglue_fused_cpu.onnx
      fp.dl_match_threshold,             // 0.7
      fp.dl_use_gpu,                     // false
      0);                                // device_id
  
  dlFeaturesInitialised_ = true;
  LOG(INFO) << "[DLFeatures] Initialization complete";
  
  #endif
}
```

**ONNX Session Creation:**

Inside `DLFeatureExtractor::initSession()`:
```cpp
sessionOptions_.SetIntraOpNumThreads(2);
sessionOptions_.SetGraphOptimizationLevel(ORT_ENABLE_EXTENDED);

// CPU path (dl_use_gpu=false):
// (no CUDA provider)

// GPU path (if dl_use_gpu=true):
OrtCUDAProviderOptions cudaOptions{};
cudaOptions.device_id = deviceId;
sessionOptions_.AppendExecutionProvider_CUDA(cudaOptions);

// Load ONNX model
session_ = std::make_unique<Ort::Session>(env_, modelPath.c_str(), sessionOptions_);
```

**State after first data association:**
- ✓ BRISK detectors ready
- ✓ ONNX Runtime sessions created
- ✓ SuperPoint model loaded in memory
- ✓ LightGlue model loaded in memory
- ✓ System ready for feature extraction/matching

---

## Part 2: Feature Detection & Description

### Step 1: detectAndDescribe() Entry

**File:** `okvis_frontend/src/Frontend.cpp:276-330`

```cpp
bool Frontend::detectAndDescribe(
    size_t cameraIndex,
    std::shared_ptr<okvis::MultiFrame> frameOut,
    const okvis::kinematics::Transformation& T_WC,
    const std::vector<cv::KeyPoint>* keypoints)
{
  OKVIS_ASSERT_TRUE(Exception, keypoints == nullptr, 
                    "external keypoints currently not supported")
  
  // ===============================================
  // DECISION POINT 1: DL Features or BRISK?
  // ===============================================
  
  #ifdef OKVIS_USE_DL_FEATURES
  if (dlFeaturesInitialised_) {
    // ========== DL PATH ==========
    goto dl_path;
  }
  #endif
  
  // Fall through to BRISK
  goto brisk_path;
}
```

### Step 2a: DL Path (SuperPoint)

**File:** `okvis_frontend/src/Frontend.cpp:290-330`

```cpp
dl_path:
{
  std::lock_guard<std::mutex> dlLock(dlMutex_);
  
  const cv::Mat& img = frameOut->image(cameraIndex);
  
  std::vector<cv::Point2f> pts;
  std::vector<float>       scores;
  cv::Mat                  descs;
  
  // ========== SUPERPOINT INFERENCE ==========
  bool ok = dlExtractor_->extract(img, pts, scores, descs);
  
  if (!ok || pts.empty()) {
    LOG(WARNING) << "[DLFeatures] cam " << cameraIndex 
                 << ": extraction returned 0 keypoints.";
    goto brisk_path;  // Fallback to BRISK
  }
  
  // ========== CONVERT TO cv::KeyPoint ==========
  {
    const int N = static_cast<int>(pts.size());
    std::vector<cv::KeyPoint> cvKpts;
    cvKpts.reserve(N);
    
    for (int i = 0; i < N; ++i) {
      cv::KeyPoint kp;
      kp.pt       = pts[i];                    // (x, y) in original image
      kp.size     = 12.0f;                     // ~3.8px RANSAC threshold
      kp.angle    = -1.0f;                     // Not oriented
      kp.response = scores[i];                 // SuperPoint confidence [0,1]
      kp.octave   = 0;                         // Single scale
      kp.class_id = -1;
      cvKpts.push_back(kp);
    }
    
    // ========== STORE IN FRAME ==========
    frameOut->resetKeypoints(cameraIndex, cvKpts);
    frameOut->resetDescriptors(cameraIndex, descs);  // CV_32F format
  }
  
  // Precompute backprojections (for triangulation)
  frameOut->computeBackProjections(cameraIndex);
  
  return true;
}
```

### Step 2b: BRISK Path (Fallback)

**File:** `okvis_frontend/src/Frontend.cpp:???`

```cpp
brisk_path:
{
  std::lock_guard<std::mutex> featureDetectorMutex(
      featureDetectorMutexes_[cameraIndex]);
  
  const cv::Mat& img = frameOut->image(cameraIndex);
  
  // ========== BRISK DETECTION ==========
  std::vector<cv::KeyPoint> keypoints;
  cv::Mat descriptors;
  
  featureDetectors_[cameraIndex]->detect(img, keypoints);
  descriptorExtractors_[cameraIndex]->compute(img, keypoints, descriptors);
  
  // ========== STORE IN FRAME ==========
  frameOut->resetKeypoints(cameraIndex, keypoints);
  frameOut->resetDescriptors(cameraIndex, descriptors);  // CV_8U format
  
  frameOut->computeBackProjections(cameraIndex);
  
  return true;
}
```

### SuperPoint Inference Details

**File:** `okvis_frontend/src/dl_features/DLFeatureExtractor.cpp:150-200`

```cpp
bool DLFeatureExtractor::extract(
    const cv::Mat& image,
    std::vector<cv::Point2f>& keypoints,
    std::vector<float>& scores,
    cv::Mat& descriptors)
{
  // ========== PREPROCESSING ==========
  
  // 1. Resize (longest edge = 512)
  double scale;
  cv::Mat resized = resizeImage(image, imageSize_, scale);
  
  // 2. Convert to grayscale if needed
  cv::Mat gray = normalizeImage(resized);
  
  // 3. Prepare ONNX input [1,1,H,W] float32
  float* input_data = gray.ptr<float>(0);
  std::vector<int64_t> input_shape = {1, 1, gray.rows, gray.cols};
  
  // ========== ONNX INFERENCE ==========
  
  Ort::Value input_tensor = Ort::Value::CreateTensor<float>(
      allocator_, input_data, gray.total(), 
      input_shape.data(), input_shape.size());
  
  std::vector<Ort::Value> output_tensors = session_->Run(
      Ort::RunOptions{nullptr},
      inputNamePtrs_.data(), &input_tensor, 1,
      outputNamePtrs_.data(), outputNamePtrs_.size());
  
  // ========== EXTRACT OUTPUTS ==========
  
  // Output 0: keypoints [1,N,2] int64
  auto kpts_tensor = output_tensors[0].GetTensorMutableData<int64_t>();
  int N = output_tensors[0].GetTensorTypeAndShapeInfo().GetShape()[1];
  
  // Output 1: scores [1,N] float32
  auto scores_data = output_tensors[1].GetTensorMutableData<float>();
  
  // Output 2: descriptors [1,256,N] float32
  auto desc_data = output_tensors[2].GetTensorMutableData<float>();
  
  // ========== CONVERT TO OUTPUT FORMAT ==========
  
  keypoints.clear();
  scores.clear();
  
  for (int i = 0; i < N; ++i) {
    float x = static_cast<float>(kpts_tensor[i * 2 + 0]) / scale;
    float y = static_cast<float>(kpts_tensor[i * 2 + 1]) / scale;
    keypoints.emplace_back(x, y);
    scores.push_back(scores_data[i]);
  }
  
  // Descriptors: [N, 256] (transpose from [1, 256, N])
  descriptors = cv::Mat(N, descriptorDim_, CV_32F);
  for (int i = 0; i < N; ++i) {
    for (int d = 0; d < descriptorDim_; ++d) {
      descriptors.at<float>(i, d) = desc_data[d * N + i];
    }
  }
  
  return true;
}
```

**State after detection:**
- SuperPoint: frame has CV_32F descriptors
- BRISK: frame has CV_8U descriptors

---

## Part 3: Feature Matching

### Step 1: Runtime Descriptor Type Detection

**File:** `okvis_frontend/src/Frontend.cpp:110-115`

```cpp
/// @brief Returns true if the current multiframe uses DL (float32) descriptors.
inline bool isDLDescriptor(const MultiFramePtr& mf, size_t cam) {
  if (mf->numKeypoints(cam) == 0) return false;
  // BRISK descriptors stored as CV_8U; DL (SuperPoint/DISK) stored as CV_32F.
  return mf->descriptorType(cam) == CV_32F;
}
```

### Step 2a: Map Matching (Landmark Association)

**File:** `okvis_frontend/src/Frontend.cpp:1614-1900`

```cpp
template<class CAMERA_GEOMETRY>
int Frontend::matchToMap(
    Estimator& estimator,
    const okvis::ViParameters& params,
    const uint64_t currentFrameId,
    const std::set<LandmarkId>* loopClosureLandmarksToUseExclusively)
{
  // For each image
  for (size_t im = 0; im < currentMultiFrame->numFrames(); ++im) {
    const bool dlMode = isDLDescriptor(multiFrame, im);
    
    // Collect landmarks to match
    for (auto& lm : landmarksToMatch) {
      const cv::Mat& currentDesc = lm.descriptors;
      
      for (size_t kp = 0; kp < numKeypoints; ++kp) {
        const cv::Mat& kpDesc = multiFrame->descriptor(im, kp);
        
        // ========== DISTANCE COMPUTATION ==========
        double dist;
        if (dlMode) {
          // DL descriptors: Cosine distance
          dist = dlDescDist(
              currentDesc.ptr<float>(0),
              kpDesc.ptr<float>(0),
              currentDesc.cols);
        } else {
          // BRISK descriptors: Hamming distance
          dist = briskDescDist(
              currentDesc.ptr<uchar>(0),
              kpDesc.ptr<uchar>(0));
        }
        
        // ========== THRESHOLD CHECK ==========
        const double matchThreshold = isDLDescriptor(multiFrame, im)
            ? params.frontend.dl_match_threshold
            : params.frontend.matching_threshold;
        
        if (dist < matchThreshold) {
          // MATCH FOUND
          matches.push_back({lm.id, kp});
        }
      }
    }
  }
}
```

### Step 2b: Distance Functions

**File:** `okvis_frontend/src/Frontend.cpp:95-108`

```cpp
// BRISK Hamming Distance
inline double briskDescDist(const uchar* a, const uchar* b) {
  // Each BRISK descriptor is 48 bytes = 3 * 16 bytes
  return static_cast<double>(brisk::Hamming::PopcntofXORed(a, b, 3));
}

// DL Cosine Distance
inline double dlDescDist(const float* a, const float* b, int D) {
  double dot = 0.0;
  for (int i = 0; i < D; ++i) 
    dot += static_cast<double>(a[i]) * static_cast<double>(b[i]);
  // SuperPoint descriptors are L2-normalised
  // cosine_similarity = dot_product
  // cosine_distance = 1 - similarity
  return 1.0 - dot;  // Range: [0, 2]
}
```

### Step 3: Stereo Matching (LightGlue)

**File:** `okvis_frontend/src/Frontend.cpp:2360-2400`

```cpp
template<class CAMERA_GEOMETRY>
void Frontend::matchStereo(
    Estimator& estimator,
    std::shared_ptr<okvis::MultiFrame> multiFrame,
    const okvis::ViParameters& params,
    bool asKeyframe)
{
  const bool dlModeLC = isDLDescriptor(multiFrame, 0) && 
                        isDLDescriptor(multiFrame, 1);
  const bool oldFrameDL = isDLDescriptor(oldMultiFrame, 0);
  
  // ========== SELECT MATCHER ==========
  if (dlModeLC && oldFrameDL && dlMatcher_) {
    
    const cv::Mat& newImg = multiFrame->image(0);
    const cv::Mat& oldImg = oldMultiFrame->image(0);
    
    const std::vector<cv::Point2f>& newKpts = 
        multiFrame->keypoints(0);
    const std::vector<cv::Point2f>& oldKpts = 
        oldMultiFrame->keypoints(0);
    
    const cv::Mat& newDesc = multiFrame->descriptors(0);
    const cv::Mat& oldDesc = oldMultiFrame->descriptors(0);
    
    std::vector<cv::DMatch> stereoMatches;
    std::vector<float> matchScores;
    
    // ========== LIGHTGLUE INFERENCE ==========
    dlMatcher_->match(
        newKpts, oldKpts,          // Keypoints
        newDesc, oldDesc,          // Descriptors
        newImg.rows, newImg.cols,  // Image dimensions
        oldImg.rows, oldImg.cols,
        stereoMatches,             // Output matches
        matchScores);              // Output scores
    
    // ========== PROCESS MATCHES ==========
    for (size_t i = 0; i < stereoMatches.size(); ++i) {
      const cv::DMatch& m = stereoMatches[i];
      float score = matchScores[i];
      
      if (score >= params.frontend.dl_match_threshold) {
        // Use match for triangulation
        triangulate(m.queryIdx, m.trainIdx);
      }
    }
    
  } else if (dlModeLC || oldFrameDL) {
    // Mixed mode: DL and BRISK - handled separately
  } else {
    // Both BRISK: use traditional matching
  }
}
```

### LightGlue Matcher Inference

**File:** `okvis_frontend/src/dl_features/DLFeatureExtractor.cpp:???`

```cpp
bool DLFeatureMatcher::match(
    const std::vector<cv::Point2f>& kpts0,
    const std::vector<cv::Point2f>& kpts1,
    const cv::Mat& desc0,
    const cv::Mat& desc1,
    int h0, int w0,
    int h1, int w1,
    std::vector<cv::DMatch>& matches,
    std::vector<float>& matchScores)
{
  // ========== NORMALIZE KEYPOINTS ==========
  auto kpts0_norm = normalizeKeypoints(kpts0, h0, w0);
  auto kpts1_norm = normalizeKeypoints(kpts1, h1, w1);
  // Format: kpt_norm = (kpt - [w/2, h/2]) / max(w, h) * 2
  // Range: [-2, 2]
  
  // ========== PREPARE ONNX INPUTS ==========
  
  // Keypoints [1, N, 2]
  std::vector<int64_t> kpts0_shape = {1, (int64_t)kpts0_norm.size(), 2};
  Ort::Value kpts0_tensor = Ort::Value::CreateTensor<float>(...);
  
  // Descriptors [1, N, 256]
  std::vector<int64_t> desc0_shape = {1, (int64_t)desc0.rows, desc0.cols};
  Ort::Value desc0_tensor = Ort::Value::CreateTensor<float>(...);
  
  // (Similar for frame 1)
  
  // ========== ONNX INFERENCE ==========
  std::vector<Ort::Value> output_tensors = session_->Run(
      Ort::RunOptions{nullptr},
      {"kpts0", "kpts1", "desc0", "desc1"},
      {kpts0_tensor, kpts1_tensor, desc0_tensor, desc1_tensor},
      {"matches0", "matching_scores0"});
  
  // ========== EXTRACT OUTPUTS ==========
  
  // Output 0: matches [1, K, 2] int32/int64
  auto matches_data = output_tensors[0].GetTensorMutableData<int32_t>();
  int K = output_tensors[0].GetTensorTypeAndShapeInfo().GetShape()[1];
  
  // Output 1: scores [1, K] float32
  auto scores_data = output_tensors[1].GetTensorMutableData<float>();
  
  // ========== CONVERT TO OUTPUT FORMAT ==========
  matches.clear();
  matchScores.clear();
  
  for (int i = 0; i < K; ++i) {
    int idx0 = matches_data[i * 2 + 0];
    int idx1 = matches_data[i * 2 + 1];
    float score = scores_data[i];
    
    cv::DMatch m;
    m.queryIdx = idx0;
    m.trainIdx = idx1;
    m.distance = 1.0f - score;  // Convert confidence to distance
    
    matches.push_back(m);
    matchScores.push_back(score);
  }
  
  return true;
}
```

---

## Part 4: RANSAC and Pose Estimation

### 3D-2D RANSAC (Pose Initialization)

**File:** `okvis_frontend/src/Frontend.cpp:???` (runRansac3d2d)

```cpp
bool Frontend::runRansac3d2d(
    Estimator &estimator,
    const okvis::cameras::NCameraSystem &nCameraSystem,
    std::shared_ptr<okvis::MultiFrame> currentFrame,
    bool initializePose,
    bool removeOutliers)
{
  // For each camera
  for (size_t im = 0; im < nCameraSystem.numCameras(); ++im) {
    
    // Collect 2D-3D correspondences
    std::vector<cv::Point2f> imagePoints;
    std::vector<cv::Point3f> objectPoints;
    
    for (auto& obs : currentFrame->observations(im)) {
      const LandmarkId lmId = obs.first;
      const KeypointIdentifier kid = obs.second;
      
      // Get 3D position from estimator
      Eigen::Vector4d hp_W = estimator.mapPoint(lmId).p_W;
      
      // Get 2D keypoint
      cv::Point2f kp = currentFrame->keypoint(im, kid.keypointIndex);
      
      imagePoints.push_back(kp);
      objectPoints.push_back({hp_W(0), hp_W(1), hp_W(2)});
    }
    
    // ========== RANSAC EXECUTION ==========
    // Uses OpenGV RANSAC
    opengv::sac::Ransac<opengv::sac_problems::absolute_pose::
        FrameAbsolutePoseSacProblem> ransac;
    
    ransac.sampleSize_ = 4;  // Min for P3P
    ransac.iterations_ = 1000;
    ransac.threshold_ = 3.0;  // pixels
    ransac.probability_ = 0.99;
    
    bool success = ransac.computeModel(...);
    
    // ========== INLIER COLLECTION ==========
    std::vector<int> inliers = ransac.inliers_;
    
    for (size_t i = 0; i < inliers.size(); ++i) {
      if (inliers[i] == 1) {
        // Add as inlier observation
      } else if (removeOutliers) {
        // Remove outlier observation
      }
    }
  }
  
  return true;
}
```

### 2D-2D RANSAC (Stereo Matching)

**File:** `okvis_frontend/src/Frontend.cpp:???` (runRansac2d2d)

```cpp
int Frontend::runRansac2d2d(
    Estimator& estimator,
    const okvis::ViParameters& params,
    uint64_t currentFrameId,
    uint64_t olderFrameId,
    bool initializePose,
    bool removeOutliers,
    bool &rotationOnly)
{
  // Get matched keypoints
  std::vector<cv::Point2f> imagePoints1, imagePoints2;
  // (populate from matches)
  
  // ========== RANSAC EXECUTION ==========
  opengv::sac::Ransac<opengv::sac_problems::relative_pose::
      FrameRelativePoseSacProblem> ransac;
  
  ransac.sampleSize_ = 5;  // 5-point algorithm
  ransac.iterations_ = 1000;
  ransac.threshold_ = 3.0;  // pixels
  
  bool success = ransac.computeModel(...);
  std::vector<int> inliers = ransac.inliers_;
  
  // ========== TRIANGULATION ==========
  int numTriangulated = 0;
  for (size_t i = 0; i < inliers.size(); ++i) {
    if (inliers[i] == 1) {
      // Triangulate to 3D
      Eigen::Vector4d hp_W = stereoTriangulate(...);
      
      // Create new landmark
      LandmarkId newLmId = createLandmark(hp_W);
      numTriangulated++;
    }
  }
  
  return numTriangulated;
}
```

---

## Summary Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRAME ARRIVAL                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │  detectAndDescribe() called      │
        └────────────────┬────────────────┘
                         │
        ┌────────────────▼────────────────┐
        │  DL Features Enabled?            │
        └────┬───────────────────────┬────┘
             │ YES                   │ NO
             │                       │
    ┌────────▼────────┐      ┌───────▼──────────┐
    │  SuperPoint     │      │  BRISK Detection │
    │  ONNX Inference │      │  & Description   │
    │                 │      └────────┬──────────┘
    │ Output:         │               │
    │ - [1,N,2] int64 │      Output: CV_8U
    │ - [1,N] scores  │      BRISK Descriptors
    │ - [1,256,N] desc│
    └────────┬────────┘
             │
       Store in Frame (CV_32F)
             │
    ┌────────▼────────────────────────────┐
    │  detectAndDescribe() Returns         │
    │  Frame has:                          │
    │  - cv::KeyPoint array                │
    │  - cv::Mat descriptors (CV_32F)      │
    └────────┬────────────────────────────┘
             │
    ┌────────▼────────────────────────────┐
    │ dataAssociationAndInitialization()   │
    │ Matches Features with:               │
    │ - Existing Landmarks                 │
    │ - Previous Frames (Stereo)           │
    │ - Loop Closure Frames (DBoW)         │
    └────────┬────────────────────────────┘
             │
        ┌────▼─────┐
        │isDLDesc? │
        └─┬──────┬─┘
     YES │      │ NO
        ┌▼──┐  ┌──▼─┐
        │LG │  │ HM │  LG = LightGlue
        └┬──┘  └──┬─┘  HM = Hamming
         │        │     Distance
    ┌────▼──┐ ┌───▼────────┐
    │Match  │ │  Distance  │
    │Scores │ │  Threshold │
    │       │ │   Check    │
    └────┬──┘ └───────┬────┘
         │            │
    ┌────▼────────────▼────────┐
    │  RANSAC Pose Estimation  │
    │  & Triangulation         │
    └────┬─────────────────────┘
         │
    ┌────▼────────────────────┐
    │  Final Optimization &   │
    │  Keyframe Selection     │
    └────────────────────────┘
```

---

## Thread Safety

**Mutex Protection:**

1. **Feature Detection (BRISK):**
   ```cpp
   std::lock_guard<std::mutex> lock(featureDetectorMutexes_[cameraIndex]);
   ```

2. **DL Inference:**
   ```cpp
   std::lock_guard<std::mutex> dlLock(dlMutex_);
   ```

Both ensure single-camera processing can happen in parallel without race conditions.

---

## Configuration Driven Behavior

| Config Setting | Value | Effect |
|---|---|---|
| `use_dl_features` | `true` | ✓ SuperPoint+LightGlue enabled |
| `dl_extractor_path` | `superpoint.onnx` | ✓ Load specific model |
| `dl_matcher_path` | `superpoint_lightglue_fused_cpu.onnx` | ✓ Use CPU matcher |
| `dl_match_threshold` | `0.7` | ✓ Confidence cutoff for matches |
| `dl_image_size` | `512` | ✓ Resize image to 512px longest edge |
| `dl_use_gpu` | `false` | ✓ CPU inference (no CUDA) |

---

## Profiling Entry Points

To add timing:
1. Wrap `dlExtractor_->extract()` at line 298
2. Wrap `dlMatcher_->match()` at line 2373
3. Compare with BRISK performance at line 330+

Typical timing (EuRoC VGA 752x480):
- SuperPoint: ~30-50ms (CPU)
- LightGlue: ~20-40ms (CPU)
- BRISK: ~5-10ms

