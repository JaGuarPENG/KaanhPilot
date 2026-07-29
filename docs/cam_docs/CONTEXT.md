# Camera and Perception

This context defines the vocabulary and responsibility boundaries for camera acquisition and the handoff of RGB-D observations to Perception.

## Language

**Camera Module**:
The hardware-facing module that manages a camera device, its streams, calibration data, alignment, and recovery. It does not detect objects, localize a target from an ROI, or plan robot motion.
_Avoid_: perception module, grasp planner

**Camera Adapter**:
A vendor-specific implementation of the Camera Module's public interface. It isolates a device SDK, such as `pyorbbecsdk`, from all consumers.
_Avoid_: shared SDK wrapper, camera business logic

**Camera Profile**:
The explicit request for a camera stream's RGB and depth modes, frame rate, and alignment requirement. An adapter either reports the actual accepted profile or fails before streaming; it does not silently substitute another profile.
_Avoid_: implicit default stream

**Alignment Mode**:
The adapter-selected method that registers depth and point-cloud pixels to RGB pixels. The G305 adapter supports both hardware D2C and software D2C behind the same public observation contract; comparative testing chooses the default by stable frame rate and observation latency, subject to a minimum usable spatial-alignment threshold.
_Avoid_: downstream alignment choice, fixed hardware assumption

**Perception**:
The top-level capability that turns camera observations and 2D Detections into sensor-space spatial estimates. It is separate from robot motion and grasp-pose planning.
_Avoid_: motion planner, camera driver

**Aligned RGB-D Observation**:
One synchronized camera observation comprising an RGB image, a depth image registered to that RGB image's pixel space, and the corresponding complete point cloud. It is the Camera Module's handoff to Perception.
_Avoid_: independent RGB and depth frames, unaligned depth frame

**Frame ID**:
A Camera Module-generated, monotonically increasing identifier for one Aligned RGB-D Observation. Downstream results retain it to prove that a Detection and the sensor data used for localization originate from the same observation.
_Avoid_: latest frame, SDK frame number

**Capture Timestamp**:
The camera device's acquisition time for an Aligned RGB-D Observation. It is used to judge observation freshness and is distinct from the host's receipt time.
_Avoid_: processing time, receive time

**Camera Stream**:
The Camera Module-owned background acquisition loop for one camera device. It publishes immutable Aligned RGB-D Observations through a bounded latest-observation buffer; consumers never access the device SDK directly.
_Avoid_: YOLO camera loop, shared SDK pipeline

**Latest-Frame Processing**:
The Perception scheduling policy that processes only the newest available Aligned RGB-D Observation on each configured inference cycle. It drops intermediate unprocessed observations rather than queueing stale work.
_Avoid_: FIFO frame backlog, every-frame processing

**Perception Cadence**:
The configured periodic rate at which Perception publishes its newest Localization Result. It is chosen from measured end-to-end inference latency and is independent of the Camera Stream rate and follower command rate.
_Avoid_: camera FPS, follower rate

**Raw Localization Result**:
The latest per-cycle Localization Result published by Perception without cross-frame smoothing, target locking, or motion decisions.
_Avoid_: stable path point, planned grasp point

**Stable Path Point**:
A temporally filtered target used by robot motion. The planner or Grasp Task Orchestrator derives it from Raw Localization Results; it is not a Camera Module or Perception output.
_Avoid_: raw target point, camera output

**Organized Point Cloud**:
A `float32` `H x W x 3` array in meters whose element at `(v, u)` corresponds to the same RGB and depth pixel. Coordinates are expressed in `camera_optical_frame`, and invalid points are `NaN`.
_Avoid_: unorganized point cloud, zero-filled invalid point

**Point-Cloud Localization**:
The Perception capability that combines a Detection's ROI with an Aligned RGB-D Observation to estimate a 3D Target Point in the camera coordinate frame. It supports optional 2D and 3D prefilters selected per task.
_Avoid_: grasp planning, point-cloud crop

**Static 2D ROI**:
A preconfigured allowed pixel region in the aligned RGB image. When enabled, Point-Cloud Localization uses its intersection with the Detection ROI.
_Avoid_: 3D workspace, detection ROI

**3D Workspace Filter**:
A preconfigured allowed spatial volume in `camera_optical_frame` that rejects points outside the physical work area. It is independently optional from a Static 2D ROI.
_Avoid_: pixel mask, robot workspace

**Localization Result**:
The outcome of Point-Cloud Localization for one Detection and matching Aligned RGB-D Observation. It is either a 3D Target Point with its observation identity and quality information, a normal NoTargetPoint outcome, or a system error.
_Avoid_: always-present target point, zero-point fallback

**NoTargetPoint**:
The normal localization outcome when filtering leaves insufficient or unstable valid point-cloud data to estimate a target position.
_Avoid_: `(0, 0, 0)`, stale target point, localization error

**Sensor Calibration**:
The active camera profile's RGB and depth intrinsics, distortion parameters, and depth-to-RGB extrinsics. The Camera Module obtains and exposes this data for aligned observation interpretation.
_Avoid_: hand-eye calibration, robot calibration

**Hand-Eye Calibration**:
The separately maintained rigid transform from `camera_optical_frame` to `robot_base`. The planner owns its validation and use; it is deferred from the camera module.
_Avoid_: camera calibration, D2C calibration

**Camera Failure**:
The terminal Camera Module state caused by a device, SDK, or stream error. The module raises the underlying camera error and does not reconnect automatically.
_Avoid_: stale observation, automatic recovery

**Camera-Failed Task**:
The Grasp Task Orchestrator outcome after a Camera Failure. The orchestrator stops an active follower through the current robot interface and terminates the task; controller-level emergency stopping is a separate future safety capability.
_Avoid_: camera retry, hardware emergency stop

**Replay Camera**:
A Camera Module adapter that emits recorded synchronized observations through the same public interface as a hardware camera. It enables deterministic end-to-end testing without a connected device.
_Avoid_: video-only test input, mock detection

**Recorded Observation Session**:
A lossless recording of aligned RGB images, aligned depth images, observation identities and capture timestamps, plus session-level actual profile, alignment mode, calibration, and depth unit. Replay reconstructs the Organized Point Cloud instead of storing it redundantly.
_Avoid_: point-cloud dump, raw-stream recording
