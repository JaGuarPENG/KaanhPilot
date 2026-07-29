# Camera and Perception Roadmap

## Goal and Scope

Build a replaceable RGB-D camera capability for the G305 and future camera models. It supplies aligned RGB-D observations to YOLO and Perception, and Perception converts a YOLO ROI into a camera-space 3D Target Point. The scope ends before hand-eye calibration, grasp-pose generation, path smoothing, and robot motion planning.

## Responsibility Boundaries

- `camera`: owns device SDK access, stream lifecycle, profile negotiation, RGB-D alignment, sensor calibration, complete organized point-cloud creation, recording, and replay.
- `perception`: owns ROI intersection and point-cloud localization. It consumes a Detection and the matching Aligned RGB-D Observation, then publishes a Raw Localization Result.
- `yolo`: consumes RGB Frames and returns same-frame Detections. It neither opens a camera nor creates 3D points.
- `planner`: owns hand-eye calibration use, target smoothing, locking, grasp/approach-pose generation, and path planning. It consumes Raw Localization Results but never accesses camera SDK data.
- `commands`: owns the task loop, follower-rate command emission, and task termination on camera failures. It does not implement RGB-D processing.

## Public Data Contract

An Aligned RGB-D Observation is immutable and contains:

- a Camera Module-generated monotonically increasing `frame_id`;
- the device Capture Timestamp;
- RGB pixels in RGB source-image coordinates;
- depth registered to those same RGB pixels;
- an Organized Point Cloud shaped `H x W x 3`, `float32`, meters, in `camera_optical_frame`, with invalid points represented by `NaN`;
- the actual accepted Camera Profile, Alignment Mode, and Sensor Calibration.

YOLO retains the source `frame_id` and capture timestamp in every Detection. Point-Cloud Localization accepts a Detection only with its matching observation `frame_id`; it must never pair a Detection with a newer "latest depth" observation.

## Camera Architecture

Define a vendor-neutral in-process Camera interface. It owns a single background Camera Stream for one device, exposes lifecycle and health state, reports capabilities and the actual accepted Camera Profile, and lets consumers obtain the latest immutable observation without direct SDK access.

Implement `OrbbecG305Camera` as the first Camera Adapter. It is the only component that imports `pyorbbecsdk` or uses its `Pipeline`, `Config`, alignment filters, stream profiles, and calibration APIs. A stable hardware serial number provides `camera_id`; USB path is not an identity.

The adapter supports both hardware D2C and software D2C. Both produce the same public observation contract. Independent G305 benchmark tests choose the default mode by stable frame rate and p95 observation latency, subject to a minimum spatial-alignment usability check.

## Point-Cloud Localization

Point-Cloud Localization runs under the top-level `perception/` capability. It supports optional, independently enabled filters:

- Static 2D ROI: intersect with the YOLO Detection ROI in aligned RGB pixel space.
- 3D Workspace Filter: reject points outside a configured volume in `camera_optical_frame`.

For Phase 1, localization filters invalid points and points outside the active depth/workspace bounds, separates the dominant valid depth layer, and estimates the target as a robust three-dimensional median. It returns a Localization Result with observation identity and quality information, a normal NoTargetPoint result when data is insufficient or unstable, or a system error. It must not return zero points or historical points as fallback targets.

## Scheduling and Motion Handoff

The Camera Stream continuously publishes the latest observation through a bounded latest-observation buffer. Perception runs at a configured Perception Cadence derived from measured end-to-end inference time. Each cycle processes the newest available frame and drops intermediate unprocessed frames rather than queueing stale work.

Perception publishes a Raw Localization Result at its cadence. It does not smooth across frames, lock a physical target, or choose robot motion. The planner or Grasp Task Orchestrator produces Stable Path Points from these raw results. `commands` runs at follower frequency and repeatedly sends the current planner-produced target; it updates that target only when planning publishes a new one.

## Calibration Boundary

The Camera Module exposes Sensor Calibration for the active profile: RGB/depth intrinsics, distortion, and depth-to-RGB extrinsics. Hand-Eye Calibration from `camera_optical_frame` to `robot_base` is not camera calibration. It belongs to `planner`, is deferred, and is required before a camera-space 3D Target Point can become a robot grasp pose.

## Failure Policy

Camera timeouts, device disconnects, and SDK stream errors are terminal Camera Failures. The Camera Module stops publishing observations and raises a typed camera error; it does not automatically reconnect. `commands` handles the failure, calls the current `stop_follower()` capability when follower mode is active, and terminates the task as camera-failed. Controller-level emergency stopping remains a separate future safety capability.

## Recording and Replay

The Camera Module records lossless aligned RGB and depth, per-frame identities and capture timestamps, plus session-level actual profile, alignment mode, calibration, and depth unit. It does not redundantly store the full Organized Point Cloud; Replay Camera reconstructs it using the recorded depth and calibration. Raw vendor-stream recording is deferred to a dedicated alignment-diagnostics mode.

## Test Strategy

Tests live outside runtime modules:

- `tests/camera/unit/`: profile negotiation, frame identity, replay, point-cloud reconstruction, ROI semantics, and error results without hardware.
- `tests/camera/integration/`: real G305 stream, calibration retrieval, hardware/software D2C, and hardware failure behavior.
- `tests/camera/benchmark/`: actual FPS, p50/p95 observation latency, dropped observations, and alignment usability reports.

First establish a G305 benchmark baseline on the deployment target. Later camera/profile changes must meet or exceed that baseline; benchmark logic is not part of the Camera Module.

## Delivery Phases

### Phase 1: Camera Foundation

Define the Camera interface, Camera Profile, observation contract, and failure types. Implement the G305 adapter with exclusive background streaming, aligned RGB-D output, organized point cloud, sensor calibration, and latest-observation access.

Acceptance: a connected G305 produces correctly paired RGB, depth, point cloud, `frame_id`, and capture timestamp for an accepted profile.

### Phase 2: Alignment and Reproducibility

Implement both G305 alignment paths, independent tests and benchmarks, recording, and Replay Camera. Choose the default alignment mode from measured FPS and latency.

Acceptance: both alignment modes satisfy basic spatial usability; recorded sessions replay into equivalent RGB-D observations and reconstructed point clouds.

### Phase 3: Point-Cloud Localization

Create the top-level `perception/` capability. Implement composable 2D/3D filters, robust median localization, quality information, NoTargetPoint, and error behavior.

Acceptance: a same-frame YOLO Detection and observation yields a 3D Target Point, NoTargetPoint, or system error with no fabricated output.

### Phase 4: Scheduled Perception and Robot Integration

Run latest-frame Perception at a measured fixed cadence. Feed raw results to planner smoothing and target locking, then send planner-produced stable targets at follower frequency. Propagate Camera Failure through `commands` to `stop_follower()` and task termination.

Acceptance: replay and G305 runs maintain fixed result publication cadence without stale-work backlogs; robot commands consume only planner-produced Stable Path Points; a camera failure halts follower mode and ends the task.

### Phase 5: Hand-Eye and Grasp Planning

Implement and validate hand-eye calibration, then transform camera-space targets into robot-base grasp and approach poses.

Acceptance: independent hand-eye calibration and grasp-pose validation pass before enabling a physical grasp loop.

## Key Risks and Dependencies

- `pyorbbecsdk` profile and D2C support vary by device and profile; both alignment paths require G305 integration testing.
- Real inference latency determines Perception Cadence. Queueing frames is prohibited because it converts throughput loss into stale robot targets.
- The current robot interface can stop follower mode but does not prove controller-level emergency-stop behavior.
- Hand-eye calibration, gripper geometry, collision constraints, and grasp-pose strategy remain hard dependencies for physical grasping.
- Recordings must retain profile and calibration with images and depth; otherwise replayed point-cloud geometry is not reproducible.
