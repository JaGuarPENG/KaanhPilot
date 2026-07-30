"""RGB-D 相机观测的本地诊断查看器。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cv2
import numpy as np

from visualization.coordinates import camera_optical_to_open3d_display

if TYPE_CHECKING:
    from camera.contracts.cam_structs import AlignedRGBDObservation


class ObservationVisualizer:
    """显示 RGB、对齐深度和完整点云；只读取 Camera 公共观测。"""

    IMAGE_WINDOW = "G305 RGB-D | Q / ESC exit"
    POINT_WINDOW = "G305 Point Cloud"

    def __init__(self, show_point_cloud: bool = True, max_field_m: float = 2.0, max_points: int = 200_000) -> None:
        if max_field_m <= 0.0 or max_points <= 0:
            raise ValueError("最大显示深度和最大点数必须为正数")
        self.max_field_m, self.max_points = max_field_m, max_points
        self.is_open, self._cloud_view_initialized = True, False
        self._o3d = self._cloud = self._cloud_window = None
        cv2.namedWindow(self.IMAGE_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.IMAGE_WINDOW, 1280, 900)
        if show_point_cloud:
            self._open_cloud_window()

    def update(self, observation: "AlignedRGBDObservation") -> bool:
        """以当前观测刷新窗口；返回 False 表示用户关闭了诊断视图。"""
        if not self.is_open:
            return False
        self._show_images(observation)
        if self._cloud_window is not None:
            self._show_cloud(observation)
        self._handle_events()
        return self.is_open

    def close(self) -> None:
        if self._cloud_window is not None:
            self._cloud_window.destroy_window()
            self._cloud_window = None
        try:
            cv2.destroyWindow(self.IMAGE_WINDOW)
        except cv2.error:
            pass
        self.is_open = False

    def _open_cloud_window(self) -> None:
        try:
            import open3d as o3d
            self._o3d = o3d
            self._cloud = o3d.geometry.PointCloud()
            self._cloud_window = o3d.visualization.Visualizer()
            self._cloud_window.create_window(self.POINT_WINDOW, width=960, height=720)
            self._cloud_window.add_geometry(self._cloud)
            self._cloud_window.add_geometry(o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1))
        except Exception as error:
            # Open3D 不可用不应阻止 RGB/深度诊断。
            print(f"[Camera Viewer] Open3D 点云窗口不可用: {error}")
            self._o3d = self._cloud = self._cloud_window = None

    def _show_images(self, observation: "AlignedRGBDObservation") -> None:
        rgb = cv2.cvtColor(observation.rgb, cv2.COLOR_RGB2BGR)
        depth = self._depth_colormap(observation.depth_m)
        overlay = rgb.copy()
        valid = observation.depth_m > 0.0
        overlay[valid] = cv2.addWeighted(rgb[valid], 0.55, depth[valid], 0.45, 0.0)
        self._label(rgb, f"RGB | frame={observation.frame_id}")
        self._label(depth, f"Aligned depth | 0-{self.max_field_m:.2f} m")
        self._label(overlay, f"RGB + depth | timestamp={observation.capture_timestamp_ms} ms")
        cv2.imshow(self.IMAGE_WINDOW, np.vstack((np.hstack((rgb, depth)), np.hstack((overlay, np.zeros_like(rgb))))))

    def _show_cloud(self, observation: "AlignedRGBDObservation") -> None:
        assert self._o3d is not None and self._cloud is not None and self._cloud_window is not None
        points = observation.point_cloud_m.reshape(-1, 3)
        colors = observation.rgb.reshape(-1, 3).astype(np.float64) / 255.0
        valid = np.isfinite(points).all(axis=1) & (points[:, 2] <= self.max_field_m)
        points, colors = points[valid], colors[valid]
        if len(points) > self.max_points:
            step = math.ceil(len(points) / self.max_points)
            points, colors = points[::step], colors[::step]
        self._cloud.points = self._o3d.utility.Vector3dVector(camera_optical_to_open3d_display(points))
        self._cloud.colors = self._o3d.utility.Vector3dVector(colors)
        self._cloud_window.update_geometry(self._cloud)
        if len(points) and not self._cloud_view_initialized:
            self._cloud_window.reset_view_point(True)
            self._cloud_view_initialized = True
        self._cloud_window.poll_events()
        self._cloud_window.update_renderer()

    def _depth_colormap(self, depth_m: np.ndarray) -> np.ndarray:
        normalized = np.clip(depth_m / self.max_field_m, 0.0, 1.0)
        normalized[depth_m <= 0.0] = 0.0
        image = cv2.applyColorMap(((1.0 - normalized) * 255.0).astype(np.uint8), cv2.COLORMAP_TURBO)
        image[depth_m <= 0.0] = 0
        return image

    @staticmethod
    def _to_display_coordinates(camera_points_m: np.ndarray) -> np.ndarray:
        """兼容既有测试入口；实现位于 visualization.coordinates。"""
        return camera_optical_to_open3d_display(camera_points_m)

    @staticmethod
    def _label(image: np.ndarray, text: str) -> None:
        cv2.putText(image, text, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(image, text, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (20, 20, 20), 1, cv2.LINE_AA)

    def _handle_events(self) -> None:
        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q"), 27):
            self.is_open = False
            return
        try:
            self.is_open = cv2.getWindowProperty(self.IMAGE_WINDOW, cv2.WND_PROP_VISIBLE) >= 1
        except cv2.error:
            self.is_open = False
