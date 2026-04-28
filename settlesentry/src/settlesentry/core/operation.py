from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import uuid4


@dataclass
class OperationLogContext:
    """
    Lightweight context for one observable operation.

    Useful for API/tool calls, Agent.next() turns, LLM calls, and other meaningful
    operation boundaries. Do not use for small validators or pure helpers.
    """

    operation: str
    operation_id: str = field(default_factory=lambda: uuid4().hex[:12])
    started_at: float = field(default_factory=perf_counter)

    @property
    def duration_ms(self) -> int:
        return int((perf_counter() - self.started_at) * 1000)

    def extra(self, **fields: Any) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation": self.operation,
            **fields,
        }

    def completed_extra(self, **fields: Any) -> dict[str, Any]:
        return self.extra(duration_ms=self.duration_ms, **fields)
