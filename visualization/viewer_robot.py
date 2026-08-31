"""通用机器人三维场景查看器。

本模块只负责渲染机器人模型和基座坐标系中的通用图元，不依赖 follower、
BridgeState、相机 SDK、YOLO 或控制器连接。上层命令负责把机器人状态、
手眼外参和感知结果转换为 :class:`ViewerRobotState` 后传入本查看器。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np


Color = str | tuple[float, float, float]


def _as_finite_vector(values: tuple[float, ...], expected_length: int, field_name: str) -> tuple[float, ...]:
    """校验并标准化固定长度的有限浮点向量。"""
    vector = np.asarray(values, dtype=float)
    if vector.shape != (expected_length,) or not np.isfinite(vector).all():
        raise ValueError(f"{field_name} 必须是有限的 {expected_length} 维向量")
    return tuple(float(value) for value in vector)


def _quaternion_to_rotation(quaternion_xyzw: tuple[float, float, float, float]) -> np.ndarray:
    """将 xyzw 四元数转换为旋转矩阵，避免为了绘图依赖 planner 模块。"""
    x, y, z, w = np.asarray(quaternion_xyzw, dtype=float)
    norm = np.linalg.norm((x, y, z, w))
    if norm < 1e-9:
        raise ValueError("坐标系四元数不能为零")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(
        (
            (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
        ),
        dtype=float,
    )


@dataclass(frozen=True, slots=True)
class SceneMarker:
    """基座坐标系中的三维点标记，位置单位恒为米。"""

    id: str
    position_m: tuple[float, float, float]
    label: str = ""
    color: Color = "#e74c3c"
    size: float = 65.0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SceneMarker.id 不能为空")
        if self.size <= 0.0:
            raise ValueError("SceneMarker.size 必须为正数")
        object.__setattr__(self, "position_m", _as_finite_vector(self.position_m, 3, "SceneMarker.position_m"))


@dataclass(frozen=True, slots=True)
class SceneCoordinateFrame:
    """基座坐标系中的局部坐标系图元。

    眼在手相机应由上层计算 ``base_from_camera`` 后作为本对象传入；Viewer
    不读取外参文件，也不读取 TCP，因此也能显示外置相机、标定板等坐标系。
    """

    id: str
    origin_m: tuple[float, float, float]
    quaternion_xyzw: tuple[float, float, float, float]
    axis_length_m: float = 0.12

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("SceneCoordinateFrame.id 不能为空")
        if self.axis_length_m <= 0.0:
            raise ValueError("SceneCoordinateFrame.axis_length_m 必须为正数")
        object.__setattr__(self, "origin_m", _as_finite_vector(self.origin_m, 3, "SceneCoordinateFrame.origin_m"))
        quaternion = _as_finite_vector(self.quaternion_xyzw, 4, "SceneCoordinateFrame.quaternion_xyzw")
        if np.linalg.norm(quaternion) < 1e-9:
            raise ValueError("SceneCoordinateFrame.quaternion_xyzw 不能为零")
        object.__setattr__(self, "quaternion_xyzw", quaternion)


@dataclass(frozen=True, slots=True)
class ViewerRobotState:
    """一次渲染所需的完整通用场景快照。

    参数表：
    - ``joints_rad``：机器人关节角，单位为弧度；如果为 ``None``，则不更新机器人模型。
    - ``markers``：基座坐标系中的点标记。
    - ``coordinate_frames``：基座坐标系中的局部坐标系图元。
    - ``status_text``：窗口标题栏显示的状态文本；如果为空，则显示默认标题。
    
    眼在手相机应由上层计算 ``base_from_camera`` 后作为本对象传入；Viewer 不读取外参文件，也不读取 TCP，因此也能显示外置相机、标定板等坐标系。
    """

    joints_rad: tuple[float, float, float, float, float, float] | None = None
    markers: tuple[SceneMarker, ...] = ()
    coordinate_frames: tuple[SceneCoordinateFrame, ...] = ()
    status_text: str = ""

    def __post_init__(self) -> None:
        if self.joints_rad is not None:
            object.__setattr__(self, "joints_rad", _as_finite_vector(self.joints_rad, 6, "ViewerRobotState.joints_rad"))
        marker_ids = [marker.id for marker in self.markers]
        frame_ids = [frame.id for frame in self.coordinate_frames]
        if len(marker_ids) != len(set(marker_ids)):
            raise ValueError("同一 ViewerRobotState 的 marker id 必须唯一")
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("同一 ViewerRobotState 的 coordinate frame id 必须唯一")


@dataclass(frozen=True, slots=True)
class ViewerRobotConfig:
    """机器人场景窗口配置；所有范围单位为米。"""

    title: str = "Robot Status Viewer"
    x_limits_m: tuple[float, float] = (-1.0, 1.5)
    y_limits_m: tuple[float, float] = (-1.0, 1.0)
    z_limits_m: tuple[float, float] = (0.0, 1.5)
    refresh_step_s: float = 0.001

    def __post_init__(self) -> None:
        for field_name, limits in (("x_limits_m", self.x_limits_m), ("y_limits_m", self.y_limits_m), ("z_limits_m", self.z_limits_m)):
            lower, upper = _as_finite_vector(limits, 2, f"ViewerRobotConfig.{field_name}")
            if lower >= upper:
                raise ValueError(f"ViewerRobotConfig.{field_name} 必须满足下界小于上界")
        if self.refresh_step_s <= 0.0:
            raise ValueError("ViewerRobotConfig.refresh_step_s 必须为正数")


class ViewerRobot:
    """使用 Robotics Toolbox PyPlot 渲染通用机器人三维场景。"""

    def __init__(self, config: ViewerRobotConfig | None = None, *, robot_factory: Callable[[], Any] | None = None) -> None:
        import roboticstoolbox as rtb

        if robot_factory is None:
            from robot.robot_dh import create_ka_ur

            robot_factory = create_ka_ur

        self._config = config or ViewerRobotConfig()
        self._robot = robot_factory()
        self._env = rtb.backends.PyPlot.PyPlot()
        self._env.launch(self._config.title, width=960, height=720)
        self._env.add(self._robot)
        self._ax = self._env.ax
        self._ax.set_xlim(self._config.x_limits_m)
        self._ax.set_ylim(self._config.y_limits_m)
        self._ax.set_zlim(self._config.z_limits_m)
        self._marker_artists: dict[str, object] = {}
        self._frame_artists: dict[str, list[object]] = {}
        self._closed = False

    @property
    def is_closed(self) -> bool:
        """窗口是否已关闭。"""
        return self._closed

    def update(self, state: ViewerRobotState) -> bool:
        """渲染一帧通用场景；窗口关闭时返回 ``False``。"""
        if self._closed:
            return False
        if state.joints_rad is not None:
            self._robot.q = np.asarray(state.joints_rad, dtype=float)
        self._replace_markers(state.markers)
        self._replace_coordinate_frames(state.coordinate_frames)
        self._ax.set_title(state.status_text or self._config.title)
        self._env.step(self._config.refresh_step_s)
        self._closed = self._env.ax.figure.canvas.manager.window is None
        return not self._closed

    def close(self) -> None:
        """关闭本地窗口，不会访问机器人或控制器。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._env.close()
        except Exception:
            pass

    def _replace_markers(self, markers: tuple[SceneMarker, ...]) -> None:
        """按完整快照替换点标记；本帧缺席的旧图元会被移除。"""
        next_ids = {marker.id for marker in markers}
        for marker_id in set(self._marker_artists) - next_ids:
            self._marker_artists.pop(marker_id).remove()
        for marker in markers:
            old = self._marker_artists.pop(marker.id, None)
            if old is not None:
                old.remove()
            point = np.asarray(marker.position_m)
            self._marker_artists[marker.id] = self._ax.scatter([point[0]], [point[1]], [point[2]], s=marker.size, c=marker.color, label=marker.label or marker.id)

    def _replace_coordinate_frames(self, coordinate_frames: tuple[SceneCoordinateFrame, ...]) -> None:
        """按完整快照替换局部坐标系，X/Y/Z 轴分别为红/绿/蓝。"""
        next_ids = {frame.id for frame in coordinate_frames}
        for frame_id in set(self._frame_artists) - next_ids:
            self._remove_coordinate_frame(frame_id)
        for frame in coordinate_frames:
            self._remove_coordinate_frame(frame.id)
            origin = np.asarray(frame.origin_m)
            rotation = _quaternion_to_rotation(frame.quaternion_xyzw)
            artists: list[object] = []
            for axis_index, color in enumerate(("r", "g", "b")):
                endpoint = origin + rotation[:, axis_index] * frame.axis_length_m
                artists.extend(self._ax.plot([origin[0], endpoint[0]], [origin[1], endpoint[1]], [origin[2], endpoint[2]], color=color, linewidth=2))
            self._frame_artists[frame.id] = artists

    def _remove_coordinate_frame(self, frame_id: str) -> None:
        for artist in self._frame_artists.pop(frame_id, []):
            artist.remove()
