# SuperVINS Integration: Practical Code Examples

## 1. Basic Extraction Example

```cpp
#include "extractor_matcher_dpl.h"
#include <opencv2/opencv.hpp>

// Initialize extractor
Extractor_DPL extractor;
extractor.initialize("path/to/superpoint.onnx", SUPERPOINT);

// Load and extract from image
cv::Mat image = cv::imread("image.jpg", cv::IMREAD_GRAYSCALE);
image.convertTo(image, CV_32F, 1.0 / 255.0);  // Normalize to [0, 1]

// Preprocess
float scale = 1.0f;
cv::Mat preprocessed = extractor.pre_process(image, scale);
std::cout << "Image was resized by factor: " << scale << std::endl;

// Extract features
auto [keypoints, descriptors] = extractor.extract_featurepoints(preprocessed);

// Rescale keypoints to original image coordinates
for (auto& kpt : keypoints)
{
    kpt.x = (kpt.x + 0.5) / scale - 0.5;
    kpt.y = (kpt.y + 0.5) / scale - 0.5;
}

std::cout << "Extracted " << keypoints.size() << " keypoints" << std::endl;

// Access descriptor for keypoint i
int descriptor_size = 256;  // For SuperPoint
for (int i = 0; i < keypoints.size(); i++)
{
    float* desc_i = descriptors + i * descriptor_size;
    
    // Do something with desc_i (pointer to 256 floats)
    float first_dim = desc_i[0];
    float last_dim = desc_i[255];
}
```

## 2. Feature Matching Example

```cpp
#include "extractor_matcher_dpl.h"
#include <opencv2/opencv.hpp>

// Initialize matcher
Matcher_DPL matcher;
matcher.initialize("path/to/lightglue.onnx", SUPERPOINT, 0.5);

// Assume we have:
// - prev_keypoints, cur_keypoints: vector<cv::Point2f> in original image coords
// - prev_descriptors, cur_descriptors: float* buffers (N_prev*256, N_cur*256)
// - prev_img, cur_img: cv::Mat original images

// Normalize keypoints for LightGlue
vector<cv::Point2f> prev_kpts_norm = matcher.pre_process(
    prev_keypoints, prev_img.rows, prev_img.cols
);
vector<cv::Point2f> cur_kpts_norm = matcher.pre_process(
    cur_keypoints, cur_img.rows, cur_img.cols
);

// Run LightGlue matching
vector<pair<int, int>> lightglue_matches = matcher.match_featurepoints(
    prev_kpts_norm, cur_kpts_norm,
    prev_descriptors, cur_descriptors
);

std::cout << "LightGlue found " << lightglue_matches.size() << " matches" << std::endl;

// RANSAC geometric verification
vector<cv::Point2f> points1, points2;
for (const auto& [i, j] : lightglue_matches)
{
    points1.push_back(prev_kpts_norm[i]);
    points2.push_back(cur_kpts_norm[j]);
}

vector<uchar> inliers;
cv::findFundamentalMat(
    points1, points2,
    cv::FM_RANSAC,
    0.05,   // Pixel error threshold
    0.99    // Confidence
);

// Filter to inliers
vector<pair<int, int>> verified_matches;
for (int i = 0; i < lightglue_matches.size(); i++)
{
    if (inliers[i])
    {
        verified_matches.push_back(lightglue_matches[i]);
    }
}

std::cout << "After RANSAC: " << verified_matches.size() << " matches" << std::endl;
```

## 3. Frame-to-Frame Tracking Integration

```cpp
#include "feature_tracker_dpl.h"
#include <opencv2/opencv.hpp>

// Global tracker instance
FeatureTrackerDPL tracker;

// Initialize (called once at startup)
string extractor_path = "path/to/superpoint.onnx";
string matcher_path = "path/to/lightglue.onnx";
tracker.initializeExtractorMatcher(
    SUPERPOINT,           // extractor type
    extractor_path,       // weight path
    matcher_path,         // weight path
    0.5                   // match threshold
);

// In main loop
void processFrame(const cv::Mat& frame, double timestamp)
{
    // FeatureTrackerDPL handles extraction + matching internally
    auto tracked_features = tracker.trackImage_dpl(
        timestamp,
        frame          // Left image
        // Optional: right image for stereo
    );
    
    // tracked_features format:
    // map<int, vector<pair<int, Matrix<double, 7, 1>>>>
    //   key: camera ID
    //   value: [(feature_id, [x, y, vx, vy, ...])]
    
    for (auto& [cam_id, features] : tracked_features)
    {
        for (auto& [feat_id, observation] : features)
        {
            double x = observation(0);
            double y = observation(1);
            double vx = observation(2);
            double vy = observation(3);
            
            // Pass to VIO backend
            // ...
        }
    }
}
```

## 4. Loop Closure with Keyframes

```cpp
#include "keyframe.h"
#include <vector>
#include <eigen3/Eigen/Dense>

// When creating a new keyframe from VIO measurements
KeyFrame* createKeyFrame(
    double timestamp,
    int index,
    Eigen::Vector3d T_w_i,
    Eigen::Matrix3d R_w_i,
    const cv::Mat& image,
    vector<cv::Point3f> pts_3d,              // Triangulated 3D points
    vector<cv::Point2f> pts_2d_uv,           // Observed 2D points
    vector<cv::Point2f> pts_2d_norm,         // Normalized 2D points
    vector<double> point_ids,
    int sequence,
    const cv::Mat& superpoint_descriptors)   // NEW: SuperPoint [N, 256]
{
    KeyFrame* kf = new KeyFrame(
        timestamp, index,
        T_w_i, R_w_i,
        image,
        pts_3d, pts_2d_uv, pts_2d_norm,
        point_ids,
        sequence,
        superpoint_descriptors  // Store SuperPoint descriptors
    );
    
    return kf;
}

// Loop closure detection with geometry verification
void checkLoopClosure(KeyFrame* current_kf, KeyFrame* candidate_kf)
{
    // findConnection handles:
    // 1. BRIEF descriptor matching (fast screening)
    // 2. PnP RANSAC verification
    // 3. Pose constraint estimation
    
    if (current_kf->findConnection(candidate_kf))
    {
        // Loop detected!
        cout << "Loop found between frame " << current_kf->index
             << " and frame " << candidate_kf->index << endl;
        
        // Get the constraint
        Eigen::Matrix<double, 8, 1> loop_info = current_kf->loop_info;
        
        Eigen::Vector3d relative_t(loop_info(0), loop_info(1), loop_info(2));
        Eigen::Quaterniond relative_q(
            loop_info(3), loop_info(4), loop_info(5), loop_info(6)
        );
        double relative_yaw = loop_info(7);
        
        cout << "Relative translation: " << relative_t.transpose() << endl;
        cout << "Relative yaw: " << relative_yaw << " deg" << endl;
        
        // Pass to pose graph optimization
        // ...
    }
}
```

## 5. Descriptor Buffer Management

```cpp
#include <vector>

// Proper way to manage descriptor buffers
class FeatureBuffer
{
private:
    struct Feature
    {
        cv::Point2f keypoint;
        std::vector<float> descriptor;  // 256 elements for SuperPoint
    };
    
    std::vector<Feature> features;
    int descriptor_size;
    
public:
    FeatureBuffer(int desc_size = 256) : descriptor_size(desc_size) {}
    
    // Store extracted features
    void addFeatures(
        const std::vector<cv::Point2f>& keypoints,
        float* descriptors_ptr,
        int num_features)
    {
        features.clear();
        for (int i = 0; i < num_features; i++)
        {
            Feature f;
            f.keypoint = keypoints[i];
            
            // Copy descriptor from raw buffer
            f.descriptor.resize(descriptor_size);
            std::copy(
                descriptors_ptr + i * descriptor_size,
                descriptors_ptr + (i + 1) * descriptor_size,
                f.descriptor.begin()
            );
            
            features.push_back(f);
        }
    }
    
    // Get for matching (reconstruct buffer)
    float* getDescriptorBuffer()
    {
        // Allocate buffer
        float* buffer = new float[features.size() * descriptor_size];
        
        // Flatten
        for (size_t i = 0; i < features.size(); i++)
        {
            std::copy(
                features[i].descriptor.begin(),
                features[i].descriptor.end(),
                buffer + i * descriptor_size
            );
        }
        
        return buffer;  // Caller must delete[]
    }
    
    const std::vector<cv::Point2f>& getKeypoints() const
    {
        // Return keypoints as vector
        static std::vector<cv::Point2f> kpts;
        kpts.clear();
        for (const auto& f : features)
        {
            kpts.push_back(f.keypoint);
        }
        return kpts;
    }
    
    size_t size() const { return features.size(); }
};

// Usage
FeatureBuffer prev_features(256);
FeatureBuffer cur_features(256);

// After extraction
prev_features.addFeatures(prev_kpts, prev_desc_buffer, num_prev);
cur_features.addFeatures(cur_kpts, cur_desc_buffer, num_cur);

// Before matching
float* prev_desc = prev_features.getDescriptorBuffer();
float* cur_desc = cur_features.getDescriptorBuffer();

auto matches = matcher.match_featurepoints(
    prev_features.getKeypoints(),
    cur_features.getKeypoints(),
    prev_desc, cur_desc
);

delete[] prev_desc;
delete[] cur_desc;
```

## 6. Coordinate Space Conversions

```cpp
#include <opencv2/opencv.hpp>
#include <cmath>

// Pixel coordinates (original image)
float px_original = 640.0f;
float py_original = 480.0f;

// Step 1: Original → Preprocessed (512x512)
float original_width = 1280.0f;
float original_height = 960.0f;
float max_dim = std::max(original_width, original_height);
float scale = 512.0f / max_dim;  // ≈ 0.533

float px_preprocessed = px_original * scale;     // ≈ 341.1
float py_preprocessed = py_original * scale;     // ≈ 256.3

// Step 2: Preprocessed → Normalized [-1, 1]
float center_x = 512.0f / 2;  // 256
float center_y = 512.0f / 2;  // 256
float norm_scale = 512.0f / 2;  // 256

float px_normalized = (px_preprocessed - center_x) / norm_scale;  // ≈ 0.334
float py_normalized = (py_preprocessed - center_y) / norm_scale;  // ≈ 0.001

// Reverse: Normalized [-1, 1] → Preprocessed
px_preprocessed = px_normalized * norm_scale + center_x;  // ≈ 341.1
py_preprocessed = py_normalized * norm_scale + center_y;  // ≈ 256.3

// Reverse: Preprocessed → Original
px_original = (px_preprocessed + 0.5) / scale - 0.5;      // ≈ 640.0
py_original = (py_preprocessed + 0.5) / scale - 0.5;      // ≈ 480.0

// ℹ️ The "+0.5" and "-0.5" account for pixel center offset
```

## 7. Error Handling

```cpp
#include "extractor_matcher_dpl.h"
#include <iostream>
#include <stdexcept>

try
{
    // Initialize with error checking
    Extractor_DPL extractor;
    
    std::string model_path = "superpoint.onnx";
    if (model_path.empty())
    {
        throw std::runtime_error("Model path not specified");
    }
    
    extractor.initialize(model_path, SUPERPOINT);
    
    // Load image
    cv::Mat image = cv::imread("test.jpg", cv::IMREAD_GRAYSCALE);
    if (image.empty())
    {
        throw std::runtime_error("Failed to load image");
    }
    
    // Preprocess
    float scale = 1.0f;
    cv::Mat preprocessed = extractor.pre_process(image, scale);
    
    // Extract
    auto [keypoints, descriptors] = extractor.extract_featurepoints(preprocessed);
    
    if (keypoints.empty())
    {
        std::cerr << "Warning: No keypoints extracted" << std::endl;
        return;
    }
    
    std::cout << "Successfully extracted " << keypoints.size() << " keypoints"
              << std::endl;
}
catch (const std::exception& e)
{
    std::cerr << "Error: " << e.what() << std::endl;
    // Handle error appropriately
}
```

## 8. Memory-Efficient Streaming

```cpp
#include "feature_tracker_dpl.h"
#include <deque>

class StreamingFeatureTracker
{
private:
    FeatureTrackerDPL tracker;
    std::deque<cv::Mat> image_buffer;
    static const int BUFFER_SIZE = 3;
    
public:
    void initialize(const std::string& extractor_path,
                   const std::string& matcher_path)
    {
        tracker.initializeExtractorMatcher(
            SUPERPOINT,
            const_cast<std::string&>(extractor_path),
            const_cast<std::string&>(matcher_path),
            0.5
        );
    }
    
    void processStreamingFrame(const cv::Mat& frame, double timestamp)
    {
        // Keep rolling buffer of images for GPU memory efficiency
        if (image_buffer.size() >= BUFFER_SIZE)
        {
            image_buffer.pop_front();
        }
        image_buffer.push_back(frame.clone());
        
        // Process current frame
        auto features = tracker.trackImage_dpl(timestamp, frame);
        
        // Features already extracted and matched
        // Memory is freed after trackImage_dpl returns
    }
};

// Usage
StreamingFeatureTracker tracker;
tracker.initialize("superpoint.onnx", "lightglue.onnx");

// Process frame stream
for (int i = 0; i < 1000; i++)
{
    cv::Mat frame = getNextFrame();
    double ts = getTimestamp();
    tracker.processStreamingFrame(frame, ts);
}
```

---

## Key Takeaways for Integration

1. **Preprocessing is critical**: Images must be resized to 512x512 and normalized to [0,1]
2. **Keypoint coordinates**: Track which space you're in (original, preprocessed, or normalized)
3. **Descriptor buffers**: Store as vectors or persistent arrays for subsequent matching
4. **RANSAC is essential**: LightGlue alone isn't sufficient for feature matching
5. **Memory management**: Be careful with float* buffers from ONNX tensors
6. **GPU acceleration**: Enable CUDA for real-time performance
7. **Error handling**: Validate model paths and image loading

