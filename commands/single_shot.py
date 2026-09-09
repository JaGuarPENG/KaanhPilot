"""单次拍照测试入口"""

from __future__ import annotations

from pathlib import Path
import time
from commands.setup import RobotConfig, RobotSetup
from planner.pose import calculate_pq_delta, quaternion_to_rotation
from planner.motion_utils import StablePoseChecker
import aris_dynamic as ad
import numpy as np
from perception.session import TargetPerceptionSession
from perception.target_tracker import SingleTargetTracker

from robot.kaanh_backend import KaanhRobotBackend

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config"

class SingleShotCommand:
    """单次拍照抓取指令"""

    def __init__(self, target):
        self.config_path: Path = DEFAULT_CONFIG_PATH
        self.target = target
        self.robot_setup = RobotSetup(DEFAULT_CONFIG_PATH)
        self.robot_config: RobotConfig = self.robot_setup.get_robot_config()
        self.robot = self.robot_setup.setup_robot(5999)
        self.camera = self.robot_setup.setup_camera(0)
        self.detector = self.robot_setup.setup_detector()
        self.localization = self.robot_setup.setup_localizer()
        self.tracker = self.robot_setup.setup_tracker()
        self.warmup_seconds = self.robot_config.camera_warmup_seconds
        self.session = self.robot_setup.setup_session(self.detector, self.target)
        self.cam_transform = self.robot_setup.setup_camera_transform()
        # 工具坐标系下的三维预抓取偏置（x、y、z），单位为米。
        self.offset = np.array((0.0, 0.0, 0.25), dtype=np.float64)

    def single_shot(self) -> None:
        if not self.robot.connect():
            raise RuntimeError("无法连接虚拟控制器")
        self.robot.login(
            str(self.robot_config.robot.user), 
            str(self.robot_config.robot.password))
        self.robot.set_jog_coordinate()
        self.robot.manual_enable()
        self.robot.set_pgm_vel(20)
        print(f"连接到虚拟控制器")
        self.camera.start()
        time.sleep(self.warmup_seconds)
        self.session.warmup(observation=self.camera.get_latest_observation())
        print(f"初始化完毕，连接到相机{self.camera._device_index}。")
        viewer_2d = self.robot_setup.start_2d_viewer()
        viewer_3d = self.robot_setup.start_3d_viewer()
        observation = self.camera.get_latest_observation()
        state = self.robot.get_robot_state()
        if state is not None:
            rbt_pq = state.tcp_pq
        else:
            print("[SingleShot] 未能获取机器人状态")
            return 0
        if observation is not None:
            result = self.session.process(observation)
            transform_result = self.cam_transform.result2base(
                result=result,
                cam_index=1,
                rbt_pq=rbt_pq,
            )
            print(f"[SingleShot] 相机检测到的目标点: {transform_result.target_point_base_m}")
            if viewer_2d is not None:
                viewer_2d.update(observation, result)
            if viewer_3d is not None:
                viewer_3d.update(result)
            # time.sleep(10.0)
        else:
            print("[SingleShot] 未能获取相机观测结果")
            return 0
        # 将工具坐标系中的三维偏置转换到机器人基坐标系。
        offset_in_base = quaternion_to_rotation(tuple(rbt_pq[3:])) @ self.offset
        target = transform_result.target_point_base_m - offset_in_base
        target_pq = [*(value * 1000.0 for value in target), *rbt_pq[3:]]
        trans_result = np.zeros(6, dtype=np.float64)
        ad.s_pq2pe(target_pq,trans_result,'321')
        trans_result_deg = np.concatenate((trans_result[:3], np.degrees(trans_result[3:])))
        print(f"[SingleShot] 目标点偏置后: {trans_result_deg}")
        self.robot.movel_model(0, trans_result_deg.tolist())

        # checker = StablePoseChecker(required_cycles=3)
        # delta_pq = calculate_pq_delta(rbt_pq, target_pq, rotation_frame="local")
        # self.robot.start_follower()
        # while True:
        #     self.robot.send_pose_pq(delta_pq)

        #     # 等待一个控制周期，再获取新状态。
        #     time.sleep(1.0 / 125)
        #     state = self.robot.get_robot_state()

        #     if checker.update(
        #         state,
        #         target_pq=target_pq,
        #         position_tolerance_mm=0.5,
        #         orientation_tolerance_deg=1.0,
        #     ):
        #         print(f"[Follow] 到达绝对目标位置.")
        #         self.robot.stop_follower()
        #         break



class SingleShotExecutor:
    def __init__(
        self,
        robot: KaanhRobotBackend,
        camera,
        detector,
        localizer,
        camera_transform,
        tracker_config,
        offset_m: tuple[float, float, float] = (0.0, -0.04, 0.305),
    ) -> None:
        self._robot = robot
        self._camera = camera
        self._detector = detector
        self._localizer = localizer
        self._camera_transform = camera_transform
        self._tracker_config = tracker_config
        self._offset_m = np.asarray(offset_m, dtype=np.float64)
        if self._offset_m.shape != (3,):
            raise ValueError("offset_m 必须是包含 x、y、z 的三维偏置")

    def _move_to_pregrasp(self, target_point_base_m: np.ndarray, tcp_pq: list[float]) -> None:
        """根据工具坐标系下的三维偏置移动到目标点的预抓取位置。"""
        # 将工具坐标系中的（x、y、z）偏置转换到机器人基坐标系。
        offset_in_base = quaternion_to_rotation(tuple(tcp_pq[3:])) @ self._offset_m
        pregrasp_point_base_m = target_point_base_m - offset_in_base
        # 暂时保持机器人当前的 X 坐标（高度），仅使用偏置后的目标 Y、Z。
        # tcp_pq 中的位置单位为毫米，此处转换为米。
        pregrasp_point_base_m[0] = tcp_pq[0] / 1000.0
        # 预抓取姿态沿用机器人当前的 TCP 姿态。
        pregrasp_pq = [*(value * 1000.0 for value in pregrasp_point_base_m), *tcp_pq[3:]]
        trans_result = np.zeros(6, dtype=np.float64)
        ad.s_pq2pe(pregrasp_pq, trans_result, '321')
        trans_result_deg = np.concatenate((trans_result[:3], np.degrees(trans_result[3:])))
        print(f"[SingleShotExecutor] 移动到预抓取位置: {trans_result_deg}")
        self._robot.movel_model(0, trans_result_deg.tolist())
        # 往前移动准备抓取
        z_offset = [0,0,0.13] #m
        trans_z_offset = quaternion_to_rotation(tuple(tcp_pq[3:])) @ z_offset
        grasp_point_base_m = pregrasp_point_base_m + trans_z_offset
        grasp_result_deg = np.concatenate((grasp_point_base_m * 1000.0, np.degrees(trans_result[3:]))) #mm, deg
        print(f"[SingleShotExecutor] 移动到抓取位置: {grasp_result_deg}")
        self._robot.movel_model(0, grasp_result_deg.tolist())
    def execute(self, target_id: str) -> None:
        # 这里不初始化资源，只执行一次新任务
        observation = self._camera.get_latest_observation()
        tcp_pq = self._robot.get_robot_state().tcp_pq

        session = TargetPerceptionSession(
            detector=self._detector,
            localizer=self._localizer,
            tracker=SingleTargetTracker(self._tracker_config),
            target_id=target_id,
        )
        result = session.process(observation)

        transform_result = self._camera_transform.result2base(
            result=result,
            cam_index=1,
            rbt_pq=tcp_pq,
        )

        self._move_to_pregrasp(
            target_point_base_m=transform_result.target_point_base_m,
            tcp_pq=tcp_pq,
        )

if __name__ == "__main__":
    command = SingleShotCommand(target="oolong_tea")
    command.single_shot()
