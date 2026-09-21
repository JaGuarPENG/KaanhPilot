"""
TestRecognitionWorkflow: 测试识别工作流,引入了缺货机制,用以测试taskrunner的整体逻辑
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import time
import numpy as np
from commands.robot_commands import RobotCommandExecutor
from commands.snapshot import SnapShotCommand
from planner.pose import quaternion_to_rotation
from robot.kaanh_backend import KaanhRobotBackend, TargetUnreachableError


SECOND_PHOTO_OFFSET_TOOL_MM = np.asarray((0.0, 20.0, -400.0))
PREGRASP_OFFSET_TOOL_MM = np.asarray((0.0, 35.0, -285.0))
FINAL_APPROACH_OFFSET_TOOL_MM = np.asarray((0.0, 0.0, 120.0))


class PickWorkflowStatus(str, Enum):
    SUCCEEDED = "succeeded"
    OUT_OF_STOCK = "out_of_stock"
    PAUSED = "paused"


class PickPauseReason(str, Enum):
    SECOND_DETECTION_FAILED = "second_detection_failed"
    TARGET_UNREACHABLE = "target_unreachable"


@dataclass(frozen=True, slots=True, eq=False)
class PickWorkflowResult:
    status: PickWorkflowStatus
    pause_reason: PickPauseReason | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.status is PickWorkflowStatus.PAUSED and self.pause_reason is None:
            raise ValueError("暂停结果必须包含 pause_reason")
        if self.status is not PickWorkflowStatus.PAUSED and self.pause_reason is not None:
            raise ValueError("非暂停结果不能包含 pause_reason")


class TestRecognitionWorkflow:
    def __init__(
        self,
        robot: KaanhRobotBackend,
        robot_executor: RobotCommandExecutor,
        snapshot_command: SnapShotCommand,
    ) -> None:
        self.robot = robot
        self.robot_executor = robot_executor
        self.snapshot_command = snapshot_command

    def execute(self, model_id: int, target_id: str) -> PickWorkflowResult:
        """Execute two-stage perception and grasping."""

        if self.robot.is_connected is False:
            raise RuntimeError("机器人未连接，请先连接机器人。")
        if model_id not in (0, 1):
            raise ValueError("model_id 只能是 0（臂1）或 1（臂2）")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("target_id 必须是非空字符串")
        if self.snapshot_command.is_initialized is False:
            raise RuntimeError("SnapShotCommand 未初始化")


        target = self.snapshot_command.capture_once(target_id)
        if target is None:
            message = f"第一次拍照未检测到目标 {target_id}，判定为无库存"
            print(f"[抓取] {message}。")
            return PickWorkflowResult(
                PickWorkflowStatus.OUT_OF_STOCK,
                message=message,
            )

        second_photo_point_mm = np.asarray(
            target.target_point_base_m,
            dtype=float,
        ) * 1000.0
        robot_state = self.robot.get_robot_state().get_model(model_id)
        rbt_pq = robot_state.tcp_pq
        rbt_pe = robot_state.tcp_pe
        second_photo_offset_base_mm = (
            quaternion_to_rotation(rbt_pq[3:7]) @ SECOND_PHOTO_OFFSET_TOOL_MM
        )
        second_photo_target_base_mm = (
            second_photo_point_mm + second_photo_offset_base_mm
        )
        second_photo_target_base_mm[0] = rbt_pq[0]
        second_photo_pe = np.concatenate(
            (second_photo_target_base_mm, rbt_pe[3:6])
        )

        print(
            f"[抓取] 已移动到 {target_id} 的第二次拍照位置 "
            f"{second_photo_target_base_mm}。"
        )

        time.sleep(5.0)
        target_second = self.snapshot_command.capture_once(target_id)
        if target_second is None:
            message = f"第二次拍照未检测到目标 {target_id}，等待人工处理"
            print(f"[抓取] {message}。")
            return PickWorkflowResult(
                PickWorkflowStatus.PAUSED,
                PickPauseReason.SECOND_DETECTION_FAILED,
                message,
            )

        pregrasp_point_mm = np.asarray(
            target_second.target_point_base_m,
            dtype=float,
        ) * 1000.0
        robot_state_point2 = self.robot.get_robot_state().get_model(model_id)
        rbt_pq_point2 = robot_state_point2.tcp_pq
        rbt_pe_point2 = robot_state_point2.tcp_pe
        pregrasp_offset_base_mm = (
            quaternion_to_rotation(rbt_pq_point2[3:7]) @ PREGRASP_OFFSET_TOOL_MM
        )
        pregrasp_target_base_mm = pregrasp_point_mm + pregrasp_offset_base_mm
        pregrasp_target_base_mm[0] = rbt_pq_point2[0]
        pregrasp_pe = np.concatenate((pregrasp_target_base_mm, rbt_pe_point2[3:6]))
        
        
        print(
            f"[抓取] 已移动到 {target_id} 的预抓取位置 "
            f"{pregrasp_target_base_mm}。"
        )

        time.sleep(0.5)
        print(
            "模拟抓取动作完成，抓取成功。"
        )
        return PickWorkflowResult(PickWorkflowStatus.SUCCEEDED)

