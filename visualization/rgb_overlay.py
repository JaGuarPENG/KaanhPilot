"""在 RGB 图像副本上绘制感知诊断信息。"""

from __future__ import annotations

import numpy as np

from perception.percept_structs import TargetPerceptionResult


def draw_target_perception(cv2: object, canvas_bgr: np.ndarray, result: TargetPerceptionResult) -> None:
    """绘制检测框、实际参与点云计算的 ROI、抓取点与阶段耗时。

    ``canvas_bgr`` 必须由调用方从原始 RGB 拷贝生成。本函数只修改该副本，
    从而不违反相机观测数组只读且可被后端并发使用的约束。
    """
    color = (0, 220, 0) if result.localization is not None and result.localization.has_target_point else (0, 180, 255)
    if result.detection is not None:
        x_min, y_min, x_max, y_max = result.detection.bbox_xyxy
        cv2.rectangle(canvas_bgr, (x_min, y_min), (x_max, y_max), color, 2)
        cv2.putText(canvas_bgr, f"{result.target_id} {result.detection.confidence:.2f}", (x_min, max(20, y_min - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        if result.localization is not None and result.localization.target_point_camera_m is not None:
            center_x, center_y = result.detection.center_pixel
            center_x = max(0, min(canvas_bgr.shape[1] - 1, round(center_x)))
            center_y = max(0, min(canvas_bgr.shape[0] - 1, round(center_y)))
            x, y, z = result.localization.target_point_camera_m
            cv2.drawMarker(canvas_bgr, (center_x, center_y), color, markerType=cv2.MARKER_CROSS, markerSize=16, thickness=2)
            cv2.putText(canvas_bgr, f"X={x:.3f} Y={y:.3f} Z={z:.3f} m", (center_x + 10, min(canvas_bgr.shape[0] - 8, center_y + 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2)

    lines = [f"status: {result.status.value}", f"frame: {result.frame_id}"]
    if result.localization is not None:
        if result.localization.roi is not None:
            roi = result.localization.roi
            # 橙色细框表示最终 ROI，可能小于 YOLO 检测框，因为还会收缩并求静态 ROI 交集。
            cv2.rectangle(canvas_bgr, (roi.x_min, roi.y_min), (roi.x_max, roi.y_max), (255, 180, 0), 1)
        lines.append(f"valid points: {result.localization.valid_point_count}")
    if result.timing is not None:
        lines.extend((f"YOLO: {result.timing.yolo_ms:.1f} ms", f"Locate: {result.timing.localization_ms:.1f} ms", f"Total: {result.timing.process_total_ms:.1f} ms"))
    for index, line in enumerate(lines):
        cv2.putText(canvas_bgr, line, (12, 28 + 24 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
