"""读取已保存的控制器 get 报文，验证多模型 RobotState 解析结果。

默认读取项目根目录的 ``2.5.10_multi.txt``：

    python commands/verify_robot_state.py

也可传入其他保存报文：

    python commands/verify_robot_state.py 2.5.10_multi_offset.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from robot.robot_state import ModelState, RobotState, parse_robot_state


MODEL_NAMES = {
    0: "七轴模型 1",
    1: "七轴模型 2",
    2: "腰部（4 电机）",
    3: "外部轴模型 1（头部水平）",
    4: "外部轴模型 2（头部俯仰）",
}


def load_saved_packet(path: Path) -> dict[str, Any]:
    """读取保存的 ``RETURN -- {...}`` 或纯 JSON 控制器报文。"""
    payload = path.read_text(encoding="utf-8-sig").strip()
    if payload.startswith("RETURN --"):
        payload = payload.removeprefix("RETURN --").strip()

    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("报文最外层必须是 JSON 对象")
    return decoded


def format_vector(value: list[float] | None) -> str:
    if value is None:
        return "无"
    return "[" + ", ".join(f"{item:.6g}" for item in value) + "]"


def print_model(model: ModelState) -> None:
    name = MODEL_NAMES.get(model.model_index, f"模型 {model.model_index}")
    print(f"\n{name}（索引 {model.model_index}）")
    print(f"  目标关节角: {format_vector(model.joints_deg)}°")
    print(f"  实际关节角: {format_vector(model.actual_joints_deg)}°")
    print(f"  实际速度:   {format_vector(model.actual_vel_deg)}")
    print(f"  实际力矩:   {format_vector(model.actual_torque)}")
    print(f"  驱动错误码: {model.driver_error_codes}")

    if model.has_tcp_pq:
        print(f"  TCP PE:     {format_vector(model.tcp_pe)}")
        print(f"  TCP PQ:     {format_vector(model.tcp_pq)}")
        print(f"  轴角 PE:    {format_vector(model.axis_pe)}")
        print(f"  轴角 PQ:    {format_vector(model.axis_pq)}")
    else:
        print("  TCP/轴角:   不适用（腰部或外部轴模型）")


def print_state(state: RobotState) -> None:
    print("解析成功")
    print(f"模型数: {len(state.models)}")
    print(f"控制器状态: {state.robot_status}；运动状态: {state.robot_motion}；moving={state.moving}")
    print(f"已使能: {state.activated}；错误码: {state.error_code}；存在错误: {state.has_error}")

    for model in state.models:
        print_model(model)

    print("\n完整调用方法")
    print("  # 1. 读取所有模型、按控制器顺序平铺的目标关节角。")
    print("  state = robot.get_robot_state()")
    print("  all_deg = state.joints_deg")
    print(f"  # 实际结果（{len(state.joints_deg or [])} 项）: {format_vector(state.joints_deg)}°")
    print()
    print("  # 2. 按模型索引读取单个模型。Python 中不能写 state.model.0。")
    print("  model1 = state.get_model(0)   # 或 state.models[0]，七轴模型 1")
    print("  model1_deg = model1.joints_deg")
    first_model = state.get_model(0)
    print(f"  # 实际结果: {format_vector(first_model.joints_deg if first_model else None)}°")
    print()
    print("  # 3. 两个七轴模型才具有业务 TCP 和轴角；腰部、头部只读取关节。")
    print("  arm2_tcp_pq = state.get_model(1).tcp_pq")
    print("  waist_deg = state.get_model(2).joints_deg")
    print(f"  # 七轴模型 2 TCP PQ: {format_vector(state.get_model(1).tcp_pq if state.get_model(1) else None)}")
    print(f"  # 腰部关节角: {format_vector(state.get_model(2).joints_deg if state.get_model(2) else None)}°")
    print()
    print("  # tcp_pq 仍默认对应七轴模型 1（模型 0），不代表所有模型。")
    print(f"  state.tcp_pq: {format_vector(state.tcp_pq)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证保存的 KAANH get 报文解析结果")
    parser.add_argument(
        "packet",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "2.5.10_multi_offset.txt",
        help="保存的 get 报文路径，默认是 2.5.10_multi_offset.txt",
    )
    args = parser.parse_args()
    packet_path = args.packet if args.packet.is_absolute() else PROJECT_ROOT / args.packet

    try:
        state = parse_robot_state(load_saved_packet(packet_path))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"读取或解析报文失败：{error}", file=sys.stderr)
        return 1

    print(f"报文文件: {packet_path}")
    print_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
