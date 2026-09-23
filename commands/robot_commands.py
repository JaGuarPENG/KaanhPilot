import math
import time
from typing import Sequence

from planner.pose import apply_tool_delta_to_pq, calculate_pq_delta
from planner.motion_utils import StablePoseChecker


class RobotCommandExecutor:
    def __init__(self, robot):
        self.robot = robot

    def move_init_pose(self):
        self.robot.movej([35.851,-70.067,-84.049,-72.301,-20.318,-54.485,-0.76,
                          -31.083,65.342,91.197,66.982,13.656,60.622,-38.768,
                          0,10,45,-55,
                          0,0])

    def move_transport_pose(self):
        self.robot.movej([35.851,-70.067,-84.049,-72.301,-20.318,-54.485,-0.76,
                          -31.083,65.342,91.197,66.982,13.656,60.622,-38.768,
                          0,-21.74,79.898,-54.088,
                          0,0])
        
    def move_place_pose(self):
        self.robot.movej([22.264,-63.443,-84.049,-67.442,4.018,-54.158,-28.223,
                          -31.083,65.342,91.197,66.982,13.656,60.622,-38.768,
                          -15,10,45,-55,
                          0,0])

    def move_to_arm1_zero(self):
        self.robot.movej_model(0,[0,0,0,0,0,0,0])

    def movel_both_arms(self):
        self.robot.movel([-377.125,255.332,240.521,2.071931,3.182989,266.245088],[378.117,294.847,282.763,358.535489,-27.296138,266.325440])

    def movel_one_arm(self):
        self.robot.movel_model(0,[-410.964,187.263,266.909,180,0,90])

    def move_arm_by_tool_offset(
        self,
        model_id: int,
        offset_mm: Sequence[float],
    ):
        """让指定机械臂沿当前末端坐标系平移指定的三维偏置。

        参数：
            model_id：机械臂模型编号，0 表示臂 1，1 表示臂 2。
            offset_mm：末端（工具）坐标系下的 ``[x, y, z]`` 平移量，
                单位为毫米。正负方向由当前末端坐标系的三个坐标轴决定。

        该指令只改变 TCP 的位置，不改变 TCP 的姿态。执行过程如下：
            1. 读取指定机械臂当前的 TCP 位姿；
            2. 将工具坐标系下的三维偏置转换到机器人基坐标系；
            3. 生成基坐标系下的绝对目标位置；
            4. 保留当前 TCP 欧拉角，并通过直线运动移动指定机械臂。

        返回值与 ``KaanhRobotBackend.movel_model`` 一致。
        """
        # 当前控制器只有模型 0 和模型 1 是具有 TCP 的机械臂。提前校验
        # model_id，可以避免把腰部或头部模型误当作机械臂进行笛卡尔运动。
        if isinstance(model_id, bool) or model_id not in (0, 1):
            raise ValueError("model_id 只能是 0（臂1）或 1（臂2）")

        # 将任意数值序列统一转换成浮点列表，并确保它确实是三维有限偏置。
        # 禁止字符串是为了避免将类似 "100" 错误拆成三个字符处理。
        if isinstance(offset_mm, (str, bytes)):
            raise ValueError("offset_mm 必须是包含 x、y、z 的三维数值序列")
        try:
            offset = [float(value) for value in offset_mm]
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(
                "offset_mm 必须是包含 x、y、z 的三维数值序列"
            ) from error
        if len(offset) != 3:
            raise ValueError("offset_mm 必须包含 x、y、z 三个数值")
        if not all(math.isfinite(value) for value in offset):
            raise ValueError("offset_mm 不能包含 NaN 或无穷大")

        state = self.robot.get_robot_state()
        model_state = state.get_model(model_id)
        if (
            model_state is None
            or not model_state.has_tcp_pq
            or not model_state.has_tcp_pe
        ):
            raise RuntimeError(f"无法获取机械臂 {model_id} 当前的完整 TCP 位姿")

        # model_state.tcp_pq 的格式为
        # [x, y, z, qx, qy, qz, qw]，其中位置单位为毫米。
        # 单位四元数表示相对旋转为零，因此计算后目标姿态保持不变；这里
        # 主要利用当前四元数把 offset 从末端坐标系旋转到基坐标系。
        target_pq = apply_tool_delta_to_pq(
            model_state.tcp_pq,
            delta_position_tool=offset,
            delta_quaternion_tool=(0.0, 0.0, 0.0, 1.0),
            rotation_frame="local",
        )

        # movel_model 接收 PE：[x, y, z, a, b, c]。前三项使用刚计算出的
        # 绝对目标位置，后三项直接沿用控制器返回的当前 TCP 欧拉角，确保
        # 本指令仅平移末端而不旋转末端。
        target_pe = [*target_pq[:3], *model_state.tcp_pe[3:]]
        print(f"[Follow] 机械臂 {model_id} 沿末端坐标系平移 {offset} mm，目标 PE: {target_pe}")
        return self.robot.movel_model(model_id, target_pe)

    def move_to_offset_destination(self, offset_pq=[15,0,0,0.0, 0.13052619222005157, 0.0, 0.9914448613738104]):
        """给定相对偏移 PQ 测试 follower 到达目标点。
        offset_pq: 工具坐标系下的增量位置和旋转四元数，前3个元素为位置增量，后4个元素为旋转四元数。
        """
        if not self.robot.start_follower():
            self.robot.follower_udp.close()
            return

        checker = StablePoseChecker(required_cycles=3)

        delta_position_tool = [offset_pq[0], offset_pq[1], offset_pq[2]] # 工具坐标系下的增量位置，单位为 mm
        delta_quaternion_tool = [offset_pq[3], offset_pq[4], offset_pq[5], offset_pq[6]] #相对偏移四元数

        target_pq = apply_tool_delta_to_pq(
            self.robot.follower_start_pq,
            delta_position_tool=delta_position_tool,
            delta_quaternion_tool=delta_quaternion_tool,
            rotation_frame="local",
        )   

        while True:
            self.robot.send_pose_pq(delta_position_tool + delta_quaternion_tool)

            # 等待一个控制周期，再获取新状态。
            time.sleep(1.0 / 125)
            state = self.robot.get_robot_state()

            if checker.update(
                state,
                target_pq=target_pq,
                position_tolerance_mm=1.0,
                orientation_tolerance_deg=1.0,
            ):
                print(f"[Follow] 到达相对目标位置.")
                self.robot.stop_follower()
                break

    def move_to_world_destination(self, world_target_pq=[-37.35274427775678,-471.4757335161937,529.60253908919,0.522637546825067,-0.4873053503733354,0.5163219398780524,0.47201180551630445]):
        """给定绝对目标 PQ 测试 follower 到达目标点。
        world_target_pq: 基坐标系下的绝对目标 PQ，前3个元素为位置，后4个元素为旋转四元数。
        """
        if not self.robot.start_follower():
            self.robot.follower_udp.close()
            return

        checker = StablePoseChecker(required_cycles=3)

        #将世界坐标系下的target_pq重新转化为tool下的delta
        delta_pq = calculate_pq_delta(self.robot.follower_start_pq, world_target_pq, rotation_frame="local")


        while True:
            self.robot.send_pose_pq(delta_pq)

            # 等待一个控制周期，再获取新状态。
            time.sleep(1.0 / 125)
            state = self.robot.get_robot_state()

            if checker.update(
                state,
                target_pq=world_target_pq,
                position_tolerance_mm=1.0,
                orientation_tolerance_deg=1.0,
            ):
                print(f"[Follow] 到达绝对目标位置.")
                self.robot.stop_follower()
                break
