/**
 * Date:  2016
 * Author: Rafael Muñoz Salinas
 * Description: demo application of DBoW3
 * License: see the LICENSE.txt file
 */

#include <iostream>
// #include <filesystem>
#include <vector>

// DBoW3
#include "DBoW3.h"
#include <dirent.h>
#include <string.h>

// OpenCV
#include <opencv2/core/core.hpp>
#include <opencv2/highgui/highgui.hpp>
#include <opencv2/features2d/features2d.hpp>
#ifdef USE_CONTRIB
#include <opencv2/xfeatures2d/nonfree.hpp>
#include <opencv2/xfeatures2d.hpp>
#endif
#include "DescManip.h"

#include <chrono>
#include <iostream>
#include "omp.h"

#include <opencv2/opencv.hpp>
#include <onnxruntime_cxx_api.h>
#include "XFeat.h"
#include "superpoint/include/superpoint.h"
// #include "./xfeat/include/XFeat.h"

using namespace DBoW3;
using namespace std;
// namespace fs = std::filesystem;

// command line parser
class CmdLineParser
{
    int argc;
    char **argv;

public:
    CmdLineParser(int _argc, char **_argv) : argc(_argc), argv(_argv) {}
    bool operator[](string param)
    {
        int idx = -1;
        for (int i = 0; i < argc && idx == -1; i++)
            if (string(argv[i]) == param)
                idx = i;
        return (idx != -1);
    }
    string operator()(string param, string defvalue = "-1")
    {
        int idx = -1;
        for (int i = 0; i < argc && idx == -1; i++)
            if (string(argv[i]) == param)
                idx = i;
        if (idx == -1)
            return defvalue;
        else
            return (argv[idx + 1]);
    }
};

// - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

// extended surf gives 128-dimensional vectors
const bool EXTENDED_SURF = false;
// - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

void wait()
{
    cout << endl
         << "Press enter to continue" << endl;
    getchar();
}

vector<string> readImagePaths(string path)
{
    vector<string> paths;
    DIR *dir;
    struct dirent *ent;
    int imageCount = 0;

    dir = opendir(path.c_str());
    if (dir != NULL)
    {
        while ((ent = readdir(dir)) != NULL)
        {
            if (ent->d_type == DT_REG)
            {
                std::string fileName = ent->d_name;

                // 支持的集中图片类型
                if (fileName.length() >= 4 && (fileName.substr(fileName.length() - 4) == ".jpg" ||
                                               fileName.substr(fileName.length() - 4) == ".png" ||
                                               fileName.substr(fileName.length() - 4) == ".gif" ||
                                               fileName.substr(fileName.length() - 4) == ".bmp"))
                {
                    imageCount++;
                }
            }
        }
        closedir(dir);
    }
    else
    {
        std::cerr << "Error opening directory: " << path << std::endl;
        paths.push_back("error");
        return paths;
    }
    std::cout << "Total number of images: " << imageCount << std::endl;

    for (int i = 1; i < imageCount + 1; i++)
    {
        string tempath = path + "/" + to_string(i) + ".png";
        paths.push_back(tempath);
    }

    return paths;
}

vector<cv::Mat> loadFeatures(std::vector<string> path_to_images, string descriptor = "") throw(std::exception)
{
    // select detector
    cv::Ptr<cv::Feature2D> fdetector;
    if (descriptor == "orb")
        fdetector = cv::ORB::create();
    else if (descriptor == "brisk")
        fdetector = cv::BRISK::create();

    else if (descriptor == "xfeat")
    {
        cout << "目前是xfeat特征进行训练" << endl;
    }

    else if (descriptor == "superpoint")
    {
        cout << "目前是superpoint特征进行训练" << endl;
    }
#ifdef OPENCV_VERSION_3
    else if (descriptor == "akaze")
        fdetector = cv::AKAZE::create();
#endif
#ifdef USE_CONTRIB
    else if (descriptor == "surf")
        fdetector = cv::xfeatures2d::SURF::create(400, 4, 2, EXTENDED_SURF);
#endif

    else
        throw std::runtime_error("Invalid descriptor");
    assert(!descriptor.empty());

    vector<cv::Mat> features;

    // 如果深度学习描述子是xfeat的话
    if (descriptor == "xfeat")
    {
        // 提取深度学习特征
        std::string xfeatModelPath = "/home/lhk/workspace/DBow3_dpl/utils/xfeat/model/xfeat_dualscale.onnx";
        std::string matchingModelPath = "/home/lhk/workspace/DBow3_dpl/utils/xfeat/model/xfeat_matching.onnx";

        // 初始化xfeat对象
        // Init xfeat object
        XFeat xfeat(xfeatModelPath, matchingModelPath);

        // 遍历每一张图像，进行特征提取，然后保存到features中
        for (size_t i = 0; i < path_to_images.size(); i++)
        {
            cv::Mat descriptors;
            cv::Mat temp_descriptors;
            cv::Mat mkpts, sc0;

            cv::Mat image = cv::imread(path_to_images[i], 0);
            if (image.empty())
                throw std::runtime_error("Could not open image" + path_to_images[i]);
            cout << "extracting" << " " << i << " " << " features" << endl;

            xfeat.detectAndCompute(image, mkpts, temp_descriptors, sc0);

            int keypoint_channel = mkpts.channels();

            std::cout << "特征点的维度为: " << keypoint_channel << std::endl;
            std::cout << "特征点的行数为: " << mkpts.rows << std::endl;
            std::cout << "特征点的列数为: " << mkpts.cols << std::endl;

            // 获取通道数，也就是获取描述子的维度
            int num_channels = temp_descriptors.channels();

            std::cout << "描述子的维度为: " << num_channels << std::endl;
            std::cout << "描述子的行数为: " << temp_descriptors.rows << std::endl;
            std::cout << "描述子的列数为: " << temp_descriptors.cols << std::endl;

            // cv::Mat temp_descriptors;
            std::vector<cv::Mat> many_descriptors;
            cv::split(temp_descriptors, many_descriptors);

            std::cout << "通道数为: " << many_descriptors.size() << std::endl;
            std::cout << many_descriptors[0].rows << std::endl;
            std::cout << many_descriptors[0].cols << std::endl;
            std::cout << many_descriptors[0].channels() << std::endl;

            // 使用 cv::vconcat() 将所有 cv::Mat 实例垂直拼接
            cv::vconcat(many_descriptors, descriptors);
            std::cout << "描述子的行数为: " << descriptors.rows << std::endl;
            std::cout << "描述子的列数为: " << descriptors.cols << std::endl;
            std::cout << descriptors.channels() << std::endl;

            cv::Mat transposed_mat;
            cv::transpose(descriptors, transposed_mat);

            std::cout << "描述子的行数为: " << transposed_mat.rows << std::endl;
            std::cout << "描述子的列数为: " << transposed_mat.cols << std::endl;
            std::cout << transposed_mat.channels() << std::endl;

            cv::Mat topRows = transposed_mat.rowRange(0, 1000);

            features.push_back(topRows);
        }
    }

    else if (descriptor == "superpoint")
    {
        superpoint sp;
        cout << "Extracting   features..." << endl;
        for (size_t i = 0; i < path_to_images.size(); ++i)
        {
            // 临时变量
            vector<cv::Point2f> cur_pts;
            vector<pair<cv::Point2f, vector<float>>> cur_dplpts_descriptors;

            cout << "reading image: " << path_to_images[i] << endl;
            cv::Mat image = cv::imread(path_to_images[i], 0);
            if (image.empty())
                throw std::runtime_error("Could not open image" + path_to_images[i]);
            // 进行特征提取
            sp.extract_features_dpl(image, cur_pts, cur_dplpts_descriptors);

            // 先初始哈化描述子，cv::Mat形式
            cv::Mat descriptors(cur_dplpts_descriptors.size(), 256, CV_32FC1);

            for (int i = 0; i < cur_dplpts_descriptors.size(); i++)
            {
                for (int j = 0; j < 256; j++)
                {
                    descriptors.at<float>(i, j) = cur_dplpts_descriptors[i].second[j];
                }
            }

            features.push_back(descriptors);
            cout << "done detecting features" << endl;
        }
    }

    // 不是采用xfeat的描述子
    else
    {

        cout << "Extracting   features..." << endl;
        for (size_t i = 0; i < path_to_images.size(); ++i)
        {
            vector<cv::KeyPoint> keypoints;
            cv::Mat descriptors;
            cout << "reading image: " << path_to_images[i] << endl;
            cv::Mat image = cv::imread(path_to_images[i], 0);
            if (image.empty())
                throw std::runtime_error("Could not open image" + path_to_images[i]);
            cout << "extracting features" << endl;

            fdetector->detectAndCompute(image, cv::Mat(), keypoints, descriptors);

            std::cout << descriptors.channels() << std::endl;
            features.push_back(descriptors);
            cout << "done detecting features" << endl;
        }
    }
    return features;
}

// ----------------------------------------------------------------------------

void testVocCreation(const vector<cv::Mat> &features)
{
    // branching factor and depth levels
    const int k = 9;
    const int L = 3;
    const WeightingType weight = TF_IDF;
    const ScoringType score = L1_NORM;

    DBoW3::Vocabulary voc(k, L, weight, score);

    cout << "Creating a small " << k << "^" << L << " vocabulary..." << endl;
    voc.create(features);
    cout << "... done!" << endl;

    cout << "Vocabulary information: " << endl
         << voc << endl
         << endl;

    // lets do something with this vocabulary
    cout << "Matching images against themselves (0 low, 1 high): " << endl;
    BowVector v1, v2;
    for (size_t i = 0; i < features.size(); i++)
    {
        voc.transform(features[i], v1);
        for (size_t j = 0; j < features.size(); j++)
        {
            voc.transform(features[j], v2);

            double score = voc.score(v1, v2);
            cout << "Image " << i << " vs Image " << j << ": " << score << endl;
        }
    }

    // save the vocabulary to disk
    cout << endl
         << "Saving vocabulary..." << endl;
    voc.save("small_voc.yml.gz");
    cout << "Done" << endl;
}

////// ----------------------------------------------------------------------------

void testDatabase(const vector<cv::Mat> &features)
{
    cout << "Creating a small database..." << endl;

    // load the vocabulary from disk
    Vocabulary voc("small_voc.yml.gz");

    std::cout << "m_k" << voc.getDepthLevels() << std::endl;

    Database db(voc, false, 0); // false = do not use direct index
                                // (so ignore the last param)
                                // The direct index is useful if we want to retrieve the features that
                                // belong to some vocabulary node.
                                // db creates a copy of the vocabulary, we may get rid of "voc" now

    // add images to the database
    cout << "features size: " << features.size() << endl;

    cout << "features[0] rows: " << features[0].rows << endl;
    cout << "features[0] cols: " << features[0].cols << endl;
    cout << "features[0] channels: " << features[0].channels() << endl;

    QueryResults ret;
    for (size_t i = 0; i < features.size(); i++)
    {
        // std::cout<<
        db.query(features[i], ret, 4);

        // ret[0] is always the same image in this case, because we added it to the
        // database. ret[1] is the second best match.
        db.add(features[i]);
        cout << "Searching for Image " << i << ". " << ret << endl;
    }

    cout << "... done!" << endl;

    cout << "Database information: " << endl
         << db << endl;

    // and query the database
    cout << "Querying the database: " << endl;

    cout << endl;

    // we can save the database. The created file includes the vocabulary
    // and the entries added
    cout << "Saving database..." << endl;
    db.save("small_db.yml.gz");
    cout << "... done!" << endl;

    // once saved, we can load it again
    cout << "Retrieving database once again..." << endl;
    Database db2("small_db.yml.gz");
    cout << "... done! This is: " << endl
         << db2 << endl;
}

// ----------------------------------------------------------------------------

int main(int argc, char **argv)
{

    // 这里进行参数的确认
    try
    {
        CmdLineParser cml(argc, argv);
        if (cml["-h"] || argc <= 1)
        {
            cerr << "请你输入两个参数，一个是描述子类型(orb,brisk,xfeat可以选择)，另外一个是构建词袋文件夹的路径" << endl;
            return -1;
        }

        string descriptor = argv[1];
        string path = argv[2];

        auto images = readImagePaths(path);
        vector<cv::Mat> features = loadFeatures(images, descriptor);

        cout << "特征提取完毕" << endl;
        testVocCreation(features);

        testDatabase(features);
    }
    catch (std::exception &ex)
    {
        cerr << ex.what() << endl;
    }

    return 0;
}
