# Robot Vision and Following

This context defines the vocabulary and responsibility boundaries for visual material detection and its handoff to robot following.

## Language

**Detection**:
A time-stamped 2D observation of one material in an image, consisting of its model class ID, stable Leaf Category, confidence, bounding box, region of interest, and center pixel.
_Avoid_: target point, 3D target

**Bounding Box (bbox)**:
The axis-aligned rectangular pixel extent of a Detection, represented as `{x_min, y_min, x_max, y_max}` in the Source Frame's original pixel space. The coordinate origin is the upper-left image corner, with x increasing rightward and y increasing downward.
_Avoid_: detection frame, object frame

**Region of Interest (ROI)**:
The image region selected by a Detection for downstream processing; in Phase 1 it is identical to the Bounding Box.
_Avoid_: crop, detection area

**Center Pixel**:
The two-dimensional image coordinate `(u, v)` at the center of a Detection's bounding box, computed in Source Frame pixel space.
_Avoid_: target point, grasp point

**Leaf Category**:
The most specific material class recognized by the detection model, such as `beverage/cola`. A Phase 1 grab task names exactly one Leaf Category. In Phase 1, each Leaf Category maps to one controlled physical material version.
_Avoid_: broad category, material category

**Controlled Material Version**:
The single approved brand, package type, and capacity supplied for a Phase 1 Leaf Category. A change to this version requires dataset and model-impact review.
_Avoid_: product variant

**Phase 1 Leaf Categories**:
The controlled material classes included in the first production validation: `beverage/mineral-water`, `beverage/oolong-tea`, `beverage/cola`, `snack/spicy-strip`, and `snack/instant-noodles`.

**Phase 1 Vision Scene**:
A controlled shelf scene with a fixed camera pose and fixed background. Materials are orderly placed with approximately consistent orientation; beverage rotation is permitted, while unordered mixing and stacking are out of scope. Multiple instances of the same Leaf Category may appear when they occupy distinct shelf slots.

**Workstation Demo Dataset**:
A versioned training and evaluation dataset captured with the controlled materials at the development workstation. It proves the Phase 1 detection workflow but does not establish shelf deployment performance.

**Shelf Validation Dataset**:
A separately versioned dataset captured at the actual fixed shelf and camera pose. It is the authoritative basis for deployment training, evaluation, and acceptance.

**Annotation Assistant**:
The offline dataset-tooling component that helps a human prepare labeled images through frame extraction, label validation, and model-assisted pre-annotations. Human review remains the authority for ground-truth labels.
_Avoid_: automatic ground truth

**Workstation Demo**:
The Phase 1 validation flow in which local images or recorded video and a Leaf Category task produce a Selected Detection with its source-frame metadata. Point-cloud estimation and physical robot control are represented only by downstream handoff contracts.

**Python Library Interface**:
The in-process integration boundary used by all current modules. Network services and message queues are out of scope until deployment requires cross-process or cross-device integration.

**RGB Frame**:
A single color image supplied by the camera module to the YOLO module, identified by its `frame_id`, capture timestamp, image dimensions, and camera identifier. Its pixels are a `uint8` `numpy.ndarray` in `H x W x 3` RGB order.
_Avoid_: camera stream

**Source Frame**:
The RGB Frame from which a Detection was produced. Every Detection retains the Source Frame's identifier and capture timestamp for downstream RGB/depth synchronization.
_Avoid_: latest frame

**Initial Acquisition**:
The task phase that obtains a valid Selected Detection before the robot approaches the material. It uses a bounded window of current frames, not an irrevocable single image.
_Avoid_: first image detection

**Fine Tracking**:
The task phase after the robot reaches its approach pose, in which current frames are repeatedly detected and selected to provide fresh 2D observations for grasping.
_Avoid_: real-time grabbing

**Target Lock**:
The task-scoped binding between the Initial Acquisition's Selected Detection and its physical material instance. Fine Tracking may update that instance's observation but must not silently switch to a different same-category instance.
_Avoid_: highest-confidence target

**Target-Loss Recovery**:
The recovery sequence for a lost Target Lock: pause target-related robot motion, attempt bounded in-place re-association, then return to Initial Acquisition. Failure to reacquire in Initial Acquisition ends the task with a target-lost error.
_Avoid_: automatic target switching

**Grasp Task Orchestrator**:
The task-level state machine that coordinates detection, target selection, point-cloud estimation, and robot motion from task start through completion or failure. It owns recovery decisions but delegates inference and robot commands to their respective modules.
_Avoid_: YOLO controller, robot command executor

**Follower Hold**:
The reversible pause mode in which the robot continues receiving its current TCP pose while follower mode remains active.
_Avoid_: stop follower

**Follower Stop**:
The terminal follower action that immediately ends follower mode and requires reconnection before a subsequent follower command.
_Avoid_: pause

**Detection Filter**:
The component that keeps only Detections whose Leaf Category exactly matches the active grab task.
_Avoid_: target selection

**Object Selector**:
The component that immediately selects at most one Detection from the active task's filtered Detections according to a selection policy. It does not perform detection, estimate a 3D Target Point, or apply temporal smoothing.
_Avoid_: YOLO selector, model selector

**Selected Detection**:
The single Detection chosen by the Object Selector as the handoff candidate for downstream processing.
_Avoid_: target point, grasp target

**NoMatch**:
The normal task result when no Detection both exactly matches the requested Leaf Category and passes the Eligibility Gate.
_Avoid_: inference error, low-confidence fallback

**Frame Detection Result**:
The complete visual result for one Source Frame: all raw Detections plus the task-specific immediate Selected Detection or NoMatch outcome.
_Avoid_: selected target only

**Eligibility Gate**:
The mandatory test that rejects Detections below the configured confidence threshold before any selection policy ranks the remaining candidates. Thresholds use a default value with optional Leaf Category overrides; no eligible Detection produces an explicit no-match outcome.
_Avoid_: weighted confidence score

**Selection Policy**:
An ordered, replaceable rule set that ranks eligible Detections. Phase 1 ranks by confidence; distance and image-center policies are deferred until their required inputs exist.
_Avoid_: final score

**Grasp Planning Layer**:
The layer that owns cross-frame smoothing, target-loss judgement, motion behavior, and other temporal decisions. It consumes immediate visual observations but does not delay YOLO inference.
_Avoid_: YOLO tracking

**3D Target Point**:
A spatial point estimated by the point-cloud module from a Detection's ROI and sensor data. It is not produced by the YOLO module.
_Avoid_: YOLO target point

## Deferred Language

**Parent Category**:
A group of Leaf Categories, such as `beverage`. Selecting a Parent Category as a grab task is outside Phase 1.
