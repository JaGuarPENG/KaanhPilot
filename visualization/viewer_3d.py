"""感知层 ROI 最终内点与抓取点的 Open3D 显示器。"""

from __future__ import annotations

import numpy as np

from perception.percept_structs import TargetPerceptionResult

def camera_optical_to_open3d_display(camera_points_m: np.ndarray) -> np.ndarray:
    """把 camera_optical_frame 转为更便于 Open3D 观察的坐标。

    项目中的真实传感器坐标保持 X 向右、Y 向下、Z 向前。Open3D 窗口中将
    Y、Z 反向，使画面符合常见的 Y 向上观察习惯。此函数只返回显示副本，
    调用方不得将其结果写回定位结果、标定数据或机器人控制输入。
    """
    return np.asarray(camera_points_m, dtype=np.float64) * np.array([1.0, -1.0, -1.0], dtype=np.float64)

class Viewer3D:
    """显示最终 ROI 内点和抓取点，不打开相机也不影响感知计算。

    调用者必须在同一线程创建、更新和销毁此对象。实时命令把它放在显示
    线程，后端只提交最新结果，因而 Open3D 刷新不会阻塞 YOLO 或点云定位。
    """

    def __init__(self) -> None:
        try:
            import open3d as o3d
        except ImportError as error:
            raise RuntimeError("显示点云需要安装 open3d") from error
        self._o3d = o3d
        self._window = o3d.visualization.Visualizer()
        self._window.create_window("3D Viewer", width=960, height=720)
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
        """更新同一批最终深度内点和由它们得到的抓取点标记。"""
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
            # 本帧无有效内点时清空旧点，不能让操作者把上一帧误认为当前结果。
            self._cloud.points = self._o3d.utility.Vector3dVector(np.empty((0, 3), dtype=np.float64))
            self._cloud.colors = self._o3d.utility.Vector3dVector(np.empty((0, 3), dtype=np.float64))
            self._window.update_geometry(self._cloud)

        if result.localization is not None and result.localization.target_point_camera_m is not None:
            target = np.asarray(result.localization.target_point_camera_m, dtype=np.float64).reshape(1, 3)
            displayed_target = camera_optical_to_open3d_display(target)[0]
            self._target_marker.translate(displayed_target - self._target_marker.get_center(), relative=True)
        else:
            # 复用一个 geometry，避免每帧增删对象；隐藏位置不会影响任何传感器坐标数据。
            self._target_marker.translate(np.array((0.0, 0.0, -100.0)) - self._target_marker.get_center(), relative=True)
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


