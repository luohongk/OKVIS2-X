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
using namespace cv;
class XFeat
{
public:
	XFeat(std::string &xfeatModelPath, std::string &matchingModelPath);
	int detectAndCompute(const cv::Mat &image, cv::Mat &mkpts, cv::Mat &feats, cv::Mat &sc);
	bool initOrtSession(const Ort::Env &env, Ort::Session &session, std::string &modelPath, int &gpuId);
	int matchStar(const cv::Mat &mkpts0, const cv::Mat &feats0, const cv::Mat &sc0, const cv::Mat &mkpts1, const cv::Mat &feats1, cv::Mat &matches, cv::Mat &batch_indexes);

	static cv::Mat warpCornersAndDrawMatches(const std::vector<cv::Point2f> &refPoints, const std::vector<cv::Point2f> &dstPoints,
									  const cv::Mat &img1, const cv::Mat &img2);

	const ORTCHAR_T *stringToOrtchar_t(std::string const &s);

	~XFeat();

	// gpu id
	int gpuId_ = 0;

	// onnxruntime
	Ort::Env env_{nullptr};
	Ort::Session xfeatSession_{nullptr};
	Ort::Session matchingSession_{nullptr};
	Ort::AllocatorWithDefaultOptions allocator;

	//
	std::vector<const char *> xfeatInputNames = {"images"};
	std::vector<const char *> xfeatOutputNames = {"mkpts", "feats", "sc"};
	std::vector<const char *> matchingInputNames = {"mkpts0", "feats0", "sc0", "mkpts1", "feats1"};
	std::vector<const char *> matchingOutputNames = {"matches", "batch_indexes"};

	bool initFinishedFlag_ = false;
};