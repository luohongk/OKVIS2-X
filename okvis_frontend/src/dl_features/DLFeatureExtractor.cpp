/**
 * OKVIS2-X - DL Feature Extraction and Matching
 *
 * Implementation of SuperPoint/DISK extraction and LightGlue matching
 * via ONNX Runtime.  Adapted from SuperVINS extractor_matcher_dpl.cpp
 * and transform_dpl.cpp.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

#ifdef OKVIS_USE_DL_FEATURES

#include <okvis/dl_features/DLFeatureExtractor.hpp>

#include <stdexcept>
#include <cstring>
#include <cassert>

#include <opencv2/imgproc.hpp>
#include <glog/logging.h>

namespace okvis {
namespace dl {

// ============================================================
// Image preprocessing utilities
// ============================================================

cv::Mat resizeImage(const cv::Mat& image,
                    int size,
                    double& scale,
                    const std::string& mode,
                    int interpolation)
{
  int h = image.rows;
  int w = image.cols;

  int ref = (mode == "min") ? std::min(h, w) : std::max(h, w);
  scale = static_cast<double>(size) / static_cast<double>(ref);

  int newH = static_cast<int>(std::round(h * scale));
  int newW = static_cast<int>(std::round(w * scale));

  // Clamp to even sizes (helps some ONNX models)
  newH = (newH % 2 == 0) ? newH : newH + 1;
  newW = (newW % 2 == 0) ? newW : newW + 1;

  cv::Mat result;
  cv::resize(image, result, cv::Size(newW, newH), 0, 0, interpolation);
  return result;
}

cv::Mat normalizeImage(const cv::Mat& image)
{
  cv::Mat gray;
  if (image.channels() == 3) {
    cv::cvtColor(image, gray, cv::COLOR_BGR2GRAY);
  } else {
    gray = image;
  }

  cv::Mat floatImg;
  gray.convertTo(floatImg, CV_32FC1, 1.0 / 255.0);
  return floatImg;
}

std::vector<cv::Point2f> normalizeKeypoints(const std::vector<cv::Point2f>& kpts,
                                            int h,
                                            int w)
{
  float cx = w * 0.5f;
  float cy = h * 0.5f;
  float scale = static_cast<float>(std::max(h, w));

  std::vector<cv::Point2f> normalized;
  normalized.reserve(kpts.size());
  for (const auto& kp : kpts) {
    normalized.emplace_back((kp.x - cx) / scale * 2.0f,
                             (kp.y - cy) / scale * 2.0f);
  }
  return normalized;
}

// ============================================================
// DLFeatureExtractor
// ============================================================

DLFeatureExtractor::DLFeatureExtractor(const std::string& modelPath,
                                       bool useCuda,
                                       int  deviceId,
                                       int  imageSize)
    : env_(ORT_LOGGING_LEVEL_WARNING, "DLFeatureExtractor"),
      imageSize_(imageSize),
      descriptorDim_(256),
      useCuda_(useCuda)
{
  initSession(modelPath, deviceId);
  queryModelMetadata();
}

void DLFeatureExtractor::initSession(const std::string& modelPath, int deviceId)
{
  sessionOptions_.SetIntraOpNumThreads(2);
  sessionOptions_.SetGraphOptimizationLevel(ORT_ENABLE_EXTENDED);

#ifdef OKVIS_USE_GPU
  if (useCuda_) {
    OrtCUDAProviderOptions cudaOptions{};
    cudaOptions.device_id = deviceId;
    sessionOptions_.AppendExecutionProvider_CUDA(cudaOptions);
    LOG(INFO) << "[DLFeatureExtractor] Using CUDA provider (device " << deviceId << ")";
  } else
#endif
  {
    LOG(INFO) << "[DLFeatureExtractor] Using CPU provider";
  }

  session_ = std::make_unique<Ort::Session>(env_, modelPath.c_str(), sessionOptions_);
  LOG(INFO) << "[DLFeatureExtractor] Loaded model: " << modelPath;
}

void DLFeatureExtractor::queryModelMetadata()
{
  // Collect input node names
  size_t numInputs = session_->GetInputCount();
  inputNames_.resize(numInputs);
  inputNamePtrs_.resize(numInputs);
  for (size_t i = 0; i < numInputs; ++i) {
    Ort::AllocatedStringPtr name = session_->GetInputNameAllocated(i, allocator_);
    inputNames_[i] = std::string(name.get());
    inputNamePtrs_[i] = inputNames_[i].c_str();
  }

  // Collect output node names
  size_t numOutputs = session_->GetOutputCount();
  outputNames_.resize(numOutputs);
  outputNamePtrs_.resize(numOutputs);
  for (size_t i = 0; i < numOutputs; ++i) {
    Ort::AllocatedStringPtr name = session_->GetOutputNameAllocated(i, allocator_);
    outputNames_[i] = std::string(name.get());
    outputNamePtrs_[i] = outputNames_[i].c_str();
  }

  LOG(INFO) << "[DLFeatureExtractor] Inputs:";
  for (auto& n : inputNames_)  LOG(INFO) << "  " << n;
  LOG(INFO) << "[DLFeatureExtractor] Outputs:";
  for (auto& n : outputNames_) LOG(INFO) << "  " << n;
}

bool DLFeatureExtractor::extract(const cv::Mat& image,
                                 std::vector<cv::Point2f>& keypoints,
                                 std::vector<float>& scores,
                                 cv::Mat& descriptors)
{
  if (image.empty()) {
    LOG(WARNING) << "[DLFeatureExtractor] Empty input image.";
    return false;
  }

  // --- Pre-process: resize then normalise to float32 [0,1] ---
  double scale = 1.0;
  cv::Mat resized = resizeImage(image, imageSize_, scale);
  cv::Mat floatImg = normalizeImage(resized);  // CV_32FC1, [0,1]

  const int H = floatImg.rows;
  const int W = floatImg.cols;

  // Build input tensor [1, 1, H, W]
  std::array<int64_t, 4> inputShape = {1, 1, H, W};
  const size_t numElems = static_cast<size_t>(H * W);

  // floatImg.isContinuous() guaranteed after resize
  std::vector<float> inputData(floatImg.ptr<float>(),
                               floatImg.ptr<float>() + numElems);

  Ort::MemoryInfo memInfo = Ort::MemoryInfo::CreateCpu(
      OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);

  Ort::Value inputTensor = Ort::Value::CreateTensor<float>(
      memInfo,
      inputData.data(),
      numElems,
      inputShape.data(),
      inputShape.size());

  // --- Run inference ---
  auto outputTensors = session_->Run(
      Ort::RunOptions{nullptr},
      inputNamePtrs_.data(),
      &inputTensor,
      1,
      outputNamePtrs_.data(),
      outputNamePtrs_.size());

  // --- Post-process ---
  // Expected outputs: keypoints [1,N,2] int64, scores [1,N] float, descriptors [1,D,N] float
  // (exact layout depends on model; handle both [1,D,N] and [1,N,D])

  // Output 0: keypoints
  auto& kptTensor = outputTensors[0];
  auto kptShape   = kptTensor.GetTensorTypeAndShapeInfo().GetShape();
  int64_t N = kptShape[1];  // number of keypoints

  if (N == 0) {
    keypoints.clear(); scores.clear(); descriptors = cv::Mat();
    return true;
  }

  // Keypoints may be int64 or float
  keypoints.clear();
  keypoints.reserve(static_cast<size_t>(N));

  {
    auto typeInfo = kptTensor.GetTensorTypeAndShapeInfo().GetElementType();
    if (typeInfo == ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64) {
      const int64_t* kdata = kptTensor.GetTensorMutableData<int64_t>();
      for (int64_t i = 0; i < N; ++i) {
        // Recover to original image coords: pt_orig = (pt_512 + 0.5) / scale - 0.5
        float x = (static_cast<float>(kdata[2*i    ]) + 0.5f) / static_cast<float>(scale) - 0.5f;
        float y = (static_cast<float>(kdata[2*i + 1]) + 0.5f) / static_cast<float>(scale) - 0.5f;
        keypoints.emplace_back(x, y);
      }
    } else {
      const float* kdata = kptTensor.GetTensorMutableData<float>();
      for (int64_t i = 0; i < N; ++i) {
        float x = (kdata[2*i    ] + 0.5f) / static_cast<float>(scale) - 0.5f;
        float y = (kdata[2*i + 1] + 0.5f) / static_cast<float>(scale) - 0.5f;
        keypoints.emplace_back(x, y);
      }
    }
  }

  // Output 1: scores [1,N]
  {
    auto& scoreTensor = outputTensors[1];
    const float* sdata = scoreTensor.GetTensorMutableData<float>();
    scores.assign(sdata, sdata + N);
  }

  // Output 2: descriptors – shape [1, N, D] (SuperPoint: [1,N,256]; DISK: [1,N,128])
  // Some exporters use [1, D, N]; detect by checking which dimension is the descriptor dim
  // (≤512 means it's D; >512 is likely N).
  {
    auto& descTensor  = outputTensors[2];
    auto  descShape   = descTensor.GetTensorTypeAndShapeInfo().GetShape();
    const float* ddata = descTensor.GetTensorMutableData<float>();

    int64_t dim1 = descShape[1];
    int64_t dim2 = descShape[2];

    if (dim2 <= 512) {
      // Shape [1, N, D]  →  dim1=N, dim2=D (standard SuperPoint/DISK output)
      descriptorDim_ = static_cast<int>(dim2);
      descriptors = cv::Mat(static_cast<int>(dim1), descriptorDim_, CV_32F);
      std::memcpy(descriptors.data, ddata, dim1 * descriptorDim_ * sizeof(float));
    } else {
      // Shape [1, D, N]  →  dim1=D, dim2=N  (transpose to [N, D])
      descriptorDim_ = static_cast<int>(dim1);
      int64_t Npts = dim2;
      descriptors = cv::Mat(static_cast<int>(Npts), descriptorDim_, CV_32F);
      for (int64_t n = 0; n < Npts; ++n) {
        float* row = descriptors.ptr<float>(static_cast<int>(n));
        for (int d = 0; d < descriptorDim_; ++d) {
          row[d] = ddata[d * Npts + n];
        }
      }
    }
  }

  return true;
}

// ============================================================
// DLFeatureMatcher
// ============================================================

DLFeatureMatcher::DLFeatureMatcher(const std::string& modelPath,
                                   float matchThreshold,
                                   bool  useCuda,
                                   int   deviceId)
    : env_(ORT_LOGGING_LEVEL_WARNING, "DLFeatureMatcher"),
      matchThreshold_(matchThreshold),
      useCuda_(useCuda)
{
  initSession(modelPath, deviceId);
}

void DLFeatureMatcher::initSession(const std::string& modelPath, int deviceId)
{
  sessionOptions_.SetIntraOpNumThreads(2);
  sessionOptions_.SetGraphOptimizationLevel(ORT_ENABLE_EXTENDED);

#ifdef OKVIS_USE_GPU
  if (useCuda_) {
    OrtCUDAProviderOptions cudaOptions{};
    cudaOptions.device_id = deviceId;
    sessionOptions_.AppendExecutionProvider_CUDA(cudaOptions);
    LOG(INFO) << "[DLFeatureMatcher] Using CUDA provider (device " << deviceId << ")";
  } else
#endif
  {
    LOG(INFO) << "[DLFeatureMatcher] Using CPU provider";
  }

  session_ = std::make_unique<Ort::Session>(env_, modelPath.c_str(), sessionOptions_);
  LOG(INFO) << "[DLFeatureMatcher] Loaded model: " << modelPath;

  // Cache I/O names
  size_t numIn = session_->GetInputCount();
  inputNames_.resize(numIn);
  inputNamePtrs_.resize(numIn);
  for (size_t i = 0; i < numIn; ++i) {
    Ort::AllocatedStringPtr name = session_->GetInputNameAllocated(i, allocator_);
    inputNames_[i] = std::string(name.get());
    inputNamePtrs_[i] = inputNames_[i].c_str();
  }

  size_t numOut = session_->GetOutputCount();
  outputNames_.resize(numOut);
  outputNamePtrs_.resize(numOut);
  for (size_t i = 0; i < numOut; ++i) {
    Ort::AllocatedStringPtr name = session_->GetOutputNameAllocated(i, allocator_);
    outputNames_[i] = std::string(name.get());
    outputNamePtrs_[i] = outputNames_[i].c_str();
  }
}

bool DLFeatureMatcher::match(const std::vector<cv::Point2f>& kpts0,
                              const std::vector<cv::Point2f>& kpts1,
                              const cv::Mat& desc0,
                              const cv::Mat& desc1,
                              int h0, int w0,
                              int h1, int w1,
                              std::vector<cv::DMatch>& matches,
                              std::vector<float>& matchScores)
{
  matches.clear();
  matchScores.clear();

  const int N = static_cast<int>(kpts0.size());
  const int M = static_cast<int>(kpts1.size());
  const int D = desc0.cols;

  if (N == 0 || M == 0) return true;

  // --- Normalise keypoints ---
  auto normKpts0 = normalizeKeypoints(kpts0, h0, w0);
  auto normKpts1 = normalizeKeypoints(kpts1, h1, w1);

  // --- Build input tensors ---
  Ort::MemoryInfo memInfo = Ort::MemoryInfo::CreateCpu(
      OrtAllocatorType::OrtArenaAllocator, OrtMemType::OrtMemTypeDefault);

  // kpts0: [1, N, 2]
  std::vector<float> kpts0Data;
  kpts0Data.reserve(N * 2);
  for (const auto& kp : normKpts0) { kpts0Data.push_back(kp.x); kpts0Data.push_back(kp.y); }
  std::array<int64_t, 3> kpts0Shape = {1, N, 2};
  auto kpts0Tensor = Ort::Value::CreateTensor<float>(
      memInfo, kpts0Data.data(), kpts0Data.size(), kpts0Shape.data(), 3);

  // kpts1: [1, M, 2]
  std::vector<float> kpts1Data;
  kpts1Data.reserve(M * 2);
  for (const auto& kp : normKpts1) { kpts1Data.push_back(kp.x); kpts1Data.push_back(kp.y); }
  std::array<int64_t, 3> kpts1Shape = {1, M, 2};
  auto kpts1Tensor = Ort::Value::CreateTensor<float>(
      memInfo, kpts1Data.data(), kpts1Data.size(), kpts1Shape.data(), 3);

  // desc0: [1, N, D]
  std::vector<float> desc0Data(desc0.ptr<float>(), desc0.ptr<float>() + N * D);
  std::array<int64_t, 3> desc0Shape = {1, N, D};
  auto desc0Tensor = Ort::Value::CreateTensor<float>(
      memInfo, desc0Data.data(), desc0Data.size(), desc0Shape.data(), 3);

  // desc1: [1, M, D]
  std::vector<float> desc1Data(desc1.ptr<float>(), desc1.ptr<float>() + M * D);
  std::array<int64_t, 3> desc1Shape = {1, M, D};
  auto desc1Tensor = Ort::Value::CreateTensor<float>(
      memInfo, desc1Data.data(), desc1Data.size(), desc1Shape.data(), 3);

  // --- Collect input tensors in the order expected by the model ---
  std::vector<Ort::Value> inputTensors;
  inputTensors.push_back(std::move(kpts0Tensor));
  inputTensors.push_back(std::move(kpts1Tensor));
  inputTensors.push_back(std::move(desc0Tensor));
  inputTensors.push_back(std::move(desc1Tensor));

  // --- Run inference ---
  auto outputTensors = session_->Run(
      Ort::RunOptions{nullptr},
      inputNamePtrs_.data(),
      inputTensors.data(),
      inputTensors.size(),
      outputNamePtrs_.data(),
      outputNamePtrs_.size());

  // --- Parse outputs ---
  // Output 0: matches  [1, K, 2] (int32 or int64)
  // Output 1: scores   [1, K]    (float32)
  if (outputTensors.empty()) return false;

  auto& matchTensor = outputTensors[0];
  auto  matchShape  = matchTensor.GetTensorTypeAndShapeInfo().GetShape();

  // matchShape: [1, K, 2] or [K, 2]
  int64_t K = (matchShape.size() == 3) ? matchShape[1] : matchShape[0];
  if (K == 0) return true;

  // Scores
  std::vector<float> rawScores;
  if (outputTensors.size() >= 2) {
    auto& scoreTensor = outputTensors[1];
    const float* sd = scoreTensor.GetTensorMutableData<float>();
    rawScores.assign(sd, sd + K);
  } else {
    rawScores.assign(K, 1.0f);
  }

  auto matchElemType = matchTensor.GetTensorTypeAndShapeInfo().GetElementType();

  for (int64_t i = 0; i < K; ++i) {
    if (rawScores[i] < matchThreshold_) continue;

    int idx0, idx1;
    if (matchElemType == ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64) {
      const int64_t* md = matchTensor.GetTensorMutableData<int64_t>();
      idx0 = static_cast<int>(md[2*i    ]);
      idx1 = static_cast<int>(md[2*i + 1]);
    } else {
      const int32_t* md = matchTensor.GetTensorMutableData<int32_t>();
      idx0 = static_cast<int>(md[2*i    ]);
      idx1 = static_cast<int>(md[2*i + 1]);
    }

    if (idx0 < 0 || idx0 >= N || idx1 < 0 || idx1 >= M) continue;

    matches.emplace_back(idx0, idx1, 1.0f - rawScores[i]);
    matchScores.push_back(rawScores[i]);
  }

  return true;
}

}  // namespace dl
}  // namespace okvis

#endif  // OKVIS_USE_DL_FEATURES
