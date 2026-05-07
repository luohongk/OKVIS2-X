# DBow3_dpl

本项目是关于如何用dbow3训练自己的词袋

### 1.图片输入格式

* 首先搞清楚图片的格式，命名方式从1.png到n.png。也可以是1.jpg到n.jpg

![My Image](source/geshi.png)

* 图片比较杂乱的话说就用文件夹中的脚本进行转化（imagesProcess.py)
  这个脚本里面，如果你的图片是jpg需要把脚本稍微改动一下
* ```
  new_filename = str(i + 1) + ".png"
  改造为
  new_filename = str(i + 1) + ".jpg"
  ```

## 安装好第三方库

Eigen,OpenCV4.5.4,onnxruntime1.17.3

## 编辑模型路径

DBow3_sp/utils/superpoint/include/superpoint.h，改成在自己电脑上的路径，模型在weight_dpl文件夹中

```
	string extractor_weight_global_path = "/home/lhk/workspace/DBow3_sp/utils/superpoint/weights_dpl/superpoint.onnx";
	string matcher_weight_global_path = "/home/lhk/workspace/DBow3_sp/utils/superpoint/weights_dpl/superpoint_lightglue_fused_cpu.onnx";
```

DBow3_sp/utils/demo_general.cpp，改称在自己电脑上的路径，模型在DBow3_sp/utils/xfeat/model文件夹中

```
        std::string xfeatModelPath = "/home/lhk/workspace/DBow3_dpl/utils/xfeat/model/xfeat_dualscale.onnx";
        std::string matchingModelPath = "/home/lhk/workspace/DBow3_dpl/utils/xfeat/model/xfeat_matching.onnx";
```

## 编译运行

```
 cd DBow3_dpl
cmake ..
make
cd utils/
使用ORB特征
./demo_general orb /home/lhk/data/renamed_images
使用XFeat特征
./demo_general xfeat /home/lhk/data/renamed_images
使用SuperPoint特征
./demo_general superpoint /home/lhk/data/renamed_images

```

能够训练了
