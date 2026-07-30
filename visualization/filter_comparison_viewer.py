"""相机深度过滤前后点云的 Open3D 对比查看器。"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np

from visualization.coordinates import camera_optical_to_open3d_display

if TYPE_CHECKING:
    from camera.contracts.cam_structs import AlignedRGBDObservation


class FilterComparisonPointCloudViewer:
    """左右显示同一 frame_id 的原始点云和相机深度过滤后点云。"""

    def __init__(self, max_field_m: float = 2.0, max_points: int = 200_000) -> None:
        if max_field_m <= 0.0 or max_points <= 0:
            raise ValueError("最大显示深度和最大点数必须为正数")
        try:
            import open3d as o3d
        except ImportError as error:
            raise RuntimeError("过滤点云对比需要安装 open3d") from error
        self._o3d, self._max_field_m, self._max_points = o3d, max_field_m, max_points
        self._is_open, self._initialized_views = True, set()
        self._raw_window, self._raw_cloud = self._create_window("G305 Raw Point Cloud", 0)
        self._filtered_window, self._filtered_cloud = self._create_window("G305 Filtered Point Cloud", 960)

    @property
    def is_open(self) -> bool:
        return self._is_open

    def update(self, raw: "AlignedRGBDObservation", filtered: "AlignedRGBDObservation") -> bool:
        if raw.frame_id != filtered.frame_id:
            raise ValueError("过滤前后观测必须属于同一 frame_id")
        if not self._is_open:
            return False
        self._update_cloud(self._raw_window, self._raw_cloud, raw, "raw")
        self._update_cloud(self._filtered_window, self._filtered_cloud, filtered, "filtered")
        return self._is_open

    def close(self) -> None:
        if self._is_open:
            self._raw_window.destroy_window()
            self._filtered_window.destroy_window()
            self._is_open = False

    def _create_window(self, title: str, left: int) -> tuple[Any, Any]:
        cloud = self._o3d.geometry.PointCloud()
        window = self._o3d.visualization.Visualizer()
        window.create_window(title, width=940, height=720, left=left, top=80)
        window.add_geometry(cloud)
        window.add_geometry(self._o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1))
        return window, cloud

    def _update_cloud(self, window: Any, cloud: Any, observation: "AlignedRGBDObservation", key: str) -> None:
        points, colors = observation.point_cloud_m.reshape(-1, 3), observation.rgb.reshape(-1, 3).astype(np.float64) / 255.0
        step = max(1, math.ceil(len(points) / self._max_points))
        points, colors = points[::step], colors[::step]
        valid = np.isfinite(points).all(axis=1) & (points[:, 2] <= self._max_field_m)
        cloud.points = self._o3d.utility.Vector3dVector(camera_optical_to_open3d_display(points[valid]))
        cloud.colors = self._o3d.utility.Vector3dVector(colors[valid])
        window.update_geometry(cloud)
        if len(points[valid]) and key not in self._initialized_views:
            window.reset_view_point(True)
            self._initialized_views.add(key)
        if window.poll_events() is False:
            self._is_open = False
            return
        window.update_renderer()
