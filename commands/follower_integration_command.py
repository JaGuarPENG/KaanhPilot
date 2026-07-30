"""G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from commands.yolo_command_support import add_common_arguments, build_camera, build_session
from commands.yolo_camera_command import AsyncResultViewer
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
    parser.add_argument("--no-perception-display", action="store_true", help="不显示 YOLO RGB 叠加和点云调试窗口")
    parser.add_argument("--no-point-cloud", action="store_true", help="保留 YOLO RGB 叠加，但不显示 ROI 过滤点云")
    args = parser.parse_args()
    config, raw_config = _load_config(args.config)
    robot = KaanhRobotBackend(str(raw_config["robot_ip"]), int(raw_config.get("control_port", 5999)), int(raw_config.get("udp_port", 9998)), timeout=float(raw_config.get("timeout_s", 3.0)))
    camera = viewer = perception_viewer = bridge = None
    try:
        if not robot.connect():
            raise RuntimeError("无法连接虚拟控制器")
        robot.login(str(raw_config.get("user", "Engineer")), str(raw_config.get("password", "")))
        robot.manual_enable()
        camera = build_camera(args)
        camera.start()
        time.sleep(args.warmup_seconds)
        # 只有点云窗口开启时才保留 ROI 过滤后的诊断点云，避免无显示场景的内存复制。
        session = build_session(
            args,
            collect_inspection=not args.no_perception_display and not args.no_point_cloud,
        )
        # 复用已有异步显示器：它持有容量为一帧的队列，不会拖慢推理和 follower 下发。
        perception_viewer = None if args.no_perception_display else AsyncResultViewer(
            show_point_cloud=not args.no_point_cloud,
        )
        viewer = None if args.no_viewer else FollowerIntegrationViewer(config)
        bridge = FollowerBridge(robot, config)
        bridge.start()
        last_frame_id = 0
        while True:
            observation = camera.get_latest_observation()
            if observation is not None and observation.frame_id != last_frame_id:
                last_frame_id = observation.frame_id
                result = session.process(observation)
                # 同一 observation/result 同时送往控制桥和可视化，保证标记来自同一帧。
                bridge.submit_perception(result)
                if perception_viewer is not None:
                    perception_viewer.submit(observation, result)
            # 用户在 RGB/点云显示器按 Q 或关闭窗口时，同样结束完整 follower 会话。
            if perception_viewer is not None and perception_viewer.is_closed:
                break
            if viewer is not None:
                if not viewer.update(bridge.latest_joints_rad, bridge.display_state):
                    break
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        if bridge is not None:
            bridge.stop()
        if perception_viewer is not None:
            perception_viewer.close()
        if viewer is not None:
            viewer.close()
        if camera is not None:
            camera.close()
        robot.close()


if __name__ == "__main__":
    main()
