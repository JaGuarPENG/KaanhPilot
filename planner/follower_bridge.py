"""感知结果到 KAANH follower 的连续桥接器。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import TYPE_CHECKING, Callable

import numpy as np

from perception.percept_structs import TargetPerceptionResult, TargetStatus
from planner.pose import calculate_pq_delta, quaternion_to_rotation
from planner.target_position_filter import TargetPositionFilter

if TYPE_CHECKING:
    # 运行时采用鸭子类型，保证独立调试视图无需 websocket 控制器依赖。
    from robot.kaanh_backend import KaanhRobotBackend

@dataclass(frozen=True, slots=True)
class BridgeState:
    """储存桥接器状态，用以在可视化中显示。
    
    参数表：
    - status: 桥接器状态，包括 "running"、"target lost"、"hold"、"stopped" 
    - raw_point_m: 原始感知点，单位为米；无效时为 None
    - filtered_point_m: 滤波后点，单位为米；无效时为 None
    - tcp_target_m: follower 目标点，单位为米；无效时为 None
    
    """
    status: str
    raw_point_m: tuple[float, float, float] | None
    filtered_point_m: tuple[float, float, float] | None
    tcp_target_m: tuple[float, float, float] | None


@dataclass(frozen=True, slots=True)
class FollowerBridgeConfig:
    """Follower 桥接器配置。

    ``camera_translation_m``、``camera_quaternion_xyzw`` 共同表示
    ``T_sim_base_from_camera``。接近距离沿启动时工具负 Z 轴施加。

    参数表：
    - camera_translation_m: 相机在仿真中相对于原点（机器人基座）平移，单位为米。
    - camera_quaternion_xyzw: 相机在仿真中相对于原点的旋转四元数，xyzw 顺序。
    - frequency_hz: 向控制器下发 follower 的频率，单位为 Hz
    - approach_distance_m: follower 目标点沿工具负 Z 轴的接近距离，单位为米
    - hold_after_s: 目标丢失后保持原地等待的时间，单位为秒
    - stop_after_s: 目标丢失后停止 follower 的时间，单位为秒


    """

    camera_translation_m: tuple[float, float, float]
    camera_quaternion_xyzw: tuple[float, float, float, float]
    frequency_hz: float = 8.0
    approach_distance_m: float = 0.1
    hold_after_s: float = 0.5
    stop_after_s: float = 2.0

    def __post_init__(self) -> None:
        if self.frequency_hz <= 0 or self.approach_distance_m < 0:
            raise ValueError("follower 频率必须为正，接近距离不能为负")
        if not 0 <= self.hold_after_s < self.stop_after_s:
            raise ValueError("必须满足 0 <= hold_after_s < stop_after_s")
        translation = np.asarray(self.camera_translation_m, dtype=float)
        quat = np.asarray(self.camera_quaternion_xyzw, dtype=float)
        if translation.shape != (3,) or quat.shape != (4,) or not np.isfinite(np.r_[translation, quat]).all():
            raise ValueError("外参必须由有限的三维平移和四元数组成")
        if np.linalg.norm(quat) < 1e-9:
            raise ValueError("外参四元数不能为零")
        is_identity = np.allclose(translation, 0) and np.allclose(quat / np.linalg.norm(quat), (0, 0, 0, 1))
        if is_identity:
            raise ValueError("外参不能为单位阵，请检查相机外参设置")


class FollowerBridge:
    """以固定频率把最新感知目标发送给 follower。

    调用方只需调用 ``submit_perception`` 提交感知结果；后台线程负责读取
    控制端口状态、执行新鲜度策略并发送命令。``stop`` 可重复调用。
    """

    def __init__(self, robot: KaanhRobotBackend, config: FollowerBridgeConfig, state: BridgeState | None = None, position_filter: TargetPositionFilter | None = None) -> None:
        self._robot, self._config = robot, config
        self._filter = position_filter
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_pq: list[float] | None = None
        self._latest_target_pq: list[float] | None = None
        self._target_lost_at: float | None = None
        self._joints_rad: np.ndarray | None = None
        self._debug = state if state is not None else BridgeState(
            status="stopped",
            raw_point_m=None,
            filtered_point_m=None,
            tcp_target_m=None,
        )

    @property
    def display_state(self) -> BridgeState:
        """Return the latest immutable debug snapshot."""
        with self._lock:
            return self._debug

    @property
    def debug_state(self) -> BridgeState:
        """调试状态兼容别名，供测试和外部显示器读取最新不可变快照。"""
        return self.display_state

    @property
    def latest_joints_rad(self) -> np.ndarray | None:
        """桥接器最后一次控制端口读取到的六轴关节角，单位为弧度。"""
        with self._lock:
            return None if self._joints_rad is None else self._joints_rad.copy()

    def start(self) -> None:
        """启动一次连续 follower 会话并锁定起始 TCP 姿态。"""
        if self._thread is not None:
            raise RuntimeError("FollowerBridge 已启动")
        if not self._robot.start_follower() or self._robot.follower_start_pq is None:
            raise RuntimeError("无法启动 follower 或读取起始 TCP PQ")
        self._start_pq = list(self._robot.follower_start_pq)
        if self._filter is not None:
            self._filter.reset()
        self._stop_event.clear()
        self._set_debug(None, None, None, "running")
        print(f"[Bridge] 启动 follower 桥接器，起始 TCP PQ: {self._start_pq}")
        self._thread = threading.Thread(target=self._run, name="follower-bridge", daemon=True)
        self._thread.start()

    def submit_perception(self, result: TargetPerceptionResult) -> None:
        """提交 perception 结果；此方法不进行网络通信，因此不会阻塞推理。"""
        if result.status == TargetStatus.TARGET_LOST:
            with self._lock:
                if self._filter is not None:
                    self._filter.reset()
                self._target_lost_at = time.monotonic()
            self._set_debug(None, None, None, "target lost")
            print("[Bridge] 目标丢失，重置滤波器并开始计时")
            return
        localization = result.localization
        if localization is None or localization.target_point_camera_m is None:
            return
        if self._start_pq is None:
            return
        raw = self._camera_to_base(localization.target_point_camera_m)
        # 滤波处理
        if self._filter is not None:
            filtered = self._filter.update(raw)
            if not filtered.accepted:
                print(f"[Bridge] 滤波器拒绝跳变点 {raw}，保持上次有效点 {self._filter.position_m}")
                self._set_debug(raw, filtered.position_m, None, "filtered rejected")
                return
            target_position = self._apply_approach_offset(filtered.position_m)
        else:
            target_position = self._apply_approach_offset(raw)
        # 目前没考虑姿态变换，统一以起始时的姿态作为 follower 目标姿态。
        # TODO: 应该在外部指定姿态，或者在感知结果中提供姿态信息。
        target_pq = [*(value * 1000.0 for value in target_position), *self._start_pq[3:]]
        with self._lock:
            self._latest_target_pq = target_pq
            self._target_lost_at = None
        self._set_debug(
            raw,
            filtered.position_m if self._filter is not None else raw,
            target_position,
            "running",
        )


    def stop(self) -> None:
        """停止后台循环和控制器 follower；用于用户退出及全部失败路径。"""
        self._stop_event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        try:
            if self._robot.follower_state:
                self._robot.stop_follower()
                print("[Bridge] 停止 follower 桥接器")
        finally:
            with self._lock:
                self._latest_target_pq, self._target_lost_at = None, None
                if self._filter is not None:
                    self._filter.reset()
            self._set_debug(None, None, None, "stopped")
            

    def _run(self) -> None:
        period = 1.0 / self._config.frequency_hz
        while not self._stop_event.is_set():
            start_time = time.monotonic()
            try:
                self._control_loop(start_time)
            except Exception as error:
                self._set_debug(None, None, None, "error")
                print(f"[Bridge] 后台循环异常: {error}")
                self._stop_event.set()
                try:
                    if self._robot.follower_state:
                        self._robot.stop_follower()
                finally:
                    if self._filter is not None:
                        self._filter.reset()
                break
            self._stop_event.wait(max(0.0, period - (time.monotonic() - start_time)))

    def _control_loop(self, now: float) -> None:
        """一个 8 Hz 周期：先读取控制端口状态，再决定发送目标或原地保持。"""
        state = self._robot.get_robot_state()
        if state.raw is None or not state.has_tcp_pq:
            raise RuntimeError("控制端口 get 未返回有效 TCP PQ")
        if state.has_joints:
            with self._lock:
                self._joints_rad = np.deg2rad(state.joints_deg)
        with self._lock:
            target, lost_at = self._latest_target_pq, self._target_lost_at
        if lost_at is not None and now - lost_at >= self._config.stop_after_s:
            raise RuntimeError("目标丢失超时")
        if target is None or (lost_at is not None and now - lost_at >= self._config.hold_after_s):
            # Hold 必须以当前 TCP 为绝对目标，转换后发送才能真正原地保持。
            target = list(state.tcp_pq)
            print(f"[Bridge] 目标丢失，保持原地 TCP PQ: {target}")
            current_debug = self.display_state
            self._set_debug(
                current_debug.raw_point_m,
                current_debug.filtered_point_m,
                tuple(value * 0.001 for value in target[:3]),
                "hold",
            )
        self._send_absolute_target(target)

    def _send_absolute_target(self, target_pq_mm: list[float]) -> None:
        if self._start_pq is None:
            raise RuntimeError("follower 起始姿态不存在")
        delta = calculate_pq_delta(self._start_pq, target_pq_mm, rotation_frame="local")
        if not self._robot.send_pose_pq(delta):
            raise RuntimeError("UDP follower 指令发送失败")

    def _set_debug(self, raw: tuple[float, float, float] | None, filtered: tuple[float, float, float] | None, target: tuple[float, float, float] | None, status: str) -> None:
        snapshot = BridgeState(
            status=status,
            raw_point_m=raw,
            filtered_point_m=filtered,
            tcp_target_m=target,
        )
        with self._lock:
            self._debug = snapshot

    def _camera_to_base(self, point_m: tuple[float, float, float]) -> tuple[float, float, float]:
        rotation = quaternion_to_rotation(self._config.camera_quaternion_xyzw)
        converted = rotation @ np.asarray(point_m, dtype=float) + np.asarray(self._config.camera_translation_m, dtype=float)
        return tuple(float(value) for value in converted)

    def _apply_approach_offset(self, point_m: tuple[float, float, float]) -> tuple[float, float, float]:
        assert self._start_pq is not None
        tool_z_in_base = quaternion_to_rotation(tuple(self._start_pq[3:]))[:, 2]
        target = np.asarray(point_m) - self._config.approach_distance_m * tool_z_in_base
        return tuple(float(value) for value in target)

