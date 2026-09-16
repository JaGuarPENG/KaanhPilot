"""G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

from __future__ import annotations

import json
from pathlib import Path
import time
from commands.setup import RobotConfig, RobotSetup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"
CAMERA_INTRINSIC_OUTPUT_PATH = DEFAULT_CONFIG_PATH / "camera" / "intri_param_cam0.json"

class GetCameraIntriCommand:
    """G305、YOLO、Perception 与虚拟控制器 follower 的完整测试入口。"""

    def __init__(self):
        self.config_path: Path = DEFAULT_CONFIG_PATH
        self.robot_setup = RobotSetup(DEFAULT_CONFIG_PATH)
        self.robot_config: RobotConfig = self.robot_setup.get_robot_config()
        self.camera = self.robot_setup.setup_camera(0)
        

    
    def get_camera_intri(self) -> None:
        try:
            self.camera.start()
            time.sleep(3)
            print(f"预热完成")
            observation=self.camera.get_latest_observation()
            print(f"初始化完毕，连接到相机{self.camera._device_index}。")
            calibration = self.camera.calibration
            if calibration is None:
                raise RuntimeError("未获取到相机标定参数，无法保存内外参。")

            rgb = calibration.rgb_intrinsics
            dist = calibration.rgb_distortion
            depth_to_rgb = calibration.depth_to_rgb

            # 将深度相机坐标系到 RGB 相机坐标系的外参转换为行优先的 4x4 齐次矩阵：
            # [R00, R01, R02, Tx, R10, R11, R12, Ty, R20, R21, R22, Tz, 0, 0, 0, 1]。
            # depth_to_rgb.translation_m 由相机适配器以米为单位提供；导出 JSON 时，
            # 齐次矩阵中的平移项 Tx、Ty、Tz 必须转换为毫米（mm），旋转项保持无单位。
            rotation = depth_to_rgb.rotation
            translation_mm = depth_to_rgb.translation_m * 1000.0
            rt_matrix = [
                float(rotation[0, 0]), float(rotation[0, 1]), float(rotation[0, 2]), float(translation_mm[0]),
                float(rotation[1, 0]), float(rotation[1, 1]), float(rotation[1, 2]), float(translation_mm[1]),
                float(rotation[2, 0]), float(rotation[2, 1]), float(rotation[2, 2]), float(translation_mm[2]),
                0.0, 0.0, 0.0, 1.0,
            ]

            camera_parameters = {
                "RT_camera": {"RT": rt_matrix},
                "intrinsic_2d_camera": {
                    # OpenCV 标准畸变参数顺序：径向 k1、k2，切向 p1、p2，径向 k3。
                    # SDK 对象字段的原始定义为 k1、k2、k3、p1、p2，因此这里必须重排。
                    "dist_coefficients": [
                        float(dist.k1), float(dist.k2), float(dist.p1), float(dist.p2), float(dist.k3)
                    ],
                    "intrinsic": [float(rgb.fx), float(rgb.fy), float(rgb.cx), float(rgb.cy)],
                },
            }
            CAMERA_INTRINSIC_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with CAMERA_INTRINSIC_OUTPUT_PATH.open("w", encoding="utf-8") as output_file:
                json.dump(camera_parameters, output_file, indent=4, ensure_ascii=False)
                output_file.write("\n")

            print("RGB size:", rgb.width, rgb.height)
            print("intrinsic:", [rgb.fx, rgb.fy, rgb.cx, rgb.cy])
            print("distortion in OpenCV order [k1, k2, p1, p2, k3]:",
                [dist.k1, dist.k2, dist.p1, dist.p2, dist.k3])
            print(f"相机内外参已保存至: {CAMERA_INTRINSIC_OUTPUT_PATH}")

        except KeyboardInterrupt:
            pass
    
        finally:
            self.camera.close()


if __name__ == "__main__":
    command = GetCameraIntriCommand()
    command.get_camera_intri()
