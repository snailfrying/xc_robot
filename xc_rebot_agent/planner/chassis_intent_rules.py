from __future__ import annotations

import re
from dataclasses import dataclass

from .action_parser import ActionCall
from .action_parser import action_call_to_payload
from .action_parser import format_action_call


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
            action = ActionCall(action_name, (profile_name,))
            return ChassisIntentRuleMatch(
                route="manual_motion",
                planner_profile_hint="motion_sequence",
                action=action_call_to_payload(action),
                action_expression=format_action_call(action),
                reason=f"deterministic explicit chassis action detected for {action_name}",
            )
    return None


def _contains_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _normalize_clause(text: str) -> str:
    cleaned = text.strip().strip(",，。.;；")
    cleaned = re.sub(r"^\s*(?:and\s+then|then|next|随后|然后|接着|之后)\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()
