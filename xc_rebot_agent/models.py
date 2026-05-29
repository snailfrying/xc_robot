from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib import parse as urllib_parse

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
    def from_api(
        cls,
        payload: dict[str, object] | None,
        *,
        default_content_type: str = "",
    ) -> "CaptureAsset | None":
        if not isinstance(payload, dict):
            return None
        download_url = str(payload.get("download_url", "") or "").strip()
        file_path = str(payload.get("file_path", "") or "").strip()
        content_type = str(payload.get("content_type", "") or "").strip()
        if not content_type:
            content_type = _infer_content_type(
                download_url=download_url,
                file_path=file_path,
                default_content_type=default_content_type,
            )
        return cls(
            content_type=content_type,
            download_url=download_url,
            file_path=file_path,
            inline_data=str(payload.get("inline_data", "") or "").strip(),
        )


@dataclass(frozen=True)
class VisionBoundingBox:
    x: float
    y: float
    w: float
    h: float

    @classmethod
    def from_api(cls, payload: dict[str, object] | None) -> "VisionBoundingBox | None":
        if not isinstance(payload, dict):
            return None
        return cls(
            x=float(payload.get("x", 0.0) or 0.0),
            y=float(payload.get("y", 0.0) or 0.0),
            w=float(payload.get("w", 0.0) or 0.0),
            h=float(payload.get("h", 0.0) or 0.0),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
        }


@dataclass(frozen=True)
class VisionTarget:
    label: str
    confidence: float
    track_id: str
    bbox: VisionBoundingBox | None
    center_depth_m: float | None
    median_depth_m: float | None
    min_depth_m: float | None
    horizontal_offset_norm: float | None
    vertical_offset_norm: float | None
    raw: dict[str, object]

    @classmethod
    def from_api(cls, payload: dict[str, object] | None) -> "VisionTarget | None":
        if not isinstance(payload, dict):
            return None
        return cls(
            label=str(payload.get("label", "") or "").strip(),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            track_id=str(payload.get("track_id", "") or "").strip(),
            bbox=VisionBoundingBox.from_api(payload.get("bbox")),
            center_depth_m=_optional_float(payload.get("center_depth_m")),
            median_depth_m=_optional_float(payload.get("median_depth_m")),
            min_depth_m=_optional_float(payload.get("min_depth_m")),
            horizontal_offset_norm=_optional_float(payload.get("horizontal_offset_norm")),
            vertical_offset_norm=_optional_float(payload.get("vertical_offset_norm")),
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "track_id": self.track_id,
            "bbox": self.bbox.to_dict() if self.bbox is not None else {},
            "center_depth_m": self.center_depth_m,
            "median_depth_m": self.median_depth_m,
            "min_depth_m": self.min_depth_m,
            "horizontal_offset_norm": self.horizontal_offset_norm,
            "vertical_offset_norm": self.vertical_offset_norm,
        }


@dataclass(frozen=True)
class VisionObstacleSummary:
    forward_clearance_m: float | None
    left_clearance_m: float | None
    right_clearance_m: float | None
    rear_clearance_m: float | None
    raw: dict[str, object]

    @classmethod
    def from_api(cls, payload: dict[str, object] | None) -> "VisionObstacleSummary":
        if not isinstance(payload, dict):
            return cls(
                forward_clearance_m=None,
                left_clearance_m=None,
                right_clearance_m=None,
                rear_clearance_m=None,
                raw={},
            )
        return cls(
            forward_clearance_m=_optional_float(payload.get("forward_clearance_m")),
            left_clearance_m=_optional_float(payload.get("left_clearance_m")),
            right_clearance_m=_optional_float(payload.get("right_clearance_m")),
            rear_clearance_m=_optional_float(payload.get("rear_clearance_m")),
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "forward_clearance_m": self.forward_clearance_m,
            "left_clearance_m": self.left_clearance_m,
            "right_clearance_m": self.right_clearance_m,
            "rear_clearance_m": self.rear_clearance_m,
        }


@dataclass(frozen=True)
class VisionSceneFlags:
    safe_to_advance: bool | None
    safe_to_retreat: bool | None
    safe_to_rotate: bool | None
    target_visible: bool | None
    target_centered: bool | None
    depth_reliable: bool | None
    requires_caution: bool | None
    raw: dict[str, object]

    @classmethod
    def from_api(cls, payload: dict[str, object] | None) -> "VisionSceneFlags":
        if not isinstance(payload, dict):
            return cls(
                safe_to_advance=None,
                safe_to_retreat=None,
                safe_to_rotate=None,
                target_visible=None,
                target_centered=None,
                depth_reliable=None,
                requires_caution=None,
                raw={},
            )
        return cls(
            safe_to_advance=_optional_bool(payload.get("safe_to_advance")),
            safe_to_retreat=_optional_bool(payload.get("safe_to_retreat")),
            safe_to_rotate=_optional_bool(payload.get("safe_to_rotate")),
            target_visible=_optional_bool(payload.get("target_visible")),
            target_centered=_optional_bool(payload.get("target_centered")),
            depth_reliable=_optional_bool(payload.get("depth_reliable")),
            requires_caution=_optional_bool(payload.get("requires_caution")),
            raw=dict(payload),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "safe_to_advance": self.safe_to_advance,
            "safe_to_retreat": self.safe_to_retreat,
            "safe_to_rotate": self.safe_to_rotate,
            "target_visible": self.target_visible,
            "target_centered": self.target_centered,
            "depth_reliable": self.depth_reliable,
            "requires_caution": self.requires_caution,
        }


@dataclass(frozen=True)
class VisualSceneUnderstanding:
    summary: str
    confidence: float
    targets: tuple[VisionTarget, ...]
    obstacles: VisionObstacleSummary
    flags: VisionSceneFlags
    recommended_action: str
    recommended_profile_name: str
    risk_reasons: tuple[str, ...]
    raw: dict[str, object]

    @classmethod
    def from_api(cls, payload: dict[str, object] | None) -> "VisualSceneUnderstanding | None":
        if not isinstance(payload, dict):
            return None
        targets_raw = payload.get("targets", [])
        targets = ()
        if isinstance(targets_raw, list):
            parsed_targets = [VisionTarget.from_api(item) for item in targets_raw if isinstance(item, dict)]
            targets = tuple(item for item in parsed_targets if item is not None)
        risk_reasons_raw = payload.get("risk_reasons", [])
        risk_reasons = ()
        if isinstance(risk_reasons_raw, list):
            risk_reasons = tuple(str(item).strip() for item in risk_reasons_raw if str(item).strip())
        return cls(
            summary=str(payload.get("summary", "") or "").strip(),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            targets=targets,
            obstacles=VisionObstacleSummary.from_api(payload.get("obstacles")),
            flags=VisionSceneFlags.from_api(payload.get("scene_flags")),
            recommended_action=str(payload.get("recommended_action", "") or "").strip(),
            recommended_profile_name=str(payload.get("recommended_profile_name", "") or "").strip(),
            risk_reasons=risk_reasons,
            raw=dict(payload),
        )

    @property
    def primary_target(self) -> VisionTarget | None:
        if not self.targets:
            return None
        return max(self.targets, key=lambda item: item.confidence)

    def short_dict(self) -> dict[str, object]:
        primary_target = self.primary_target
        return {
            "summary": self.summary,
            "confidence": self.confidence,
            "target_count": len(self.targets),
            "primary_target_label": primary_target.label if primary_target is not None else "",
            "primary_target_depth_m": primary_target.center_depth_m if primary_target is not None else None,
            "forward_clearance_m": self.obstacles.forward_clearance_m,
            "safe_to_advance": self.flags.safe_to_advance,
            "safe_to_retreat": self.flags.safe_to_retreat,
            "safe_to_rotate": self.flags.safe_to_rotate,
            "target_visible": self.flags.target_visible,
            "target_centered": self.flags.target_centered,
            "recommended_action": self.recommended_action,
            "risk_reasons": list(self.risk_reasons),
        }


@dataclass(frozen=True)
class CaptureObservation:
    image_id: str
    created_at: str
    return_mode: str
    rgb: CaptureAsset | None
    depth: CaptureAsset | None
    rgb_data_url: str
    depth_data_url: str
    scene_understanding: VisualSceneUnderstanding | None
    raw: dict[str, object]

    def short_dict(self) -> dict[str, object]:
        return {
            "image_id": self.image_id,
            "created_at": self.created_at,
            "return_mode": self.return_mode,
            "has_rgb": bool(self.rgb_data_url),
            "has_depth": bool(self.depth_data_url),
            "has_scene_understanding": self.scene_understanding is not None,
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


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_bool(value: object) -> bool | None:
    if value in (None, ""):
        return None
    return bool(value)


def _infer_content_type(*, download_url: str, file_path: str, default_content_type: str) -> str:
    candidate = file_path or download_url
    path = urllib_parse.urlparse(candidate).path.lower() if candidate else ""
    if path.endswith(".png") or path.endswith("/depth"):
        return "image/png"
    if path.endswith(".jpg") or path.endswith(".jpeg") or path.endswith("/rgb"):
        return "image/jpeg"
    return default_content_type
