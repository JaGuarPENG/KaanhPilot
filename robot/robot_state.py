from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any


Vector = list[float]


@dataclass
class ModelState:
    """一个控制器模型的状态。

    ``model_index`` 与 ``motion_msg.motor_pos`` 的外层数组下标一致。``pe``
    保留该模型的原始控制器位姿向量：两条手臂为 6 维、腰部为 3 维、两个
    头部模型均为 1 维。多模型
    控制器的模型 0、1 是七轴臂；其 TCP 和轴角数据分别位于 ``pe/pq`` 的
    ``(0, 1)``、``(2, 3)`` 项。其余模型只记录关节相关状态，不把控制器
    为占位返回的 PQ 当作 TCP。
    """

    model_index: int
    joints_deg: Vector | None = None
    actual_joints_deg: Vector | None = None
    actual_vel_deg: Vector | None = None
    actual_torque: Vector | None = None
    joint_motion_state: list[bool] = field(default_factory=list)
    driver_error_codes: list[int] = field(default_factory=list)
    pe: Vector | None = None
    tcp_pe: Vector | None = None
    tcp_pq: Vector | None = None
    axis_pe: Vector | None = None
    axis_pq: Vector | None = None

    @property
    def has_joints(self) -> bool:
        return self.joints_deg is not None and len(self.joints_deg) > 0

    @property
    def has_tcp_pe(self) -> bool:
        return self.tcp_pe is not None and len(self.tcp_pe) == 6

    @property
    def has_tcp_pq(self) -> bool:
        return self.tcp_pq is not None and len(self.tcp_pq) == 7


@dataclass
class RobotState:
    """统一的机器人状态表示

    参数表：
    - models: 所有模型的状态，按控制器模型索引排列
    - joints_deg: 所有模型的目标关节角，按模型索引和关节索引平铺，单位为度
    - actual_joints_deg: 所有模型的实际关节角，按相同顺序平铺，单位为度
    - tcp_position: TCP位置，单位为米
    - tcp_pe: TCP位姿，321欧拉角表示，单位为米和弧度
    - tcp_pq: TCP位姿，四元数表示，单位为米和弧度，顺序为 [x, y, z, qx, qy, qz, qw]
    - follower_active: 是否处于follower模式
    - follower_mode: follower模式名称
    - moving: 机器人是否在移动
    - error_code: 错误码
    - has_error: 是否存在错误
    - driver_error_codes: 驱动错误码列表
    - robot_status: 机器人状态描述
    - robot_motion: 机器人运动状态描述
    - op_mode: 操作模式
    - activated: 是否已激活
    - jog_coordinate: 当前JOG坐标系
    - timestamp: 状态更新时间戳
    - raw: 原始状态字典，包含所有未解析的字段
    """

    models: list[ModelState] = field(default_factory=list)
    joints_deg: Vector | None = None
    actual_joints_deg: Vector | None = None
    tcp_position: Vector | None = None
    tcp_pe: Vector | None = None
    tcp_pq: Vector | None = None
    follower_active: bool = False
    follower_mode: str | None = None
    moving: bool = False
    error_code: int | None = None
    has_error: bool = False
    driver_error_codes: list[int] = field(default_factory=list)
    robot_status: str | None = None
    robot_motion: str | None = None
    op_mode: str | None = None
    activated: bool = False
    jog_coordinate: str | None = None
    timestamp: float = field(default_factory=time.time)
    raw: dict[str, Any] | None = None

    def get_model(self, model_index: int) -> ModelState | None:
        """按控制器模型索引获取状态；索引不存在时返回 ``None``。"""
        if 0 <= model_index < len(self.models):
            return self.models[model_index]
        return None

    @property
    def has_joints(self) -> bool:
        return self.joints_deg is not None and len(self.joints_deg) > 0

    @property
    def has_tcp_position(self) -> bool:
        return self.tcp_position is not None and len(self.tcp_position) == 3

    @property
    def has_tcp_pe(self) -> bool:
        return self.tcp_pe is not None and len(self.tcp_pe) == 6

    @property
    def has_tcp_pq(self) -> bool:
        return self.tcp_pq is not None and len(self.tcp_pq) == 7



def parse_robot_state(raw_status: dict[str, Any] | None) -> RobotState:
    """Parse one controller `get` response into a RobotState.

    Known KAANH controller fields:
        ret_context.motion_msg.motor_pos -> motor joint angles in degrees
        ret_context.motion_msg.actual_pos -> actual joint angles in degrees
        ret_context.motion_msg.pe -> TCP pose as 321 Euler, [x, y, z, rx, ry, rz]
        ret_context.motion_msg.pq -> TCP pose as quaternion, [x, y, z, qx, qy, qz, qw]
        ret_context.follower_msg.running_state -> follower active flag

    Position units are kept exactly as the controller reports them. In your
    sample `pe/pq` position looks like millimeters.
    """

    raw_status = _normalize_raw_status(raw_status)
    state = RobotState(raw=raw_status)
    if not isinstance(raw_status, dict):
        return state

    ctx = raw_status.get("ret_context", raw_status)
    if not isinstance(ctx, dict):
        return state

    motion = ctx.get("motion_msg", {})
    if not isinstance(motion, dict):
        motion = {}

    follower = _dict_or_empty(ctx.get("follower_msg"))
    robot = _dict_or_empty(ctx.get("robot_msg"))
    driver = _dict_or_empty(ctx.get("driver_msg"))
    log = _dict_or_empty(ctx.get("log_msg"))

    state.follower_active = bool(follower.get("running_state", False))
    state.follower_mode = _optional_str(follower.get("follower_mode"))

    state.models = _parse_model_states(motion, driver)
    _set_default_model_compatibility_fields(state)
    direct_tcp_position = _first_vector(
        motion,
        keys=(
            "tcp_position",
            "actual_tcp_position",
            "tool_position",
            "cart_position",
            "position",
        ),
        expected_len=3,
    )
    if direct_tcp_position is not None:
        state.tcp_position = direct_tcp_position

    # 多模型报文的 motion_state 在静止时也可能全部为 true，故控制器给出
    # 明确 motion 文本时必须优先使用它。
    controller_motion = _optional_str(robot.get("motion"))
    state.moving = (
        controller_motion == "Moving"
        if controller_motion is not None
        else (
            _motion_state_is_moving(motion.get("motion_state"))
            or _first_bool(ctx, motion, keys=("moving", "is_moving", "in_motion"))
        )
    )
    state.robot_status = _optional_str(robot.get("status"))
    state.robot_motion = _optional_str(robot.get("motion"))
    state.op_mode = _optional_str(robot.get("op_mode"))
    state.activated = robot.get("activate") == "Enabled"
    state.jog_coordinate = _optional_str(motion.get("jog_coordinate"))

    state.driver_error_codes = _int_list(driver.get("driver_err_code"))
    state.error_code = _first_int(
        robot,
        log,
        keys=("cs_error_code", "error_code", "err_code", "state_id"),
    )
    state.has_error = bool(log.get("has_error", False)) or any(
        code != 0 for code in state.driver_error_codes
    )
    if state.error_code == 0 and not state.has_error:
        state.error_code = None

    return state


def _parse_model_states(motion: dict[str, Any], driver: dict[str, Any]) -> list[ModelState]:
    """解析单模型及 2.5.10 多模型的关节与位姿状态。"""
    joint_groups = _first_vector_groups(
        motion,
        keys=("motor_pos", "joint_pos", "joint_position", "joints"),
    )
    actual_groups = _first_vector_groups(
        motion,
        keys=("actual_pos", "actual_joint_pos", "actual_joints"),
    )
    velocity_groups = _first_vector_groups(motion, keys=("actual_vel",))
    torque_groups = _first_vector_groups(motion, keys=("actual_toq", "actual_torque"))
    pe_groups = _first_vector_groups(
        motion,
        keys=("pe", "tcp_pe", "actual_tcp_pe", "cart_pe", "tool_pe", "pose_pe"),
    )
    pq_groups = _first_vector_groups(
        motion,
        keys=("pq", "tcp_pq", "actual_tcp_pq", "cart_pq", "tool_pq", "pose_pq"),
    )
    motion_flags = _bool_list(motion.get("motion_state"))
    driver_errors = _int_list(driver.get("driver_err_code"))

    model_count = max(
        len(joint_groups), len(actual_groups), len(velocity_groups), len(torque_groups)
    )
    if model_count == 0:
        return []

    states: list[ModelState] = []
    flag_offset = error_offset = 0
    for index in range(model_count):
        joints = _group_at(joint_groups, index)
        joint_count = len(joints) if joints is not None else 0
        states.append(
            ModelState(
                model_index=index,
                joints_deg=joints,
                actual_joints_deg=_group_at(actual_groups, index),
                actual_vel_deg=_group_at(velocity_groups, index),
                actual_torque=_group_at(torque_groups, index),
                joint_motion_state=motion_flags[flag_offset : flag_offset + joint_count],
                driver_error_codes=driver_errors[error_offset : error_offset + joint_count],
            )
        )
        flag_offset += joint_count
        error_offset += joint_count

    # 2.5.10 multi: [arm1 TCP, arm1 axis, arm2 TCP, arm2 axis, ...].
    # 仅两条七轴臂拥有业务 TCP；腰和两个外部轴的其余 PQ 是控制器占位数据。
    for model_index, pose_index in ((0, 0), (1, 2)):
        model = states[model_index] if model_index < len(states) else None
        if model is None:
            continue
        model.pe = _group_at(pe_groups, pose_index)
        model.tcp_pe = _vector_of_length(model.pe, 6)
        model.tcp_pq = _vector_of_length(_group_at(pq_groups, pose_index), 7)
        model.axis_pe = _group_at(pe_groups, pose_index + 1)
        model.axis_pq = _vector_of_length(_group_at(pq_groups, pose_index + 1), 7)

    # 腰部和头部模型没有 6 维 TCP；保留原始 PE，以便 mvl 仅移动手臂时
    # 仍能将其作为完整多模型目标的一部分原样回填。
    for model_index, pose_index in ((2, 4), (3, 5), (4, 6)):
        model = states[model_index] if model_index < len(states) else None
        if model is not None:
            model.pe = _group_at(pe_groups, pose_index)

    return states


def _set_default_model_compatibility_fields(state: RobotState) -> None:
    """填充全局关节字段，并保留模型 0 的默认 TCP 字段。

    多模型控制器没有单一的全局 TCP，故 ``tcp_*`` 仍映射到模型 0。
    关节字段则按控制器 ``motor_pos`` 的模型顺序平铺。例如当前五模型
    配置返回 20 项：``[arm1(7), arm2(7), waist(4), head_yaw(1),
    head_pitch(1)]``。
    """
    state.joints_deg = _flatten_model_vectors(state.models, "joints_deg")
    state.actual_joints_deg = _flatten_model_vectors(
        state.models, "actual_joints_deg"
    )

    default_model = state.get_model(0)
    if default_model is None:
        return
    state.tcp_pe = default_model.tcp_pe
    state.tcp_pq = default_model.tcp_pq
    if state.tcp_pq is not None:
        state.tcp_position = list(state.tcp_pq[:3])


def _flatten_model_vectors(
    models: list[ModelState], attribute: str
) -> Vector | None:
    """按模型索引把指定的关节向量连接为一个全局关节向量。"""
    flattened: Vector = []
    found = False
    for model in models:
        vector = getattr(model, attribute)
        if vector is not None:
            flattened.extend(vector)
            found = True
    return flattened if found else None


def _normalize_raw_status(raw_status: Any) -> dict[str, Any] | None:
    """Accept both backend-parsed dicts and raw JSON-shaped controller dicts."""
    if not isinstance(raw_status, dict):
        return None

    normalized = dict(raw_status)
    ret_context = normalized.get("ret_context")
    if isinstance(ret_context, str):
        try:
            normalized["ret_context"] = json.loads(ret_context)
        except json.JSONDecodeError:
            pass
    return normalized


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_vector(
    data: dict[str, Any],
    keys: tuple[str, ...],
    expected_len: int,
) -> Vector | None:
    for key in keys:
        value = data.get(key)
        vector = _to_vector(value)
        if vector is not None and len(vector) == expected_len:
            return vector
    return None


def _first_vector_groups(
    data: dict[str, Any], keys: tuple[str, ...]
) -> list[Vector]:
    """读取控制器的 ``[[model0], [model1], ...]`` 数组。

    单模型旧报文与多模型报文都使用二维数组。为兼容少数直接返回
    ``[j1, ...]`` 的旧设备，直接向量也被包装为一个模型。
    """
    for key in keys:
        value = data.get(key)
        groups = _to_vector_groups(value)
        if groups is not None:
            return groups
    return []


def _to_vector_groups(value: Any) -> list[Vector] | None:
    if not isinstance(value, (list, tuple)):
        return None
    if not value:
        return []

    direct_vector = _to_vector(value)
    if direct_vector is not None:
        return [direct_vector]

    groups: list[Vector] = []
    for item in value:
        vector = _to_vector(item)
        if vector is None:
            return None
        groups.append(vector)
    return groups


def _group_at(groups: list[Vector], index: int) -> Vector | None:
    return list(groups[index]) if 0 <= index < len(groups) else None


def _vector_of_length(value: Vector | None, expected_len: int) -> Vector | None:
    return value if value is not None and len(value) == expected_len else None


def _to_vector(value: Any) -> Vector | None:
    """Convert controller vector shapes to a simple list[float]."""
    if value is None:
        return None

    # Some fields, such as motor_pos, may be shaped like [[j1, ..., j6]].
    while isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
        value = value[0]

    if not isinstance(value, (list, tuple)):
        return None

    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _bool_list(value: Any) -> list[bool]:
    if not isinstance(value, list):
        return []
    return [bool(item) for item in value]


def _first_bool(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    for data in (primary, secondary):
        for key in keys:
            value = data.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in ("true", "1", "yes", "moving"):
                    return True
                if lowered in ("false", "0", "no", "idle", "stop", "stopped"):
                    return False
    return False


def _motion_state_is_moving(value: Any) -> bool:
    if isinstance(value, list):
        return any(bool(item) for item in value)
    return bool(value)


def _first_int(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    keys: tuple[str, ...],
) -> int | None:
    for data in (primary, secondary):
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
