"""只消费感知诊断结果的 Open3D 点云显示器。"""

from __future__ import annotations

from typing import Any

import numpy as np

from camera.visualization.rgbd_viewer import camera_optical_to_open3d_display
from perception.percept_struct import TargetPerceptionResult


class PerceptionPointCloudViewer:
    """显示最终 ROI 内点和抓取点，不打开相机也不影响感知计算。

    调用者必须在同一线程创建、更新、销毁此对象。实时命令将它放在独立
    显示线程，主感知循环只向该线程提交最新结果。
    """

    def __init__(self, title: str = "YOLO ROI Filtered Point Cloud") -> None:
        try:
            import open3d as o3d
        except ImportError as error:
            raise RuntimeError("显示 ROI 过滤点云需要安装 open3d") from error
        self._o3d = o3d
        self._window = o3d.visualization.Visualizer()
        self._window.create_window(title, width=960, height=720)
        self._cloud = o3d.geometry.PointCloud()
        self._target_marker = o3d.geometry.TriangleMesh.create_sphere(radius=0.018)
        self._target_marker.paint_uniform_color((1.0, 0.0, 0.0))
        self._window.add_geometry(self._cloud)
        self._window.add_geometry(self._target_marker)
        self._window.add_geometry(o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.1))
        self._opened, self._has_view = True, False

    @property
    def is_open(self) -> bool:
        return self._opened

    def update(self, result: TargetPerceptionResult) -> bool:
        """用同一批最终深度内点更新点云和抓取点标记。"""
        if not self._opened:
            return False
        inspection = None if result.localization is None else result.localization.inspection
        if inspection is not None:
            points = camera_optical_to_open3d_display(inspection.points_m)
            colors = inspection.colors_rgb.astype(np.float64) / 255.0
            self._cloud.points = self._o3d.utility.Vector3dVector(points)
            self._cloud.colors = self._o3d.utility.Vector3dVector(colors)
            self._window.update_geometry(self._cloud)
            if len(points) and not self._has_view:
                self._window.reset_view_point(True)
                self._has_view = True
        else:
            # 本帧没有可显示的最终内点时必须清空旧几何，不能误把上一帧当成当前结果。
            self._cloud.points = self._o3d.utility.Vector3dVector(np.empty((0, 3), dtype=np.float64))
            self._cloud.colors = self._o3d.utility.Vector3dVector(np.empty((0, 3), dtype=np.float64))
            self._window.update_geometry(self._cloud)

        if result.localization is not None and result.localization.target_point_camera_m is not None:
            target = np.asarray(result.localization.target_point_camera_m, dtype=np.float64).reshape(1, 3)
            # Open3D 的显示变换只用于观察，绝不回写定位坐标或机器人输入。
            displayed_target = camera_optical_to_open3d_display(target)[0]
            self._target_marker.translate(displayed_target - self._target_marker.get_center(), relative=True)
            self._window.update_geometry(self._target_marker)
        else:
            # 把标记移出可视区域；Open3D 保持同一 geometry 可避免频繁增删对象。
            hidden_target = np.array((0.0, 0.0, -100.0), dtype=np.float64)
            self._target_marker.translate(hidden_target - self._target_marker.get_center(), relative=True)
            self._window.update_geometry(self._target_marker)

        if self._window.poll_events() is False:
            self._opened = False
            return False
        self._window.update_renderer()
        return True

    def close(self) -> None:
        if self._opened:
            self._window.destroy_window()
            self._opened = False
