# Enable C++11
set(CMAKE_CXX_STANDARD 14)

#onnxruntime providers
option(onnxruntime_USE_CUDA "Build with CUDA support" OFF)
option(onnxruntime_USE_TENSORRT "Build with TensorRT support" OFF)
option(LIBPNG_ROOTDIR "libpng root dir")
option(ONNXRUNTIME_ROOTDIR "onnxruntime root dir")

set(ONNXRUNTIME_ROOTDIR "/home/lhk/Thirdparty/onnxruntime")
include_directories("${ONNXRUNTIME_ROOTDIR}/include")
link_directories("${ONNXRUNTIME_ROOTDIR}/lib")



find_package(JPEG)
find_package(PNG)
find_package(Eigen3)
# find_package(OpenCV  REQUIRED)
find_package(OpenCV 4 PATHS "/home/lhk/Thirdparty/opencv4.5.4")

# file(GLOB SRC_SOURCE "XFeat.cpp")

INCLUDE_DIRECTORIES(
${PROJECT_SOURCE_DIR}/src/
${PROJECT_SOURCE_DIR}/utils/xfeat/include/
${PROJECT_SOURCE_DIR}/utils/superpoint/include/
${PROJECT_SOURCE_DIR}/utils/superpoint/ort_include/
# ${OpenCV_INCLUDE_DIRS}
${EIGEN3_INCLUDE_DIR}
)


LINK_LIBRARIES(${PROJECT_NAME})
link_libraries(${OpenCV_LIBRARIES})
IF(OPENCV_VERSION_3)

file(GLOB XFEAT "xfeat/include/*.cpp")
file(GLOB SUPERPOINT "superpoint/include/*.cpp")

ADD_EXECUTABLE(demo_general demo_general.cpp ${XFEAT} ${SUPERPOINT})

target_link_libraries(demo_general ${OpenCV_LIBRARIES} onnxruntime)
ADD_EXECUTABLE(create_voc_step0 create_voc_step0.cpp)
ADD_EXECUTABLE(create_voc_step1 create_voc_step1.cpp)
INSTALL(TARGETS  demo_general  create_voc_step0  create_voc_step1  RUNTIME DESTINATION bin)
ENDIF()
