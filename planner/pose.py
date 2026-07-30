"""基于四元数的机器人位姿规划工具。

约定：
    pq = [x, y, z, qx, qy, qz, qw]
    get 返回的绝对位置单位为 mm
    follower 下发的位置增量单位为 m
    四元数使用 [qx, qy, qz, qw] 顺序

本模块只负责位姿数学计算，不负责机器人通信。
"""

from __future__ import annotations

import math
import numpy as np
from typing import Sequence


NumberVector = Sequence[float]
Quaternion = tuple[float, float, float, float]


def calculate_pq_delta(
    current_pq: NumberVector,
    target_pq: NumberVector,
    *,
    rotation_frame: str = "local",
) -> list[float]:
    """计算两个绝对 pq 之间的相对增量。

    输入位置默认是 get 返回的 mm，输出位置默认是 m。
    旋转部分通过四元数乘法计算，不能直接对四元数数组做减法。
    """
    _validate_length(current_pq, 7, "current_pq")
    _validate_length(target_pq, 7, "target_pq")
    _validate_rotation_frame(rotation_frame)

    current_q = _normalize_quaternion(tuple(float(v) for v in current_pq[3:]))
    target_q = _normalize_quaternion(tuple(float(v) for v in target_pq[3:]))

    # q 和 -q 表示同一个旋转，选择较短的四元数路径。
    if _quaternion_dot(current_q, target_q) < 0.0:
        target_q = _quaternion_scale(target_q, -1.0)

    current_q_inverse = _quaternion_conjugate(current_q)
    if rotation_frame == "local":
        delta_q = _quaternion_multiply(current_q_inverse, target_q)
    else:
        delta_q = _quaternion_multiply(target_q, current_q_inverse)
    # 先计算基座标系下的位置差
    delta_position_world = [
        target_pq[i] - current_pq[i]
        for i in range(3)
    ]

    # 再使用起始姿态的逆旋转，转换到 tool 坐标系
    delta_position_tool = _rotate_vector_by_quaternion(
        delta_position_world,
        current_q_inverse,
    )

    return delta_position_tool + list(_normalize_quaternion(delta_q))


def axis_angle_to_quaternion(axis: int, angle_rad: float) -> list[float]:
    """根据坐标轴和弧度角生成相对旋转四元数。
    axis=0、1、2 分别表示 X、Y、Z 轴。
    返回顺序为 [qx, qy, qz, qw]。
    """
    if axis not in (0, 1, 2):
        raise ValueError("axis 必须是 0、1 或 2，分别表示 X、Y、Z 轴")
    half_angle = float(angle_rad) / 2.0
    quaternion = [0.0, 0.0, 0.0, math.cos(half_angle)]
    quaternion[axis] = math.sin(half_angle)
    return quaternion


def apply_tool_delta_to_pq(
    start_pq: NumberVector,
    delta_position_tool: NumberVector,
    delta_quaternion_tool: NumberVector,
    *,
    rotation_frame: str = "local",
) -> list[float]:
    """将工具坐标系下的位姿增量转换为基坐标系绝对目标 pq。

    参数：
        start_pq：follower 启动时的绝对 TCP pq，位置单位为 mm。
        delta_position_tool：工具坐标系下的位置增量，单位为 mm。
        delta_quaternion_tool：工具坐标系下的相对旋转四元数。
        rotation_frame：默认 local，表示增量相对于工具坐标系。

    local 模式使用刚体变换组合：
        T_target = T_start * T_delta
        p_target = p_start + R_start * delta_position_tool
        q_target = q_start * q_delta

    返回基坐标系下的绝对目标 pq，位置单位为 mm。
    """
    _validate_length(start_pq, 7, "start_pq")
    _validate_length(delta_position_tool, 3, "delta_position_tool")
    _validate_length(delta_quaternion_tool, 4, "delta_quaternion_tool")
    _validate_rotation_frame(rotation_frame)

    start_position = [float(value) for value in start_pq[:3]]
    start_q = _normalize_quaternion(tuple(float(v) for v in start_pq[3:]))
    delta_q = _normalize_quaternion(
        tuple(float(v) for v in delta_quaternion_tool)
    )

    # 控制器使用米发送增量，状态中的绝对位置使用毫米。
    delta_position_mm = [float(value) for value in delta_position_tool]

    if rotation_frame == "local":
        delta_position_base_mm = _rotate_vector_by_quaternion(
            delta_position_mm,
            start_q,
        )
        target_q = _quaternion_multiply(start_q, delta_q)
    else:
        # world 模式下，位置增量已经在基坐标系中。
        delta_position_base_mm = delta_position_mm
        target_q = _quaternion_multiply(delta_q, start_q)

    target_position = [
        start_position[index] + delta_position_base_mm[index]
        for index in range(3)
    ]
    return target_position + list(_normalize_quaternion(target_q))


def quaternion_to_rotation(quaternion_xyzw: tuple[float, float, float, float]) -> np.ndarray:
    """把 xyzw 四元数转换为 3x3 旋转矩阵，输入零范数会明确失败。"""
    x, y, z, w = (float(value) for value in quaternion_xyzw)
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < 1e-9:
        raise ValueError("四元数不能为零")
    x, y, z, w = x / norm, y / norm, z / norm, w / norm
    return np.array(((1 - 2 * (y*y + z*z), 2 * (x*y - z*w), 2 * (x*z + y*w)), (2 * (x*y + z*w), 1 - 2 * (x*x + z*z), 2 * (y*z - x*w)), (2 * (x*z - y*w), 2 * (y*z + x*w), 1 - 2 * (x*x + y*y))), dtype=float)



def _rotate_vector_by_quaternion(
    vector: NumberVector,
    quaternion: Quaternion,
) -> list[float]:
    """使用四元数将工具坐标系向量旋转到基坐标系。"""
    _validate_length(vector, 3, "vector")
    vector_q = (float(vector[0]), float(vector[1]), float(vector[2]), 0.0)
    rotated_q = _quaternion_multiply(
        _quaternion_multiply(quaternion, vector_q),
        _quaternion_conjugate(quaternion),
    )
    return list(rotated_q[:3])


def _quaternion_multiply(first: Quaternion, second: Quaternion) -> Quaternion:
    """计算两个 [qx, qy, qz, qw] 四元数的乘积。"""
    x1, y1, z1, w1 = first
    x2, y2, z2, w2 = second
    return (
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    )


def _quaternion_conjugate(quaternion: Quaternion) -> Quaternion:
    x, y, z, w = quaternion
    return (-x, -y, -z, w)


def _normalize_quaternion(quaternion: Quaternion) -> Quaternion:
    norm = math.sqrt(sum(value * value for value in quaternion))
    if norm <= 1e-12:
        raise ValueError("四元数长度不能为零")
    return tuple(value / norm for value in quaternion)  # type: ignore[return-value]


def _quaternion_scale(quaternion: Quaternion, scale: float) -> Quaternion:
    return tuple(value * scale for value in quaternion)  # type: ignore[return-value]


def _quaternion_dot(first: Quaternion, second: Quaternion) -> float:
    return sum(left * right for left, right in zip(first, second))




# 验证有效性
def _validate_length(values: NumberVector, expected: int, name: str) -> None:
    if len(values) != expected:
        raise ValueError(f"{name} 必须包含 {expected} 个数值")


def _validate_rotation_frame(rotation_frame: str) -> None:
    if rotation_frame not in ("local", "world"):
        raise ValueError("rotation_frame 必须是 'local' 或 'world'")


if __name__ == "__main__":
    # 测试示例
    input_angle = 15.0
    input_axis = 1  # y 轴
    quaternion = axis_angle_to_quaternion(axis=input_axis, angle_rad=math.radians(input_angle))


    print("目标 PQ:", quaternion)