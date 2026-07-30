"""G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from commands.yolo_command_support import add_common_arguments, build_camera, build_session
from planner.follower_bridge import FollowerBridge, FollowerBridgeConfig
from robot.kaanh_backend import KaanhRobotBackend
from visualization.follower_integration_viewer import FollowerIntegrationViewer


def _load_config(path: Path) -> tuple[FollowerBridgeConfig, dict[str, object]]:
    """加载 JSON 配置并构造经过严格校验的桥接器配置。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    extrinsic = data["sim_base_from_camera"]
    config = FollowerBridgeConfig(
        tuple(extrinsic["translation_m"]), tuple(extrinsic["quaternion_xyzw"]),
        float(data.get("frequency_hz", 8.0)),
        float(data.get("approach_distance_m", 0.1)), float(data.get("hold_after_s", 0.5)), float(data.get("stop_after_s", 2.0)),
    )
    return config, data


def main() -> None:
    parser = argparse.ArgumentParser(description="运行真实 G305 到虚拟 follower 的连续测试")
    add_common_arguments(parser)
    parser.add_argument("--config", required=True, type=Path, help="包含外参和控制器地址的 JSON 配置")
    parser.add_argument("--no-viewer", action="store_true", help="不创建仿真调试窗口")
    args = parser.parse_args()
    config, raw_config = _load_config(args.config)
    robot = KaanhRobotBackend(str(raw_config["robot_ip"]), int(raw_config.get("control_port", 5999)), int(raw_config.get("udp_port", 9998)), timeout=float(raw_config.get("timeout_s", 3.0)))
    camera = viewer = bridge = None
    try:
        if not robot.connect():
            raise RuntimeError("无法连接虚拟控制器")
        robot.login(str(raw_config.get("user", "Engineer")), str(raw_config.get("password", "")))
        robot.manual_enable()
        camera = build_camera(args)
        camera.start()
        time.sleep(args.warmup_seconds)
        session = build_session(args)
        viewer = None if args.no_viewer else FollowerIntegrationViewer(config)
        bridge = FollowerBridge(robot, config)
        bridge.start()
        last_frame_id = 0
        while True:
            observation = camera.get_latest_observation()
            if observation is not None and observation.frame_id != last_frame_id:
                last_frame_id = observation.frame_id
                bridge.submit_perception(session.process(observation))
            if viewer is not None:
                if not viewer.update(bridge.latest_joints_rad, bridge.display_state):
                    break
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        if bridge is not None:
            bridge.stop()
        if viewer is not None:
            viewer.close()
        if camera is not None:
            camera.close()
        robot.close()


if __name__ == "__main__":
    main()
