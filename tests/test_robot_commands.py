import math

import pytest

from commands.robot_commands import RobotCommandExecutor
from robot.robot_state import ModelState, RobotState


class FakeRobot:
    """为指令测试提供状态，并记录最终下发的直线运动目标。"""

    def __init__(self, state: RobotState) -> None:
        self.state = state
        self.movel_calls = []

    def get_robot_state(self) -> RobotState:
        return self.state

    def movel_model(self, model_id: int, target_pe: list[float]):
        self.movel_calls.append((model_id, target_pe))
        return "ok"


def test_move_arm_by_tool_offset_moves_selected_arm_in_its_tool_frame():
    # 臂 2 的末端绕基坐标系 Z 轴旋转 90° 后，末端 +X 对应基坐标系 +Y。
    half_sqrt = math.sqrt(0.5)
    state = RobotState(
        models=[
            ModelState(model_index=0),
            ModelState(
                model_index=1,
                tcp_pq=[100.0, 200.0, 300.0, 0.0, 0.0, half_sqrt, half_sqrt],
                tcp_pe=[100.0, 200.0, 300.0, 10.0, 20.0, 30.0],
            ),
        ]
    )
    robot = FakeRobot(state)
    executor = RobotCommandExecutor(robot)

    result = executor.move_arm_by_tool_offset(1, [10.0, 0.0, 0.0])

    assert result == "ok"
    assert len(robot.movel_calls) == 1
    model_id, target_pe = robot.movel_calls[0]
    assert model_id == 1
    assert target_pe == pytest.approx(
        [100.0, 210.0, 300.0, 10.0, 20.0, 30.0]
    )


@pytest.mark.parametrize("model_id", [-1, 2, True])
def test_move_arm_by_tool_offset_rejects_non_arm_model(model_id):
    executor = RobotCommandExecutor(FakeRobot(RobotState()))

    with pytest.raises(ValueError, match="model_id"):
        executor.move_arm_by_tool_offset(model_id, [10.0, 0.0, 0.0])


@pytest.mark.parametrize(
    "offset",
    ([1.0, 2.0], [1.0, 2.0, 3.0, 4.0], [1.0, math.nan, 3.0], "123"),
)
def test_move_arm_by_tool_offset_rejects_invalid_offset(offset):
    executor = RobotCommandExecutor(FakeRobot(RobotState()))

    with pytest.raises(ValueError, match="offset_mm"):
        executor.move_arm_by_tool_offset(0, offset)


def test_move_arm_by_tool_offset_requires_selected_arm_tcp_pose():
    state = RobotState(models=[ModelState(model_index=0)])
    executor = RobotCommandExecutor(FakeRobot(state))

    with pytest.raises(RuntimeError, match="完整 TCP 位姿"):
        executor.move_arm_by_tool_offset(0, [10.0, 0.0, 0.0])
