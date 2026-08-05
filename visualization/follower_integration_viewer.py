"""外置相机 follower 集成调试视图。

可独立运行：``python -m visualization.follower_integration_viewer``。
独立模式使用零位机器人和示例点，只用于检查外参、相机朝向和标记位置；正式
命令则持续调用 ``update`` 以显示真实桥接状态。
"""

from __future__ import annotations

import argparse
import numpy as np

# from planner.follower_bridge import FollowerBridgeConfig, BridgeState
from planner.pose import quaternion_to_rotation


from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True, slots=True)
class FollowerSceneConfig:
    camera_origin_in_base_m: tuple[float, float, float]
    camera_to_base_quaternion_xyzw: tuple[float, float, float, float]
    camera_axis_length_m: float = 0.12


class FollowerDisplayState(Protocol):
    status: str
    raw_point_m: tuple[float, float, float] | None
    filtered_point_m: tuple[float, float, float] | None
    tcp_target_m: tuple[float, float, float] | None


class FollowerIntegrationViewer:
    """PyPlot 调试窗口，展示机器人、固定相机、原始/滤波/TCP 三类点。"""

    def __init__(self, config: FollowerSceneConfig) -> None:
        import roboticstoolbox as rtb
        from robot.robot_dh import create_ka_ur

        self._config = config
        self._robot = create_ka_ur()
        self._env = rtb.backends.PyPlot.PyPlot()
        self._env.launch("Follower Integration Debug")
        self._env.add(self._robot)
        self._ax = self._env.ax
        self._ax.set_xlim((-1.0, 1.5))
        self._ax.set_ylim((-1.0, 1.0))
        self._ax.set_zlim((0.0, 1.5))
        self._camera_artists: list[object] = []
        self._markers: dict[str, object] = {}
        self._draw_camera()

    def update(self, joints_rad: np.ndarray | None, state: FollowerDisplayState) -> bool:
        """更新机器人关节和三个目标点；窗口关闭时返回 False。"""
        if joints_rad is not None and np.asarray(joints_rad).shape == (6,):
            self._robot.q = np.asarray(joints_rad, dtype=float)
        self._set_marker("raw", state.raw_point_m, "#e74c3c", "raw")
        self._set_marker("filtered", state.filtered_point_m, "#f1c40f", "filtered")
        self._set_marker("tcp target", state.tcp_target_m, "#2ecc71", "tcp target")
        self._set_marker("camera", self._config.camera_origin_in_base_m, "#3498db", "camera")
        self._ax.set_title(f"Follower Integration: {state.status}")
        self._env.step(0.001)
        return self._env.ax.figure.canvas.manager.window is not None

    def close(self) -> None:
        """关闭窗口；命令退出时调用，不影响机器人控制状态。"""
        try:
            self._env.close()
        except Exception:
            pass

    def _draw_camera(self) -> None:
        """按配置外参在基座系中画出相机坐标轴。"""
        origin = np.asarray(self._config.camera_origin_in_base_m)
        rotation = quaternion_to_rotation(self._config.camera_to_base_quaternion_xyzw)
        for index, color in enumerate(("r", "g", "b")):
            endpoint = origin + rotation[:, index] * 0.12
            self._camera_artists.extend(self._ax.plot([origin[0], endpoint[0]], [origin[1], endpoint[1]], [origin[2], endpoint[2]], color=color, linewidth=2))

    def _set_marker(self, name: str, point_m: tuple[float, float, float] | None, color: str, label: str) -> None:
        old = self._markers.pop(name, None)
        if old is not None:
            old.remove()
        if point_m is not None:
            point = np.asarray(point_m)
            self._markers[name] = self._ax.scatter([point[0]], [point[1]], [point[2]], s=65, c=color, label=label)


def main() -> None:
    """独立检查外参与示例点显示的最小入口，不连接相机或机器人控制器。"""
    parser = argparse.ArgumentParser(description="独立检查 follower 集成坐标参数")

    joint_deg = [0.0, 0.0, 150.0, -150.0, -90.0, 0.0]
    joint_rad = [np.deg2rad(x) for x in joint_deg]


    parser.add_argument("--translation-m", type=float, nargs=3, default=(0.0, 0.0, 0.5))
    parser.add_argument("--quaternion", type=float, nargs=4, default=(-0.5, 0.5, -0.5, 0.5))
    parser.add_argument("--raw-point-m", type=float, nargs=3, default=(0.2, 0.0, 0.4))
    args = parser.parse_args()
    config = FollowerSceneConfig(
        camera_origin_in_base_m=tuple(args.translation_m),
        camera_to_base_quaternion_xyzw=tuple(args.quaternion),
        camera_axis_length_m=0.12
    )
    viewer = FollowerIntegrationViewer(config)
    state = FollowerDisplayState(
        status="独立参数检查",
        raw_point_m=tuple(args.raw_point_m),
        filtered_point_m=tuple(args.raw_point_m),
        tcp_target_m=tuple(args.raw_point_m),
    )
    try:
        while viewer.update(joint_rad, state):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        viewer.close()


if __name__ == "__main__":
    main()
