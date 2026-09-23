"""仅保存等待订单 ID 的线程安全有界 FIFO。

活动订单由 ``TaskRunner`` 单独持有，因此不占用这里的容量。该封装刻意把
FIFO 细节隔离出来，未来替换为优先级队列时无需改变订单状态模型。
"""

from __future__ import annotations

from collections import deque
import threading
import time

from taskrunner.errors import QueueClosedError, QueueFullError


class BoundedOrderQueue:
    """线程安全、可删除元素的等待订单 FIFO。"""

    def __init__(self, capacity: int = 10) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity 必须是正整数")
        self._capacity = capacity
        self._items: deque[str] = deque()
        self._closed = False
        self._condition = threading.Condition()

    @property
    def capacity(self) -> int:
        return self._capacity

    def put(self, order_id: str) -> None:
        """把订单追加到队尾；队列满或关闭时抛出明确异常。"""

        with self._condition:
            if self._closed:
                raise QueueClosedError("订单队列已关闭")
            if len(self._items) >= self._capacity:
                raise QueueFullError(f"订单队列已满，最多等待 {self._capacity} 个订单")
            self._items.append(order_id)
            self._condition.notify()

    def get(self, timeout: float | None = None) -> str:
        """阻塞取得队首订单。

        ``timeout=None`` 表示一直等待；超时使用 ``time.monotonic``，避免
        系统时钟校准导致等待时间异常。关闭队列会唤醒所有等待线程。
        """

        if timeout is not None and timeout < 0:
            raise ValueError("timeout 不能为负数")
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items:
                if self._closed:
                    raise QueueClosedError("订单队列已关闭")
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待订单超时")
                self._condition.wait(remaining)
            if self._closed:
                raise QueueClosedError("订单队列已关闭")
            return self._items.popleft()

    def remove(self, order_id: str) -> bool:
        """删除尚未领取的排队订单，供“排队中取消”使用。"""

        with self._condition:
            try:
                self._items.remove(order_id)
            except ValueError:
                return False
            return True

    def snapshot(self) -> tuple[str, ...]:
        """按当前 FIFO 顺序返回不可变 ID 副本。"""

        with self._condition:
            return tuple(self._items)

    def close(self) -> None:
        """永久关闭队列，并唤醒正在等待订单的 Worker。"""

        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def __len__(self) -> int:
        with self._condition:
            return len(self._items)
