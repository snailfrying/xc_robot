from __future__ import annotations

from dataclasses import dataclass, field


class XcRebotError(RuntimeError):
    """Base exception for the xc_rebot agent."""


@dataclass
class RobotApiError(XcRebotError):
    message: str
    http_status: int | None = None
    code: int | None = None
    payload: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [self.message]
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        if self.code is not None:
            parts.append(f"code={self.code}")
        return ",".join(parts)


class RobotProtocolError(XcRebotError):
    """Raised when robot payload format is invalid."""


class PlannerError(XcRebotError):
    """Raised when LLM planning fails or returns invalid content."""


class PointResolutionError(XcRebotError):
    """Raised when point resolution is internally inconsistent."""


class ObservationError(XcRebotError):
    """Raised when camera capture or image materialization fails."""


class VisionServiceError(XcRebotError):
    """Raised when structured scene understanding cannot be produced reliably."""


class ActionExecutionError(XcRebotError):
    """Raised when a synchronous action cannot be completed safely."""
