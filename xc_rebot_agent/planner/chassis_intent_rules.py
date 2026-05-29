from __future__ import annotations

import re
from dataclasses import dataclass

from .action_parser import ActionCall
from .action_parser import action_call_to_payload
from .action_parser import format_action_call
from .action_parser import parse_action_payload


@dataclass(frozen=True)
class ChassisIntentRuleMatch:
    route: str
    planner_profile_hint: str
    action: dict[str, object]
    action_expression: str
    reason: str


SEQUENTIAL_SPLIT_PATTERNS = (
    r"\s*(?:;|；|\n)+\s*",
    r"\s*(?:,?\s*then\b|,?\s*and then\b|after that\b|next\b)\s*",
    r"\s*(?:然后|接着|随后|之后|再去|再到)\s*",
)

STOP_PATTERNS = (
    r"\bemergency stop\b",
    r"\bhalt\b",
    r"\bstop\b",
    r"立刻停止",
    r"紧急停止",
    r"急停",
    r"停车",
    r"停下",
    r"停止",
)

EXPLICIT_MOTION_PATTERNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "move_forward",
        (
            r"\bmove forward\b",
            r"\bgo forward\b",
            r"\bforward\b",
            r"向前",
            r"前进",
            r"往前",
        ),
        "explore_forward",
    ),
    (
        "move_backward",
        (
            r"\bmove backward\b",
            r"\bgo backward\b",
            r"\bbackward\b",
            r"\breverse\b",
            r"向后",
            r"后退",
            r"往后",
        ),
        "explore_backward",
    ),
    (
        "turn_left",
        (
            r"\bturn left\b",
            r"\bleft\b",
            r"左转",
            r"向左",
            r"往左",
        ),
        "explore_left",
    ),
    (
        "turn_right",
        (
            r"\bturn right\b",
            r"\bright\b",
            r"右转",
            r"向右",
            r"往右",
        ),
        "explore_right",
    ),
)

EXPLORATION_INTENT_PATTERNS: tuple[str, ...] = (
    r"\bexplore\b",
    r"\bsearch\b",
    r"\bscan\b",
    r"\binspect\b",
    r"\blook for\b",
    r"\bfind\b",
    r"\blocate\b",
    r"\bwhere is\b",
    r"\bcheck\b",
    r"\bobserve\b",
    r"探索",
    r"寻找",
    r"查找",
    r"搜寻",
    r"观察",
    r"看看",
    r"定位",
    r"识别",
    r"判断",
    r"门在哪",
    r"门在那",
    r"告诉我.*在哪",
    r"告诉我.*在那",
    r"前面是什么",
    r"周围是什么",
)

DISTANCE_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>m|meter|meters|metre|metres|cm|centimeter|centimeters|centimetre|centimetres|米|厘米|公分)",
    flags=re.IGNORECASE,
)

ANGLE_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>deg|degree|degrees|°|度)",
    flags=re.IGNORECASE,
)


def split_ordered_subgoals(goal_text: str) -> list[str]:
    clauses = [goal_text.strip()]
    for pattern in SEQUENTIAL_SPLIT_PATTERNS:
        next_clauses: list[str] = []
        for clause in clauses:
            next_clauses.extend(re.split(pattern, clause, flags=re.IGNORECASE))
        clauses = next_clauses
    normalized: list[str] = []
    for clause in clauses:
        cleaned = _normalize_clause(clause)
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized or [goal_text.strip()]


def detect_explicit_chassis_intent(goal_text: str) -> ChassisIntentRuleMatch | None:
    stripped_goal = goal_text.strip()
    if not stripped_goal:
        return None
    if _contains_any_pattern(stripped_goal, STOP_PATTERNS):
        action = ActionCall("stop", ("explicit_stop_request",))
        return ChassisIntentRuleMatch(
            route="stop_request",
            planner_profile_hint="motion_sequence",
            action=action_call_to_payload(action),
            action_expression=format_action_call(action),
            reason="deterministic explicit stop request detected from the subgoal text",
        )
    for action_name, patterns, profile_name in EXPLICIT_MOTION_PATTERNS:
        if _contains_any_pattern(stripped_goal, patterns):
            action = _build_motion_action(action_name, profile_name, stripped_goal)
            return ChassisIntentRuleMatch(
                route="manual_motion",
                planner_profile_hint="motion_sequence",
                action=action_call_to_payload(action),
                action_expression=format_action_call(action),
                reason=f"deterministic explicit chassis action detected for {action_name}",
            )
    return None


def has_scene_exploration_intent(goal_text: str) -> bool:
    stripped_goal = goal_text.strip()
    if not stripped_goal:
        return False
    if not _contains_any_pattern(stripped_goal, EXPLORATION_INTENT_PATTERNS):
        return False
    # Pure stop requests remain deterministic even if they mention checking or observing.
    if _contains_any_pattern(stripped_goal, STOP_PATTERNS):
        return False
    return True


def _build_motion_action(action_name: str, profile_name: str, goal_text: str) -> ActionCall:
    if action_name in {"move_forward", "move_backward"}:
        distance_m = _extract_distance_m(goal_text)
        if distance_m is not None:
            return ActionCall(action_name, (profile_name, distance_m))
    if action_name in {"turn_left", "turn_right"}:
        angle_deg = _extract_angle_deg(goal_text)
        if angle_deg is not None:
            return ActionCall(action_name, (profile_name, angle_deg))
    return ActionCall(action_name, (profile_name,))


def _extract_distance_m(text: str) -> float | None:
    match = DISTANCE_PATTERN.search(text)
    if match is None:
        return None
    value = float(match.group("value"))
    unit = match.group("unit").lower()
    if unit in {"cm", "centimeter", "centimeters", "centimetre", "centimetres", "厘米", "公分"}:
        value /= 100.0
    return round(value, 4)


def _extract_angle_deg(text: str) -> float | None:
    match = ANGLE_PATTERN.search(text)
    if match is None:
        return None
    return round(float(match.group("value")), 2)


def split_explicit_motion_steps(payload: dict[str, object]) -> list[dict[str, object]]:
    action = parse_action_payload(payload)
    if len(action.arguments) < 2:
        return [payload]
    profile_name = str(action.arguments[0]).strip()
    total_scalar = float(action.arguments[1])
    if action.name in {"move_forward", "move_backward"}:
        step_limit = 0.3
        precision = 4
    else:
        step_limit = 30.0
        precision = 2
    remaining = total_scalar
    steps: list[dict[str, object]] = []
    while remaining > step_limit + 1e-9:
        steps.append(action_call_to_payload(ActionCall(action.name, (profile_name, round(step_limit, precision)))))
        remaining = round(remaining - step_limit, precision)
    if remaining > 0.0:
        steps.append(action_call_to_payload(ActionCall(action.name, (profile_name, round(remaining, precision)))))
    return steps or [payload]


def _contains_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _normalize_clause(text: str) -> str:
    cleaned = text.strip().strip(",，。.;；")
    cleaned = re.sub(r"^\s*(?:and\s+then|then|next|随后|然后|接着|之后)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()
