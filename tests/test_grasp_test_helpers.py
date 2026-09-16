import unittest

import numpy as np

from commands.hand_commands import GraspCommand
from commands.snapshot import TargetPoint
from grasp_test import _read_arm_tcp, _target_pe_with_tool_offset


class FakeModelState:
    tcp_pq = [100.0, 200.0, 300.0, 0.0, 0.0, 0.0, 1.0]
    tcp_pe = [100.0, 200.0, 300.0, 1.0, 2.0, 3.0]
    has_tcp_pq = True
    has_tcp_pe = True


class FakeRobotState:
    def get_model(self, model_id):
        return FakeModelState() if model_id == 0 else None


class FakeStateRobot:
    def __init__(self):
        self.get_state_calls = 0

    def get_robot_state(self):
        self.get_state_calls += 1
        return FakeRobotState()


class FakeHandRobot:
    def __init__(self):
        self.hand_moves = []

    def hand_move(self, **parameters):
        self.hand_moves.append(parameters)


class GraspTestHelperTests(unittest.TestCase):
    def test_arm_tcp_pq_and_pe_come_from_one_state_snapshot(self):
        robot = FakeStateRobot()

        tcp_pq, tcp_pe = _read_arm_tcp(robot, 0)

        self.assertEqual(robot.get_state_calls, 1)
        np.testing.assert_array_equal(tcp_pq, FakeModelState.tcp_pq)
        np.testing.assert_array_equal(tcp_pe, FakeModelState.tcp_pe)

    def test_target_point_meters_are_converted_to_millimeters_once(self):
        point = TargetPoint(
            target_id="mineral_water",
            frame_id=1,
            capture_timestamp_ms=100,
            target_point_camera_m=(0.1, 0.2, 1.0),
            target_point_base_m=(1.0, 2.0, 3.0),
            detection_confidence=0.9,
            valid_point_count=10,
        )

        target_pe = _target_pe_with_tool_offset(
            point,
            np.asarray(FakeModelState.tcp_pq),
            np.asarray(FakeModelState.tcp_pe),
            np.asarray((10.0, 20.0, 30.0)),
        )

        # X 按当前演示约束保持不变；Y/Z 为目标米转毫米后加工具系偏置。
        np.testing.assert_allclose(target_pe, (100.0, 2020.0, 3030.0, 1.0, 2.0, 3.0))

    def test_grasp_command_uses_the_injected_robot(self):
        robot = FakeHandRobot()

        GraspCommand(robot).grasp()

        self.assertEqual(len(robot.hand_moves), 1)
        self.assertEqual(robot.hand_moves[0]["j2"], 5800)


if __name__ == "__main__":
    unittest.main()
