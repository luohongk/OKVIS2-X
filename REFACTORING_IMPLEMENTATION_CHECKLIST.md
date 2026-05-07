# DBoW3 Refactoring Implementation Checklist

## Pre-Implementation Setup

- [ ] **Version Control**
  - [ ] Create branch: `git checkout -b feature/dbow3-refactoring`
  - [ ] Ensure clean working tree: `git status`
  - [ ] Tag current baseline: `git tag baseline-pre-refactor-$(date +%Y%m%d)`

- [ ] **Baseline Metrics**
  - [ ] Build current code: `cmake && make -j8`
  - [ ] Run profiler on current Frontend.cpp (lines 1039-1075 specific focus)
  - [ ] Document baseline latencies:
    - [ ] Feature extraction time (DL path)
    - [ ] Feature extraction time (BRISK fallback)
    - [ ] Loop closure query time
    - [ ] Total frontend frame processing time
  - [ ] Document code metrics:
    - [ ] Total Frontend.cpp LOC
    - [ ] Duplicate code blocks (verifyRecognisedPlace logic)
    - [ ] Thread safety complexity (mutex count)

- [ ] **Testing Infrastructure**
  - [ ] Enable/create unit test targets for Frontend class
  - [ ] Create synthetic feature data for testing (DL float32 and BRISK binary)
  - [ ] Set up test fixtures for vocabulary and database operations

---

## Phase 1: Fix Hard-Coded Paths & Runtime Configuration

**Effort:** 1-2 weeks | **Impact:** 5% latency reduction | **Risk:** Low

### Files to Modify

1. **okvis_frontend/include/okvis/Frontend.hpp** (lines 116-137)
   - [ ] Review DBoW class declaration (lines ~116-137)
   - [ ] Identify hard-coded paths: `/home/lhk/workspace/...`
   - [ ] Plan member variables for configuration

2. **okvis_common/include/okvis/Parameters.hpp** (lines 110-120)
   - [ ] Add new parameters to FrontendParameters struct:
     - `vocab_path_override` (string, default empty)
     - `db_path_override` (string, default empty)
     - `use_secondary_brisk` (bool, default true for now)
   - [ ] Document each parameter with comments

3. **okvis_frontend/src/Frontend.cpp** (lines 1039-1075)
   - [ ] Locate hard-coded vocabulary path strings
   - [ ] Locate hard-coded BRISK extraction trigger logic
   - [ ] Replace with runtime configuration reads

4. **config/euroc/okvis2.yaml** (lines 71-87)
   - [ ] Add new YAML fields:
     ```yaml
     frontend:
       dbow:
         vocab_path: "/path/to/vocab.yml.gz"
         vocab_path_override: ""
         use_secondary_brisk_extraction: true
     ```

### Code Changes Template

**Before:**
```cpp
// Hard-coded path in Frontend constructor
std::string vocabPath = "/home/lhk/workspace/DBow3_dpl/utils/vocab.yml.gz";
```

**After:**
```cpp
// Constructor now reads from parameters
Frontend::Frontend(const Parameters& params) 
    : params_(params) {
  std::string vocabPath = 
    params_.frontendParams.vocab_path_override.empty() 
      ? params_.frontendParams.vocab_path 
      : params_.frontendParams.vocab_path_override;
  // ... load vocabulary
}
```

### Testing

- [ ] Unit test: verify path override works
- [ ] Unit test: verify default path is used when override empty
- [ ] Integration test: load vocabulary from config
- [ ] Integration test: verify database initialization with custom path

### Validation

- [ ] Compile without errors
- [ ] Run existing tests (should pass)
- [ ] Verify vocabulary loads correctly
- [ ] Confirm no latency regression

---

## Phase 2: Parallel Feature Extraction

**Effort:** 2-3 weeks | **Impact:** 35% latency reduction | **Risk:** Medium

### Files to Modify

1. **okvis_frontend/include/okvis/Frontend.hpp**
   - [ ] Add member variable: `std::vector<std::future<cv::Mat>> feature_futures_`
   - [ ] Add method: `std::future<cv::Mat> extractFeaturesAsync(const cv::Mat& image, FeatureType type)`
   - [ ] Add synchronization point method: `void waitForFeatures()`

2. **okvis_frontend/src/Frontend.cpp** (lines 289-331 and 332-382)
   - [ ] Extract DL feature extraction into standalone function
   - [ ] Extract BRISK feature extraction into standalone function
   - [ ] Refactor detectAndDescribe() to launch async tasks
   - [ ] Create synchronization logic in processFrame()

### Code Changes Template

**Before:**
```cpp
// Sequential extraction (lines 1062-1074)
cv::Mat dlFeatures = dlExtractor_->extract(image);
cv::Mat briskFeatures = briskExtractor_->extract(image);  // Wasteful when using DL!
```

**After:**
```cpp
// Parallel extraction
std::future<cv::Mat> dlFuture = std::async(std::launch::async, [this, &image]() {
  return dlExtractor_->extract(image);
});
std::future<cv::Mat> briskFuture;

// Only extract BRISK if actually needed
if (shouldUseBriskFallback_) {
  briskFuture = std::async(std::launch::async, [this, &image]() {
    return briskExtractor_->extract(image);
  });
}

// Wait for DL features (critical path)
cv::Mat dlFeatures = dlFuture.get();

// BRISK extraction happens in parallel if needed
if (briskFuture.valid()) {
  cv::Mat briskFeatures = briskFuture.get();
}
```

### Testing

- [ ] Unit test: verify async extraction completes
- [ ] Unit test: verify results match sequential extraction
- [ ] Unit test: verify exception handling in async tasks
- [ ] Thread safety test: concurrent frame processing
- [ ] Performance test: measure parallelism benefit

### Validation

- [ ] Compile without errors
- [ ] Run thread sanitizer: `valgrind --tool=helgrind`
- [ ] Measure latency: should see 35% improvement
- [ ] Verify correctness: visual loop closure results unchanged
- [ ] Stress test: high frame rate scenarios

---

## Phase 3: Descriptor Abstraction Layer

**Effort:** 3-4 weeks | **Impact:** 40% complexity reduction | **Risk:** Medium-High

### Files to Modify/Create

1. **okvis_frontend/include/okvis/DescriptorExtractor.hpp** (NEW)
   - [ ] Define abstract base class `DescriptorExtractor`
   - [ ] Pure virtual methods:
     - `cv::Mat extract(const cv::Mat& image)` 
     - `std::string type() const`
     - `int dimension() const`
     - `DescriptorType descriptorType() const` (returns BINARY or FLOAT32)

2. **okvis_frontend/include/okvis/DLFeatureExtractor.hpp** (NEW)
   - [ ] Specialization: `class DLFeatureExtractor : public DescriptorExtractor`
   - [ ] Members: `onnxruntime_session_`, `model_path_`
   - [ ] Implementation of all pure virtual methods
   - [ ] Private method: `preprocessImage()`

3. **okvis_frontend/include/okvis/BriskFeatureExtractor.hpp** (NEW)
   - [ ] Specialization: `class BriskFeatureExtractor : public DescriptorExtractor`
   - [ ] Members: `cv::Ptr<cv::Feature2D> detector_`
   - [ ] Implementation of all pure virtual methods

4. **okvis_frontend/include/okvis/DescriptorMatcher.hpp** (NEW)
   - [ ] Define abstract base class `DescriptorMatcher`
   - [ ] Pure virtual methods:
     - `std::vector<Match> match(const cv::Mat& descriptors1, const cv::Mat& descriptors2)`
     - `std::string type() const`

5. **okvis_frontend/include/okvis/DLFeatureMatcher.hpp** (NEW)
   - [ ] Specialization: `class DLFeatureMatcher : public DescriptorMatcher`
   - [ ] Members: `onnxruntime_session_`, `matcher_model_path_`

6. **okvis_frontend/include/okvis/BriskFeatureMatcher.hpp** (NEW)
   - [ ] Specialization: `class BriskFeatureMatcher : public DescriptorMatcher`
   - [ ] Use cv::BFMatcher with Hamming distance

7. **okvis_frontend/src/Frontend.hpp** (update)
   - [ ] Replace:
     - `std::unique_ptr<XFeat> dlExtractor_;` 
     - `std::unique_ptr<LightGlueMatcher> dlMatcher_;`
   - [ ] With:
     - `std::unique_ptr<DescriptorExtractor> featureExtractor_;`
     - `std::unique_ptr<DescriptorMatcher> featureMatcher_;`

### Code Changes Template

**Before (lines 403-495):**
```cpp
// Duplicated matching logic for DL vs BRISK
if (descriptorType == CV_32F) {
  // DL matching using cosine distance
  // 50 lines of code
} else {
  // BRISK matching using Hamming distance
  // 40 lines of code
}
```

**After:**
```cpp
// Unified matching interface
std::vector<Match> matches = featureMatcher_->match(desc1, desc2);
```

### Testing

- [ ] Unit test: DLFeatureExtractor interface compliance
- [ ] Unit test: BriskFeatureExtractor interface compliance
- [ ] Unit test: DLFeatureMatcher produces correct results
- [ ] Unit test: BriskFeatureMatcher produces correct results
- [ ] Integration test: verifyRecognisedPlace() with unified interface
- [ ] Regression test: loop closure results unchanged

### Validation

- [ ] Compile without errors
- [ ] All tests pass
- [ ] Verify descriptor format compatibility automatically checked
- [ ] Code duplication reduced (measure with `cloc` tool)
- [ ] Cyclomatic complexity reduced

---

## Phase 4: Unified Loop Closure Interface

**Effort:** 2-3 weeks | **Impact:** 30% code reduction in place recognition | **Risk:** Medium

### Files to Modify/Create

1. **okvis_frontend/include/okvis/LoopClosureMatcher.hpp** (NEW)
   - [ ] Define class `LoopClosureMatcher`
   - [ ] Members:
     - `std::unique_ptr<DBoW3::Vocabulary> vocabulary_;`
     - `std::unique_ptr<DBoW3::Database> database_;`
     - `std::unique_ptr<DescriptorExtractor> extractor_;`
     - `std::unique_ptr<DescriptorMatcher> matcher_;`
   - [ ] Public methods:
     - `QueryResults query(const cv::Mat& descriptors, int maxResults = 4)`
     - `void add(const cv::Mat& descriptors, EntryId id)`
     - `bool initialize(const std::string& vocabPath)`

2. **okvis_frontend/include/okvis/Frontend.hpp** (update)
   - [ ] Replace DBoW class (lines 116-137) with:
     - `std::unique_ptr<LoopClosureMatcher> loopClosureMatcher_;`

3. **okvis_frontend/src/Frontend.cpp** (update)
   - [ ] Lines 1039-1075: Replace feature path selection with:
     ```cpp
     loopClosureMatcher_->query(features, 4);
     ```
   - [ ] Lines 1078-1120: Simplify multi-agent recognition
   - [ ] Lines 1122-1135: Simplify main loop closure query

### Code Changes Template

**Before (lines 1039-1075):**
```cpp
// 37 lines of feature type checking and conditional extraction
if (params_.use_dl_features && features.type() == CV_32F) {
  // Extract BRISK (wasteful!)
  // Transform both BRISK and DL features separately
} else if (features.type() == CV_8U) {
  // BRISK-only path
}
```

**After:**
```cpp
// Single unified interface
loopClosureMatcher_->query(features, 4);
```

### Testing

- [ ] Unit test: LoopClosureMatcher initialization
- [ ] Unit test: query returns results
- [ ] Unit test: add stores features correctly
- [ ] Integration test: multi-agent recognition works
- [ ] Integration test: main loop closure detection works
- [ ] Performance test: no regression from abstraction layer

### Validation

- [ ] Compile without errors
- [ ] All tests pass
- [ ] Verify loop closure matches unchanged (compare binary outputs)
- [ ] Measure code reduction (~250 LOC eliminated)
- [ ] Cyclomatic complexity significantly reduced

---

## Phase 5: Component DBoW Manager

**Effort:** 1-2 weeks | **Impact:** 20% design clarity improvement | **Risk:** Low

### Files to Modify/Create

1. **okvis_frontend/include/okvis/DBoWManager.hpp** (NEW)
   - [ ] Define class `DBoWManager`
   - [ ] Manages all DBoW-related components:
     - Vocabulary lifecycle
     - Database persistence
     - Multi-agent/multi-session coordination
     - Thread synchronization
   - [ ] Public methods:
     - `initialize(const FrontendParameters& params)`
     - `registerSession(SessionId id)`
     - `unregisterSession(SessionId id)`
     - `queryAllSessions(const cv::Mat& features) -> QueryResults`

2. **okvis_frontend/src/DBoWManager.cpp** (NEW)
   - [ ] Implement lifecycle management
   - [ ] Implement session registration/query logic
   - [ ] Implement thread-safe operations

3. **okvis_frontend/include/okvis/Frontend.hpp** (update)
   - [ ] Replace individual loop closure members with:
     - `std::unique_ptr<DBoWManager> dbow_manager_;`

### Code Changes Template

**Before (lines 1078-1120):**
```cpp
// Manual multi-agent/multi-session management
for (auto& agent : agents_) {
  for (auto& session : agent.sessions) {
    QueryResults ret = session.database.query(features);
    // Manual result aggregation
  }
}
```

**After:**
```cpp
// Unified manager interface
QueryResults ret = dbow_manager_->queryAllSessions(features);
```

### Testing

- [ ] Unit test: DBoWManager initialization
- [ ] Unit test: session registration
- [ ] Unit test: multi-session query aggregation
- [ ] Integration test: existing multi-agent logic works
- [ ] Thread safety test: concurrent session operations

### Validation

- [ ] Compile without errors
- [ ] All tests pass
- [ ] Verify multi-session query results unchanged
- [ ] Simplification confirmed through code review

---

## Phase 6: DL-Based DBoW Vocabulary (Optional)

**Effort:** 3-4 weeks | **Impact:** 40% additional latency reduction | **Risk:** High

### Prerequisite

- [ ] Phases 1-5 completed and validated
- [ ] Have DL training dataset available
- [ ] Have ONNX model export infrastructure

### Files to Modify/Create

1. **okvis_frontend/include/DBoW2/FDLFeature.hpp** (NEW)
   - [ ] Template specialization for DL descriptors
   - [ ] Static const L = 256 (dimension for 256-D features)
   - [ ] Static distance function using cosine distance
   - [ ] Static meanValue function using vector averaging

2. **DBoW3_dpl-main/utils/train_dl_vocabulary.cpp** (NEW)
   - [ ] Training script to create DL vocabulary
   - [ ] Load pre-trained DL feature extractor
   - [ ] Extract features from training set
   - [ ] Create DBoW vocabulary with FDLFeature template
   - [ ] Save vocabulary file

3. **okvis_frontend/include/okvis/DLVocabularyBuilder.hpp** (NEW)
   - [ ] Helper class for vocabulary training
   - [ ] Support for batch DL feature extraction
   - [ ] Vocabulary serialization

### Code Changes Template

**Before (Phase 5 state):**
```cpp
// DL features must be converted for BRISK vocabulary
cv::Mat dlFeatures = dlExtractor_->extract(image);
cv::Mat briskFeatures = convertDLToBRISK(dlFeatures);  // Unnecessary conversion
loopClosureMatcher_->query(briskFeatures, 4);  // Using wrong descriptor type!
```

**After:**
```cpp
// DL features directly used in DL vocabulary
cv::Mat dlFeatures = dlExtractor_->extract(image);
loopClosureMatcher_->query(dlFeatures, 4);  // Native DL matching!
```

### Training Workflow

1. [ ] Prepare training dataset (1000+ images)
2. [ ] Extract DL features from all training images
3. [ ] Run vocabulary training script
4. [ ] Save trained vocabulary file
5. [ ] Validate vocabulary quality (retrieval metrics)
6. [ ] Update configuration to use new vocabulary

### Testing

- [ ] Unit test: FDLFeature distance function (cosine distance)
- [ ] Unit test: FDLFeature meanValue computation
- [ ] Training test: vocabulary training completes
- [ ] Integration test: trained vocabulary loads correctly
- [ ] Evaluation test: loop closure accuracy on benchmark dataset
- [ ] Performance test: measure additional latency reduction

### Validation

- [ ] Compile without errors
- [ ] All tests pass
- [ ] Loop closure detection accuracy meets/exceeds BRISK baseline
- [ ] Latency improvement measured (target: 40% additional reduction)
- [ ] Vocabulary file size acceptable
- [ ] Training time reasonable for re-training

---

## Post-Implementation Cleanup

### Documentation

- [ ] Update Frontend.hpp documentation
- [ ] Update Frontend.cpp inline comments
- [ ] Create architecture documentation: `DBOW3_REFACTORED_ARCHITECTURE.md`
- [ ] Update README with new parameters
- [ ] Create migration guide for users

### Code Quality

- [ ] Run `clang-format` on all modified files
- [ ] Run `clang-tidy` for static analysis
- [ ] Run `cppcheck` for semantic issues
- [ ] Code review by team lead
- [ ] Address all review comments

### Performance Validation

- [ ] Compare baseline vs final metrics
- [ ] Document all performance improvements
- [ ] Create performance regression test suite
- [ ] Add continuous benchmarking to CI pipeline

### Version Control

- [ ] Create summary commit message documenting all changes
- [ ] Tag final version: `git tag refactor-complete-$(date +%Y%m%d)`
- [ ] Create pull request with comprehensive description
- [ ] Obtain approvals from code reviewers
- [ ] Merge to main branch

### Deployment

- [ ] Update configuration documentation
- [ ] Test on different platforms (Linux, macOS)
- [ ] Verify backward compatibility (vocabulary file format)
- [ ] Create release notes
- [ ] Announce changes to team

---

## Success Criteria

### Performance
- [ ] **35% latency reduction** (Phase 2 parallelization)
- [ ] **40% complexity reduction** (Phase 3 abstraction)
- [ ] **40% additional latency** reduction (Phase 6 optional)
- [ ] **Overall target: 50-60% frontend latency reduction**

### Code Quality
- [ ] **250-300 LOC eliminated** (duplicate code removed)
- [ ] **32-50% LOC reduction** in loop closure code
- [ ] Cyclomatic complexity reduced by >30%
- [ ] No new warnings from static analysis tools

### Functionality
- [ ] All existing tests pass
- [ ] Loop closure detection results unchanged (binary identical)
- [ ] No regressions in SLAM performance
- [ ] Multi-agent/multi-session recognition works correctly

### Documentation
- [ ] All new classes/methods fully documented
- [ ] Configuration parameters documented
- [ ] Architecture diagrams updated
- [ ] Migration guide created

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| Thread safety issues | Medium | High | Extensive testing, address sanitizer, helgrind |
| Performance regression | Low | High | Baseline metrics, comparison at each phase |
| Vocabulary loading issues | Low | Medium | Test vocabulary compatibility, versioning |
| Integration breaks | Medium | High | Comprehensive unit tests, CI validation |
| DL model format changes | Low | Medium | Version model paths, fallback to BRISK |

---

## Timeline Estimate

| Phase | Duration | Cumulative |
|-------|----------|-----------|
| Setup & Baseline | 2-3 days | 2-3 days |
| Phase 1 (Config) | 1-2 weeks | 1.5-2.5 weeks |
| Phase 2 (Parallel) | 2-3 weeks | 3.5-5.5 weeks |
| Phase 3 (Abstraction) | 3-4 weeks | 6.5-9.5 weeks |
| Phase 4 (Unified) | 2-3 weeks | 8.5-12.5 weeks |
| Phase 5 (Manager) | 1-2 weeks | 9.5-14.5 weeks |
| Phase 6 (DL Vocab) | 3-4 weeks | 12.5-18.5 weeks |
| Testing & Validation | 1-2 weeks | 13.5-20.5 weeks |

**Minimum viable (Phases 1-4): 8.5-12.5 weeks with 60% of target improvement**
**Full implementation (Phases 1-6): 13.5-20.5 weeks with 80% of target improvement**

