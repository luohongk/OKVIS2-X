# OKVIS2-X Framework Analysis: Support for 4-Fisheye Circular Camera Rig

## Executive Summary

**The OKVIS2-X framework DOES support a 4-fisheye-camera rig arranged in a circular helmet configuration with cameras roughly 90 degrees apart.** The framework has been designed for multi-camera systems with flexible camera geometries and does not enforce forward-facing or overlapping field-of-view constraints at the system level.

---

## 1. CAMERA MODELS SUPPORTED

### Supported Camera Models (Fully Confirmed):

1. **Pinhole + RadialTangential** - Standard pinhole with 4-parameter radial-tangential distortion
2. **Pinhole + EquidistantDistortion (Fisheye)** - Pinhole camera with equidistant/fisheye distortion model
3. **Pinhole + RadialTangentialDistortion8** - Extended 8-parameter radial-tangential distortion
4. **EUCM (Enhanced Unified Camera Model)** - Non-linear omnidirectional/wide-angle camera model

### Fisheye Model Details:
- **Type**: Equidistant distortion (commonly called "fisheye")
- **Parameters**: 4-parameter model (k1, k2, k3, k4)
- **Mathematical form**: Equidistant mapping where angle maps linearly to image radius
- **Files**: `okvis_cv/include/okvis/cameras/EquidistantDistortion.hpp`

### EUCM Model Details:
- **Type**: Enhanced Unified Camera Model - supports wide-angle and omnidirectional cameras
- **Parameters**: 6 intrinsics (focal length u/v, principal point, alpha, beta)
- **Alpha range**: [0,1] parameter allowing different projection models
- **Beta parameter**: > 0 for shape control
- **Files**: `okvis_cv/include/okvis/cameras/EucmCamera.hpp`

### Config File Support:
```yaml
cam_model: pinhole          # Camera model type
distortion_type: equidistant # or radialtangential, radialtangential8
# For EUCM:
cam_model: eucm
eucm_parameters: [alpha_value, beta_value]
```

---

## 2. MULTI-CAMERA SUPPORT: NO MAXIMUM LIMIT

### Number of Cameras:
- **No hardcoded maximum**: The framework uses `size_t` and `std::vector` for camera storage
- **Tested configurations**: Up to 5+ cameras (demonstrated in `config/hilti22/okvis2.yaml`)
- **Real-world config**: Hilti22 dataset uses **5 equidistant fisheye cameras**
- **Dynamic sizing**: Camera system expands as cameras are added

### Evidence from Code:

**From `okvis_cv/include/okvis/cameras/NCameraSystem.hpp`:**
```cpp
inline size_t numCameras() const;  // Returns actual number of cameras
inline size_t numUsedCameras() const;  // Returns number used for SLAM

// Storage mechanism - unlimited
std::vector<std::shared_ptr<const okvis::kinematics::Transformation>> T_SC_;
std::vector<std::shared_ptr<const cameras::CameraBase>> cameraGeometries_;
std::vector<DistortionType> distortionTypes_;
```

**Adding cameras is done iteratively:**
```cpp
void addCamera(
    std::shared_ptr<const okvis::kinematics::Transformation> T_SC,
    std::shared_ptr<const cameras::CameraBase> cameraGeometry,
    DistortionType distortionType,
    bool computeOverlaps = true,
    const CameraType & cameraType = CameraType());
```

### Multi-Camera Configuration Example (Hilti22 with 5 fisheye cameras):
```yaml
cameras:
  - {T_SC: [...], distortion_type: equidistant, focal_length: [...], ...}  # Camera 0
  - {T_SC: [...], distortion_type: equidistant, focal_length: [...], ...}  # Camera 1
  - {T_SC: [...], distortion_type: equidistant, focal_length: [...], ...}  # Camera 2
  - {T_SC: [...], distortion_type: equidistant, focal_length: [...], ...}  # Camera 3
  - {T_SC: [...], distortion_type: equidistant, focal_length: [...], ...}  # Camera 4

camera_parameters:
  sync_cameras: [0, 1, 2, 3, 4]  # All cameras synchronized
```

---

## 3. CONSTRAINTS ON CAMERA PLACEMENT/GEOMETRY

### What IS Supported:

1. **Non-central camera rigs**: Yes
   - Cameras can have different positions in the IMU frame
   - Each camera has its own extrinsic transformation T_SC (IMU-to-camera)
   - No requirement for cameras to share a common projection center

2. **Arbitrary orientations**: Yes
   - Cameras can face any direction
   - 90-degree separation is fully supported
   - Cameras need not be forward-facing

3. **No overlapping FOV requirement**: At system level, no
   - Framework CAN work without overlap between cameras
   - However, feature matching requires SOME overlap for initialization

4. **Circular/helmet arrangements**: Yes
   - Supported through flexible T_SC extrinsics
   - Non-central camera adapter explicitly handles this

### What IS CONSTRAINED:

1. **Feature matching requires overlap**:
   - While circular rigs can work, when a frame needs to be initialized or tracked
   - Landmarks visible in BOTH cameras improve matching reliability
   - Formula from Frontend.cpp shows epipolar geometry checks

2. **Keyframe overlap threshold**:
   - Default: `keyframe_overlap: 0.59-0.60` (59-60% overlap with previous keyframe)
   - This is configurable: `setKeyframeInsertionOverlapThreshold()`
   - Purpose: Determines when a new keyframe should be inserted
   - Can be adjusted for non-overlapping rigs

3. **Overlap computation**:
   - System automatically computes field-of-view overlaps between all cameras
   - Done during initialization via `computeOverlaps()`
   - No assertion that overlap must exist; computed for tracking purposes

### Implementation Details (from `okvis_cv/src/NCameraSystem.cpp`):

```cpp
void NCameraSystem::computeOverlaps() {
  // For each camera pair, computes which pixels of one camera see the other
  for (size_t cameraIndex = 0; cameraIndex < N; ++cameraIndex) {
    for (size_t otherIndex = 0; otherIndex < N; ++otherIndex) {
      // Backproject pixel from camera A
      camera->backProject(pixel, &ray_C);
      
      // Transform ray to camera B frame
      ray_Cother = T_Cother_C.C() * ray_C;
      
      // Project into camera B
      status = otherCamera->project(ray_Cother, &imagePoint);
      
      // Mark overlapping pixels
      if (status == ProjectionStatus::Successful) {
        overlapMat.at<uchar>(v, u) = 1;
      }
    }
  }
}
```

---

## 4. CAMERA CONFIGURATION FORMAT

### Configuration File Structure (YAML):

```yaml
%YAML:1.0
cameras:
  - T_SC: [4x4 transformation matrix 16 elements]
    image_dimension: [width, height]
    distortion_coefficients: [k1, k2, k3, k4, ...]
    distortion_type: equidistant  # or radialtangential, radialtangential8
    focal_length: [f_u, f_v]
    principal_point: [c_u, c_v]
    cam_model: pinhole  # or eucm
    camera_type: gray   # gray, rgb, gray+depth, rgb+depth
    mapping: true/false
    mapping_rectification: true/false
    slam_use: okvis     # none, okvis, okvis-depth, okvis-virtual

camera_parameters:
  timestamp_tolerance: 0.005    # seconds
  sync_cameras: [0, 1, 2, 3]    # which cameras to synchronize
  image_delay: 0.0              # timestamp offset correction
  online_calibration:
    do_extrinsics: true/false    # online calibrate camera poses
    sigma_r: 0.01               # position prior (meters)
    sigma_alpha: 0.1            # rotation prior (radians)
  deep_stereo_indices: [0, 1]   # for stereo depth network
  fov_scale: 1.0                # scale for rectification
```

### For EUCM cameras:

```yaml
- cam_model: eucm
  T_SC: [...]
  image_dimension: [width, height]
  focal_length: [f_u, f_v]
  principal_point: [c_u, c_v]
  eucm_parameters: [alpha, beta]  # 0 <= alpha <= 1, beta > 0
```

### Real Example: Hilti22 (5 Equidistant Fisheye Cameras):

From `config/hilti22/okvis2.yaml`:
```yaml
cameras:
  - T_SC: [0.0076, 0.0032, 0.9999, 0.0530, ...]
    image_dimension: [720, 540]
    distortion_coefficients: [-0.03646, -0.00544, 0.00275, -0.00111]
    distortion_type: equidistant
    focal_length: [350.37, 350.46]
    principal_point: [367.59, 253.84]
    cam_model: pinhole
    camera_type: gray
    slam_use: okvis
    
  - T_SC: [...]  # Camera 2
  - T_SC: [...]  # Camera 3
  - T_SC: [...]  # Camera 4
  - T_SC: [...]  # Camera 5

camera_parameters:
  sync_cameras: [0, 1, 2, 3, 4]
  fov_scale: 0.9
```

---

## 5. FEATURE TRACKING & MATCHING

### Multi-Camera Feature Matching:

**From `okvis_frontend/src/Frontend.cpp`:**

1. **Detection per camera**: Independent keypoint detection for each camera
2. **Matching across cameras**:
   - Motion stereo matching (temporal + spatial)
   - Map matching (match against landmarks)
   - Epipolar geometry checks enabled

### Key Functions:

```cpp
template<class CAMERA_GEOMETRY>
int Frontend::matchMotionStereo(
    Estimator& estimator, 
    const ViParameters &params,
    const uint64_t currentFrameId, 
    bool& rotationOnly);

// Computes overlaps between frames
double overlapFraction(const MultiFramePtr frameA, const MultiFramePtr frameB);

// Checks which frames have sufficient overlap
std::vector<StateId> matchFrameIds;
for(auto & id : allFrames) {
  const double overlap = estimator.overlapFraction(
        estimator.multiFrame(previousFrameId),
        estimator.multiFrame(id));
  if(overlap > 1.0e-8) {  // Any overlap triggers matching
    matchFrameIds.push_back(id);
  }
}
```

### Non-Central Camera Support:

**Explicit support in `FrameNoncentralAbsoluteAdapter`:**
```cpp
/// Adapter for absolute pose RANSAC (3D2D) with non-central cameras,
/// i.e. could be a multi-camera-setup.
class FrameNoncentralAbsoluteAdapter : public AbsoluteAdapterBase {
  // Handles arbitrary camera positions and orientations
  opengv::translation_t getCamOffset(size_t index);
  opengv::rotation_t getCamRotation(size_t index);
  // ... properly triangulates across non-overlapping or arbitrarily arranged cameras
};
```

### Epipolar Geometry and Constraints:

The code uses epipolar geometry for validation:
```cpp
// From Frontend.cpp line ~1879
const Eigen::Vector3d e0_W = it->second.e_W.col(d);
const Eigen::Vector3d e1_W = e1_W;  // bearing vector from other camera

if(e0_W.dot(e1_W) < cos6Sigma) {
  // Check epipolar plane constraint
  const Eigen::Vector3d et_W = (T_WC1.r() - r0_W).normalized();
  const Eigen::Vector3d n0_W = e0_W.cross(et_W).normalized();
  const Eigen::Vector3d n1_W = e1_W.cross(et_W).normalized();
  if((n0_W.dot(n1_W) < cos6Sigma)) {
    continue;  // Not in epipolar plane
  }
}
```

**Key Point**: The epipolar checks are ONLY done when bearings suggest potential matches. For non-overlapping cameras, these checks won't apply, and features simply won't match (which is expected).

---

## 6. CIRCULAR/OMNIDIRECTIONAL RIG SUPPORT

### Native Support Indicators:

1. **Non-central camera adapter**: Already discussed
2. **Field-of-view overlap computation**: Handles arbitrary rigs
3. **Equidistant distortion model**: Specifically designed for fisheye/wide-angle
4. **EUCM model**: Designed for omnidirectional cameras
5. **No forward-facing requirement**: Code allows arbitrary T_SC rotations

### Configuration Recommendations for Circular Helmet Rig:

```yaml
# 4-fisheye circular helmet arrangement (90° apart)
cameras:
  - T_SC: [camera_0_transform]      # Front-facing camera
    distortion_type: equidistant
    focal_length: [f_x, f_y]
    principal_point: [c_x, c_y]
    cam_model: pinhole
    camera_type: gray
    slam_use: okvis
    
  - T_SC: [camera_1_transform]      # Right-side camera (90° rotation about vertical)
    distortion_type: equidistant
    # ... same parameters as above
    
  - T_SC: [camera_2_transform]      # Back-facing camera (180° rotation)
    distortion_type: equidistant
    # ... same parameters
    
  - T_SC: [camera_3_transform]      # Left-side camera (270° rotation)
    distortion_type: equidistant
    # ... same parameters

camera_parameters:
  timestamp_tolerance: 0.005
  sync_cameras: [0, 1, 2, 3]        # Synchronize all 4 cameras
  online_calibration:
    do_extrinsics: true             # Important: online calibrate camera poses
    sigma_r: 0.01                   # relax prior on extrinsics
    sigma_alpha: 0.1
```

### What Works Without Modification:

1. ✅ **Equidistant fisheye distortion** - 100% supported
2. ✅ **4-camera configuration** - No limit on number
3. ✅ **Circular arrangement** - No constraints on relative rotations
4. ✅ **Non-overlapping FOV** - System doesn't require overlap
5. ✅ **Online extrinsic calibration** - Can refine T_SC transforms
6. ✅ **Non-central geometry** - Explicit adapter support

### Potential Limitations to Manage:

1. **Initialization phase**: 
   - May require some overlap for initial motion estimation
   - Workaround: Tilt robot slightly to create temporary overlap during startup
   - Once initialized, can rotate back

2. **Feature association**:
   - Features only match if visible in multiple cameras
   - For non-overlapping views, each camera essentially acts independently for feature tracking
   - This is NOT a bug; it's expected behavior

3. **Tracking loss scenarios**:
   - If all cameras lose track (high motion), recovery may be slower
   - Mitigation: Ensure enough frame-to-frame overlap at high speeds

4. **Scale ambiguity**:
   - With non-overlapping cameras, monocular scale ambiguity applies to each camera
   - Mitigation: IMU integration provides scale - OKVIS2 fuses IMU and vision

---

## 7. COMPREHENSIVE FEATURE SUPPORT SUMMARY

| Feature | Supported | Notes |
|---------|-----------|-------|
| **Camera Models** |
| Pinhole + RadialTangential | ✅ Yes | Standard camera model |
| Pinhole + Equidistant (Fisheye) | ✅ Yes | Ideal for wide-angle/omnidirectional |
| EUCM (Enhanced Unified) | ✅ Yes | Omnidirectional camera model |
| **Multi-Camera** |
| Multiple cameras | ✅ Yes | No hardcoded maximum |
| 4+ camera rigs | ✅ Yes | Tested with 5 cameras (Hilti22) |
| **Geometry** |
| Non-central cameras | ✅ Yes | Full support via non-central adapter |
| Arbitrary orientations | ✅ Yes | Any T_SC rotation allowed |
| 90° separation | ✅ Yes | No constraint against this |
| Circular/helmet arrangement | ✅ Yes | No geometry constraints |
| **Feature Processing** |
| Independent detection per camera | ✅ Yes | Parallelized |
| Cross-camera matching | ✅ Yes | When overlap exists or landmarks shared |
| Motion stereo matching | ✅ Yes | Temporal + spatial tracking |
| Epipolar geometry | ✅ Yes | Used for validation when applicable |
| **Calibration** |
| Online extrinsic calibration | ✅ Yes | Refine T_SC poses during operation |
| Online intrinsic calibration | ✅ Yes | Online parameter refinement |

---

## 8. ARCHITECTURE OVERVIEW

### Camera System Architecture:

```
ViParameters (configuration)
    ↓
NCameraSystem (multi-camera manager)
    ├─ T_SC[0..N] (extrinsic transforms)
    ├─ CameraGeometry[0..N]
    │   ├─ PinholeCamera + EquidistantDistortion
    │   ├─ PinholeCamera + RadialTangentialDistortion
    │   └─ EucmCamera
    ├─ DistortionType[0..N]
    ├─ OverlapMaps[N×N] (field-of-view overlaps)
    └─ CameraType[0..N] (color/gray/depth/etc)
        ↓
    Frontend (feature detection & matching)
        ├─ Per-camera BRISK detection
        ├─ Cross-camera matching
        ├─ FrameNoncentralAbsoluteAdapter (for 3D2D RANSAC)
        └─ Motion stereo matching
            ↓
    Estimator (optimization backend)
        ├─ Landmark tracking across cameras
        ├─ Pose estimation (non-central capable)
        └─ Bundle adjustment
```

### Data Flow for Circular Rig:

```
Time t: Capture images from 4 cameras simultaneously
         ↓
    Detect keypoints in each camera independently
         ↓
    Match keypoints:
      - Within camera (temporal)
      - Across cameras (if visible landmarks)
         ↓
    3D2D RANSAC (non-central adapter handles arbitrary positions)
         ↓
    Update camera extrinsics (online calibration)
         ↓
    Optimize landmark positions
         ↓
    Continue...
```

---

## 9. IMPLEMENTATION RECOMMENDATIONS

### For Your 4-Fisheye Circular Helmet Rig:

1. **Configuration File Template**:
   - Use `config/hilti22/okvis2.yaml` as reference (5 fisheye cameras)
   - Set `distortion_type: equidistant` for each camera
   - Specify T_SC transforms for 90° separation
   - Set `sync_cameras: [0, 1, 2, 3]`

2. **Online Calibration**:
   ```yaml
   online_calibration:
     do_extrinsics: true      # Refine camera poses online
     sigma_r: 0.01            # position prior (m)
     sigma_alpha: 0.1         # rotation prior (rad)
   ```

3. **Feature Matching Parameters**:
   - `keyframe_overlap: 0.5-0.6` works for rigs with partial overlap
   - For non-overlapping, reduce threshold or manage initialization specially

4. **IMU Integration**:
   - Essential for scale recovery (avoids monocular ambiguity)
   - Especially important with non-overlapping cameras

5. **Testing Strategy**:
   - Start with overlapping views (robot in confined space)
   - Gradually transition to full 360° operation
   - Monitor tracking statistics in frontend logs

---

## 10. CONCLUSION

**OKVIS2-X is fully capable of supporting a 4-fisheye-camera circular helmet rig with:

1. **Fisheye model support**: Equidistant distortion explicitly implemented
2. **Multi-camera framework**: Unlimited camera count, already tested with 5+
3. **Non-central geometry**: Dedicated adapter for arbitrary camera positions
4. **No geometry constraints**: Doesn't enforce overlapping FOV or forward-facing cameras
5. **Flexible configuration**: YAML-based setup requires only extrinsic transforms
6. **Online refinement**: Can calibrate camera poses during operation

The main consideration is **feature matching**: features visible in multiple cameras enable triangulation and robust tracking. For non-overlapping views, the IMU is critical for scale recovery, which OKVIS2 natively integrates.**

---

## Appendix: Key Files Reference

| File | Purpose |
|------|---------|
| `okvis_cv/include/okvis/cameras/EquidistantDistortion.hpp` | Fisheye distortion model |
| `okvis_cv/include/okvis/cameras/EucmCamera.hpp` | Omnidirectional camera model |
| `okvis_cv/include/okvis/cameras/NCameraSystem.hpp` | Multi-camera manager |
| `okvis_frontend/include/opengv/absolute_pose/FrameNoncentralAbsoluteAdapter.hpp` | Non-central camera RANSAC |
| `okvis_common/src/ViParametersReader.cpp` | Config file parser |
| `config/hilti22/okvis2.yaml` | 5-camera fisheye example |
| `okvis_frontend/src/Frontend.cpp` | Feature tracking engine |
