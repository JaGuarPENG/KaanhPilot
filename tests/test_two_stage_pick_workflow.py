from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from robot.kaanh_backend import RobotCommandError, TargetUnreachableError
from taskrunner.orders import pick_result_to_task_result
from taskrunner.taskrunner_contracts import TaskStatus
from workflows.pick_result import PickWorkflowResult, PickWorkflowStatus
from workflows.two_stage_pick_workflow import (
    TwoStagePickWorkflow,
)


class FakeRobot:
    is_connected = True

    def __init__(self, movel_errors=()) -> None:
        self._movel_errors = deque(movel_errors)
        self.movel_calls = []
        self.model_state = SimpleNamespace(
            tcp_pq=[500.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            tcp_pe=[500.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )

    def get_robot_state(self):
        return SimpleNamespace(get_model=lambda _model_id: self.model_state)

    def movel_model(self, model_id, target_pe):
        self.movel_calls.append((model_id, list(target_pe)))
        if self._movel_errors:
            error = self._movel_errors.popleft()
            if error is not None:
                raise error
        return b'{"ret_code": 0}'


class FakeRobotExecutor:
    def __init__(self, offset_errors=()) -> None:
        self._offset_errors = deque(offset_errors)
        self.offset_calls = []

    def move_arm_by_tool_offset(self, model_id, offset):
        self.offset_calls.append((model_id, list(offset)))
        if self._offset_errors:
            error = self._offset_errors.popleft()
            if error is not None:
                raise error


class FakeHandExecutor:
    def __init__(self) -> None:
        self.calls = []

    def reinitialize(self, hand_id):
        self.calls.append(("reinitialize", hand_id))

    def prepare(self, hand_id):
        self.calls.append(("prepare", hand_id))

    def grasp(self, hand_id):
        self.calls.append(("grasp", hand_id))


class FakeSnapshotCommand:
    is_initialized = True

    def __init__(self, captures) -> None:
        self._captures = deque(captures)
        self.calls = []

    def capture_once(self, target_id, **kwargs):
        self.calls.append((target_id, kwargs))
        return self._captures.popleft()


def target(x=0.1, y=0.2, z=0.3):
    return SimpleNamespace(target_point_base_m=(x, y, z))


def build_workflow(*, captures, movel_errors=(), offset_errors=()):
    robot = FakeRobot(movel_errors)
    robot_executor = FakeRobotExecutor(offset_errors)
    hand = FakeHandExecutor()
    workflow = TwoStagePickWorkflow(
        robot,
        robot_executor,
        hand,
        FakeSnapshotCommand(captures),
    )
    return workflow, robot, robot_executor, hand


def test_first_detection_miss_means_out_of_stock(monkeypatch):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    workflow, robot, _, hand = build_workflow(captures=[None])

    result = workflow.execute(0, "mineral_water")

    assert result.status is PickWorkflowStatus.OUT_OF_STOCK
    assert robot.movel_calls == []
    assert ("grasp", 15) not in hand.calls


def test_second_detection_miss_pauses(monkeypatch):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    workflow, _, _, hand = build_workflow(captures=[target(), None])

    result = workflow.execute(0, "mineral_water")

    assert result.status is PickWorkflowStatus.SECOND_DETECTION_FAILED
    assert result.message == "第二次拍照未找到与第一次拍照一致的目标，等待人工处理"
    assert ("grasp", 15) not in hand.calls


def test_second_capture_matches_against_first_base_point(monkeypatch):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    first = target(0.11, 0.22, 0.33)
    workflow, _, _, _ = build_workflow(captures=[first, target()])

    workflow.execute(0, "mineral_water")

    calls = workflow.snapshot_command.calls
    assert calls[0] == ("mineral_water", {})
    assert calls[1] == (
        "mineral_water",
        {
            "reference_point_base_m": first.target_point_base_m,
            "maximum_match_distance_m": workflow.instance_match_distance_m,
        },
    )


@pytest.mark.parametrize(
    ("movel_errors", "captures", "stage"),
    [
        ([TargetUnreachableError("unreachable")], [target()], "第二次拍照位置"),
        (
            [None, TargetUnreachableError("unreachable")],
            [target(), target()],
            "预抓取位置",
        ),
    ],
)
def test_pregrasp_movel_target_unreachable_pauses(
    monkeypatch,
    movel_errors,
    captures,
    stage,
):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    workflow, _, _, hand = build_workflow(
        captures=captures,
        movel_errors=movel_errors,
    )

    result = workflow.execute(0, "mineral_water")

    assert result.status is PickWorkflowStatus.TARGET_UNREACHABLE
    assert stage in result.message
    assert ("grasp", 15) not in hand.calls


def test_final_approach_target_unreachable_pauses(monkeypatch):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    workflow, _, _, hand = build_workflow(
        captures=[target(), target()],
        offset_errors=[TargetUnreachableError("unreachable")],
    )

    result = workflow.execute(0, "mineral_water")

    assert result.status is PickWorkflowStatus.TARGET_UNREACHABLE
    assert "最终抓取位置" in result.message
    assert ("grasp", 15) not in hand.calls


def test_post_grasp_movement_error_stays_fatal(monkeypatch):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    workflow, _, _, hand = build_workflow(
        captures=[target(), target()],
        offset_errors=[None, RobotCommandError("retreat failed")],
    )

    with pytest.raises(RobotCommandError, match="retreat failed"):
        workflow.execute(0, "mineral_water")
    assert ("grasp", 15) in hand.calls


def test_successful_pick_returns_structured_success(monkeypatch):
    monkeypatch.setattr("workflows.two_stage_pick_workflow.time.sleep", lambda _s: None)
    workflow, robot, robot_executor, hand = build_workflow(
        captures=[target(), target()],
    )

    result = workflow.execute(0, "mineral_water")

    assert result.status is PickWorkflowStatus.SUCCEEDED
    assert len(robot.movel_calls) == 2
    assert len(robot_executor.offset_calls) == 2
    assert ("grasp", 15) in hand.calls


def test_workflow_result_contract_maps_into_taskrunner() -> None:
    paused = pick_result_to_task_result(
        PickWorkflowResult(
            PickWorkflowStatus.TARGET_UNREACHABLE,
            "unreachable",
        )
    )
    out_of_stock = pick_result_to_task_result(
        PickWorkflowResult(
            PickWorkflowStatus.OUT_OF_STOCK,
            message="empty",
        )
    )

    assert paused.status is TaskStatus.PAUSED
    assert paused.error_code == "target_unreachable"
    assert out_of_stock.status is TaskStatus.FAILED
    assert out_of_stock.error_code == "out_of_stock"


def test_structured_results_use_normal_dataclass_equality() -> None:
    succeeded = PickWorkflowResult(PickWorkflowStatus.SUCCEEDED)
    out_of_stock = PickWorkflowResult(PickWorkflowStatus.OUT_OF_STOCK)

    assert succeeded == PickWorkflowResult(PickWorkflowStatus.SUCCEEDED)
    assert succeeded != out_of_stock
