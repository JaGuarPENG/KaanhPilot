"""基于检测 ROI 的有组织点云过滤和鲁棒目标点估计。"""

from __future__ import annotations

import numpy as np

from camera.contracts.cam_structs import AlignedRGBDObservation
from perception.percept_structs import LocalizationConfig, LocalizationResult, PixelROI, PointCloudInspection
from yolo.contracts.yolo_structs import Detection


class RoiPointCloudLocalizer:
    """对单帧、单个 Detection 定位；不持有追踪状态，也不访问相机 SDK。"""

    def __init__(self, config: LocalizationConfig, collect_inspection: bool = False) -> None:
        self._config = config
        # 实时显示才需要保留每帧 N x 3 点数组；普通后端输出保持轻量。
        self._collect_inspection = collect_inspection

    def localize(self, observation: AlignedRGBDObservation, detection: Detection) -> LocalizationResult:
        if detection.frame_id != observation.frame_id:
            raise ValueError("检测与点云观测必须拥有同一 frame_id")
        if detection.capture_timestamp_ms != observation.capture_timestamp_ms:
            raise ValueError("检测与点云观测必须拥有同一采集时间戳")

        height, width = observation.rgb.shape[:2]
        roi = self._effective_roi(detection, width, height)
        if roi is None:
            return LocalizationResult(detection, None, None, 0, 0, None, None)
        # 行对应y，列对应x，reshape(-1, 3) 将 ROI 内的点云展平为 N×3 的数组。
        points = observation.point_cloud_m[roi.y_min:roi.y_max, roi.x_min:roi.x_max].reshape(-1, 3)
        colors = observation.rgb[roi.y_min:roi.y_max, roi.x_min:roi.x_max].reshape(-1, 3)
        roi_point_count = len(points)
        accepted_mask = self._accepted_point_mask(points)
        filtered, filtered_colors = points[accepted_mask], colors[accepted_mask]
        if len(filtered) < self._config.minimum_valid_points:
            return LocalizationResult(detection, None, roi, roi_point_count, len(filtered), None, None, self._inspection(filtered, filtered_colors))

        # 计算ROI内深度中位数
        depth_median = float(np.median(filtered[:, 2]))
        # 根据深度中位数以及 depth_inlier_half_width_m 过滤深度内点
        inlier_mask = np.abs(filtered[:, 2] - depth_median) <= self._config.depth_inlier_half_width_m
        depth_inliers, inlier_colors = filtered[inlier_mask], filtered_colors[inlier_mask]
        if len(depth_inliers) < self._config.minimum_valid_points:
            return LocalizationResult(detection, None, roi, roi_point_count, len(depth_inliers), depth_median, None, self._inspection(depth_inliers, inlier_colors))
        point = tuple(float(value) for value in np.median(depth_inliers, axis=0))
        # 计算深度分布（四分位距）
        depth_spread = float(np.percentile(depth_inliers[:, 2], 75) - np.percentile(depth_inliers[:, 2], 25))
        return LocalizationResult(detection, point, roi, roi_point_count, len(depth_inliers), depth_median, depth_spread, self._inspection(depth_inliers, inlier_colors))

    def _effective_roi(self, detection: Detection, width: int, height: int) -> PixelROI | None:
        """裁剪检测框、按比例向内收缩，并与用户给定静态区域求交集。"""
        x_min, y_min = max(0, detection.x_min), max(0, detection.y_min)
        x_max, y_max = min(width, detection.x_max), min(height, detection.y_max)
        shrink_x = int((x_max - x_min) * self._config.roi_shrink_ratio)
        shrink_y = int((y_max - y_min) * self._config.roi_shrink_ratio)
        if x_max - shrink_x <= x_min + shrink_x or y_max - shrink_y <= y_min + shrink_y:
            return None
        roi = PixelROI(x_min + shrink_x, y_min + shrink_y, x_max - shrink_x, y_max - shrink_y)
        return roi.intersect(self._config.static_roi) if self._config.static_roi is not None else roi

    # 目前想不到这个有啥用
    def _accepted_point_mask(self, points: np.ndarray) -> np.ndarray:
        """返回通过 NaN、用户深度范围和 3D 工作空间筛选的布尔掩码。"""
        mask = np.isfinite(points).all(axis=1)
        if self._config.workspace is not None:
            minimum = np.asarray(self._config.workspace.minimum_m, dtype=np.float32)
            maximum = np.asarray(self._config.workspace.maximum_m, dtype=np.float32)
            mask &= ((points >= minimum) & (points <= maximum)).all(axis=1)
        return mask
    # 拿来输出单张点云用的
    def _inspection(self, points: np.ndarray, colors: np.ndarray) -> PointCloudInspection | None:
        """只有诊断模式复制最终点云，避免常规实时流程产生不必要的内存分配。"""
        if not self._collect_inspection:
            return None
        return PointCloudInspection(points.copy(), colors.copy())
