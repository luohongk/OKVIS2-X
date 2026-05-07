/*
 * @Author: Hongkun Luo
 * @Date: 2024-07-17 11:19:53
 * @LastEditors: Hongkun Luo
 * @Description:
 *
 * Hongkun Luo
 */
#include "superpoint.h"
using namespace cv;

superpoint::superpoint()
{
    // new codes: initialize deep learning extractor and matcher
    // string extractorDPL_path = "/home/lhk/catkin_ws/src/VINS-Fusion-LightGlue/vins_estimator/weights_dpl/superpoint.onnx";
    // string matcherDPL_path = "/home/lhk/catkin_ws/src/VINS-Fusion-LightGlue/vins_estimator/weights_dpl/superpoint_lightglue_fused_cpu.onnx";
    initializeExtractorMatcher(0, extractor_weight_global_path, matcher_weight_global_path, matche_score_threshold); // initialize deep-learning based extractor and matcher
}

void superpoint::initializeExtractorMatcher(int extractor_type_, string &extractor_weight_path, string &matcher_weight_path, float matcher_threshold)
{
    extractor_type = extractor_type_;
    FeatureExtractorDPL = std::make_shared<Extractor_DPL>();
    FeatureExtractorDPL->initialize(extractor_weight_path, extractor_type_);

    FeatureMatcherDPL = std::make_shared<Matcher_DPL>();
    FeatureMatcherDPL->initialize(matcher_weight_path, extractor_type_, matcher_threshold);

    if (extractor_type_ == SUPERPOINT)
    {
        descriptor_size = SUPERPOINT_SIZE;
    }
    else if (extractor_type_ == DISK)
    {
        descriptor_size = DISK_SIZE;
    }
}

void superpoint::extract_features_dpl(cv::Mat img, vector<cv::Point2f> &pts, vector<pair<cv::Point2f, vector<float>>> &dplpts_descriptors)
{
    cv::Mat im = img.clone();

    row=im.rows;
    col=im.cols;
    
    cv::Mat im_preprocessed = Extractor_PreProcess(im, scale);
    std::pair<std::vector<cv::Point2f>, float *> result_dplpts_descriptors = FeatureExtractorDPL->extract_featurepoints(im_preprocessed);

    int n = result_dplpts_descriptors.first.size();
    for (int i = 0; i < n; i++)
    {

        cv::Point2f dplpt = result_dplpts_descriptors.first[i];
        cv::Point2f pt = cv::Point2f((dplpt.x + 0.5) / FeatureExtractorDPL->scale - 0.5, (dplpt.y + 0.5) / FeatureExtractorDPL->scale - 0.5);
        if (!inBorder(pt))
            continue;
        std::vector<float> descriptor(result_dplpts_descriptors.second + i * descriptor_size, result_dplpts_descriptors.second + (i + 1) * descriptor_size);
        pts.push_back(pt);
        dplpts_descriptors.push_back(make_pair(dplpt, descriptor));
    }
    // std::cout<<dplpts_descriptors[0].first<<endl;
    // std::cout<<dplpts_descriptors[0].second<<endl;
}
cv::Mat superpoint::Extractor_PreProcess(const cv::Mat &Image, float &scale)
{
    float temp_scale = scale;
    cv::Mat tempImage = Image.clone();
    // std::cout << "[INFO] Image info :  width : " << Image.cols << " height :  " << Image.rows << std::endl;

    std::string fn = "max";
    std::string interp = "area";
    cv::Mat resize_img = ResizeImage(tempImage, IMAGE_SIZE_DPL, scale, fn, interp);
    cv::Mat resultImage = NormalizeImage(resize_img);
    // if (cfg.extractorType == "superpoint")
    //{
    // std::cout << "[INFO] ExtractorType Superpoint turn RGB to Grayscale" << std::endl;
    // resultImage = RGB2Grayscale(resultImage);
    //}
    // std::cout << "[INFO] Scale from " << temp_scale << " to " << scale << std::endl;

    return resultImage;
}

cv::Mat superpoint::NormalizeImage(cv::Mat &Image)
{
    cv::Mat normalizedImage = Image.clone();

    if (Image.channels() == 3)
    {
        cv::cvtColor(normalizedImage, normalizedImage, cv::COLOR_BGR2RGB);
        normalizedImage.convertTo(normalizedImage, CV_32F, 1.0 / 255.0);
    }
    else if (Image.channels() == 1)
    {
        Image.convertTo(normalizedImage, CV_32F, 1.0 / 255.0);
    }
    else
    {
        throw std::invalid_argument("[ERROR] Not an image");
    }

    return normalizedImage;
}

cv::Mat superpoint::ResizeImage(const cv::Mat &Image, int size, float &scale, const std::string &fn,
                                const std::string &interp)
{
    // Resize an image to a fixed size, or according to max or min edge.
    int h = Image.rows;
    int w = Image.cols;

    std::function<int(int, int)> func;
    if (fn == "max")
    {
        func = [](int a, int b)
        { return (std::max)(a, b); };
        ;
    }
    else if (fn == "min")
    {
        func = [](int a, int b)
        { return (std::min)(a, b); };
    }
    else
    {
        throw std::invalid_argument("[ERROR] Incorrect function: " + fn);
    }

    int h_new, w_new;
    if (size == 512 || size == 1024 || size == 2048)
    {
        scale = static_cast<float>(size) / static_cast<float>(func(h, w));
        h_new = static_cast<int>(round(h * scale));
        w_new = static_cast<int>(round(w * scale));
    }
    else
    {
        throw std::invalid_argument("Incorrect new size: " + std::to_string(size));
    }

    int mode;
    if (interp == "linear")
    {
        mode = cv::INTER_LINEAR;
    }
    else if (interp == "cubic")
    {
        mode = cv::INTER_CUBIC;
    }
    else if (interp == "nearest")
    {
        mode = cv::INTER_NEAREST;
    }
    else if (interp == "area")
    {
        mode = cv::INTER_AREA;
    }
    else
    {
        throw std::invalid_argument("[ERROR] Incorrect interpolation mode: " + interp);
    }

    cv::Mat resizeImage;
    cv::resize(Image, resizeImage, cv::Size(w_new, h_new), 0, 0, mode);

    return resizeImage;
}

bool superpoint::inBorder(const cv::Point2f &pt)
{
    const int BORDER_SIZE = 1;
    int img_x = cvRound(pt.x);
    int img_y = cvRound(pt.y);
    return BORDER_SIZE <= img_x && img_x < col - BORDER_SIZE && BORDER_SIZE <= img_y && img_y < row - BORDER_SIZE;
}

superpoint::~superpoint()
{
}
