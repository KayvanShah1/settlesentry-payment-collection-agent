from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from time import process_time
from typing import Any, Callable, Optional

from settlesentry.core import OperationLogContext, get_logger

logger = get_logger("TimedRun")


def utc_now_iso() -> str:
    """Return a stable UTC timestamp used for run boundary logs."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_duration(seconds: float) -> str:
    """Format a duration in seconds into a compact human-readable string."""
    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"

    if seconds < 60:
        return f"{seconds:.3f} s"

    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours} h {minutes:02} m {secs:02} s"


@dataclass
class TimedOperation:
    """
    Small timing helper that aligns with OperationLogContext structured extras.
    """

    context: OperationLogContext
    started_at_utc: str = field(default_factory=utc_now_iso)

    @classmethod
    def begin(cls, operation: str) -> TimedOperation:
        return cls(context=OperationLogContext(operation=operation))

    @property
    def duration_seconds(self) -> float:
        return self.context.duration_seconds

    @property
    def duration_human(self) -> str:
        return format_duration(self.duration_seconds)

    def started_extra(self, **fields: Any) -> dict[str, Any]:
        return self.context.started_extra(started_at_utc=self.started_at_utc, **fields)

    def completed_extra(self, **fields: Any) -> dict[str, Any]:
        return self.context.completed_extra(
            started_at_utc=self.started_at_utc,
            ended_at_utc=utc_now_iso(),
            duration_human=self.duration_human,
            **fields,
        )


def timed_run(func: Optional[Callable] = None, *, name: Optional[str] = None):
    """Decorator to time a function and emit structured start/end logs."""

    def decorator(inner_func: Callable):
        @wraps(inner_func)
        def wrapper(*args, **kwargs):
            op_name = name or inner_func.__name__
            run = TimedOperation.begin(op_name)
            cpu_started = process_time()

            logger.info("timed_run_started", extra=run.started_extra())

            try:
                return inner_func(*args, **kwargs)
            finally:
                cpu_seconds = process_time() - cpu_started
                logger.info(
                    "timed_run_completed",
                    extra=run.completed_extra(
                        cpu_duration_ms=int(cpu_seconds * 1000),
                        cpu_duration_human=format_duration(cpu_seconds),
                    ),
                )

        return wrapper

    if func is None:
        return decorator

    return decorator(func)
