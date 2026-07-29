# Keep Point-Cloud Localization in Perception

ROI filtering and 3D Target Point estimation live in the top-level Perception capability, not in `camera` or `planner`. Camera remains hardware and calibration agnostic of task semantics; planner remains responsible for hand-eye transforms and motion decisions, so either camera hardware or grasp planning can change independently.
