from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unittest

from taskrunner.errors import FatalExecutionError
from taskrunner.orders import HardwareBeverageOrderActions, TestRecognitionOrders
from taskrunner.taskrunner_contracts import (
    PauseReason,
    RobotTaskType,
    TaskStatus,
)


class FakePickWorkflow:
    def __init__(self, result=0) -> None:
        self.result = result
        self.calls: list[tuple[int, str]] = []

    def execute(self, model_id: int, target_id: str):
        self.calls.append((model_id, target_id))
        return self.result


class FakeRobotExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def move_transport_pose(self) -> None:
        self.calls.append(("transport", None))

    def move_place_pose(self) -> None:
        self.calls.append(("place", None))

    def move_arm_by_tool_offset(self, model_id: int, offset) -> None:
        self.calls.append(("offset", (model_id, list(offset))))

    def move_init_pose(self) -> None:
        self.calls.append(("init", None))


class FakeHandExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def release(self, hand_id: int) -> None:
        self.calls.append(("release", hand_id))


@dataclass
class FakeNavigationResult:
    success: bool
    message: str = ""


class FakeAgv:
    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.calls: list[int] = []

    def navigate_to(self, station_id: int) -> FakeNavigationResult:
        self.calls.append(station_id)
        return FakeNavigationResult(self.success, "navigation failed")


class WorkflowStatus(str, Enum):
    OUT_OF_STOCK = "out_of_stock"
    PAUSED = "paused"


@dataclass
class StructuredWorkflowResult:
    status: WorkflowStatus
    pause_reason: str | None = None
    message: str | None = None


class HardwareBeverageOrderActionsTests(unittest.TestCase):
    def make_actions(self, workflow_result=0, *, agv_success: bool = True):
        workflow = FakePickWorkflow(workflow_result)
        robot = FakeRobotExecutor()
        hand = FakeHandExecutor()
        agv = FakeAgv(agv_success)
        actions = HardwareBeverageOrderActions(
            pick_workflow=workflow,
            robot_executor=robot,
            hand_executor=hand,
            agv=agv,
        )
        return actions, workflow, robot, hand, agv

    def test_fixed_hardware_sequence(self) -> None:
        actions, workflow, robot, hand, agv = self.make_actions()

        for task_type in RobotTaskType:
            result = actions.execute(
                task_type,
                item_id="water",
                target_id="mineral_water",
            )
            self.assertEqual(result.status, TaskStatus.SUCCEEDED)

        self.assertEqual(workflow.calls, [(0, "mineral_water")])
        self.assertEqual(agv.calls, [5, 4])
        self.assertEqual(
            robot.calls,
            [
                ("transport", None),
                ("place", None),
                ("offset", (0, [35.5, 0, 0])),
                ("transport", None),
                ("init", None),
            ],
        )
        self.assertEqual(hand.calls, [("release", 15)])

    def test_structured_out_of_stock_result(self) -> None:
        actions, *_ = self.make_actions(
            StructuredWorkflowResult(
                WorkflowStatus.OUT_OF_STOCK,
                message="first detection missed",
            )
        )
        result = actions.execute(
            RobotTaskType.PICK,
            item_id="water",
            target_id="mineral_water",
        )
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.error_code, "out_of_stock")

    def test_structured_pause_result(self) -> None:
        actions, *_ = self.make_actions(
            StructuredWorkflowResult(
                WorkflowStatus.PAUSED,
                pause_reason=PauseReason.TARGET_UNREACHABLE.value,
            )
        )
        result = actions.execute(
            RobotTaskType.PICK,
            item_id="water",
            target_id="mineral_water",
        )
        self.assertEqual(result.status, TaskStatus.PAUSED)
        self.assertEqual(result.pause_reason, PauseReason.TARGET_UNREACHABLE)

    def test_legacy_failure_is_not_misclassified_as_pause(self) -> None:
        actions, *_ = self.make_actions(1)
        result = actions.execute(
            RobotTaskType.PICK,
            item_id="water",
            target_id="mineral_water",
        )
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.error_code, "pick_failed")

    def test_agv_failure_is_fatal(self) -> None:
        actions, *_ = self.make_actions(agv_success=False)
        with self.assertRaisesRegex(FatalExecutionError, "navigation failed"):
            actions.execute(
                RobotTaskType.TRANSPORT_TO_DROPOFF,
                item_id="water",
                target_id="mineral_water",
            )

    def test_paused_cancel_only_returns_to_initial_pose(self) -> None:
        actions, _, robot, hand, agv = self.make_actions()
        actions.cancel_paused_pick()
        self.assertEqual(robot.calls, [("init", None)])
        self.assertEqual(hand.calls, [])
        self.assertEqual(agv.calls, [])


class TestRecognitionOrdersTests(unittest.TestCase):
    def make_actions(self, workflow_result=0, *, model_id: int = 0):
        workflow = FakePickWorkflow(workflow_result)
        robot = FakeRobotExecutor()
        actions = TestRecognitionOrders(
            pick_workflow=workflow,
            robot_executor=robot,
            model_id=model_id,
            stage_delay_s=0,
        )
        return actions, workflow, robot

    def test_four_stage_test_sequence_without_agv_or_hand(self) -> None:
        actions, workflow, robot = self.make_actions(model_id=1)

        for task_type in RobotTaskType:
            result = actions.execute(
                task_type,
                item_id="water",
                target_id="mineral_water",
            )
            self.assertEqual(result.status, TaskStatus.SUCCEEDED)

        self.assertEqual(workflow.calls, [(1, "mineral_water")])
        self.assertEqual(
            robot.calls,
            [
                ("transport", None),
                ("place", None),
                ("offset", (1, [35.5, 0, 0])),
                ("init", None),
            ],
        )

    def test_pick_results_use_shared_normalization(self) -> None:
        actions, *_ = self.make_actions(
            StructuredWorkflowResult(
                WorkflowStatus.OUT_OF_STOCK,
                message="first detection missed",
            )
        )
        result = actions.execute(
            RobotTaskType.PICK,
            item_id="water",
            target_id="mineral_water",
        )
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.error_code, "out_of_stock")

    def test_cancel_returns_simulated_robot_to_initial_pose(self) -> None:
        actions, _, robot = self.make_actions()
        actions.cancel_paused_pick()
        self.assertEqual(robot.calls, [("init", None)])

    def test_rejects_invalid_model_or_delay(self) -> None:
        with self.assertRaises(ValueError):
            self.make_actions(model_id=2)
        with self.assertRaises(ValueError):
            TestRecognitionOrders(
                pick_workflow=FakePickWorkflow(),
                robot_executor=FakeRobotExecutor(),
                stage_delay_s=-1,
            )


if __name__ == "__main__":
    unittest.main()
