from __future__ import annotations

import io
import itertools
import time
import unittest

from taskrunner.cli import CommandLoop, SimulatedActions
from taskrunner.runner import TaskRunner
from taskrunner.taskrunner_contracts import OrderStatus


class CommandLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        ids = (f"cli-{number}" for number in itertools.count(1))
        self.runner = TaskRunner(
            SimulatedActions(task_delay_s=0.0),
            order_id_factory=lambda: next(ids),
        )
        self.runner.start()
        self.output = io.StringIO()
        self.loop = CommandLoop(self.runner, self.output)

    def tearDown(self) -> None:
        try:
            self.runner.shutdown()
        except Exception:
            pass

    def test_submit_status_queue_and_quit(self) -> None:
        self.assertTrue(self.loop.handle("submit water"))
        self.assertIn('"order_id": "cli-1"', self.output.getvalue())
        self.assertTrue(self.loop.handle("status cli-1"))
        self.assertTrue(self.loop.handle("queue"))

        # The zero-delay simulated chain should finish before quit. If the
        # worker has not cleared its current pointer yet, querying status gives
        # it one scheduling opportunity without adding a CLI-only core hook.
        for _ in range(1000):
            if (
                self.runner.get_order("cli-1").status is OrderStatus.SUCCEEDED
                and self.runner.get_queue().current_order is None
            ):
                break
            time.sleep(0.001)
        self.assertFalse(self.loop.handle("quit"))

    def test_bad_command_is_nonfatal(self) -> None:
        self.assertTrue(self.loop.handle("something unexpected"))
        self.assertIn("未知命令", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
