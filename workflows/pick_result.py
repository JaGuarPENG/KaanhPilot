"""抓取 Workflow 的稳定结果契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PickWorkflowStatus(str, Enum):
    """抓取流程能够正常返回的四种业务结果。"""

    SUCCEEDED = "succeeded"
    OUT_OF_STOCK = "out_of_stock"
    SECOND_DETECTION_FAILED = "second_detection_failed"
    TARGET_UNREACHABLE = "target_unreachable"


@dataclass(frozen=True, slots=True)
class PickWorkflowResult:
    """抓取结果；致命硬件故障不包装在这里，而是直接抛出异常。"""

    status: PickWorkflowStatus
    message: str | None = None
