# YOLO Phase 1 Scope

## Workstation Demo

The workstation demo accepts local images or recorded video and one exact Leaf Category grab task. Each frame produces all raw Detections plus a task-specific immediate Selected Detection or explicit `NoMatch` outcome, with source-frame metadata. Point-cloud estimation and physical robot control are downstream contracts only. Invalid input, model-load failure, and inference failure are errors, not `NoMatch` outcomes.

## Acceptance Basis

Evaluate the independently held-out workstation dataset separately for every Phase 1 Leaf Category. The acceptance report must include category correctness, detection recall when the requested object is present, false-positive behavior when it is absent or mismatched, bounding-box localization quality for ROI use, and end-to-end latency from frame input to Selected Detection.

Specific numeric thresholds will be set after representative workstation data exists.

## Detection Geometry

Bounding boxes and ROIs are axis-aligned source-image pixel rectangles in `xyxy` format. Rotated boxes are out of scope.
