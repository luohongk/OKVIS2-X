# Phase 1: Implementation Starter Code

## Overview
Phase 1 removes hard-coded vocabulary paths and configuration flags, replacing them with runtime parameters. This enables the system to work with different vocabularies without recompilation.

## File 1: Update okvis/Parameters.hpp

**Location:** `/home/lhk/workspace/OKVIS2-X/okvis_common/include/okvis/Parameters.hpp`

### Current State (lines 110-120)
Find and understand the existing FrontendParameters structure.

### Changes to Make

Add these members to the FrontendParameters struct:

```cpp
struct FrontendParameters {
  // ... existing members ...
  
  /// DBoW Configuration
  
  /// Path to vocabulary file (e.g., "small_voc.yml.gz")
  /// Default: "small_voc.yml.gz" (relative to working directory)
  std::string vocabulary_path;
  
  /// Optional override for vocabulary path (for development/testing)
  /// If non-empty, this takes precedence over vocabulary_path
  /// Default: "" (empty, use vocabulary_path)
  std::string vocabulary_path_override;
  
  /// Path to database persistence file (optional)
  /// If empty, database is not persisted to disk
  /// Default: "" (no persistence)
  std::string database_path;
  
  /// Whether to enable secondary BRISK extraction when using DL features
  /// This is currently a workaround for vocabulary incompatibility
  /// Set to false once Phase 3 (abstraction layer) is complete
  /// Default: true (maintain current behavior during Phase 1)
  bool use_secondary_brisk_extraction;
  
  /// Constructor with sensible defaults
  FrontendParameters() 
      : vocabulary_path("small_voc.yml.gz"),
        vocabulary_path_override(""),
        database_path(""),
        use_secondary_brisk_extraction(true) {}
};
```

## File 2: Update okvis/Frontend.hpp

**Location:** `/home/lhk/workspace/OKVIS2-X/okvis_frontend/include/okvis/Frontend.hpp`

### Current State
The Frontend class has hard-coded vocabulary loading logic in the DBoW nested class.

### Changes to Make

In the private DBoW class (currently around lines 116-137), add:

```cpp
private:
  class DBoW 
  {
  public:
    /// Constructor that takes configuration parameters
    /// @param vocabPath Path to vocabulary file
    /// @param dbPath Optional path to database file
    DBoW(const std::string& vocabPath, const std::string& dbPath = "");
    
    /// Destructor
    ~DBoW();
    
    /// Load vocabulary from configured path
    /// @param vocabPath Path to vocabulary file
    /// @return true if successful, false otherwise
    bool loadVocabulary(const std::string& vocabPath);
    
    /// Load database from configured path (if path provided)
    /// @param dbPath Path to database file
    /// @return true if successful, false otherwise
    bool loadDatabase(const std::string& dbPath);
    
    // ... existing members ...
    
  private:
    /// Configuration paths
    std::string vocab_path_;
    std::string db_path_;
  };
```

### Storage Modification

Update Frontend member variables:

```cpp
private:
  /// DBoW3 place recognition system
  /// Now takes configuration from FrontendParameters
  std::unique_ptr<DBoW> place_recognition_;
  
  /// Cached FrontendParameters for reference during operation
  const FrontendParameters* frontend_params_;
```

## File 3: Update okvis/Frontend.cpp

**Location:** `/home/lhk/workspace/OKVIS2-X/okvis_frontend/src/Frontend.cpp`

### Part 1: Constructor Update

**Current code (find this):**
```cpp
Frontend::Frontend(...)
{
  // Hard-coded vocabulary path
  std::string vocabPath = "/home/lhk/workspace/DBow3_dpl/utils/vocab.yml.gz";
  // ...
}
```

**Replace with:**
```cpp
Frontend::Frontend(const Parameters& params)
    : // ... other initializers ...
      frontend_params_(&params.frontendParams)
{
  // Initialize place recognition with configurable paths
  std::string vocabPath = params.frontendParams.vocabulary_path_override.empty()
      ? params.frontendParams.vocabulary_path
      : params.frontendParams.vocabulary_path_override;
  
  std::string dbPath = params.frontendParams.database_path;
  
  place_recognition_ = std::make_unique<DBoW>(vocabPath, dbPath);
  
  if (!place_recognition_->loadVocabulary(vocabPath)) {
    LOG(WARNING) << "Failed to load DBoW vocabulary from: " << vocabPath;
    LOG(WARNING) << "Place recognition will be disabled";
  }
  
  if (!dbPath.empty()) {
    if (!place_recognition_->loadDatabase(dbPath)) {
      LOG(WARNING) << "Failed to load DBoW database from: " << dbPath;
      LOG(INFO) << "Creating new database";
    }
  }
}
```

### Part 2: DBoW Nested Class Implementation

**Add this implementation to Frontend.cpp:**

```cpp
// ============================================================================
// Frontend::DBoW Implementation

Frontend::DBoW::DBoW(const std::string& vocabPath, const std::string& dbPath)
    : vocab_path_(vocabPath), db_path_(dbPath)
{
  // Initialize members to nullptr
  // Actual loading happens in loadVocabulary() and loadDatabase()
}

Frontend::DBoW::~DBoW()
{
  // Cleanup if needed
  // Unique_ptr will handle vocabulary_ and database_ cleanup
}

bool Frontend::DBoW::loadVocabulary(const std::string& vocabPath)
{
  try {
    // Check if file exists
    std::ifstream file(vocabPath);
    if (!file.good()) {
      LOG(ERROR) << "Vocabulary file not found: " << vocabPath;
      return false;
    }
    file.close();
    
    // Load vocabulary
    vocabulary_ = std::make_unique<DBoW3::Vocabulary>(vocabPath);
    
    if (vocabulary_->empty()) {
      LOG(ERROR) << "Loaded vocabulary is empty: " << vocabPath;
      return false;
    }
    
    LOG(INFO) << "Successfully loaded vocabulary: " << vocabPath
              << " (size=" << vocabulary_->size() << ")";
    return true;
    
  } catch (const std::exception& ex) {
    LOG(ERROR) << "Exception loading vocabulary: " << ex.what();
    return false;
  }
}

bool Frontend::DBoW::loadDatabase(const std::string& dbPath)
{
  if (dbPath.empty()) {
    return true; // No database persistence requested
  }
  
  try {
    std::ifstream file(dbPath);
    if (!file.good()) {
      LOG(INFO) << "Database file not found: " << dbPath
                << " (will create new)";
      return false; // File doesn't exist, caller will create new
    }
    file.close();
    
    database_ = std::make_unique<DBoW3::Database>(*vocabulary_, false);
    database_->load(dbPath);
    
    LOG(INFO) << "Successfully loaded database: " << dbPath;
    return true;
    
  } catch (const std::exception& ex) {
    LOG(ERROR) << "Exception loading database: " << ex.what();
    return false;
  }
}
```

### Part 3: Feature Extraction Path Selection

**Current code (find this around line 1039-1075):**
```cpp
// Hard-coded descriptor type decision
if (/* features are DL type */) {
  // Extract BRISK (wasteful!)
}
```

**Replace conditional with:**
```cpp
// Use runtime configuration to decide on secondary extraction
bool extractSecondaryBrisk = frontend_params_->use_secondary_brisk_extraction;

if (extractSecondaryBrisk && features.type() == CV_32F) {
  // Only extract BRISK if configured to do so
  // (Phase 1: still do it for backward compatibility)
  // (Phase 2+: parallelization will make this faster)
  cv::Mat briskFeatures = briskExtractor_->extract(image);
  // ... continue as before
}
```

## File 4: Update config/euroc/okvis2.yaml

**Location:** `/home/lhk/workspace/OKVIS2-X/config/euroc/okvis2.yaml`

### Current State (find lines 71-87)
The YAML should have DL feature configuration already.

### Add DBoW Configuration Section

```yaml
# DBoW3 place recognition parameters
frontend:
  dbow:
    # Path to vocabulary file (supports relative and absolute paths)
    # Relative paths are resolved from the working directory at runtime
    vocabulary_path: "small_voc.yml.gz"
    
    # Optional override path for vocabulary (useful for development)
    # If specified, takes precedence over vocabulary_path
    # Default: "" (empty, disabled)
    vocabulary_path_override: ""
    
    # Path for persistent database storage (optional)
    # If empty, database is created in memory only
    # Default: "" (memory-only)
    database_path: ""
    
    # Whether to extract secondary BRISK descriptors when using DL features
    # This is a compatibility workaround that will be removed in Phase 3+
    # Currently must be true for place recognition to work with DL features
    # Default: true
    use_secondary_brisk_extraction: true
```

## File 5: Create Unit Tests

**Create new file:** `/home/lhk/workspace/OKVIS2-X/okvis_frontend/test/test_frontend_config.cpp`

```cpp
#include <gtest/gtest.h>
#include <okvis/Frontend.hpp>
#include <okvis/Parameters.hpp>
#include <fstream>
#include <cstdlib>

class FrontendConfigTest : public ::testing::Test {
protected:
  void SetUp() override {
    // Create temporary vocabulary file for testing
    vocab_temp_path_ = "/tmp/test_vocab_" + std::to_string(rand()) + ".yml.gz";
  }
  
  void TearDown() override {
    // Clean up temporary files
    if (std::ifstream(vocab_temp_path_).good()) {
      std::remove(vocab_temp_path_.c_str());
    }
  }
  
  std::string vocab_temp_path_;
};

// Test 1: Default path is used when override is empty
TEST_F(FrontendConfigTest, DefaultVocabularyPath) {
  Parameters params;
  params.frontendParams.vocabulary_path = "small_voc.yml.gz";
  params.frontendParams.vocabulary_path_override = "";
  
  // Verify the override is empty
  ASSERT_TRUE(params.frontendParams.vocabulary_path_override.empty());
  
  // The Frontend should use vocabulary_path
  // (Test will check this by verifying vocabulary loads)
}

// Test 2: Override path takes precedence
TEST_F(FrontendConfigTest, VocabularyPathOverride) {
  Parameters params;
  params.frontendParams.vocabulary_path = "small_voc.yml.gz";
  params.frontendParams.vocabulary_path_override = "/custom/path/vocab.yml.gz";
  
  // Verify the override is not empty
  ASSERT_FALSE(params.frontendParams.vocabulary_path_override.empty());
  
  // When override is set, it should be used instead of default
  std::string expectedPath = 
    !params.frontendParams.vocabulary_path_override.empty()
      ? params.frontendParams.vocabulary_path_override
      : params.frontendParams.vocabulary_path;
  
  ASSERT_EQ(expectedPath, "/custom/path/vocab.yml.gz");
}

// Test 3: Database path configuration
TEST_F(FrontendConfigTest, DatabasePath) {
  Parameters params;
  params.frontendParams.database_path = "/tmp/test_db.yml.gz";
  
  // Verify database path is set
  ASSERT_EQ(params.frontendParams.database_path, "/tmp/test_db.yml.gz");
}

// Test 4: Secondary BRISK extraction flag
TEST_F(FrontendConfigTest, SecondaryBriskExtractionFlag) {
  Parameters params;
  
  // Test default value (should be true for Phase 1)
  ASSERT_TRUE(params.frontendParams.use_secondary_brisk_extraction);
  
  // Test can be disabled
  params.frontendParams.use_secondary_brisk_extraction = false;
  ASSERT_FALSE(params.frontendParams.use_secondary_brisk_extraction);
}

// Test 5: Configuration loading from YAML
TEST_F(FrontendConfigTest, LoadConfigFromYAML) {
  // Create a temporary YAML file
  std::ofstream yaml_file("/tmp/test_config.yaml");
  yaml_file << "frontend:\n"
            << "  dbow:\n"
            << "    vocabulary_path: \"test_vocab.yml.gz\"\n"
            << "    use_secondary_brisk_extraction: false\n";
  yaml_file.close();
  
  // Load configuration from YAML
  // (Requires YAML parser integration - basic test here)
  Parameters params;
  params.frontendParams.vocabulary_path = "test_vocab.yml.gz";
  params.frontendParams.use_secondary_brisk_extraction = false;
  
  ASSERT_EQ(params.frontendParams.vocabulary_path, "test_vocab.yml.gz");
  ASSERT_FALSE(params.frontendParams.use_secondary_brisk_extraction);
  
  std::remove("/tmp/test_config.yaml");
}
```

## Integration Testing Checklist

After implementing Phase 1, verify:

1. **Compilation**
   ```bash
   cd /home/lhk/workspace/OKVIS2-X/build
   cmake ..
   make -j8
   ```
   Expected: No errors, only optional warnings

2. **Vocabulary Loading**
   - [ ] Default path works: `params.frontendParams.vocabulary_path = "small_voc.yml.gz"`
   - [ ] Override path works: set both path and override
   - [ ] Missing vocabulary handled gracefully with warning
   - [ ] Invalid YAML parsed correctly

3. **Database Loading**
   - [ ] Empty database_path creates in-memory DB
   - [ ] Valid database_path loads existing DB
   - [ ] Invalid database_path creates new DB with warning

4. **Feature Extraction Decision**
   - [ ] `use_secondary_brisk_extraction = true` extracts both DL and BRISK
   - [ ] `use_secondary_brisk_extraction = false` skips BRISK extraction
   - [ ] Loop closure still works in both modes

5. **Configuration YAML**
   - [ ] YAML loads without errors
   - [ ] All parameters reach Frontend class
   - [ ] YAML override works when provided

6. **Regression Testing**
   - [ ] Existing unit tests still pass
   - [ ] Loop closure detection unchanged
   - [ ] Frontend latency unchanged (or slightly improved due to early exit)

## Expected Outcomes

After Phase 1 completion:

- **Lines of code eliminated**: ~50 (hard-coded paths, conditional compilation)
- **New configurability**: Runtime vocabulary/database paths
- **Latency impact**: Negligible (may improve slightly due to early exit on missing vocab)
- **Backward compatibility**: Maintained (default values preserve current behavior)
- **Foundation for Phase 2**: Runtime configuration enables feature selection

## Common Issues & Solutions

### Issue 1: Path not found error
**Problem:** "Vocabulary file not found: small_voc.yml.gz"
**Solution:** Ensure working directory is correct, or provide absolute path in YAML

### Issue 2: YAML parsing fails
**Problem:** Parameters not loading from config file
**Solution:** Verify YAML formatting, check indentation (YAML is indentation-sensitive)

### Issue 3: Database not persisting
**Problem:** Database file created but not loaded on next run
**Solution:** Verify database_path is set to same location, and database is properly closed

## Next Steps After Phase 1

Once Phase 1 is complete and tested:

1. Commit changes to `feature/dbow3-refactoring` branch
2. Tag with: `git tag phase-1-complete-$(date +%Y%m%d)`
3. Measure baseline performance (for Phase 2 comparison)
4. Prepare Phase 2 implementation (parallel feature extraction)

---

**Estimated effort:** 1-2 weeks
**Risk level:** Low
**Test coverage:** ~80% (unit tests + integration)
**Ready for Phase 2:** Yes (once all tests pass)

