"""纯软件动作：只返回执行结果，排队和任务状态仍由 TaskRunner 管理。"""
import math
import threading
import time

from taskrunner.errors import FatalExecutionError
from taskrunner.orders import BEVERAGE_TARGET_IDS
from taskrunner.taskrunner_contracts import PauseReason, RobotTaskType, TaskExecutionResult


class SimulatedActions:
    def __init__(self, delay=1.0):
        if not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError('模拟阶段耗时必须为 0～60 秒')
        self.delay = delay
        self._scenarios = {}
        self._lock = threading.Lock()

    def set_scenario(self, item_id, scenario):
        if item_id not in BEVERAGE_TARGET_IDS:
            raise ValueError('未知 Runner 物品')
        if scenario not in ('available', 'sold-out', 'confirmation-empty', 'paused', 'fatal'):
            raise ValueError('未知模拟场景')
        with self._lock:
            self._scenarios[item_id] = scenario

    def execute(self, task_type, *, item_id, target_id):
        time.sleep(self.delay)
        if task_type is RobotTaskType.PICK:
            with self._lock:
                scenario = self._scenarios.get(item_id, 'available')
            if scenario in ('sold-out', 'confirmation-empty'):
                return TaskExecutionResult.failed('out_of_stock', '模拟识别未发现库存')
            if scenario == 'paused':
                return TaskExecutionResult.paused(PauseReason.SECOND_DETECTION_FAILED, '模拟二次识别失败')
            if scenario == 'fatal':
                raise FatalExecutionError('模拟控制器故障；请重启服务')
        return TaskExecutionResult.succeeded('模拟阶段完成')

    def cancel_paused_pick(self):
        time.sleep(self.delay)
