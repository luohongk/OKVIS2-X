/**
 * OKVIS2-X - Open Keyframe-based Visual-Inertial SLAM
 *
 * DL feature extraction using SuperPoint or DISK via ONNX Runtime.
 * Adapted from SuperVINS (https://github.com/supervinss/SuperVINS)
 * extractor_matcher_dpl.h / transform_dpl.h
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#pragma once

#ifdef OKVIS_USE_DL_FEATURES

#include <string>
#include <vector>
#include <memory>
#include <utility>

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include <onnxruntime_cxx_api.h>

namespace okvis {
namespace dl {

// ---------------------------------------------------------------------------
// Image pre-processing utilities
// ---------------------------------------------------------------------------

/**
 * @brief Resize an image so that its longest edge equals `size`.
 * @param image        Input image (grayscale or colour, any depth).
 * @param size         Target size for the longest edge (e.g. 512).
 * @param[out] scale   The scale factor applied (new/old).
 * @param mode         "max" (default) – resize by longest edge.
 *                     "min" – resize by shortest edge.
 * @param interpolation OpenCV interpolation flag (default: INTER_AREA).
 * @return Resized image.
 */
cv::Mat resizeImage(const cv::Mat& image,
                    int size,
                    double& scale,
                    const std::string& mode = "max",
                    int interpolation = cv::INTER_AREA);

/**
 * @brief Convert grayscale/BGR image to a float32 tensor normalised to [0,1].
 *        Output is always single-channel float32.
 * @param image  Input image (CV_8U grayscale or BGR).
 * @return CV_32FC1 image with values in [0,1].
 */
cv::Mat normalizeImage(const cv::Mat& image);

/**
 * @brief Normalise keypoint coordinates for LightGlue input.
 *        kpt_norm = (kpt - [w/2, h/2]) / max(w, h) * 2
 * @param kpts   Keypoints in pixel space.
 * @param h      Image height.
 * @param w      Image width.
 * @return Normalised keypoints.
 */
std::vector<cv::Point2f> normalizeKeypoints(const std::vector<cv::Point2f>& kpts,
                                            int h,
                                            int w);

// ---------------------------------------------------------------------------
// SuperPoint / DISK extractor
// ---------------------------------------------------------------------------

/**
 * @brief Deep-learning feature extractor using SuperPoint or DISK (ONNX Runtime).
 *
 * Input  : grayscale image.
 * Outputs: keypoints (pixel coords), scores, 256-dim float descriptors.
 *
 * Expected ONNX model I/O (SuperPoint):
 *   Input  [0]: "image"        shape [1,1,H,W]  float32
 *   Output [0]: "keypoints"    shape [1,N,2]    int64
 *   Output [1]: "scores"       shape [1,N]      float32
 *   Output [2]: "descriptors"  shape [1,256,N]  float32
 */
class DLFeatureExtractor {
 public:
  /**
   * @brief Constructor.
   * @param modelPath    Path to the ONNX model file.
   * @param useCuda      Run inference on GPU (CUDA).
   * @param deviceId     CUDA device id (ignored when useCuda=false).
   * @param imageSize    Resize longest edge to this before inference (e.g. 512).
   */
  DLFeatureExtractor(const std::string& modelPath,
                     bool useCuda = false,
                     int  deviceId = 0,
                     int  imageSize = 512);

  ~DLFeatureExtractor() = default;

  // Non-copyable
  DLFeatureExtractor(const DLFeatureExtractor&) = delete;
  DLFeatureExtractor& operator=(const DLFeatureExtractor&) = delete;

  /**
   * @brief Extract keypoints and descriptors from a grayscale image.
   * @param image          Input image (CV_8U, grayscale or BGR).
   * @param[out] keypoints Detected keypoints in original image coords.
   * @param[out] scores    Keypoint confidence scores.
   * @param[out] descriptors  Row-major float descriptor matrix, shape [N, descDim].
   *                          Rows correspond to keypoints.
   * @return True on success.
   */
  bool extract(const cv::Mat& image,
               std::vector<cv::Point2f>& keypoints,
               std::vector<float>& scores,
               cv::Mat& descriptors);

  /// @brief Descriptor dimension (256 for SuperPoint, 128 for DISK).
  int descriptorDim() const { return descriptorDim_; }

  /// @brief Target inference image size (longest edge).
  int imageSize() const { return imageSize_; }

 private:
  Ort::Env              env_;
  Ort::SessionOptions   sessionOptions_;
  std::unique_ptr<Ort::Session> session_;
  Ort::AllocatorWithDefaultOptions allocator_;

  int  imageSize_;
  int  descriptorDim_;  // filled after first inference
  bool useCuda_;

  // ONNX I/O names (owned strings, kept alive during session)
  std::vector<std::string> inputNames_;
  std::vector<std::string> outputNames_;
  std::vector<const char*> inputNamePtrs_;
  std::vector<const char*> outputNamePtrs_;

  void initSession(const std::string& modelPath, int deviceId);
  void queryModelMetadata();
};

// ---------------------------------------------------------------------------
// LightGlue matcher (fused ONNX model)
// ---------------------------------------------------------------------------

/**
 * @brief Feature matcher using the fused SuperPoint+LightGlue ONNX model.
 *
 * Expected ONNX model I/O (fused LightGlue):
 *   Inputs:
 *     [0] "kpts0"  [1,N,2] float32  – normalised keypoints of frame 0
 *     [1] "kpts1"  [1,M,2] float32  – normalised keypoints of frame 1
 *     [2] "desc0"  [1,N,D] float32  – descriptors of frame 0
 *     [3] "desc1"  [1,M,D] float32  – descriptors of frame 1
 *   Outputs:
 *     [0] "matches0"        [1,K,2] int32 or int64  – (idx0, idx1) pairs
 *     [1] "matching_scores0" [1,K] float32          – confidence scores
 */
class DLFeatureMatcher {
 public:
  /**
   * @brief Constructor.
   * @param modelPath     Path to the fused LightGlue ONNX model file.
   * @param matchThreshold Minimum matching score to keep a match.
   * @param useCuda       Run inference on GPU (CUDA).
   * @param deviceId      CUDA device id (ignored when useCuda=false).
   */
  DLFeatureMatcher(const std::string& modelPath,
                   float matchThreshold = 0.7f,
                   bool  useCuda = false,
                   int   deviceId = 0);

  ~DLFeatureMatcher() = default;

  // Non-copyable
  DLFeatureMatcher(const DLFeatureMatcher&) = delete;
  DLFeatureMatcher& operator=(const DLFeatureMatcher&) = delete;

  /**
   * @brief Match two sets of keypoints+descriptors with LightGlue.
   * @param kpts0  Keypoints of frame 0 (pixel coords).
   * @param kpts1  Keypoints of frame 1 (pixel coords).
   * @param desc0  Descriptor matrix [N,D] float32 for frame 0.
   * @param desc1  Descriptor matrix [M,D] float32 for frame 1.
   * @param h0, w0 Image dimensions of frame 0 (for normalisation).
   * @param h1, w1 Image dimensions of frame 1 (for normalisation).
   * @param[out] matches  Resulting (idx0, idx1) index pairs.
   * @param[out] matchScores  Corresponding confidence scores.
   * @return True on success.
   */
  bool match(const std::vector<cv::Point2f>& kpts0,
             const std::vector<cv::Point2f>& kpts1,
             const cv::Mat& desc0,
             const cv::Mat& desc1,
             int h0, int w0,
             int h1, int w1,
             std::vector<cv::DMatch>& matches,
             std::vector<float>& matchScores);

  /// @brief Change the match threshold at runtime.
  void setMatchThreshold(float t) { matchThreshold_ = t; }
  float matchThreshold() const { return matchThreshold_; }

 private:
  Ort::Env              env_;
  Ort::SessionOptions   sessionOptions_;
  std::unique_ptr<Ort::Session> session_;
  Ort::AllocatorWithDefaultOptions allocator_;

  float matchThreshold_;
  bool  useCuda_;

  std::vector<std::string> inputNames_;
  std::vector<std::string> outputNames_;
  std::vector<const char*> inputNamePtrs_;
  std::vector<const char*> outputNamePtrs_;

  void initSession(const std::string& modelPath, int deviceId);
};

}  // namespace dl
}  // namespace okvis

#endif  // OKVIS_USE_DL_FEATURES
