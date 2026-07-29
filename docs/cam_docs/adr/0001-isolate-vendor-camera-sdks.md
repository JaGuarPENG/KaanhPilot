# Isolate Vendor Camera SDKs Behind Camera Adapters

Camera consumers use a vendor-neutral in-process Camera interface, while `OrbbecG305Camera` alone depends on `pyorbbecsdk`. This prevents YOLO, Perception, planner, and commands from coupling to G305-specific pipelines, profiles, alignment APIs, or calibration types, and permits a future camera model to be added as another adapter.
