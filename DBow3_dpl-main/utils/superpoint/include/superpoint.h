/*
 * @Author: Hongkun Luo
 * @Date: 2024-05-16 10:00:25
 * @LastEditors: Hongkun Luo
 * @Description:
 *
 * Hongkun Luo
 */
#include <chrono>
#include <iostream>
#include "omp.h"

#include <opencv2/opencv.hpp>
#include <onnxruntime_cxx_api.h>
#include <vector>
#include "vector"
using namespace cv;
using namespace std;

#include <iostream>
#include <functional>
#include "extractor_matcher_dpl.h"
class superpoint
{
public:
	superpoint();
	// int detectAndCompute(const cv::Mat &image, cv::Mat &mkpts, cv::Mat &feats, cv::Mat &sc);
	// bool initOrtSession(const Ort::Env &env, Ort::Session &session, std::string &modelPath, int &gpuId);
	// int matchStar(const cv::Mat &mkpts0, const cv::Mat &feats0, const cv::Mat &sc0, const cv::Mat &mkpts1, const cv::Mat &feats1, cv::Mat &matches, cv::Mat &batch_indexes);

	// static cv::Mat warpCornersAndDrawMatches(const std::vector<cv::Point2f> &refPoints, const std::vector<cv::Point2f> &dstPoints,
	// 								  const cv::Mat &img1, const cv::Mat &img2);

	// const ORTCHAR_T *stringToOrtchar_t(std::string const &s);

	unsigned int IMAGE_SIZE_DPL = 512;
	float scale = 1.0f;
	int extractor_type = 0;
	int descriptor_size = 256;

	string extractor_weight_global_path = "/home/lhk/workspace/DBow3_sp/utils/superpoint/weights_dpl/superpoint.onnx";
	string matcher_weight_global_path = "/home/lhk/workspace/DBow3_sp/utils/superpoint/weights_dpl/superpoint_lightglue_fused_cpu.onnx";

	float matche_score_threshold = 0.5;

	int row, col;

	std::shared_ptr<Extractor_DPL> FeatureExtractorDPL;
	std::shared_ptr<Matcher_DPL> FeatureMatcherDPL;

	void initializeExtractorMatcher(int extractor_type_, string &extractor_weight_path, string &matcher_weight_path, float matcher_threshold = 0.5);

	void extract_features_dpl(cv::Mat img, vector<cv::Point2f> &pts, vector<pair<cv::Point2f, vector<float>>> &dplpts_descriptors);

	cv::Mat Extractor_PreProcess(const cv::Mat &srcImage, float &scale);

	cv::Mat ResizeImage(const cv::Mat &Image, int size, float &scale, const std::string &fn, const std::string &interp);

	bool inBorder(const cv::Point2f &pt);

	cv::Mat NormalizeImage(cv::Mat &Image);

	~superpoint();

	// gpu id
	// int gpuId_ = 0;

	// // onnxruntime
	// Ort::Env env_{nullptr};
	// Ort::Session xfeatSession_{nullptr};
	// Ort::Session matchingSession_{nullptr};
	// Ort::AllocatorWithDefaultOptions allocator;

	// //
	// std::vector<const char *> xfeatInputNames = {"images"};
	// std::vector<const char *> xfeatOutputNames = {"mkpts", "feats", "sc"};
	// std::vector<const char *> matchingInputNames = {"mkpts0", "feats0", "sc0", "mkpts1", "feats1"};
	// std::vector<const char *> matchingOutputNames = {"matches", "batch_indexes"};

	// bool initFinishedFlag_ = false;
};