# 应该只负责拍照，抓取。因此主要管理灵巧手状态以及机器人移动到拍照位置、预抓取位置、最终抓取位置。其他的运输、放置等动作应该由外部调用。
import time
import numpy as np
from commands.robot_commands import RobotCommandExecutor
from commands.hand_commands import HandCommandExecutor
from commands.snapshot import SnapShotCommand
from robot.kaanh_backend import KaanhRobotBackend
from planner.pose import quaternion_to_rotation

SECOND_PHOTO_OFFSET_TOOL_MM = np.asarray((0.0, 20.0, -400.0))
PREGRASP_OFFSET_TOOL_MM = np.asarray((0.0, 35.0, -285.0))
FINAL_APPROACH_OFFSET_TOOL_MM = np.asarray((0.0, 0.0, 120.0))

class TwoStagePickWorkflow:

    def __init__(self, 
                 robot: KaanhRobotBackend,
                 robot_executor: RobotCommandExecutor,
                 hand_executor: HandCommandExecutor,
                 snapshot_command: SnapShotCommand):
        
        self.robot = robot
        self.robot_executor = robot_executor
        self.hand_executor = hand_executor
        self.snapshot_command = snapshot_command
       
    """
     执行两阶段抓取工作流。
        参数：model_id (int): 机器人模型 ID，只能是 0 或 1
                target_id (str): 目标 ID
        返回：int: 执行结果，0 表示成功，非 0 表示失败
    """
    def execute(self, model_id: int, target_id: str)-> int:
        if self.robot.is_connected is False:
            raise RuntimeError("机器人未连接，请先连接机器人。")
        if model_id not in (0, 1):
           raise ValueError("model_id 只能是 0（臂1）或 1（臂2）")
        if target_id is None or not isinstance(target_id, str):
           raise ValueError("target_id 必须是非空字符串")
        if self.snapshot_command.is_initialized is False:
           raise RuntimeError("SnapShotCommand 未初始化")
        
        self.hand_executor.reinitialize(15)
        self.hand_executor.prepare(15)
        # 拍照定位
        target = self.snapshot_command.capture_once(target_id)
        #
        if target is None:
            print(f"[抓取] 未检测到目标 {target_id}，无法抓取。")
            return 1
        # point = x,y,z in camera frame
        second_photo_point_mm = (
            np.asarray(target.target_point_base_m, dtype=float) * 1000.0
        )
        rbt_pq = self.robot.get_robot_state().get_model(model_id).tcp_pq
        rbt_pe = self.robot.get_robot_state().get_model(model_id).tcp_pe
        second_photo_offset_base_mm = quaternion_to_rotation(rbt_pq[3:7]) @ SECOND_PHOTO_OFFSET_TOOL_MM
        second_photo_target_base_mm = second_photo_point_mm + second_photo_offset_base_mm
        # 保留拍照高度（x）
        second_photo_target_base_mm[0] = rbt_pq[0]
        # 拼装成pe
        second_photo_pe = np.concatenate((second_photo_target_base_mm, rbt_pe[3:6])) #mm, deg
        self.robot.movel_model(model_id, second_photo_pe)
        print(f"[抓取] 已移动到 {target_id} 的拍照位置 {second_photo_target_base_mm}。")
        # 执行第二次拍照定位
        time.sleep(0.5)
        target_second = self.snapshot_command.capture_once(target_id)
        if target_second is None:
            print(f"[抓取] 第二次拍照未检测到目标 {target_id}，无法抓取。")
            return 1
        # 计算预抓取位置
        pregrasp_point_mm = (
            np.asarray(target_second.target_point_base_m, dtype=float) * 1000.0
        )
        rbt_pq_point2 = self.robot.get_robot_state().get_model(model_id).tcp_pq
        rbt_pe_point2 = self.robot.get_robot_state().get_model(model_id).tcp_pe
        pregrasp_offset_base_mm = quaternion_to_rotation(rbt_pq_point2[3:7]) @ PREGRASP_OFFSET_TOOL_MM
        pregrasp_target_base_mm = pregrasp_point_mm + pregrasp_offset_base_mm
        # 保留抓取高度（x）
        pregrasp_target_base_mm[0] = rbt_pq_point2[0]
        pregrasp_pe = np.concatenate((pregrasp_target_base_mm, rbt_pe_point2[3:6])) #mm, deg
        self.robot.movel_model(model_id, pregrasp_pe)
        self.hand_executor.prepare(15)
        print(f"[抓取] 已移动到 {target_id} 的预抓取位置 {pregrasp_target_base_mm}。")
        # 执行抓取
        # 计算最终抓取位置
        time.sleep(0.1)
        self.robot_executor.move_arm_by_tool_offset(model_id, FINAL_APPROACH_OFFSET_TOOL_MM)
        self.hand_executor.grasp(15)
        # 临时动作编排，用来测试
        time.sleep(0.5)
        self.robot_executor.move_arm_by_tool_offset(model_id, [-50, 0,-250])
        # self.robot_executor.move_arm_by_tool_offset(model_id, [0, 0, -250])
        # self.robot_executor.move_arm_by_tool_offset(model_id, [0, 0, 250])
        # # self.robot_executor.move_arm_by_tool_offset(model_id, [55, 0, 0])
        # self.hand_executor.release(15)
        # self.robot_executor.move_arm_by_tool_offset(model_id, [0, 0, -250])
        # self.robot_executor.move_init_pose()
        return 0
         