# OKVIS2-X DBoW3 Integration Refactoring Analysis

## 1. Current Architecture Overview

### 1.1 Component Integration Points

The codebase integrates three feature descriptor systems:

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend.cpp (1400+ lines)               │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────────┐  ┌──────────────────────┐          │
│  │   BRISK Pipeline     │  │   DL Features Path   │          │
│  │  (Traditional)       │  │  (DeepLearning)      │          │
│  ├──────────────────────┤  ├──────────────────────┤          │
│  │ detectAndDescribe()  │  │ initialiseDlFeatures│          │
│  │ - ORB/BRISK/AKAZE   │  │ - SuperPoint        │          │
│  │ - Hamming Distance   │  │ - LightGlue Matcher │          │
│  │                      │  │ - Float32 (256-dim) │          │
│  └──────────────────────┘  └──────────────────────┘          │
│           ↓                           ↓                       │
│  ┌──────────────────────────────────────────────────┐        │
│  │         Feature Matching & 3D Association        │        │
│  │    matchToMap() / matchMotionStereo() etc.       │        │
│  └──────────────────────────────────────────────────┘        │
│           ↓                                                   │
│  ┌──────────────────────────────────────────────────┐        │
│  │      Place Recognition & Loop Closure           │        │
│  │  (DBoW-based Bag-of-Words with BRISK only)      │        │
│  │                                                  │        │
│  │  - DBoW class (PIMPL) wraps vocabulary & db    │        │
│  │  - queryDatabase() → getFilteredDBoWResult()    │        │
│  │  - verifyRecognisedPlace() + RANSAC 3D→2D      │        │
│  │  - Ceres pose refinement                        │        │
│  └──────────────────────────────────────────────────┘        │
│           ↓                                                   │
│  ┌──────────────────────────────────────────────────┐        │
│  │    Multi-Session/Multi-Agent Place Recognition │        │
│  │         (componentDBows_ vector)                 │        │
│  └──────────────────────────────────────────────────┘        │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 File Organization

**DBow3_dpl-main Library:**
- Vocabulary tree with hierarchical k-means (k=9, L=3 in demo)
- Supports multiple descriptor types via template specialization
- Query and scoring with multiple metrics (L1_NORM, L2_NORM, CHI_SQUARE, BHATTACHARYYA, KL, DOT_PRODUCT)

**OKVIS2-X Frontend Integration:**
- `Frontend.hpp`: Main class structure (556 lines)
- `Frontend.cpp`: Implementation (1400+ lines)
- `DLFeatureExtractor.hpp`: ONNX Runtime wrapper for SuperPoint/LightGlue
- `FBrisk.hpp/cpp`: DBoW2 template specialization for BRISK binary descriptors (48 bytes)

**Parameter Configuration:**
- `Parameters.hpp`: FrontendParameters struct with DL feature flags
- `euroc/okvis2.yaml`: YAML configuration with DL extractor paths and thresholds

---

## 2. Current DBoW Integration Details

### 2.1 DBoW Class (PIMPL Pattern - Lines 116-137 of Frontend.cpp)

```cpp
class Frontend::DBoW {
  DBoW2::TemplatedVocabulary<DBoW2::FBrisk::TDescriptor, DBoW2::FBrisk> vocabulary;
  DBoW2::TemplatedDatabase<DBoW2::FBrisk::TDescriptor, DBoW2::FBrisk> database;
};
```

**Key Characteristics:**
- **Vocabulary file**: `small_voc.yml.gz` (gzipped YAML format)
- **Descriptor type**: BRISK only (48 bytes per descriptor, binary)
- **Tree structure**: k=9, L=3 (from demo_general.cpp parameters)
- **Database tracking**: Maintains association between image IDs and vocabulary words
- **No direct index**: Line 138 in Frontend.cpp: `database(vocabulary, false, 0)` - disables direct index feature

### 2.2 Feature Path Selection Logic (Lines 1039-1075 in Frontend.cpp)

```cpp
const bool dlModeForDBoW = (framesInOut->numKeypoints() > 0
    && numCameras_ > 0
    && framesInOut->numKeypoints(0) > 0
    && framesInOut->descriptorType(0) == CV_32F);  // ← Detects DL features

if (!dlModeForDBoW) {
    // BRISK mode: use descriptors from main pipeline
    // Direct copy of 48-byte BRISK descriptors
} else {
    // DL mode: side-run BRISK on camera 0 image ONLY
    // Purpose: Get BRISK descriptors for DBoW vocabulary
    // DL descriptors (256-dim float) cannot be added to BRISK vocabulary
}
```

**Critical Insight**: When DL features are active, **two separate feature extractions** happen:
1. **Primary path**: SuperPoint keypoints + LightGlue matching (float descriptors)
2. **Secondary path**: BRISK detection+description on camera 0 (for DBoW place recognition only)

### 2.3 Place Recognition Pipeline (Lines 1078-1120 and 1122-1135)

**Two-tier loop closure architecture:**

**Tier 1: Multi-Session/Multi-Agent Recognition** (componentDBows_)
```cpp
for (uint64_t c = 0; c < componentDBows_.size(); ++c) {
    getFilteredDBoWResult(componentDBows_.at(c), features, stateIds);
    // For each matched state ID:
    if (p > 0.4) {  // Confidence threshold
        verifyRecognisedPlace(...);  // 3D→2D RANSAC
        estimator.T_AiS_[StateId(...)][c] = T_Sold_Snew;  // Store transformation
    }
}
```

**Tier 2: Main Loop Closures** (dBow_)
```cpp
getFilteredDBoWResult(dBow_, features, stateIds);
// Similar verification and RANSAC logic
```

---

## 3. Descriptor Distance Metrics

### 3.1 BRISK Hamming Distance (Lines 64-67 of FBrisk.cpp)

```cpp
double FBrisk::distance(const FBrisk::TDescriptor &a, const FBrisk::TDescriptor &b) {
    return double(brisk::Hamming::PopcntofXORed(&a.front(), &b.front(), L/16));
}
```

- **Method**: Popcount of XORed binary descriptors
- **Time complexity**: O(6) operations for 48 bytes (48/8=6 uint64_t)
- **Used in**: DBoW vocabulary tree node clustering during kmeans

### 3.2 DL Feature Distance (Cosine Similarity)

From Frontend.cpp lines 1-100 (shown in previous context):
```cpp
double dlDescDist(const cv::Mat& a, const cv::Mat& b) {
    return 1.0 - cosine_similarity(a, b);
}
```

- **Method**: 1 - cosine similarity of float vectors
- **Time complexity**: O(256) dot product operations
- **Used in**: SuperPoint matching via LightGlue

---

## 4. Potential Refactoring Opportunities

### 4.1 **Critical Issues**

#### Issue 1: Descriptor Format Incompatibility
**Problem**: DL features (float32, 256-dim) fundamentally incompatible with DBoW BRISK vocabulary (binary, 48 bytes)

**Current Workaround** (Lines 1059-1074):
- When DL features detected, spawn **second feature extraction thread** to get BRISK descriptors
- Only BRISK descriptors added to DBoW vocabulary
- DL descriptors used only for primary matching

**Refactoring Options**:
1. **Option A: Template-based DBoW descriptor support** (Moderate effort)
   - Create new FDLFeature template specialization in DBoW2
   - Implement meanValue(), distance() for float vectors
   - Adapt vocabulary tree kmeans to use cosine distance
   - **Pros**: Eliminates redundant BRISK extraction; leverages DL features for place recognition
   - **Cons**: Requires DBoW library modification; changes vocabulary file format

2. **Option B: Dual-vocabulary architecture** (Low effort)
   - Keep current BRISK vocabulary for place recognition
   - Add parallel DL-feature vocabulary for enhanced matching
   - **Pros**: Minimal changes; backward compatible
   - **Cons**: Doubles memory footprint; increases query latency

3. **Option C: Hybrid distance metrics** (Low-medium effort)
   - Quantize float descriptors to binary (e.g., via Hamming threshold)
   - Use LSH or product quantization for approximate matching
   - **Pros**: Reuses existing BRISK infrastructure
   - **Cons**: Information loss; requires careful quantization tuning

#### Issue 2: Performance Inefficiency - Redundant Feature Extraction
**Problem**: In DL mode, BRISK detection+compute runs sequentially on full image after SuperPoint extraction

**Current code** (Lines 1062-1067):
```cpp
std::lock_guard<std::mutex> lock(*featureDetectorMutexes_[0]);
featureDetectors_[0]->detect(framesInOut->image(0), briskKpts);  // Full image re-scan
if (!briskKpts.empty()) {
    descriptorExtractors_[0]->compute(framesInOut->image(0), briskKpts, briskDescs);
}
```

**Refactoring Opportunity**:
- **Option A: Batch feature extraction** (Medium effort)
  - Extract SuperPoint + BRISK features in parallel thread pools
  - **Estimated gain**: 15-25% wall-clock time reduction
  
- **Option B: Selective extraction** (Low effort)
  - Extract BRISK only on image regions with SuperPoint matches
  - Skip BRISK extraction if SuperPoint returns >N keypoints
  - **Estimated gain**: 10-15% time reduction; requires confidence tuning

#### Issue 3: Hard-coded Vocabulary File Path
**Problem**: Line 122 in Frontend.cpp:
```cpp
vocabulary(dBowVocDir+"/small_voc.yml.gz")  // Assumes specific filename
```

**Refactoring Opportunity**:
- Accept vocabulary filename as parameter
- **Impact**: Allows flexible vocabulary selection at runtime

### 4.2 **Architectural Improvements**

#### Opportunity 1: Abstraction Layer for Feature Descriptors
**Current state**: Frontend.cpp tightly couples BRISK, DL, and ORB paths

**Refactoring**:
```cpp
class DescriptorExtractor {
    virtual void extract(const cv::Mat& image, 
                        std::vector<cv::KeyPoint>& kpts,
                        cv::Mat& descriptors) = 0;
    virtual int descriptorSize() = 0;
    virtual int descriptorType() = 0;  // CV_8U vs CV_32F
};

class BriskExtractor : public DescriptorExtractor { ... };
class DLExtractor : public DescriptorExtractor { ... };
class OrbExtractor : public DescriptorExtractor { ... };
```

**Benefits**:
- Eliminates conditional compilation flags (#ifdef OKVIS_USE_DL_FEATURES)
- Enables runtime feature extractor switching
- **Estimated lines reduced**: 200-300 in Frontend.cpp

#### Opportunity 2: Unified Loop Closure Interface
**Current state**: Separate code paths for verifyRecognisedPlace() with DL vs BRISK matching

**Refactoring**:
```cpp
class LoopClosureMatcher {
    virtual bool match(const cv::Mat& queryDesc,
                      const cv::Mat& refDesc,
                      std::vector<cv::DMatch>& matches) = 0;
};

class BriskLoopClosureMatcher : public LoopClosureMatcher { ... };
class DLLoopClosureMatcher : public LoopClosureMatcher { ... };
```

**Location**: Lines 403-495 in Frontend.cpp (verifyRecognisedPlace DL matching section)

**Benefits**:
- **Reduced complexity**: 90-line method becomes two 45-line specializations
- **Testability**: Each matcher independently unit-testable

#### Opportunity 3: Component DBoW Management
**Current state**: componentDBows_ is std::vector<std::unique_ptr<DBoW>>

**Refactoring**:
```cpp
class ComponentDBoWManager {
    void queryAll(const std::vector<uchar>& features,
                 std::map<uint64_t, std::vector<std::pair<StateId, double>>>& results);
    void addComponent(const DBoW2::TemplatedVocabulary<...>& vocab);
};
```

**Current code** (Lines 1080-1119):
```cpp
for (uint64_t c = 0; c < componentDBows_.size(); ++c) {
    getFilteredDBoWResult(componentDBows_.at(c), features, stateIds);
    // ...
}
```

**Benefits**:
- Encapsulates multi-agent/multi-session logic
- **Estimated lines saved**: 40-50

---

## 5. DBoW Integration Points - Detailed Code Map

### 5.1 Vocabulary Creation Path (Not in current frontend, but in demo_general.cpp)

**File**: DBow3_dpl-main/utils/demo_general.cpp, lines 287-325
```cpp
void testVocCreation(const vector<cv::Mat> &features) {
    const int k = 9;
    const int L = 3;
    const WeightingType weight = TF_IDF;
    const ScoringType score = L1_NORM;
    
    DBoW3::Vocabulary voc(k, L, weight, score);
    voc.create(features);
    voc.save("small_voc.yml.gz");
}
```

**Key Parameters**:
- `k=9`: Branching factor (9-way tree)
- `L=3`: Depth levels (tree height = 3, total nodes ≈ 9^3 = 729 leaves)
- `TF_IDF`: Term-frequency inverse-document-frequency weighting
- `L1_NORM`: Manhattan distance scoring

**OKVIS2-X Uses Pre-trained Vocabulary**:
- Loads from `small_voc.yml.gz` (not created at runtime)
- Location: `resources/small_voc.yml.gz` copied to build directory

### 5.2 Database Querying Path (Frontend.cpp)

**Step 1**: getFilteredDBoWResult() - Line 849+
```cpp
int Frontend::getFilteredDBoWResult(const std::unique_ptr<DBoW> &dBow,
                                    const vector<std::vector<uchar>> &features,
                                    vector<std::pair<StateId, double>> &stateIds) {
    DBoW2::QueryResults dBoWResult;
    dBow->database.query(features);  // ← Core DBoW query
    // Filtering and sorting logic
}
```

**Step 2**: Query process (DBoW2 library, opaque to frontend):
1. **Transform features to BoW vectors**: For each BRISK descriptor, traverse vocabulary tree
   - Start at root, compute distance to k child nodes
   - Take minimum distance path downward
   - Reach leaf node → assign word ID
   
2. **Score against all database entries**: Use scoring metric (L1_NORM)
   - Each entry has stored BoW vector from keyframe
   - Score = similarity metric(query_bow, entry_bow)
   
3. **Return top-N results sorted by score**

**Step 3**: Verification - verifyRecognisedPlace() - Lines 384-846
```cpp
bool Frontend::verifyRecognisedPlace(...) {
    // DL matching section (Lines 403-495)
    if (params.frontend.use_dl_features) {
        dlMatcher_->match(queryDescs, refDescs, ...);
    } else {
        // BRISK Hamming matching
    }
    
    // 3D→2D RANSAC (Lines 589-622)
    // Ceres refinement (Lines 678-845)
}
```

### 5.3 Feature Addition to Database (Lines 241-261)

```cpp
// Create component DBoW at line 242
componentDBows_.emplace_back(std::unique_ptr<DBoW>(new DBoW(dBow_->vocabulary)));

// Add features at lines 244-261
for (size_t i = 0; i < numCameras_; ++i) {
    // Extract BRISK descriptors...
    dBow->database.add(features);  // ← Adds BoW vector to database
}
```

**Note**: Lines 250-254 skip DL descriptors:
```cpp
if (framesInOut->descriptorType(im) == CV_32F) {
    // DL descriptors -- cannot be added to BRISK DBoW vocabulary
    continue;
}
```

---

## 6. Configuration Parameter Flow

### 6.1 Parameter Reading (Parameters.hpp, lines 110-120)

```cpp
struct FrontendParameters {
    float detection_threshold;
    float absolute_threshold;
    float matching_threshold;
    int octaves;
    int max_num_keypoints;
    float keyframe_overlap;
    bool use_cnn;
    bool parallelise_detection;
    int num_matching_threads;
    
    // DL feature parameters
    bool use_dl_features;
    std::string dl_extractor_type;
    std::string dl_extractor_path;
    std::string dl_matcher_path;
    double dl_match_threshold;
    int dl_image_size;
    bool dl_use_gpu;
};
```

### 6.2 YAML Configuration (euroc/okvis2.yaml, lines 71-87)

```yaml
frontend_parameters:
    use_dl_features: true
    dl_extractor_type: superpoint
    dl_extractor_path: "/home/lhk/workspace/OKVIS2-X/weight/superpoint.onnx"
    dl_matcher_path: "/home/lhk/workspace/OKVIS2-X/weight/superpoint_lightglue_fused_cpu.onnx"
    dl_match_threshold: 0.7
    dl_image_size: 512
    dl_use_gpu: false
```

### 6.3 Initialization in Constructor (Frontend.cpp, lines 192-225)

```cpp
void Frontend::initialiseDlFeatures(const FrontendParameters& params) {
    if (!params.use_dl_features) return;
    
    dlExtractor_ = std::make_unique<okvis::dl::DLFeatureExtractor>(
        params.dl_extractor_path,
        params.dl_use_gpu,
        0,  // device ID
        params.dl_image_size);
    
    dlMatcher_ = std::make_unique<okvis::dl::DLFeatureMatcher>(
        params.dl_matcher_path,
        params.dl_match_threshold,
        params.dl_use_gpu,
        0);
}
```

---

## 7. Compilation Flags and Conditional Code

### 7.1 Current Flags

**In CMakeLists.txt**:
```cmake
if(USE_DL_FEATURES AND USE_GPU)
    add_definitions(-DOKVIS_USE_DL_FEATURES -DOKVIS_USE_GPU)
endif()
if(USE_NN)
    add_definitions(-DOKVIS_USE_NN -DC10_USE_GLOG)
endif()
```

### 7.2 Conditional Code Sections

| Line Range | Condition | Purpose |
|-----------|-----------|---------|
| 192-225 | `#ifdef OKVIS_USE_DL_FEATURES` | DL features initialization |
| 289-331 | `#ifdef OKVIS_USE_DL_FEATURES` | DL extraction path in detectAndDescribe |
| 332-382 | else | BRISK fallback path |
| 403-495 | varies | DL vs BRISK matching in verification |
| 1059-1074 | `#ifdef OKVIS_USE_DL_FEATURES` | Secondary BRISK extraction for DBoW |
| 1237-1273 | `#ifdef OKVIS_USE_NN` | CNN classification threads |

**Refactoring Opportunity**: Replace compile-time flags with runtime configuration
- Reduces code duplication
- Enables feature toggling without recompilation
- **Effort**: Medium (requires careful state management)

---

## 8. Thread Safety Analysis

### 8.1 Current Mutex Usage

**In Frontend.cpp**:
```cpp
featureDetectorMutexes_;      // Per-camera
featureComputerMutexes_;      // Per-camera
dlMutex_;                      // DL extractor access
```

**Critical Sections**:
- Line 1063: `std::lock_guard<std::mutex> lock(*featureDetectorMutexes_[0]);`
  - Protects BRISK detection on camera 0 (secondary extraction path)
  - Duration: ~10-50ms depending on image size

- DL extractor: Serialized access to avoid ONNX concurrency issues

**Refactoring Opportunity**: Thread pool pattern
```cpp
class ThreadPoolExecutor {
    Future<ExtractionResult> extract(const cv::Mat& image, 
                                    DescriptorExtractor* extractor);
};
```
- **Benefit**: Avoid mutex contention; better CPU utilization
- **Estimated improvement**: 20-30% latency reduction in multi-camera setup

---

## 9. Summary of Refactoring Recommendations

### Priority 1 (High Impact, Medium Effort)
1. **Eliminate redundant BRISK extraction** (Issue 2)
   - Implement option B: batch feature extraction
   - Expected time savings: 15-25%

2. **Abstract descriptor extraction** (Opportunity 1)
   - Create DescriptorExtractor interface
   - Lines saved: 200-300
   - Code clarity: High

### Priority 2 (Medium Impact, Low Effort)
1. **Parameterize vocabulary filename** (Issue 3)
   - Allow runtime vocabulary selection
   - Lines changed: ~5

2. **Replace compile-time with runtime flags** (Section 7.2)
   - Remove #ifdef OKVIS_USE_DL_FEATURES
   - Lines saved: 100-150

### Priority 3 (Long-term, Higher Effort)
1. **Template-based DL feature support in DBoW** (Issue 1, Option A)
   - Enable direct DL descriptor place recognition
   - Estimated effort: 3-4 weeks
   - Impact: Eliminates entire secondary extraction path

2. **Unified loop closure interface** (Opportunity 2)
   - Separate matching strategies
   - Lines saved: 40-50

3. **Component DBoW manager** (Opportunity 3)
   - Encapsulate multi-agent logic
   - Lines saved: 40-50

---

## 10. Code Statistics

| Component | Files | Lines | Language |
|-----------|-------|-------|----------|
| DBow3_dpl-main | 15+ | ~2000 | C++ (header-heavy) |
| OKVIS2-X Frontend | 5 | ~2100 | C++ |
| Parameter config | 2 | ~300 | C++ + YAML |
| **Total integration** | 22+ | ~4400 | Mixed |

**Hotspots** (>100 lines each):
- Frontend.cpp lines 384-846: verifyRecognisedPlace()
- Frontend.cpp lines 918-1236: dataAssociationAndInitialization()
- DLFeatureExtractor.hpp: ONNX session management

