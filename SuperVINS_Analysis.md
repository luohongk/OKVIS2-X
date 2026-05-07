# SuperVINS SuperPoint + LightGlue Integration Analysis

## Overview
SuperVINS integrates **SuperPoint feature extraction** with **LightGlue feature matching** through ONNX Runtime for accelerated inference. The system uses these deep learning-based features for both frame-to-frame tracking and loop closure detection.

---

## 1. Directory Structure

### Main Components:
```
SuperVINS-main/
├── supervins_estimator/           # VIO frontend
│   └── src/featureTracker/
│       ├── extractor_matcher_dpl.h/.cpp    # Core extraction/matching classes
│       ├── feature_tracker_dpl.h/.cpp      # Integration layer
│       ├── transform_dpl.h/.cpp            # Image preprocessing utilities
│       ├── ort_include/                    # ONNX Runtime headers
│       └── ... other files
│
├── supervins_loop_fusion/         # Loop closure backend
│   └── src/
│       ├── keyframe.h/.cpp                 # Keyframe + feature storage
│       ├── pose_graph.h/.cpp               # Pose graph optimization
│       └── ... other files
│
└── camera_models/                 # Camera calibration models
```

---

## 2. SuperPoint Feature Extraction

### Class: `Extractor_DPL`
**File:** `extractor_matcher_dpl.h/cpp`

#### Constructor & Initialization
```cpp
class Extractor_DPL {
public:
    Extractor_DPL(unsigned int _num_threads=1);
    void initialize(std::string extractorPath, int extractor_type_);
    
    // ONNX Runtime session and memory management
    std::unique_ptr<Ort::Session> Session;
    Ort::Env env;
    Ort::SessionOptions session_options;
    
    // Input/Output metadata
    std::vector<char *> InputNodeNames;
    std::vector<std::vector<int64_t>> InputNodeShapes;
    std::vector<char *> OutputNodeNames;
    std::vector<std::vector<int64_t>> OutputNodeShapes;
    
    // Processing
    float scale = 1.0f;
    int extractor_type = 0;  // 0 = SUPERPOINT, 1 = DISK
    unsigned int IMAGE_SIZE_DPL = 512;  // Resize target
};
```

#### Initialization Flow
```cpp
void Extractor_DPL::initialize(std::string extractorPath, int extractor_type_)
{
    // 1. Set up ONNX Runtime environment
    env = Ort::Env(ORT_LOGGING_LEVEL_WARNING, "LightGlueDecoupleOnnxRunner Extractor");
    session_options = Ort::SessionOptions();
    
    // 2. Configure GPU acceleration
    OrtCUDAProviderOptions cuda_options{};
    cuda_options.device_id = 0;
    cuda_options.cudnn_conv_algo_search = OrtCudnnConvAlgoSearchDefault;
    // ... other GPU settings
    
    session_options.AppendExecutionProvider_CUDA(cuda_options);
    session_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_EXTENDED);
    
    // 3. Load ONNX model
    Session = std::make_unique<Ort::Session>(env, extractorPath.c_str(), session_options);
    
    // 4. Inspect model I/O structure
    size_t numInputNodes = Session->GetInputCount();
    // Store InputNodeNames, InputNodeShapes
    size_t numOutputNodes = Session->GetOutputCount();
    // Store OutputNodeNames, OutputNodeShapes
}
```

#### Pre-processing
```cpp
cv::Mat Extractor_DPL::pre_process(const cv::Mat &Image, float &scale)
{
    // 1. Resize to 512x512 (or 1024x2048)
    //    - fn = "max": resize so max dimension becomes IMAGE_SIZE_DPL
    //    - interp = "area": use area interpolation
    cv::Mat resize_img = ResizeImage(Image, IMAGE_SIZE_DPL, scale, "max", "area");
    
    // 2. Normalize to [0, 1]
    cv::Mat resultImage = NormalizeImage(resize_img);
    
    // 3. For SuperPoint: convert RGB → Grayscale (single channel)
    if (extractor_type == SUPERPOINT && tempImage.channels() == 3)
    {
        resultImage = RGB2Grayscale(resultImage);  // Output: 1-channel
    }
    
    return resultImage;  // Returns: CV_32F, normalized
}
```

#### Feature Extraction
```cpp
std::pair<std::vector<cv::Point2f>, float *> Extractor_DPL::extract_featurepoints(
    const cv::Mat &image)
{
    // 1. Set input tensor shape based on extractor type
    if (extractor_type == SUPERPOINT)
    {
        InputNodeShapes[0] = {1, 1, image.height, image.width};  // [batch, channels, H, W]
    }
    else if (extractor_type == DISK)
    {
        InputNodeShapes[0] = {1, 3, image.height, image.width};
    }
    
    // 2. Prepare input tensor with image data
    std::vector<float> srcInputTensorValues(InputTensorSize);
    if (extractor_type == SUPERPOINT)
    {
        srcInputTensorValues.assign(image.begin<float>(), image.end<float>());
    }
    // For DISK: handle 3-channel RGB
    
    // 3. Create ONNX tensor
    std::vector<Ort::Value> input_tensors;
    input_tensors.push_back(Ort::Value::CreateTensor<float>(
        memory_info_handler, srcInputTensorValues.data(), srcInputTensorValues.size(),
        InputNodeShapes[0].data(), InputNodeShapes[0].size()));
    
    // 4. Run inference
    auto output_tensor = Session->Run(
        Ort::RunOptions{nullptr}, 
        InputNodeNames.data(), 
        input_tensors.data(),
        input_tensors.size(), 
        OutputNodeNames.data(), 
        OutputNodeNames.size());
    
    // 5. Post-process results
    return post_process(std::move(output_tensor));
}
```

#### Post-processing
```cpp
std::pair<std::vector<cv::Point2f>, float *> Extractor_DPL::post_process(
    std::vector<Ort::Value> tensor)
{
    // Outputs: [keypoints, scores, descriptors]
    
    // 1. Extract keypoints (int64_t coordinates)
    std::vector<int64_t> kpts_Shape = tensor[0].GetTensorTypeAndShapeInfo().GetShape();
    int64_t *kpts = (int64_t *)tensor[0].GetTensorMutableData<void>();
    
    // 2. Extract confidence scores
    float *scores = (float *)tensor[1].GetTensorMutableData<void>();
    
    // 3. Extract descriptors (float dense vectors)
    //    - SuperPoint: 256-dim descriptors
    //    - DISK: 128-dim descriptors
    float *desc = (float *)tensor[2].GetTensorMutableData<void>();
    
    // 4. Convert keypoints from int64 to cv::Point2f
    std::vector<cv::Point2f> kpts_f;
    for (int i = 0; i < kpts_Shape[1] * 2; i += 2)
    {
        kpts_f.emplace_back(cv::Point2f(kpts[i], kpts[i + 1]));
    }
    
    return {kpts_f, desc};  // Returns: points + raw descriptor buffer
}
```

#### Key Data Structures (Extraction Output)
```
Output: std::pair<std::vector<cv::Point2f>, float *>

First element:  std::vector<cv::Point2f>
  - N keypoints (normalized pixel coordinates in resized image)
  - Each point: (x, y)

Second element: float *
  - Raw buffer with N * descriptor_size floats
  - For SuperPoint: N * 256 floats
  - For DISK: N * 128 floats
  - Memory layout: [desc0_all_128_values, desc1_all_128_values, ...]
  - Accessed via: desc[i * descriptor_size + j] for i-th point, j-th dimension
```

---

## 3. LightGlue Feature Matching

### Class: `Matcher_DPL`
**File:** `extractor_matcher_dpl.h/cpp`

#### Constructor & Initialization
```cpp
class Matcher_DPL {
public:
    Matcher_DPL(unsigned int _num_threads=1);
    void initialize(std::string matcherPath, int extractor_type_, float matchThresh_);
    
    // ONNX Runtime session
    std::unique_ptr<Ort::Session> Session;
    Ort::Env env;
    Ort::SessionOptions session_options;
    
    // I/O metadata
    std::vector<char *> InputNodeNames;
    std::vector<std::vector<int64_t>> InputNodeShapes;
    std::vector<char *> OutputNodeNames;
    std::vector<std::vector<int64_t>> OutputNodeShapes;
    
    // Parameters
    float matchThresh = 0.5;  // Confidence threshold
    int extractor_type = 0;   // Must match extractor type
};
```

#### Pre-processing (Keypoint Normalization)
```cpp
std::vector<cv::Point2f> Matcher_DPL::pre_process(
    std::vector<cv::Point2f> kpts, int h, int w)
{
    return NormalizeKeypoints(kpts, h, w);
    
    // Normalizes to [-1, 1] range:
    // normalized_kpt = (kpt - center) / scale
    // where center = (w/2, h/2), scale = max(w,h)/2
}
```

#### Feature Matching
```cpp
std::vector<std::pair<int, int>> Matcher_DPL::match_featurepoints(
    std::vector<cv::Point2f> kpts0,      // Previous frame keypoints (normalized)
    std::vector<cv::Point2f> kpts1,      // Current frame keypoints (normalized)
    float *desc0,                        // Previous frame descriptors
    float *desc1)                        // Current frame descriptors
{
    // 1. Set up input tensor shapes
    InputNodeShapes[0] = {1, (int64_t)kpts0.size(), 2};        // kpts0: [1, N0, 2]
    InputNodeShapes[1] = {1, (int64_t)kpts1.size(), 2};        // kpts1: [1, N1, 2]
    
    if (extractor_type == SUPERPOINT)
    {
        InputNodeShapes[2] = {1, (int64_t)kpts0.size(), 256};   // desc0: [1, N0, 256]
        InputNodeShapes[3] = {1, (int64_t)kpts1.size(), 256};   // desc1: [1, N1, 256]
    }
    else if (extractor_type == DISK)
    {
        InputNodeShapes[2] = {1, (int64_t)kpts0.size(), 128};   // desc0: [1, N0, 128]
        InputNodeShapes[3] = {1, (int64_t)kpts1.size(), 128};   // desc1: [1, N1, 128]
    }
    
    // 2. Convert keypoints to float arrays [x0, y0, x1, y1, ...]
    float *kpts0_data = new float[kpts0.size() * 2];
    float *kpts1_data = new float[kpts1.size() * 2];
    
    for (size_t i = 0; i < kpts0.size(); ++i)
    {
        kpts0_data[i * 2] = kpts0[i].x;
        kpts0_data[i * 2 + 1] = kpts0[i].y;
    }
    // Same for kpts1
    
    // 3. Create ONNX input tensors
    std::vector<Ort::Value> input_tensors;
    input_tensors.push_back(Ort::Value::CreateTensor<float>(
        memory_info_handler, kpts0_data, kpts0.size() * 2,
        InputNodeShapes[0].data(), InputNodeShapes[0].size()));
    input_tensors.push_back(Ort::Value::CreateTensor<float>(
        memory_info_handler, kpts1_data, kpts1.size() * 2,
        InputNodeShapes[1].data(), InputNodeShapes[1].size()));
    input_tensors.push_back(Ort::Value::CreateTensor<float>(
        memory_info_handler, desc0, kpts0.size() * 256,
        InputNodeShapes[2].data(), InputNodeShapes[2].size()));
    input_tensors.push_back(Ort::Value::CreateTensor<float>(
        memory_info_handler, desc1, kpts1.size() * 256,
        InputNodeShapes[3].data(), InputNodeShapes[3].size()));
    
    // 4. Run matching inference
    auto output_tensor = Session->Run(
        Ort::RunOptions{nullptr}, 
        InputNodeNames.data(), 
        input_tensors.data(),
        input_tensors.size(), 
        OutputNodeNames.data(), 
        OutputNodeNames.size());
    
    // 5. Post-process matches
    return post_process();
}
```

#### Post-processing (Match Filtering)
```cpp
std::vector<std::pair<int, int>> Matcher_DPL::post_process()
{
    // Outputs: [matches, match_scores]
    
    // 1. Extract match indices (int64_t pairs)
    std::vector<int64_t> matches_Shape = outputtensors[0].GetTensorTypeAndShapeInfo().GetShape();
    int64_t *matches = (int64_t *)outputtensors[0].GetTensorMutableData<void>();
    
    // 2. Extract match confidence scores
    float *mscores = (float *)outputtensors[1].GetTensorMutableData<void>();
    
    // 3. Filter matches by confidence threshold
    std::vector<std::pair<int, int>> good_matches;
    for (int i = 0; i < matches_Shape[0]; i++)
    {
        if (mscores[i] > this->matchThresh)  // Default: 0.5
        {
            good_matches.emplace_back(
                std::make_pair(matches[i * 2], matches[i * 2 + 1])
            );
        }
    }
    
    return good_matches;
}
```

#### LightGlue Output Format
```
Match Output: std::vector<std::pair<int, int>>

Each pair: (idx0, idx1)
  - idx0: Index in previous frame keypoints
  - idx1: Index in current frame keypoints
  - Only includes matches with confidence > matchThresh
  - Empty if no matches found
```

---

## 4. Integration: Frame-to-Frame Tracking

### Class: `FeatureTrackerDPL`
**File:** `feature_tracker_dpl.h/cpp`

#### Key Members
```cpp
class FeatureTrackerDPL {
public:
    // Extractors & Matchers
    std::shared_ptr<Extractor_DPL> FeatureExtractorDPL;
    std::shared_ptr<Matcher_DPL> FeatureMatcherDPL;
    
    // Feature storage: vector of (point, descriptor_vector) pairs
    vector<pair<cv::Point2f, vector<float>>> prev_dplpts_descriptors;
    vector<pair<cv::Point2f, vector<float>>> cur_dplpts_descriptors;
    vector<pair<cv::Point2f, vector<float>>> cur_dplpts_right_descriptors;  // For stereo
    
    // Configuration
    int extractor_type = 0;      // SUPERPOINT or DISK
    int descriptor_size = 256;   // 256 for SuperPoint, 128 for DISK
    unsigned int IMAGE_SIZE_DPL = 512;
    
    // Tracking state
    vector<cv::Point2f> cur_pts, prev_pts;
    vector<int> ids;
    vector<int> track_cnt;
    bool hasPrediction;
    vector<cv::Point2f> predict_pts;
};
```

#### Initialization
```cpp
void FeatureTrackerDPL::initializeExtractorMatcher(
    int extractor_type_, 
    string &extractor_weight_path, 
    string &matcher_weight_path, 
    float matcher_threshold = 0.5)
{
    extractor_type = extractor_type_;
    
    // Create and initialize extractor
    FeatureExtractorDPL = std::make_shared<Extractor_DPL>();
    FeatureExtractorDPL->initialize(extractor_weight_path, extractor_type_);
    
    // Create and initialize matcher
    FeatureMatcherDPL = std::make_shared<Matcher_DPL>();
    FeatureMatcherDPL->initialize(matcher_weight_path, extractor_type_, matcher_threshold);
    
    // Set descriptor size
    if (extractor_type_ == SUPERPOINT)
        descriptor_size = SUPERPOINT_SIZE;  // 256
    else if (extractor_type_ == DISK)
        descriptor_size = DISK_SIZE;        // 128
}
```

#### Feature Extraction Integration
```cpp
void FeatureTrackerDPL::extract_features_dpl(
    cv::Mat img, 
    vector<cv::Point2f> &pts,                    // Output: pixel coordinates (original image)
    vector<pair<cv::Point2f, vector<float>>> &dplpts_descriptors)  // Output
{
    // 1. Preprocess image
    cv::Mat im = img.clone();
    cv::Mat im_preprocessed = Extractor_PreProcess(im, FeatureExtractorDPL->scale);
    
    // 2. Extract features
    std::pair<std::vector<cv::Point2f>, float *> result_dplpts_descriptors = 
        FeatureExtractorDPL->extract_featurepoints(im_preprocessed);
    
    // 3. Rescale points back to original image coordinates
    //    Compensation for pre-processing resize
    int n = result_dplpts_descriptors.first.size();
    for (int i = 0; i < n; i++)
    {
        cv::Point2f dplpt = result_dplpts_descriptors.first[i];  // In resized image
        
        // Rescale from preprocessed size back to original
        cv::Point2f pt = cv::Point2f(
            (dplpt.x + 0.5) / FeatureExtractorDPL->scale - 0.5,
            (dplpt.y + 0.5) / FeatureExtractorDPL->scale - 0.5
        );
        
        // Filter points outside image border
        if (!inBorder(pt))
            continue;
        
        // Extract descriptor for this point
        std::vector<float> descriptor(
            result_dplpts_descriptors.second + i * descriptor_size,
            result_dplpts_descriptors.second + (i + 1) * descriptor_size
        );
        
        pts.push_back(pt);
        dplpts_descriptors.push_back(make_pair(dplpt, descriptor));
    }
}
```

#### Feature Matching Integration
```cpp
void FeatureTrackerDPL::match_features_dpl(
    cv::Mat prev_img_,
    cv::Mat cur_img_,
    vector<pair<cv::Point2f, vector<float>>> &prev_dplpts_descriptors_,
    vector<pair<cv::Point2f, vector<float>>> &cur_dplpts_descriptors_,
    vector<pair<int, int>> &result_matches,
    double &ransacReprojThreshold)
{
    // 1. Prepare keypoint and descriptor arrays
    int n_pre = prev_dplpts_descriptors_.size();
    int n_cur = cur_dplpts_descriptors_.size();
    
    vector<cv::Point2f> prev_dplpts, cur_dplpts;
    float prev_descriptors[n_pre * descriptor_size];
    float cur_descriptors[n_cur * descriptor_size];
    
    // Flatten data structures
    for (int i = 0; i < n_pre; i++)
    {
        prev_dplpts.push_back(prev_dplpts_descriptors_[i].first);
        vector<float> desc = prev_dplpts_descriptors_[i].second;
        int idx = i * descriptor_size;
        for (float desc_value : desc)
        {
            prev_descriptors[idx++] = desc_value;
        }
    }
    // Same for current frame
    
    // 2. Normalize keypoints to [-1, 1]
    vector<cv::Point2f> prev_dplpts_normalized = 
        FeatureMatcherDPL->pre_process(prev_dplpts, prev_img_.rows, prev_img_.cols);
    vector<cv::Point2f> cur_dplpts_normalized = 
        FeatureMatcherDPL->pre_process(cur_dplpts, cur_img_.rows, cur_img_.cols);
    
    // 3. Run LightGlue matching
    vector<pair<int, int>> tem_matches = 
        FeatureMatcherDPL->match_featurepoints(
            prev_dplpts_normalized, 
            cur_dplpts_normalized, 
            prev_descriptors, 
            cur_descriptors
        );
    
    // 4. RANSAC geometric verification (Fundamental Matrix)
    vector<cv::Point2f> points1, points2;
    for (const auto &match : tem_matches)
    {
        points1.push_back(prev_dplpts_normalized[match.first]);
        points2.push_back(cur_dplpts_normalized[match.second]);
    }
    
    std::vector<uchar> inliersMask;
    cv::Mat fundamentalMatrix = cv::findFundamentalMat(
        points1, points2, 
        cv::FM_RANSAC, 
        ransacReprojThreshold,  // Typically 0.05-0.06
        0.99                    // Confidence
    );
    
    // 5. Keep only inlier matches
    for (int i = 0; i < inliersMask.size(); ++i)
    {
        if (inliersMask[i])
        {
            result_matches.push_back(tem_matches[i]);
        }
    }
}
```

#### Main Tracking Loop
```cpp
map<int, vector<pair<int, Eigen::Matrix<double, 7, 1>>>> 
FeatureTrackerDPL::trackImage_dpl(double _cur_time, const cv::Mat &_img, const cv::Mat &_img1)
{
    // 1. Extract features from current frame
    extract_features_dpl(cur_img, cur_pts, cur_dplpts_descriptors);
    
    // 2. If previous frame exists, match features
    if (prev_pts.size() > 0)
    {
        vector<pair<int, int>> matches;
        
        if (hasPrediction)
        {
            // Match with motion prediction guidance
            match_with_predictions_dpl(
                prev_img, cur_img, 
                prev_dplpts_descriptors, cur_dplpts_descriptors,
                predict_pts, cur_pts, matches, 
                ransacReprojThreshold
            );
        }
        else
        {
            // Regular matching
            match_features_dpl(
                prev_img, cur_img,
                prev_dplpts_descriptors, cur_dplpts_descriptors,
                matches, 
                ransacReprojThreshold
            );
        }
        
        // 3. Update tracking: keep matched points, add new ones
        for (auto match : matches)
        {
            // Propagate ID and track count
            temp_ids.push_back(ids[match.first]);
            temp_track_cnt.push_back(track_cnt[match.first] + 1);
        }
        
        // 4. Add new unmatched points with new IDs
        for (int i = 0; i < cur_pts.size(); i++)
        {
            if (!cur_matched_indices.count(i))
            {
                temp_track_cnt.push_back(1);
                temp_ids.push_back(n_id++);
            }
        }
    }
    
    // 5. Stereo matching if available
    if (!_img1.empty() && stereo_cam)
    {
        extract_features_dpl(rightImg, cur_right_pts, cur_dplpts_right_descriptors);
        vector<pair<int, int>> right_matches;
        match_features_dpl(
            cur_img, rightImg,
            cur_dplpts_descriptors, cur_dplpts_right_descriptors,
            right_matches, ransacReprojThreshold
        );
    }
}
```

---

## 5. Loop Closure Detection & Matching

### KeyFrame Storage
**File:** `keyframe.h/cpp`

#### Keyframe Structure with SuperPoint
```cpp
class KeyFrame {
public:
    // Pose information
    Eigen::Vector3d vio_T_w_i;      // VIO-estimated pose
    Eigen::Matrix3d vio_R_w_i;
    Eigen::Vector3d T_w_i;          // Final optimized pose
    Eigen::Matrix3d R_w_i;
    
    // Image and keypoints
    cv::Mat image;
    vector<cv::Point3f> point_3d;   // 3D points from triangulation
    vector<cv::Point2f> point_2d_uv;
    vector<cv::Point2f> point_2d_norm;
    vector<double> point_id;
    
    // BRIEF descriptors (fast matching baseline)
    vector<cv::KeyPoint> keypoints;
    vector<cv::KeyPoint> keypoints_norm;
    vector<BRIEF::bitset> brief_descriptors;
    
    // **NEW: SuperPoint Descriptors for LightGlue**
    cv::Mat SuperPointDescriptors;  // Shape: [N, 256] or [N, 128] depending on type
    
    // Loop closure info
    bool has_loop;
    int loop_index;
    Eigen::Matrix<double, 8, 1> loop_info;  // [t_x, t_y, t_z, q_w, q_x, q_y, q_z, yaw]
};
```

#### Keyframe Constructor with SuperPoint Descriptors
```cpp
KeyFrame::KeyFrame(
    double _time_stamp, int _index, 
    Vector3d &_vio_T_w_i, Matrix3d &_vio_R_w_i, 
    cv::Mat &_image,
    vector<cv::Point3f> &_point_3d, 
    vector<cv::Point2f> &_point_2d_uv, 
    vector<cv::Point2f> &_point_2d_norm,
    vector<double> &_point_id, 
    int _sequence,
    cv::Mat descriptors)  // <-- SuperPoint descriptors passed in
{
    // ... standard initialization ...
    
    // Store SuperPoint descriptors
    SuperPointDescriptors = descriptors;  // Shape: [N_keypoints, 256/128]
    
    // Compute BRIEF descriptors for loop detection candidates
    computeWindowBRIEFPoint();  // For VIO-tracked points
    computeBRIEFPoint();        // For full-image FAST detection
}
```

#### Loop Connection Finding with Geometric Verification
```cpp
bool KeyFrame::findConnection(KeyFrame *old_kf)
{
    // 1. Match using BRIEF descriptors first (vocabulary-guided search)
    //    This filters candidates from vocabulary BoW lookup
    searchByBRIEFDes(
        matched_2d_old,      // Output: matched 2D pixel coords in old frame
        matched_2d_old_norm, // Output: normalized coordinates
        status,              // Output: match validity
        old_kf->brief_descriptors, 
        old_kf->keypoints, 
        old_kf->keypoints_norm
    );
    
    // Reduce vectors to valid matches
    reduceVector(matched_2d_cur, status);
    reduceVector(matched_2d_old, status);
    reduceVector(matched_2d_cur_norm, status);
    reduceVector(matched_2d_old_norm, status);
    reduceVector(matched_3d, status);
    reduceVector(matched_id, status);
    
    // 2. RANSAC + PnP verification (Fundamental Matrix + PnP)
    if ((int)matched_2d_cur.size() > MIN_LOOP_NUM)  // MIN_LOOP_NUM = 18
    {
        status.clear();
        
        // Use PnP RANSAC to verify 2D-3D correspondences
        PnPRANSAC(
            matched_2d_old_norm, 
            matched_3d, 
            status,
            PnP_T_old,    // Output: pose of old frame
            PnP_R_old
        );
        
        // Filter by geometric verification result
        reduceVector(matched_2d_cur, status);
        reduceVector(matched_2d_old, status);
        reduceVector(matched_2d_cur_norm, status);
        reduceVector(matched_2d_old_norm, status);
        reduceVector(matched_3d, status);
        reduceVector(matched_id, status);
    }
    
    // 3. Compute relative pose (current w.r.t. old)
    if ((int)matched_2d_cur.size() > MIN_LOOP_NUM)
    {
        relative_t = PnP_R_old.transpose() * (origin_vio_T - PnP_T_old);
        relative_q = PnP_R_old.transpose() * origin_vio_R;
        relative_yaw = Utility::normalizeAngle(
            Utility::R2ypr(origin_vio_R).x() - Utility::R2ypr(PnP_R_old).x()
        );
        
        // 4. Sanity check on relative pose
        if (abs(relative_yaw) < 30.0 && relative_t.norm() < 20.0)
        {
            has_loop = true;
            loop_index = old_kf->index;
            loop_info << relative_t.x(), relative_t.y(), relative_t.z(),
                        relative_q.w(), relative_q.x(), relative_q.y(), relative_q.z(),
                        relative_yaw;
            return true;
        }
    }
    
    return false;
}
```

#### PnP RANSAC Verification
```cpp
void KeyFrame::PnPRANSAC(
    const vector<cv::Point2f> &matched_2d_old_norm,  // Normalized pixel coords in old frame
    const std::vector<cv::Point3f> &matched_3d,      // 3D points in world frame
    std::vector<uchar> &status,
    Eigen::Vector3d &PnP_T_old,                      // Output: old frame pose
    Eigen::Matrix3d &PnP_R_old)
{
    // Setup camera intrinsics (normalized plane projection)
    cv::Mat K = (cv::Mat_<double>(3, 3) << 1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0);
    
    // Initial pose guess (from VIO)
    Matrix3d R_w_c = origin_vio_R * qic;
    Vector3d T_w_c = origin_vio_T + origin_vio_R * tic;
    
    // Run PnP RANSAC
    cv::solvePnPRansac(
        matched_3d,             // 3D points
        matched_2d_old_norm,    // 2D projections (normalized)
        K, cv::Mat(),           // Camera matrix, no distortion
        rvec, t,                // Output rotation/translation vectors
        true,                   // Use extrinsic guess
        100,                    // RANSAC iterations
        10.0 / 460.0,           // Pixel error threshold
        0.99,                   // Confidence
        inliers                 // Output: inlier indices
    );
    
    // Mark inliers in status vector
    for (int i = 0; i < (int)matched_2d_old_norm.size(); i++)
        status.push_back(0);
    
    for (int i = 0; i < inliers.rows; i++)
    {
        int n = inliers.at<int>(i);
        status[n] = 1;
    }
    
    // Convert output to Eigen format
    cv::cv2eigen(R_pnp, R_pnp_eigen);
    cv::cv2eigen(t_pnp, t_pnp_eigen);
    
    // Output: world-to-camera transform
    PnP_R_old = R_w_c_old * qic.transpose();
    PnP_T_old = T_w_c_old - PnP_R_old * tic;
}
```

---

## 6. Key Data Structures Summary

### Extraction Output (SuperPoint)
```cpp
std::pair<std::vector<cv::Point2f>, float *>
├─ First (keypoints):
│  └─ std::vector<cv::Point2f>
│     └─ N cv::Point2f(x, y) in preprocessed image coordinates
│
└─ Second (descriptors):
   └─ float * (raw buffer)
      ├─ Layout: [d0_dim0, d0_dim1, ..., d0_dim255, d1_dim0, ...]
      ├─ Size: N * 256 floats
      └─ Accessed as: desc[i * 256 + j]
```

### Feature Storage (for matching)
```cpp
struct FeatureWithDescriptor {
    cv::Point2f keypoint;           // Pixel coordinates
    std::vector<float> descriptor;  // 256-dimensional vector (SuperPoint)
};

// In FeatureTrackerDPL:
std::vector<pair<cv::Point2f, vector<float>>> cur_dplpts_descriptors;
```

### Matching Output (LightGlue)
```cpp
std::vector<std::pair<int, int>>
├─ Each pair: (idx_prev, idx_curr)
├─ Only includes matches with confidence > matchThresh
└─ Used to establish correspondences between frames
```

### Keyframe Feature Storage
```cpp
// BRIEF-based (fast screening, vocabulary-guided)
vector<cv::KeyPoint> keypoints;
vector<BRIEF::bitset> brief_descriptors;  // ~256 bits each

// SuperPoint-based (accurate matching, potentially future)
cv::Mat SuperPointDescriptors;  // [N, 256] or [N, 128] CV_32F
```

---

## 7. RANSAC and Geometric Verification

### Frame-to-Frame Matching
```cpp
// After LightGlue matching:
cv::Mat fundamentalMatrix = cv::findFundamentalMat(
    points1,                    // Previous frame keypoints (normalized)
    points2,                    // Current frame keypoints (normalized)
    cv::FM_RANSAC,              // Algorithm
    ransacReprojThreshold,      // Pixel error threshold (0.05-0.06 typical)
    0.99,                       // Confidence threshold
    inliersMask                 // Output: inlier mask
);

// Keep only geometrically valid matches
for (int i = 0; i < inliersMask.size(); ++i)
{
    if (inliersMask[i])
    {
        result_matches.push_back(tem_matches[i]);
    }
}
```

### Loop Closure Verification
```
Two-stage verification:
1. Descriptor Matching:
   - BRIEF descriptors from vocabulary lookup
   - Quick screening using Hamming distance
   
2. Geometric Verification:
   - PnP RANSAC on 2D-3D correspondences
   - Validates pose consistency with triangulated points
   - Output: Relative pose (translation + rotation + yaw)
   - Final validation: relative_yaw < 30°, relative_t.norm() < 20m
```

---

## 8. Configuration & Parameters

### From `parameters.h`
```cpp
// Extractor Types
enum ExtractorType {
    SUPERPOINT = 0,  // 256-dim descriptors
    DISK = 1         // 128-dim descriptors
};

enum DescriptorSize {
    SUPERPOINT_SIZE = 256,
    DISK_SIZE = 128
};

// Global parameters (loaded from config file)
extern string extractor_weight_global_path;      // Path to ONNX model
extern string matcher_weight_global_path;        // Path to LightGlue ONNX model
extern double ransacReprojThreshold;             // RANSAC threshold
extern float MATCHER_THRESHOLD;                  // LightGlue confidence threshold
```

### Loop Closure Parameters
```cpp
#define MIN_LOOP_NUM 18  // Minimum matches required for loop closure

// In PnP RANSAC:
- RANSAC iterations: 100
- Pixel error threshold: 10.0 / 460.0
- Confidence: 0.99

// Relative pose sanity checks:
- Yaw difference < 30°
- Translation norm < 20m
```

---

## 9. Integration API Summary

### For Integration into Another Codebase

#### Minimal Usage Example
```cpp
// 1. Initialize
Extractor_DPL extractor;
extractor.initialize("superpoint.onnx", SUPERPOINT);

Matcher_DPL matcher;
matcher.initialize("lightglue.onnx", SUPERPOINT, 0.5);

// 2. Extract features from an image
cv::Mat image = cv::imread("image.jpg", cv::IMREAD_GRAYSCALE);
image.convertTo(image, CV_32F, 1.0 / 255.0);

cv::Mat preprocessed = extractor.pre_process(image, scale);
auto [keypoints, descriptors] = extractor.extract_featurepoints(preprocessed);

// 3. Match features between two images
std::vector<cv::Point2f> kpts0_norm = matcher.pre_process(kpts0, h0, w0);
std::vector<cv::Point2f> kpts1_norm = matcher.pre_process(kpts1, h1, w1);

auto matches = matcher.match_featurepoints(
    kpts0_norm, kpts1_norm, 
    desc0, desc1  // Raw descriptor buffers
);

// 4. Apply geometric verification
std::vector<cv::Point2f> pts0, pts1;
for (auto [i, j] : matches)
{
    pts0.push_back(kpts0_norm[i]);
    pts1.push_back(kpts1_norm[j]);
}

std::vector<uchar> inliers;
cv::findFundamentalMat(pts0, pts1, cv::FM_RANSAC, 0.05, 0.99, inliers);
```

#### Key Input/Output Contracts

**Extractor Input:**
- `cv::Mat` image: CV_32F, single-channel (for SuperPoint), normalized to [0, 1]
- Output: `{vector<Point2f> keypoints, float* descriptor_buffer}`

**Matcher Input:**
- `vector<Point2f>` kpts0, kpts1: Normalized to [-1, 1]
- `float*` desc0, desc1: Raw buffers with (N * descriptor_size) floats
- Output: `vector<pair<int, int>>` matches

**Memory Notes:**
- Extractor outputs own descriptor buffer (owned by ONNX tensor)
- Matcher inputs must persist during session.Run() call
- Keypoint normalization is separate preprocessing step

