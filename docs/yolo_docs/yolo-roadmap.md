# YOLO Vision Roadmap

## Goal and Scope

Build a workstation YOLO demonstration for five controlled Leaf Categories: `beverage/mineral-water`, `beverage/oolong-tea`, `beverage/cola`, `snack/spicy-strip`, and `snack/instant-noodles`. A grab task requests exactly one Leaf Category.

The current scope ends with a 2D Selected Detection or NoMatch. Voice interaction, LLM intent parsing, 3D point-cloud estimation, grasp execution, and follower recovery are outside this phase.

## Module Boundaries

- Camera module: owns capture, reconnection, frame rate, depth/point-cloud data, and calibration. It supplies RGB Frames.
- YOLO detector: accepts one RGB Frame and returns all same-frame 2D Detections. It does not access a camera, create 3D points, track across frames, or move the robot.
- Detection Filter and Object Selector: exact-match the active Leaf Category, reject detections below the confidence threshold, and immediately choose the highest-confidence eligible detection.
- Annotation Assistant: offline tooling for frame extraction, label validation, import/export, and later model-assisted pre-annotation. Human review owns ground truth.
- Grasp Task Orchestrator: a later `commands`-package state machine coordinating camera, YOLO, point cloud, and robot motion. It owns temporal smoothing, target locks, and recovery decisions.

## Interface Contract

- All current integration boundaries are in-process Python library interfaces.
- An RGB Frame is an RGB `uint8` `numpy.ndarray` shaped `H x W x 3`, plus `frame_id`, capture timestamp, dimensions, and camera identifier.
- A Detection includes `model_class_id`, stable `leaf_category`, confidence, axis-aligned `bbox`/ROI in source-image `xyxy` pixels, center pixel `(u, v)`, and Source Frame metadata.
- A Frame Detection Result returns all raw Detections plus the task-specific immediate Selected Detection or NoMatch.
- NoMatch is a normal result. Invalid input, model-load failure, and inference failure are errors.

## Priorities and Dependencies

1. Define the Python result types, label mapping, confidence configuration, and local image/video demo harness.
2. Build the Workstation Demo Dataset, annotation workflow, training baseline, and model artifact once data work resumes.
3. Validate detection quality and latency on a held-out workstation dataset.
4. Build the separate camera module and validate RGB/depth synchronization using `frame_id` and capture timestamps.
5. Add point-cloud 3D target estimation and approach-pose planning.
6. Integrate the Grasp Task Orchestrator in `commands`; later add Follower Hold and target-loss recovery.
7. Recollect and relabel a separate Shelf Validation Dataset, retrain or fine-tune, and validate deployment on NVIDIA Jetson AGX Thor.

## Deferred Decisions and Risks

- Workstation and shelf datasets/models must remain versioned separately. Workstation success does not prove shelf performance.
- Training, labeling execution, numeric quality thresholds, and model hyperparameters are intentionally deferred.
- Follower Hold is required for later target-loss recovery but is not a YOLO-phase deliverable.
- YOLO framework and model-weight licensing must be reviewed before commercial or closed-source delivery.
- A 2D center pixel is not a robot-executable 3D target point; point-cloud estimation and calibration remain a hard downstream dependency.

## Phase 1 Acceptance

Using local images or recorded video, a grab task for each Leaf Category produces the complete Frame Detection Result with correct source-frame metadata and axis-aligned ROI/bbox semantics. Evaluate independent test data separately per class for category correctness, recall, false-positive behavior, bounding-box localization quality, and frame-to-result latency. Numeric thresholds follow representative data collection.
