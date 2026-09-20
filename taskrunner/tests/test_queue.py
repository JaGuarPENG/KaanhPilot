from __future__ import annotations

import unittest

from taskrunner.errors import QueueClosedError, QueueFullError
from taskrunner.queue import BoundedOrderQueue


class BoundedOrderQueueTests(unittest.TestCase):
    def test_fifo_capacity_and_removal(self) -> None:
        queue = BoundedOrderQueue(capacity=2)
        queue.put("order-1")
        queue.put("order-2")

        with self.assertRaises(QueueFullError):
            queue.put("order-3")

        self.assertTrue(queue.remove("order-1"))
        self.assertFalse(queue.remove("missing"))
        queue.put("order-3")
        self.assertEqual(queue.get(), "order-2")
        self.assertEqual(queue.get(), "order-3")

    def test_close_wakes_and_rejects_access(self) -> None:
        queue = BoundedOrderQueue()
        queue.close()

        with self.assertRaises(QueueClosedError):
            queue.put("order-1")
        with self.assertRaises(QueueClosedError):
            queue.get()


if __name__ == "__main__":
    unittest.main()
