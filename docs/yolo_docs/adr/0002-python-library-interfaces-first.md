# Use Python Library Interfaces First

The initial YOLO, annotation-assistance, and task-orchestration components will integrate as in-process Python library interfaces. This keeps the workstation demo testable and avoids committing to network transport before camera and deployment topology are known; a later adapter may expose these interfaces across processes or devices.
