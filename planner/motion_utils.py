"""机器人运动到位判断工具。

位置使用控制器 get 返回的毫米，姿态优先使用 pq 判断。
PE 和 PQ 都是控制器返回的绝对 TCP 位姿，而不是 follower 下发的增量值。
"""

from __future__ import annotations

import math
from typing import Sequence
from robot.robot_state import RobotState


def position_distance(
    current_position: Sequence[float],
    target_position: Sequence[float],
) -> float:
    """计算两个位置之间的直线距离，输入和输出单位均为毫米。"""
    _validate_length(current_position, 3, "current_position")
    _validate_length(target_position, 3, "target_position")
    return math.sqrt(
        sum(
            (float(current_position[index]) - float(target_position[index])) ** 2
            for index in range(3)
        )
    )


def quaternion_angle_error(
    current_pq: Sequence[float],
    target_pq: Sequence[float],
) -> float:
    """计算两个 pq 的最小旋转夹角，返回弧度。

    pq 格式为 [x, y, z, qx, qy, qz, qw]。
    q 和 -q 表示同一个姿态，因此使用点积绝对值。
    """
    _validate_length(current_pq, 7, "current_pq")
    _validate_length(target_pq, 7, "target_pq")

    current_q = _normalize_quaternion(current_pq[3:])
    target_q = _normalize_quaternion(target_pq[3:])
    dot = abs(sum(left * right for left, right in zip(current_q, target_q)))
    dot = max(-1.0, min(1.0, dot))
    return 2.0 * math.acos(dot)


def is_position_reached(
    state: RobotState,
    target_position: Sequence[float],
    tolerance: float = 1.0,
) -> bool:
    """判断 TCP 位置是否到达目标，tolerance 单位为毫米。"""
    if not state.has_tcp_position:
        return False
    return position_distance(state.tcp_position, target_position) <= tolerance


def is_orientation_reached(
    state: RobotState,
    *,
    target_pq: Sequence[float] | None = None,
    tolerance_deg: float = 1.0,
) -> bool:
    """判断 TCP 姿态是否到达目标。
    使用 pq 计算,因为四元数不会受到欧拉角奇异点影响。
    """
    tolerance_rad = math.radians(tolerance_deg)

    if target_pq is not None and state.has_tcp_pq:
        return quaternion_angle_error(state.tcp_pq, target_pq) <= tolerance_rad
    else:
        print("[Follow] 姿态检查: FALSE, 无法获取目标姿态或当前姿态")
        return False


def is_pose_reached(
    state: RobotState,
    *,
    target_pq: Sequence[float] | None = None,
    position_tolerance_mm: float = 1.0,
    orientation_tolerance_deg: float = 1.0,
    require_orientation: bool = True,
) -> bool:
    """同时判断 TCP 位置和姿态是否到达目标。
    参数 robot_state 是最新的机器人状态,target_pq 是目标位姿,前3个元素为位置,后4个元素为旋转四元数。
    position_tolerance_mm 是位置容差,单位为毫米,orientation_tolerance_deg 是姿态容差，单位为度。
    当 require_orientation=False 时，只判断位置，兼容当前仅位置控制的流程。
    """
    target_position = target_pq[:3] if target_pq is not None else None

    position_ok = is_position_reached(
        state,
        target_position,
        tolerance=position_tolerance_mm,
    )

    if not position_ok:
        return False

    if not require_orientation:
        return True
    
    orientation_ok = is_orientation_reached(
        state,
        target_pq=target_pq,
        tolerance_deg=orientation_tolerance_deg,
    )
    if not orientation_ok:
        # print(f"[Follow] Orientation check: FALSE, angle error: {math.degrees(quaternion_angle_error(state.tcp_pq, target_pq)):.3f} deg")
        return False
    else:
        return True 

  


class StablePoseChecker:
    """要求连续多个状态周期到位后才返回 True。
    声明时指定 required_cycles，调用 update() 输入最新状态，连续满足条件后返回 True。
    调用 update() 时可以指定目标位姿和容差，传入参数state, target_pq, position_tolerance_mm, orientation_tolerance_deg。
    调用 reset() 可以重置计数，开始新的目标动作。
    """

    def __init__(self, required_cycles: int = 3):
        if required_cycles < 1:
            raise ValueError("required_cycles 必须大于等于 1")
        self.required_cycles = required_cycles
        self._reached_cycles = 0

    def update(
        self,
        state: RobotState,
        *,
        target_pq: Sequence[float] | None = None,
        position_tolerance_mm: float = 1.0,
        orientation_tolerance_deg: float = 1.0,
        require_orientation: bool = True,
    ) -> bool:
        """输入一次最新状态，连续满足条件后返回 True。
        参数说明：
        - state: 最新的机器人状态
        - target_pq: 目标位姿
        - position_tolerance_mm: 位置容差，单位为毫米
        - orientation_tolerance_deg: 姿态容差，单位为度
        - require_orientation: 是否要求姿态匹配
        """
        reached = is_pose_reached(
            state,
            target_pq=target_pq,
            position_tolerance_mm=position_tolerance_mm,
            orientation_tolerance_deg=orientation_tolerance_deg,
            require_orientation=require_orientation,
        )

        if reached:
            self._reached_cycles += 1
        else:
            # 中间有一次超出容差，就重新累计，避免抖动时误判。
            self._reached_cycles = 0

        return self._reached_cycles >= self.required_cycles

    def reset(self) -> None:
        """开始新的目标动作前重置计数。"""
        self._reached_cycles = 0


def _normalize_quaternion(values: Sequence[float]) -> tuple[float, float, float, float]:
    norm = math.sqrt(sum(float(value) ** 2 for value in values))
    if norm <= 1e-12:
        raise ValueError("四元数长度不能为零")
    return tuple(float(value) / norm for value in values)  # type: ignore[return-value]


def _validate_length(values: Sequence[float], expected: int, name: str) -> None:
    if len(values) != expected:
        raise ValueError(f"{name} 必须包含 {expected} 个数值")
