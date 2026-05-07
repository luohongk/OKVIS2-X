#include "XFeat.h"
using namespace cv;

XFeat::XFeat(std::string &xfeatModelPath, std::string &matchingModelPath)
{
    const ORTCHAR_T *ortXfeatModelPath = stringToOrtchar_t(xfeatModelPath);
    const ORTCHAR_T *ortMatchingModelPath = stringToOrtchar_t(matchingModelPath);

    env_ = Ort::Env{OrtLoggingLevel::ORT_LOGGING_LEVEL_FATAL, "xfeat_demo"}; //  ORT_LOGGING_LEVEL_VERBOSE, ORT_LOGGING_LEVEL_FATAL

    std::vector<std::string> availableProviders = Ort::GetAvailableProviders();
    std::cout << "All available accelerators:" << std::endl;
    for (int i = 0; i < availableProviders.size(); i++)
    {
        std::cout << "  " << i + 1 << ". " << availableProviders[i] << std::endl;
    }

    // init sessions
    initOrtSession(env_, xfeatSession_, xfeatModelPath, gpuId_);
    initOrtSession(env_, matchingSession_, matchingModelPath, gpuId_);
}

XFeat::~XFeat()
{
    env_.release();
    xfeatSession_.release();
    matchingSession_.release();
}

bool XFeat::initOrtSession(const Ort::Env &env, Ort::Session &session, std::string &modelPath, int &gpuId)
{
    const ORTCHAR_T *ortModelPath = stringToOrtchar_t(modelPath);

    bool sessionIsAvailable = false;
    /*
    if (sessionIsAvailable == false)
    {
        try
        {
            Ort::SessionOptions session_options;
            session_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);

            // try Tensorrt
            OrtTensorRTProviderOptions trtOptions{};
            trtOptions.device_id = gpuId;
            trtOptions.trt_fp16_enable = 1;
            trtOptions.trt_engine_cache_enable = 1;
            trtOptions.trt_engine_cache_path = "./trt_engine_cache";


            trtOptions.trt_max_workspace_size = (size_t)4 * 1024 * 1024 * 1024;

            session_options.AppendExecutionProvider_TensorRT(trtOptions);

            session = Ort::Session(env, ortModelPath, session_options);

            sessionIsAvailable = true;
            std::cout << "Using accelerator: Tensorrt" << std::endl;
        }
        catch (Ort::Exception e)
        {
            std::cout << "Exception code: " << e.GetOrtErrorCode() << ", exception: " << e.what() << std::endl;
            std::cout << "Failed to init Tensorrt accelerator, Trying another accelerator..." << std::endl;
            sessionIsAvailable = false;
        }
        catch (...)
        {
            std::cout << "Failed to init Tensorrt accelerator, Trying another accelerator..." << std::endl;
            sessionIsAvailable = false;
        }
    }
    */

    if (sessionIsAvailable == false)
    {
        try
        {
            Ort::SessionOptions session_options;
            session_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);

            OrtCUDAProviderOptions cuda0ptions;
            cuda0ptions.device_id = gpuId;
            cuda0ptions.gpu_mem_limit = 4 << 30;

            session_options.AppendExecutionProvider_CUDA(cuda0ptions);

            session = Ort::Session(env, ortModelPath, session_options);

            sessionIsAvailable = true;
            std::cout << "Using accelerator: CUDA" << std::endl;
        }
        catch (Ort::Exception e)
        {
            std::cout << "Exception code: " << e.GetOrtErrorCode() << ", exception: " << e.what() << std::endl;
            std::cout << "Failed to init CUDA accelerator, Trying another accelerator..." << std::endl;
            sessionIsAvailable = false;
        }
        catch (...)
        {
            std::cout << "Failed to init CUDA accelerator, Trying another accelerator..." << std::endl;
            sessionIsAvailable = false;
        }
    }
    if (sessionIsAvailable == false)
    {
        try
        {
            Ort::SessionOptions session_options;
            session_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);

            session = Ort::Session(env, ortModelPath, session_options);

            sessionIsAvailable = true;
            std::cout << "Using accelerator: CPU" << std::endl;
        }
        catch (Ort::Exception e)
        {
            std::cout << "Exception code: " << e.GetOrtErrorCode() << ", exception: " << e.what() << std::endl;
            std::cout << "Failed to init CPU accelerator, Trying another accelerator..." << std::endl;
            sessionIsAvailable = false;
        }
        catch (...)
        {
            std::cout << "Failed to init CPU accelerator." << std::endl;
            sessionIsAvailable = false;
        }
    }

    if (sessionIsAvailable == true)
    {
        Ort::AllocatorWithDefaultOptions allocator;
        // Get input layers count
        size_t num_input_nodes = session.GetInputCount();

        // Get input layer type, shape, name
        for (int i = 0; i < num_input_nodes; i++)
        {

            // Name
            std::string input_name = std::string(session.GetInputNameAllocated(i, allocator).get());

            std::cout << "Input " << i << ": " << input_name << ", shape: (";

            // Type
            Ort::TypeInfo type_info = session.GetInputTypeInfo(i);
            auto tensor_info = type_info.GetTensorTypeAndShapeInfo();

            ONNXTensorElementDataType type = tensor_info.GetElementType();

            // Shape
            std::vector<int64_t> input_node_dims = tensor_info.GetShape();

            for (int j = 0; j < input_node_dims.size(); j++)
            {
                std::cout << input_node_dims[j];
                if (j == input_node_dims.size() - 1)
                {
                    std::cout << ")" << std::endl;
                }
                else
                {
                    std::cout << ", ";
                }
            }
        }

        // Get output layers count
        size_t num_output_nodes = session.GetOutputCount();

        // Get output layer type, shape, name
        for (int i = 0; i < num_output_nodes; i++)
        {
            // Name
            std::string output_name = std::string(session.GetOutputNameAllocated(i, allocator).get());
            std::cout << "Output " << i << ": " << output_name << ", shape: (";

            // type
            Ort::TypeInfo type_info = session.GetOutputTypeInfo(i);
            auto tensor_info = type_info.GetTensorTypeAndShapeInfo();

            ONNXTensorElementDataType type = tensor_info.GetElementType();

            // shape
            std::vector<int64_t> output_node_dims = tensor_info.GetShape();
            for (int j = 0; j < output_node_dims.size(); j++)
            {
                std::cout << output_node_dims[j];
                if (j == output_node_dims.size() - 1)
                {
                    std::cout << ")" << std::endl;
                }
                else
                {
                    std::cout << ", ";
                }
            }
        }
    }
    else
    {
        std::cout << modelPath << " is invalid model." << std::endl;
    }

    return sessionIsAvailable;
}

int XFeat::detectAndCompute(const cv::Mat &image, cv::Mat &mkpts, cv::Mat &feats, cv::Mat &sc)
{
    // Pre process
    cv::Mat preProcessedImage = cv::Mat::zeros(image.rows, image.cols, CV_32FC3);
    int stride = preProcessedImage.rows * preProcessedImage.cols;
#pragma omp parallel for
    for (int i = 0; i < stride; i++) // HWC -> CHW, BGR -> RGB
    {
        *((float *)preProcessedImage.data + i) = (float)*(image.data + i * 3 + 2);
        *((float *)preProcessedImage.data + i + stride) = (float)*(image.data + i * 3 + 1);
        *((float *)preProcessedImage.data + i + stride * 2) = (float)*(image.data + i * 3);
    }

    // Create input tensor
    int64_t input_size = preProcessedImage.rows * preProcessedImage.cols * 3;
    std::vector<int64_t> input_node_dims = {1, 3, preProcessedImage.rows, preProcessedImage.cols};
    auto memory_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    Ort::Value input_tensor = Ort::Value::CreateTensor<float>(memory_info, (float *)(preProcessedImage.data), input_size, input_node_dims.data(), input_node_dims.size());
    assert(input_tensor.IsTensor());

    // Run sessionn
    auto output_tensors =
        xfeatSession_.Run(Ort::RunOptions{nullptr}, xfeatInputNames.data(),
                          &input_tensor, xfeatInputNames.size(), xfeatOutputNames.data(), xfeatOutputNames.size());
    assert(output_tensors.size() == xfeatOutputNames.size() && output_tensors.front().IsTensor());

    // Get outputs
    auto mkptsShape = output_tensors[0].GetTensorTypeAndShapeInfo().GetShape();
    int dim1 = static_cast<int>(mkptsShape[0]); // 1
    int dim2 = static_cast<int>(mkptsShape[1]); // 4800
    int dim3 = static_cast<int>(mkptsShape[2]); // 2
    float *mkptsDataPtr = output_tensors[0].GetTensorMutableData<float>();
    // To cv::Mat
    mkpts = cv::Mat(dim1, dim2, CV_32FC(dim3), mkptsDataPtr).clone();

    auto featsShape = output_tensors[1].GetTensorTypeAndShapeInfo().GetShape();
    dim1 = static_cast<int>(featsShape[0]); // 1
    dim2 = static_cast<int>(featsShape[1]); // 4800
    dim3 = static_cast<int>(featsShape[2]); // 64
    float *featsDataPtr = output_tensors[1].GetTensorMutableData<float>();
    feats = cv::Mat(dim1, dim2, CV_32FC(dim3), featsDataPtr).clone();

    auto scShape = output_tensors[2].GetTensorTypeAndShapeInfo().GetShape();
    dim1 = static_cast<int>(scShape[0]); // 1
    dim2 = static_cast<int>(scShape[1]); // 4800
    float *scDataPtr = output_tensors[2].GetTensorMutableData<float>();
    sc = cv::Mat(dim1, dim2, CV_32F, scDataPtr).clone();

    return 0;
}

int XFeat::matchStar(const cv::Mat &mkpts0, const cv::Mat &feats0, const cv::Mat &sc0, const cv::Mat &mkpts1, const cv::Mat &feats1, cv::Mat &matches, cv::Mat &batch_indexes)
{
    auto memory_info = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);

    int64_t mkpts0_size = mkpts0.rows * mkpts0.cols * mkpts0.channels();
    std::vector<int64_t> mkpts0_dims = {mkpts0.rows, mkpts0.cols, mkpts0.channels()}; // 1x4800x2
    Ort::Value mkpts0_tensor = Ort::Value::CreateTensor<float>(memory_info, (float *)(mkpts0.data), mkpts0_size, mkpts0_dims.data(), mkpts0_dims.size());

    int64_t feats0_size = feats0.rows * feats0.cols * feats0.channels();
    std::vector<int64_t> feats0_dims = {feats0.rows, feats0.cols, feats0.channels()}; // 1x4800x64
    Ort::Value feats0_tensor = Ort::Value::CreateTensor<float>(memory_info, (float *)(feats0.data), feats0_size, feats0_dims.data(), feats0_dims.size());

    int64_t sc0_size = sc0.rows * sc0.cols;
    std::vector<int64_t> sc0_dims = {sc0.rows, sc0.cols}; // 1x4800
    Ort::Value sc0_tensor = Ort::Value::CreateTensor<float>(memory_info, (float *)(sc0.data), sc0_size, sc0_dims.data(), sc0_dims.size());

    int64_t mkpts1_size = mkpts1.rows * mkpts1.cols * mkpts1.channels();
    std::vector<int64_t> mkpts1_dims = {mkpts1.rows, mkpts1.cols, mkpts1.channels()}; // 1x4800x2
    Ort::Value mkpts1_tensor = Ort::Value::CreateTensor<float>(memory_info, (float *)(mkpts1.data), mkpts1_size, mkpts1_dims.data(), mkpts1_dims.size());

    int64_t feats1_size = feats1.rows * feats1.cols * feats1.channels();
    std::vector<int64_t> feats1_dims = {feats1.rows, feats1.cols, feats1.channels()}; // 1x4800x64
    Ort::Value feats1_tensor = Ort::Value::CreateTensor<float>(memory_info, (float *)(feats1.data), feats1_size, feats1_dims.data(), feats1_dims.size());

    // Create input tensors
    std::vector<Ort::Value> input_tensors;
    input_tensors.push_back(std::move(mkpts0_tensor));
    input_tensors.push_back(std::move(feats0_tensor));
    input_tensors.push_back(std::move(sc0_tensor));
    input_tensors.push_back(std::move(mkpts1_tensor));
    input_tensors.push_back(std::move(feats1_tensor));

    // Run session
    auto output_tensors =
        matchingSession_.Run(Ort::RunOptions{nullptr}, matchingInputNames.data(),
                             input_tensors.data(), matchingInputNames.size(), matchingOutputNames.data(), matchingOutputNames.size());

    std::cout << output_tensors.size() << std::endl;
    std::cout << xfeatOutputNames.size() << std::endl;

    // assert(output_tensors.size() == xfeatOutputNames.size() && output_tensors.front().IsTensor());
    assert(output_tensors.size() == matchingOutputNames.size() && output_tensors.front().IsTensor());

    // Get outputs
    auto matchesShape = output_tensors[0].GetTensorTypeAndShapeInfo().GetShape();
    int dim1 = static_cast<int>(matchesShape[0]); // num
    int dim2 = static_cast<int>(matchesShape[1]); // 4
    // To cv::Mat
    float *matchesDataPtr = output_tensors[0].GetTensorMutableData<float>();
    matches = cv::Mat(dim1, dim2, CV_32F, matchesDataPtr).clone();

    auto batch_indexesShape = output_tensors[1].GetTensorTypeAndShapeInfo().GetShape();
    dim1 = static_cast<int>(batch_indexesShape[0]); // num

    float *batch_indexesDataPtr = output_tensors[0].GetTensorMutableData<float>();
    batch_indexes = cv::Mat(dim1, 1, CV_32F, batch_indexesDataPtr).clone();

    return 0;
}

cv::Mat XFeat::warpCornersAndDrawMatches(const std::vector<cv::Point2f> &refPoints, const std::vector<cv::Point2f> &dstPoints, const cv::Mat &img1, const cv::Mat &img2)
{
    // Step 1: Calculate the Homography matrix and mask
    cv::Mat mask;
    cv::Mat H = cv::findHomography(refPoints, dstPoints, cv::USAC_MAGSAC, 3.5, mask, 1000, 0.999);
    mask = mask.reshape(1, mask.total()); // Flatten the mask

    // Step 2: Get corners of the first image (img1)
    std::vector<cv::Point2f> cornersImg1 = {cv::Point2f(0, 0), cv::Point2f(img1.cols - 1, 0),
                                            cv::Point2f(img1.cols - 1, img1.rows - 1), cv::Point2f(0, img1.rows - 1)};
    std::vector<cv::Point2f> warpedCorners(4);

    // Step 3: Warp corners to the second image (img2) space
    cv::perspectiveTransform(cornersImg1, warpedCorners, H);

    // Step 4: Draw the warped corners in image2
    cv::Mat img2WithCorners = img2.clone();
    for (size_t i = 0; i < warpedCorners.size(); i++)
    {
        cv::line(img2WithCorners, warpedCorners[i], warpedCorners[(i + 1) % 4], cv::Scalar(0, 255, 0), 4);
    }

    // Step 5: Prepare keypoints and matches for drawMatches function
    std::vector<cv::KeyPoint> keypoints1, keypoints2;
    std::vector<cv::DMatch> matches;
    for (size_t i = 0; i < refPoints.size(); i++)
    {
        if (mask.at<uchar>(i))
        { // Only consider inliers
            keypoints1.emplace_back(refPoints[i], 5);
            keypoints2.emplace_back(dstPoints[i], 5);
        }
    }
    for (size_t i = 0; i < keypoints1.size(); i++)
    {
        matches.emplace_back(i, i, 0);
    }

    // Draw inlier matches
    cv::Mat imgMatches;
    cv::drawMatches(img1, keypoints1, img2WithCorners, keypoints2, matches, imgMatches, cv::Scalar(0, 255, 0), cv::Scalar::all(-1), std::vector<char>(), cv::DrawMatchesFlags::NOT_DRAW_SINGLE_POINTS);

    return imgMatches;
}

// for onnx model path
const ORTCHAR_T *XFeat::stringToOrtchar_t(std::string const &s)
{
#ifdef _WIN32
    const char *CStr = s.c_str();
    size_t len = strlen(CStr) + 1;
    size_t converted = 0;
    wchar_t *WStr;
    WStr = (wchar_t *)malloc(len * sizeof(wchar_t));
    mbstowcs_s(&converted, WStr, len, CStr, _TRUNCATE);

    return WStr;
#else
    return s.c_str();
#endif // _WIN32
}
