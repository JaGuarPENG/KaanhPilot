"""眼在手相机与机器人姿态的三维显示验证指令。

本指令的目标是验证 ``ViewerRobot`` 的通用使用方式，并直观检查手眼外参：

    T_base_camera = T_base_tcp × T_tcp_camera

其中：
    - T_base_tcp：控制器 5888 端口实时返回的 TCP ``pq``；
    - T_tcp_camera：config/calibration/eye_in_hand_cam1.json 中的手眼外参；
    - T_base_camera：本文件实时计算后传给 ViewerRobot 的相机坐标系。

安全说明：
    本文件只连接 5888 监控端口，只发送 login 与 get。它不会连接 5999，
    不会使能机器人，也不会发送 JOG、MoveJ 或 follower 指令。

运行方式（在项目根目录执行）：
    C:\\Users\\11051\\miniconda3\\envs\\pyagent\\python.exe -m commands.viewer_robot_eye_in_hand

所有需要修改的参数均集中在下方“用户配置区”，无需在命令行传参数。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

import numpy as np

from robot.kaanh_backend import KaanhRobotBackend
from robot.robot_state import RobotState
from visualization.viewer_robot import (
    SceneCoordinateFrame,
    SceneMarker,
    ViewerRobot,
    ViewerRobotConfig,
    ViewerRobotState,
)


# ============================================================================
# 用户配置区
# ============================================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 机器人连接信息与手眼外参。若需要验证 cam2，请把第二个路径改为
# ``config/calibration/eye_in_hand_cam2.json``。
ROBOT_CONFIG_PATH = PROJECT_ROOT / "config" / "robot" / "robot_config.json"
HAND_EYE_CONFIG_PATH = PROJECT_ROOT / "config" / "calibration" / "eye_in_hand_cam1.json"

# 本指令只读控制器状态，因此固定使用监控端口，而非 5999 控制端口。
MONITOR_PORT = 5888

# 轮询与终端日志频率。可按网络情况调大轮询间隔。
POLL_INTERVAL_S = 0.02
PRINT_INTERVAL_S = 1.0

# 三维窗口的基座坐标系视野范围，单位为米。
VIEWER_CONFIG = ViewerRobotConfig(
    title="Eye-in-hand Camera / Robot Viewer | 5888 monitor only",
    x_limits_m=(-1.0, 1.5),
    y_limits_m=(-1.0, 1.0),
    z_limits_m=(0.0, 1.5),
)

# TCP 与相机坐标轴的可视化长度，单位为米。这些数值只影响显示效果。
TCP_AXIS_LENGTH_M = 0.10
CAMERA_AXIS_LENGTH_M = 0.12


@dataclass(frozen=True, slots=True)
class HandEyeExtrinsic:
    """手眼标定文件中 ``T_tcp_camera`` 的标准化表示。"""

    translation_m: tuple[float, float, float]
    quaternion_xyzw: tuple[float, float, float, float]


def _read_json(path: Path) -> dict[str, object]:
    """读取配置文件，并在格式错误时给出清晰路径。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise RuntimeError(f"找不到配置文件：{path}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"配置文件不是有效 JSON：{path}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"配置文件根节点必须是对象：{path}")
    return data


def _load_hand_eye_extrinsic(path: Path) -> HandEyeExtrinsic:
    """加载 eye_in_hand_cam1.json 中的末端到相机变换。"""
    document = _read_json(path)
    raw_pose = document.get("camera_pose_in_end")
    if not isinstance(raw_pose, dict):
        raise RuntimeError(f"{path} 必须包含 camera_pose_in_end 对象")
    try:
        translation = tuple(float(value) for value in raw_pose["translation_m"])
        quaternion = tuple(float(value) for value in raw_pose["quaternion_xyzw"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"{path} 的 translation_m 或 quaternion_xyzw 格式错误") from error
    if len(translation) != 3 or len(quaternion) != 4:
        raise RuntimeError("手眼外参必须包含 3 个平移值和 4 个 xyzw 四元数值")
    if not np.isfinite(translation).all() or not np.isfinite(quaternion).all() or np.linalg.norm(quaternion) < 1e-9:
        raise RuntimeError("手眼外参包含非有限值或零四元数")
    return HandEyeExtrinsic(translation, _normalize_quaternion(quaternion))


def _normalize_quaternion(quaternion_xyzw: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """归一化 xyzw 四元数，避免控制器或配置的小数误差累积。"""
    values = np.asarray(quaternion_xyzw, dtype=float)
    norm = np.linalg.norm(values)
    if norm < 1e-9:
        raise ValueError("四元数不能为零")
    return tuple(float(value / norm) for value in values)


def _quaternion_to_rotation(quaternion_xyzw: tuple[float, float, float, float]) -> np.ndarray:
    """把 xyzw 四元数转换为 3×3 旋转矩阵。"""
    x, y, z, w = _normalize_quaternion(quaternion_xyzw)
    return np.array(
        (
            (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
        ),
        dtype=float,
    )


def _quaternion_multiply(
    left_xyzw: tuple[float, float, float, float],
    right_xyzw: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """计算旋转组合 ``R_left × R_right`` 对应的 xyzw 四元数。"""
    x1, y1, z1, w1 = _normalize_quaternion(left_xyzw)
    x2, y2, z2, w2 = _normalize_quaternion(right_xyzw)
    return _normalize_quaternion((
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ))


def _camera_pose_in_base(
    tcp_pq: list[float],
    hand_eye: HandEyeExtrinsic,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """由实时 TCP 与手眼外参计算相机在基座系中的位置和旋转。

    控制器当前 ``pq`` 约定为 ``[x, y, z, qx, qy, qz, qw]``，其位置单位是
    毫米；手眼外参平移单位是米。因此在运算前将 TCP 平移除以 1000。
    """
    if len(tcp_pq) != 7:
        raise ValueError("TCP pq 必须包含 7 个值")
    tcp_position_base_m = np.asarray(tcp_pq[:3], dtype=float) / 1000.0
    tcp_quaternion_base = _normalize_quaternion(tuple(float(value) for value in tcp_pq[3:]))

    # p_base_camera = p_base_tcp + R_base_tcp × p_tcp_camera
    camera_position_base_m = tcp_position_base_m + _quaternion_to_rotation(tcp_quaternion_base) @ np.asarray(hand_eye.translation_m)
    # R_base_camera = R_base_tcp × R_tcp_camera
    camera_quaternion_base = _quaternion_multiply(tcp_quaternion_base, hand_eye.quaternion_xyzw)
    return tuple(float(value) for value in camera_position_base_m), camera_quaternion_base


def _build_viewer_state(state: RobotState, hand_eye: HandEyeExtrinsic) -> ViewerRobotState:
    """将 5888 原始机器人状态与手眼外参适配为 Viewer 的通用输入。"""
    joints_rad = None
    if state.has_joints:
        # 控制器返回关节角为度，Robotics Toolbox 使用弧度。
        joints_rad = tuple(float(value) for value in np.deg2rad(state.joints_deg))

    markers: tuple[SceneMarker, ...] = ()
    coordinate_frames: tuple[SceneCoordinateFrame, ...] = ()
    if state.has_tcp_pq:
        tcp_pq = state.tcp_pq
        assert tcp_pq is not None  # 仅帮助类型检查；has_tcp_pq 已经保证不为 None。
        tcp_position_m = tuple(float(value) / 1000.0 for value in tcp_pq[:3])
        tcp_quaternion = _normalize_quaternion(tuple(float(value) for value in tcp_pq[3:]))
        camera_position_m, camera_quaternion = _camera_pose_in_base(tcp_pq, hand_eye)

        # TCP 和相机都作为通用 SceneCoordinateFrame 传入；Viewer 并不知道
        # 它们来自 5888 或手眼外参文件。
        coordinate_frames = (
            SceneCoordinateFrame("tcp", tcp_position_m, tcp_quaternion, TCP_AXIS_LENGTH_M),
            SceneCoordinateFrame("eye_in_hand_camera", camera_position_m, camera_quaternion, CAMERA_AXIS_LENGTH_M),
        )
        markers = (
            SceneMarker("tcp_origin", tcp_position_m, "TCP", "#f1c40f", 55.0),
            SceneMarker("camera_origin", camera_position_m, "camera", "#3498db", 55.0),
        )

    status = state.robot_status or "unknown"
    error = state.error_code if state.error_code is not None else 0
    return ViewerRobotState(
        joints_rad=joints_rad,
        markers=markers,
        coordinate_frames=coordinate_frames,
        status_text=f"5888 monitor | status={status} | error={error}",
    )


def main() -> None:
    """启动只读监控，实时显示机器人、TCP 与眼在手相机。"""
    robot: KaanhRobotBackend | None = None
    viewer: ViewerRobot | None = None
    last_print_at = 0.0

    try:
        robot_config = _read_json(ROBOT_CONFIG_PATH)
        hand_eye = _load_hand_eye_extrinsic(HAND_EYE_CONFIG_PATH)
        ip = str(robot_config["robot_ip"])
        user = str(robot_config.get("user", "Engineer"))
        password = str(robot_config.get("password", ""))
        timeout_s = float(robot_config.get("timeout_s", 5.0))
        udp_port = int(robot_config.get("udp_port", 9998))

        # 虽然 KaanhRobotBackend 构造时需要 udp_port，但本指令不启动 follower，
        # 因而不会创建或使用 UDP 数据通道。
        robot = KaanhRobotBackend(ip, port=MONITOR_PORT, udp_port=udp_port, timeout=timeout_s)
        print(f"[5888监控] 正在连接 {ip}:{MONITOR_PORT} ...")
        if not robot.connect():
            raise RuntimeError("无法连接机器人 5888 监控端口")
        robot.login(user, password)
        print(f"[标定] 已加载手眼外参：{HAND_EYE_CONFIG_PATH}")
        print("[安全] 本指令不会使能或移动机器人；请使用示教器手动移动机器人。")

        viewer = ViewerRobot(VIEWER_CONFIG)
        while True:
            # 只读状态。若没有完整 tcp_pq，本帧仅更新可用的机器人关节；旧的
            # TCP/相机图元会被移除，避免误把上一帧相机位置当成实时位置。
            state = robot.get_robot_state()
            if not viewer.update(_build_viewer_state(state, hand_eye)):
                break

            now = time.monotonic()
            if now - last_print_at >= PRINT_INTERVAL_S:
                if state.has_tcp_pq:
                    camera_position_m, _ = _camera_pose_in_base(state.tcp_pq or [], hand_eye)
                    print(
                        "[状态] "
                        f"TCP(mm)=({state.tcp_pq[0]:.1f}, {state.tcp_pq[1]:.1f}, {state.tcp_pq[2]:.1f}) | "
                        f"相机基座坐标(m)=({camera_position_m[0]:.3f}, {camera_position_m[1]:.3f}, {camera_position_m[2]:.3f})"
                    )
                else:
                    print("[状态] 本帧没有有效 TCP pq，未显示眼在手相机坐标系")
                last_print_at = now
            time.sleep(POLL_INTERVAL_S)

    except KeyboardInterrupt:
        print("\n[结束] 收到 Ctrl+C。")
    finally:
        if viewer is not None:
            viewer.close()
        if robot is not None:
            robot.close()
        print("[结束] 已关闭机器人监控和三维窗口。")


if __name__ == "__main__":
    main()
