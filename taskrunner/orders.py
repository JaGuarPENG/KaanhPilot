"""饮料订单实体、固定任务链及真机动作适配器。

这里的 ``BeverageOrder``/``RobotTask`` 是 Runner 内部可变对象；外部只能
获得 ``taskrunner_contracts.py`` 中的不可变快照。首版没有第二个任务
FIFO，四个 ``RobotTask`` 只是同一订单内按顺序解锁的状态记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Protocol

from taskrunner.errors import FatalExecutionError
from taskrunner.taskrunner_contracts import (
    OrderSnapshot,
    OrderStatus,
    PauseReason,
    RobotTaskSnapshot,
    RobotTaskType,
    TaskExecutionResult,
    TaskStatus,
)


BEVERAGE_TARGET_IDS = {
    # 对外商品 ID -> YOLO 检测标签。新饮料必须在这里明确登记。
    "water": "mineral_water",
    "cola": "coco_cola",
    "oolong_tea": "oolong_tea",
}


class BeverageOrderActions(Protocol):
    """Runner 所依赖的饮料动作端口。

    单元测试可注入假实现；真机运行使用 ``HardwareBeverageOrderActions``。
    ``execute`` 的普通结果必须返回 ``TaskExecutionResult``，致命硬件错误
    则直接抛出异常。
    """

    def execute(
        self, task_type: RobotTaskType, *, item_id: str, target_id: str
    ) -> TaskExecutionResult: ...

    def cancel_paused_pick(self) -> None: ...


def normalize_pick_result(raw_result: Any) -> TaskExecutionResult:
    """把抓取 Workflow 的不同结果形式转换为 Runner 统一契约。

    结构化 Workflow 通过 ``status``、``pause_reason`` 和 ``message`` 暴露
    结果。整数分支仅用于兼容尚未迁移完成的旧 Workflow。
    """

    if isinstance(raw_result, TaskExecutionResult):
        return raw_result
    if raw_result == 0:
        return TaskExecutionResult.succeeded()
    if isinstance(raw_result, int):
        return TaskExecutionResult.failed(
            "pick_failed",
            "旧版 TwoStagePickWorkflow 未提供可区分的失败原因",
        )

    status = getattr(raw_result, "status", None)
    status_value = getattr(status, "value", status)
    if status_value in ("succeeded", "success"):
        return TaskExecutionResult.succeeded(getattr(raw_result, "message", None))
    if status_value == "out_of_stock":
        return TaskExecutionResult.failed(
            "out_of_stock", getattr(raw_result, "message", None)
        )
    if status_value == "paused":
        raw_reason = getattr(raw_result, "pause_reason", None)
        reason_value = getattr(raw_reason, "value", raw_reason)
        try:
            reason = PauseReason(reason_value)
        except (TypeError, ValueError) as error:
            raise FatalExecutionError(
                f"Workflow 返回未知暂停原因: {reason_value!r}"
            ) from error
        return TaskExecutionResult.paused(
            reason, getattr(raw_result, "message", None)
        )
    raise FatalExecutionError(f"Workflow 返回未知结果: {raw_result!r}")


@dataclass(slots=True)
class RobotTask:
    """订单内部的可变任务状态；不直接暴露给调用方。"""

    task_id: str
    task_type: RobotTaskType
    status: TaskStatus
    pause_reason: PauseReason | None = None
    error_code: str | None = None
    message: str | None = None

    def snapshot(self) -> RobotTaskSnapshot:
        """复制为不可变快照，避免调用方篡改 Runner 内部状态。"""

        return RobotTaskSnapshot(
            task_id=self.task_id,
            task_type=self.task_type,
            status=self.status,
            pause_reason=self.pause_reason,
            error_code=self.error_code,
            message=self.message,
        )


@dataclass(slots=True)
class BeverageOrder:
    """一个饮料订单及其固定四阶段任务链。"""

    order_id: str
    item_id: str
    target_id: str
    status: OrderStatus = OrderStatus.QUEUED
    tasks: list[RobotTask] = field(default_factory=list)
    pause_reason: PauseReason | None = None
    error_code: str | None = None
    message: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_requested: bool = False

    @classmethod
    def create(cls, order_id: str, item_id: str, target_id: str) -> "BeverageOrder":
        """创建任务链；只有抓取任务初始可执行，其余任务均为 BLOCKED。"""

        task_types = (
            RobotTaskType.PICK,
            RobotTaskType.TRANSPORT_TO_DROPOFF,
            RobotTaskType.PLACE,
            RobotTaskType.RETURN_AND_RESET,
        )
        tasks = [
            RobotTask(
                task_id=f"{order_id}:{task_type.value}",
                task_type=task_type,
                status=TaskStatus.QUEUED if index == 0 else TaskStatus.BLOCKED,
            )
            for index, task_type in enumerate(task_types)
        ]
        return cls(order_id=order_id, item_id=item_id, target_id=target_id, tasks=tasks)

    def current_task(self) -> RobotTask | None:
        """返回尚在排队、执行、暂停或取消中的第一个任务。"""

        for task in self.tasks:
            if task.status in {
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
                TaskStatus.PAUSED,
                TaskStatus.CANCELLING,
            }:
                return task
        return None

    def skip_unstarted_tasks(self) -> None:
        """把失败/取消后确定不会执行的后续任务统一标为 SKIPPED。"""

        for task in self.tasks:
            if task.status in {TaskStatus.BLOCKED, TaskStatus.QUEUED}:
                task.status = TaskStatus.SKIPPED

    def snapshot(self) -> OrderSnapshot:
        """生成包含完整任务链的不可变订单快照。"""

        current = self.current_task()
        return OrderSnapshot(
            order_id=self.order_id,
            item_id=self.item_id,
            target_id=self.target_id,
            status=self.status,
            tasks=tuple(task.snapshot() for task in self.tasks),
            current_task_type=None if current is None else current.task_type,
            pause_reason=self.pause_reason,
            error_code=self.error_code,
            message=self.message,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class HardwareBeverageOrderActions:
    """把现有 Workflow、Command 和 AGV Backend 适配为任务动作端口。

    本类只负责编排首版已冻结的动作，不管理队列和状态。所有未被明确转成
    ``TaskExecutionResult`` 的异常都会穿透到 Runner，并触发致命停止。
    """

    def __init__(
        self,
        *,
        pick_workflow: Any,
        robot_executor: Any,
        hand_executor: Any,
        agv: Any,
        model_id: int = 0,
        hand_id: int = 15,
        dropoff_station_id: int = 5,
        pickup_station_id: int = 4,
    ) -> None:
        self._pick_workflow = pick_workflow
        self._robot_executor = robot_executor
        self._hand_executor = hand_executor
        self._agv = agv
        self._model_id = model_id
        self._hand_id = hand_id
        self._dropoff_station_id = dropoff_station_id
        self._pickup_station_id = pickup_station_id

    def execute(
        self, task_type: RobotTaskType, *, item_id: str, target_id: str
    ) -> TaskExecutionResult:
        """执行一个固定阶段，并返回其正常结果。

        ``item_id`` 用于业务追踪；抓取实际使用映射后的 ``target_id``。
        运输、放置、复位任何异常都不得降级成暂停。
        """

        if task_type is RobotTaskType.PICK:
            raw_result = self._pick_workflow.execute(self._model_id, target_id)
            return normalize_pick_result(raw_result)
        if task_type is RobotTaskType.TRANSPORT_TO_DROPOFF:
            self._robot_executor.move_transport_pose()
            self._navigate_to(self._dropoff_station_id)
            return TaskExecutionResult.succeeded()
        if task_type is RobotTaskType.PLACE:
            self._robot_executor.move_place_pose()
            self._robot_executor.move_arm_by_tool_offset(self._model_id, [35.5, 0, 0])
            self._hand_executor.release(self._hand_id)
            self._robot_executor.move_transport_pose()
            return TaskExecutionResult.succeeded()
        if task_type is RobotTaskType.RETURN_AND_RESET:
            self._navigate_to(self._pickup_station_id)
            self._robot_executor.move_init_pose()
            return TaskExecutionResult.succeeded()
        raise ValueError(f"不支持的机器人任务类型: {task_type}")

    def cancel_paused_pick(self) -> None:
        """暂停抓取的唯一首版取消恢复动作：机械臂回约定初始位。"""

        self._robot_executor.move_init_pose()

    def _navigate_to(self, station_id: int) -> None:
        """同步等待 AGV 结果；失败或不确定结果按致命故障处理。"""

        result = self._agv.navigate_to(station_id)
        if getattr(result, "success", None) is not True:
            message = getattr(result, "message", None) or f"AGV 未能到达站点 {station_id}"
            raise FatalExecutionError(message)

    @staticmethod
    def _normalize_pick_result(raw_result: Any) -> TaskExecutionResult:
        """兼容原有测试和调用方；新代码应使用模块级转换函数。"""

        return normalize_pick_result(raw_result)


class TestRecognitionOrders:
    """识别测试环境使用的四阶段订单动作适配器。

    抓取阶段委托给 ``workflows.test_recognition_workflow``；其余阶段只向
    模拟机器人发送示教动作，并用等待模拟 AGV 行驶，不连接真实 AGV 或
    灵巧手。该类实现 ``BeverageOrderActions``，自身不是识别 Workflow。
    """

    # 防止 pytest 因类名以 Test 开头而尝试把业务类当作测试用例收集。
    __test__ = False

    def __init__(
        self,
        *,
        # pick_workflow: Any,
        robot_executor: Any,
        model_id: int = 0,
        stage_delay_s: float = 3.0,
    ) -> None:
        if model_id not in (0, 1):
            raise ValueError("model_id 只能是 0 或 1")
        if stage_delay_s < 0:
            raise ValueError("stage_delay_s 不能为负数")
        # self._pick_workflow = pick_workflow
        self._robot_executor = robot_executor
        self._model_id = model_id
        self._stage_delay_s = stage_delay_s

    def execute(
        self, task_type: RobotTaskType, *, item_id: str, target_id: str
    ) -> TaskExecutionResult:
        """执行一个固定阶段，并返回其正常结果。

        ``item_id`` 用于业务追踪；抓取实际使用映射后的 ``target_id``。
        运输、放置、复位任何异常都不得降级成暂停。
        """

        if task_type is RobotTaskType.PICK:
            # raw_result = self._pick_workflow.execute(self._model_id, target_id)
            # return normalize_pick_result(raw_result)
            return TaskExecutionResult.succeeded("模拟抓取完成")
        if task_type is RobotTaskType.TRANSPORT_TO_DROPOFF:
            self._robot_executor.move_transport_pose()
            time.sleep(self._stage_delay_s)
            return TaskExecutionResult.succeeded("模拟 AGV 已到达放置位置")
        if task_type is RobotTaskType.PLACE:
            self._robot_executor.move_place_pose()
            # self._robot_executor.move_arm_by_tool_offset(self._model_id, [35.5, 0, 0])
            time.sleep(self._stage_delay_s)
            return TaskExecutionResult.succeeded("模拟放置完成（未操作灵巧手）")
        if task_type is RobotTaskType.RETURN_AND_RESET:
            time.sleep(self._stage_delay_s)
            self._robot_executor.move_init_pose()
            return TaskExecutionResult.succeeded("模拟 AGV 返回并完成复位")
        raise ValueError(f"不支持的机器人任务类型: {task_type}")

    def cancel_paused_pick(self) -> None:
        """暂停抓取的唯一首版取消恢复动作：机械臂回约定初始位。"""

        self._robot_executor.move_init_pose()

    @staticmethod
    def _normalize_pick_result(raw_result: Any) -> TaskExecutionResult:
        """兼容原有测试；实际转换由模块级函数统一实现。"""

        return normalize_pick_result(raw_result)
