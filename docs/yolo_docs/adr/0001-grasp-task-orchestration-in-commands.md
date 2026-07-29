# Place Grasp Task Orchestration in Commands

The `commands` package is the application-facing entry point for grab planning and robot motion. It will contain a dedicated Grasp Task Orchestrator that owns the task state machine, while YOLO, camera/point-cloud processing, and low-level robot command execution remain separate collaborators. This preserves a single operational entry point without coupling perception or task recovery to the existing synchronous motion executor.

## Consequences

The current `RobotCommandExecutor` must evolve into a cancellable, observable motion interface before it can support target-loss recovery during fine tracking. That follower-control work is deferred to the robot-vision integration phase and is not a Phase 1 YOLO deliverable.
