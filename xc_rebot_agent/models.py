from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .constants import ACTIVE_NAV_STATES, TERMINAL_NAV_STATES


@dataclass(frozen=True)
class PointOfInterest:
    point_id: str
    name: str
    aliases: tuple[str, ...] = ()
    raw: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, object], aliases: tuple[str, ...] = ()) -> "PointOfInterest":
        payload_aliases = payload.get("aliases", [])
        merged_aliases: list[str] = []
        if isinstance(payload_aliases, list):
            merged_aliases.extend(str(alias).strip() for alias in payload_aliases if str(alias).strip())
        merged_aliases.extend(str(alias).strip() for alias in aliases if str(alias).strip())
        return cls(
            point_id=str(payload.get("point_id", "")).strip(),
            name=str(payload.get("name", "")).strip(),
            aliases=tuple(dict.fromkeys(merged_aliases)),
            raw=dict(payload),
        )

    def search_terms(self) -> tuple[str, ...]:
        values = [self.point_id, self.name, *self.aliases]
        return tuple(value.strip() for value in values if value and value.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "point_id": self.point_id,
            "name": self.name,
            "aliases": list(self.aliases),
        }


@dataclass(frozen=True)
class RobotApiEnvelope:
    code: int
    msg: str
    data: dict[str, object]
    raw: dict[str, object]

    @property
    def is_success(self) -> bool:
        return self.code == 0

    def to_trace(self) -> dict[str, object]:
        return {
            "code": self.code,
            "msg": self.msg,
            "data": self.data,
        }


@dataclass(frozen=True)
class RobotStatus:
    robot_state: str
    nav_state: str
    target_point_id: str
    pose: dict[str, object]
    battery: dict[str, object]
    localization: dict[str, object]
    errors: tuple[object, ...]
    raw: dict[str, object]

    @classmethod
    def from_api(cls, payload: dict[str, object]) -> "RobotStatus":
        nav = payload.get("nav", {}) if isinstance(payload.get("nav", {}), dict) else {}
        return cls(
            robot_state=str(payload.get("robot_state", "")).strip(),
            nav_state=str(nav.get("state", "")).strip(),
            target_point_id=str(nav.get("target_point_id", "") or "").strip(),
            pose=dict(payload.get("pose", {})) if isinstance(payload.get("pose", {}), dict) else {},
            battery=dict(payload.get("battery", {})) if isinstance(payload.get("battery", {}), dict) else {},
            localization=dict(payload.get("localization", {})) if isinstance(payload.get("localization", {}), dict) else {},
            errors=tuple(payload.get("errors", [])) if isinstance(payload.get("errors", []), list) else (),
            raw=dict(payload),
        )

    @property
    def localization_valid(self) -> bool:
        return bool(self.localization.get("valid", False))

    @property
    def is_navigation_active(self) -> bool:
        return self.nav_state in ACTIVE_NAV_STATES

    @property
    def is_navigation_terminal(self) -> bool:
        return self.nav_state in TERMINAL_NAV_STATES

    def short_dict(self) -> dict[str, object]:
        return {
            "robot_state": self.robot_state,
            "nav_state": self.nav_state,
            "target_point_id": self.target_point_id,
            "localization_valid": self.localization_valid,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class CaptureAsset:
    content_type: str
    download_url: str
    file_path: str
    inline_data: str

    @classmethod
    def from_api(cls, payload: dict[str, object] | None) -> "CaptureAsset | None":
        if not isinstance(payload, dict):
            return None
        return cls(
            content_type=str(payload.get("content_type", "") or "").strip(),
            download_url=str(payload.get("download_url", "") or "").strip(),
            file_path=str(payload.get("file_path", "") or "").strip(),
            inline_data=str(payload.get("inline_data", "") or "").strip(),
        )


@dataclass(frozen=True)
class CaptureObservation:
    image_id: str
    created_at: str
    return_mode: str
    rgb: CaptureAsset | None
    depth: CaptureAsset | None
    rgb_data_url: str
    depth_data_url: str
    raw: dict[str, object]

    def short_dict(self) -> dict[str, object]:
        return {
            "image_id": self.image_id,
            "created_at": self.created_at,
            "return_mode": self.return_mode,
            "has_rgb": bool(self.rgb_data_url),
            "has_depth": bool(self.depth_data_url),
        }


@dataclass(frozen=True)
class GoalResolution:
    route: str
    reason: str
    confidence: float
    point: PointOfInterest | None = None
    action: dict[str, object] = field(default_factory=dict)
    action_expression: str = ""


@dataclass(frozen=True)
class TaskSubgoal:
    sequence_id: int
    route: str
    goal_text: str
    reason: str
    confidence: float
    planner_profile_hint: str = ""
    point: PointOfInterest | None = None
    action: dict[str, object] = field(default_factory=dict)
    action_expression: str = ""

    def to_trace(self) -> dict[str, object]:
        return {
            "sequence_id": self.sequence_id,
            "route": self.route,
            "goal_text": self.goal_text,
            "reason": self.reason,
            "confidence": self.confidence,
            "planner_profile_hint": self.planner_profile_hint,
            "point_id": self.point.point_id if self.point is not None else "",
            "action": self.action,
            "action_expression": self.action_expression,
        }


@dataclass(frozen=True)
class TaskPlan:
    route: str
    reason: str
    confidence: float
    subgoals: tuple[TaskSubgoal, ...]

    def to_trace(self) -> dict[str, object]:
        return {
            "route": self.route,
            "reason": self.reason,
            "confidence": self.confidence,
            "subgoal_count": len(self.subgoals),
            "subgoals": [subgoal.to_trace() for subgoal in self.subgoals],
        }


@dataclass(frozen=True)
class PlannerDecision:
    response: str
    action: dict[str, object]
    action_expression: str
    reason: str
    confidence: float
    observation_focus: str
    target_hint: str
    subgoal_state: str
    stop: bool
    raw: dict[str, object]

    def to_trace(self) -> dict[str, object]:
        return {
            "response": self.response,
            "action": self.action,
            "action_expression": self.action_expression,
            "reason": self.reason,
            "confidence": self.confidence,
            "observation_focus": self.observation_focus,
            "target_hint": self.target_hint,
            "subgoal_state": self.subgoal_state,
            "stop": self.stop,
        }


@dataclass(frozen=True)
class ExecutionResult:
    action: dict[str, object]
    action_expression: str
    ok: bool
    summary: str
    started_at: str
    finished_at: str
    status_before: dict[str, object]
    status_after: dict[str, object]
    events: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "action_expression": self.action_expression,
            "ok": self.ok,
            "summary": self.summary,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "events": list(self.events),
        }

    @classmethod
    def timestamps(
        cls,
        *,
        action: dict[str, object],
        action_expression: str,
        ok: bool,
        summary: str,
        status_before: dict[str, object],
        status_after: dict[str, object],
        events: list[dict[str, object]],
        started_at: datetime,
        finished_at: datetime,
    ) -> "ExecutionResult":
        return cls(
            action=action,
            action_expression=action_expression,
            ok=ok,
            summary=summary,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(),
            status_before=status_before,
            status_after=status_after,
            events=tuple(events),
        )
