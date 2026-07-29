from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any


Vector = list[float]


@dataclass
class RobotState:
    """Normalized robot state used by planner/controller code.

    The backend talks to the robot and receives raw JSON. This class keeps the
    rest of the program from depending on the exact nested controller format.
    """

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

    @property
    def has_joints(self) -> bool:
        return self.joints_deg is not None and len(self.joints_deg) == 6

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

    state.joints_deg = _first_vector(
        motion,
        keys=(
            "motor_pos",
            "joint_pos",
            "joint_position",
            "joints",
        ),
        expected_len=6,
    )

    state.actual_joints_deg = _first_vector(
        motion,
        keys=(
            "actual_pos",
            "actual_joint_pos",
            "actual_joints",
        ),
        expected_len=6,
    )

    state.tcp_pe = _first_vector(
        motion,
        keys=(
            "pe",
            "tcp_pe",
            "actual_tcp_pe",
            "cart_pe",
            "tool_pe",
            "pose_pe",
            "tcp_pose",
            "cart_pos",
        ),
        expected_len=6,
    )

    state.tcp_pq = _first_vector(
        motion,
        keys=(
            "pq",
            "tcp_pq",
            "actual_tcp_pq",
            "cart_pq",
            "tool_pq",
            "pose_pq",
        ),
        expected_len=7,
    )

    state.tcp_position = _first_vector(
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
    if state.tcp_position is None and state.tcp_pq is not None:
        state.tcp_position = list(state.tcp_pq[:3])

    follower = _dict_or_empty(ctx.get("follower_msg"))
    robot = _dict_or_empty(ctx.get("robot_msg"))
    driver = _dict_or_empty(ctx.get("driver_msg"))
    log = _dict_or_empty(ctx.get("log_msg"))

    state.follower_active = bool(follower.get("running_state", False))
    state.follower_mode = _optional_str(follower.get("follower_mode"))

    state.moving = (
        _motion_state_is_moving(motion.get("motion_state"))
        or _first_bool(ctx, motion, keys=("moving", "is_moving", "in_motion"))
        or robot.get("motion") == "Moving"
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
