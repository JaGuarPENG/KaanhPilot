import time
from planner.pose import apply_tool_delta_to_pq, calculate_pq_delta
from planner.motion_utils import StablePoseChecker


class RobotCommandExecutor:
    def __init__(self, robot):
        self.robot = robot

    def move_init_pose(self):
        self.robot.movej([0, 0, 150, -60, -90, 0], [100, 200, 100])

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

    def move_to_world_destination(self, world_target_pq=[422.6999999999964, 121.89999999999692, 219.81488474825716, 0.9848077530122085, -1.9882226384013025e-15, 0.17364817766692858, 2.216514249238528e-15]):
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
