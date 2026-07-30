# Planning and Follower Integration

This context defines the vocabulary and responsibility boundaries for transforming perception results into follower commands and visualizing that integration in simulation.

## Language

**External-Camera Simulation**:
A control-validation mode in which a physical camera remains fixed in the real world while only the robot model moves in simulation. It validates the perception-to-follower command path, but is not an eye-in-hand closed loop and must not model the camera pose as changing with the simulated TCP.
_Avoid_: eye-in-hand simulation, hand-eye feedback test

**Fixed Eye-to-Hand Extrinsic**:
The explicit, static rigid transform `T_sim_base_from_camera` that converts a point in `camera_optical_frame` into the simulated robot base frame for External-Camera Simulation. It is a bridge configuration owned by the planning and follower integration, not a camera calibration result. Startup requires it to be configured in meters and quaternion rotation; an identity transform is permitted only when an explicit development mode enables it.
_Avoid_: implicit coordinate identity, dynamic hand-eye transform

**Fixed-Orientation Follower Target**:
A TCP target whose position is driven by a transformed perception result while its orientation remains equal to the TCP orientation recorded when follower mode starts. The target is represented as an absolute pose before conversion to the follower's relative command.
_Avoid_: object orientation estimate, free orientation following

**Approach Offset**:
A configured 100 mm default displacement from a transformed object point along the negative local Z axis of the TCP orientation locked at follower start. It keeps the TCP at a safe standoff during position-control validation; its distance and sign are configurable and its local axis is transformed into the simulated robot base frame before application.
_Avoid_: object point equals TCP target, fixed base-axis clearance

**Target Position Filter**:
A planning component that converts successive valid follower-target positions into a smooth target stream. Its initial policy is an exponential moving average with an `alpha` default of 0.35 and innovation rejection with a 50 mm default threshold. It retains only its current state and can be replaced by a different filtering policy without changing perception or follower transport. It resets when the target is lost and reacquired or when follower mode restarts, and seeds from the first subsequent valid target.
_Avoid_: camera filter chain, FIFO target backlog, follower transport logic

**Latest Follower Target**:
The one most recent valid Fixed-Orientation Follower Target retained for periodic command transmission. A newer target replaces it immediately; intermediate targets are not queued.
_Avoid_: FIFO target queue, command history

**Follower Bridge**:
The planning integration component that starts follower mode, records its TCP orientation, and is the sole sender of follower commands at 8 Hz. Perception and the Target Position Filter only update the Latest Follower Target; they never transmit follower protocol messages.
_Avoid_: perception-owned UDP sender, inference-coupled command cadence

**Follower Freshness Policy**:
The timing rule for the Latest Follower Target. For up to 0.5 seconds without a valid update, it may be retransmitted at the follower cadence; from 0.5 seconds through 2 seconds the follower enters Follower Hold by transmitting the current measured TCP pose at the follower cadence; at 2 seconds it receives Follower Stop and requires explicit restart. The policy starts this timing and resets the Target Position Filter only when Perception reports TARGET_LOST, not for an individual NO_TARGET_POINT or NO_MATCH outcome.
_Avoid_: indefinite stale-target following, immediate stop on one missed update

**Virtual Controller Backend**:
The configured robot-controller endpoint that accepts the existing follower protocol while executing it in a virtual environment. It is a deployment property of the backend endpoint, not a replacement robot transport or a follower simulator.
_Avoid_: simulated UDP client, physical-robot safety guarantee

**Control-Port State Readback**:
The Follower Bridge's use of `get` on its own control connection to obtain the latest TCP state while follower mode is active. It is non-blocking for this controller and does not depend on the independent monitoring connection, which remains available for its separate responsibility. An exception, non-status response, or state without a valid TCP PQ is a communication failure.
_Avoid_: monitor-thread dependency, shared WebSocket reader

**Follower Bridge Failure**:
The terminal result of a Follower Bridge communication failure or follower send failure. The bridge attempts Follower Stop, clears its Latest Follower Target and Target Position Filter, and marks the active test as failed rather than retrying a stale command.
_Avoid_: silent empty state, indefinite stale-command retry

**Continuous Follower Session**:
An active Follower Bridge session that keeps accepting newer targets after it reaches a prior target. It ends only when the user exits, the Follower Freshness Policy stops it, or a Follower Bridge Failure occurs.
_Avoid_: stop-on-reach follower command, one-target follower session

**Follower Integration Test Command**:
The standalone command that starts camera acquisition, detection, perception, the Follower Bridge, and the Follower Integration Debug View as one External-Camera Simulation test. User exit initiates Follower Stop before the command releases resources.
_Avoid_: keyboard-triggered discrete robot action, orphaned follower session

**Follower Integration Debug View**:
The simulation diagnostic view that renders the fixed camera coordinate frame and frustum, the raw perception point, the filtered target point, and the offset TCP follower target as distinct visual entities.
_Avoid_: one ambiguous grasp-point marker, camera-image overlay
