from __future__ import annotations

from ..errors import PlannerError
from ..models import PointOfInterest
from ..models import TaskPlan
from ..models import TaskSubgoal
from .action_parser import format_action_call
from .action_parser import parse_action_payload
from .chassis_intent_rules import detect_explicit_chassis_intent
from .chassis_intent_rules import has_scene_exploration_intent
from .chassis_intent_rules import split_explicit_motion_steps
from .chassis_intent_rules import split_ordered_subgoals
from .contracts import build_task_decomposition_contract
from .prompts import build_task_decomposition_system_prompt


class GoalRouter:
    def __init__(self, *, settings, point_resolver, llm_client, logger):
        self._settings = settings
        self._point_resolver = point_resolver
        self._llm_client = llm_client
        self._logger = logger

    def route(self, goal_text: str, points: list[PointOfInterest]) -> TaskPlan:
        clauses = self._decompose_goal(goal_text, points)
        subgoals: list[TaskSubgoal] = []
        for sequence_id, clause in enumerate(clauses, start=1):
            subgoals.extend(self._expand_subgoals(clause, points, sequence_id_start=len(subgoals) + 1))

        if len(subgoals) == 1:
            subgoal = subgoals[0]
            return TaskPlan(
                route=subgoal.route,
                reason=subgoal.reason,
                confidence=subgoal.confidence,
                subgoals=(subgoal,),
            )

        return TaskPlan(
            route="task_plan",
            reason="goal decomposed into ordered semantic subgoals",
            confidence=min(subgoal.confidence for subgoal in subgoals),
            subgoals=tuple(subgoals),
        )

    def _build_subgoal(
        self,
        goal_text: str,
        points: list[PointOfInterest],
        *,
        sequence_id: int,
    ) -> TaskSubgoal:
        if has_scene_exploration_intent(goal_text):
            return TaskSubgoal(
                sequence_id=sequence_id,
                route="scene_exploration",
                goal_text=goal_text,
                reason="goal asks to search, inspect, locate, or explain something in the scene; keep motion decisions inside visual exploration",
                confidence=self._settings.routing.react_confidence,
                planner_profile_hint="scene_exploration",
            )
        semantic_hint = detect_explicit_chassis_intent(goal_text)
        if semantic_hint is not None:
            return TaskSubgoal(
                sequence_id=sequence_id,
                route=semantic_hint.route,
                goal_text=goal_text,
                reason=semantic_hint.reason,
                confidence=1.0,
                planner_profile_hint=semantic_hint.planner_profile_hint,
                action=semantic_hint.action,
                action_expression=semantic_hint.action_expression,
            )

        point_resolution = self._point_resolver.resolve(goal_text, points)
        if point_resolution is not None and point_resolution.point is not None:
            return TaskSubgoal(
                sequence_id=sequence_id,
                route="navigation_hint",
                goal_text=goal_text,
                reason=f"{point_resolution.reason}; expose as map-point hint for planner verification",
                confidence=point_resolution.confidence,
                planner_profile_hint="navigation_sequence",
                point=point_resolution.point,
            )

        return TaskSubgoal(
            sequence_id=sequence_id,
            route="scene_exploration",
            goal_text=goal_text,
            reason="no deterministic point target; planner must decide the next atomic action from the newest result",
            confidence=self._settings.routing.react_confidence,
            planner_profile_hint="scene_exploration",
        )

    def _expand_subgoals(
        self,
        goal_text: str,
        points: list[PointOfInterest],
        *,
        sequence_id_start: int,
    ) -> list[TaskSubgoal]:
        seed = self._build_subgoal(goal_text, points, sequence_id=sequence_id_start)
        if seed.route != "manual_motion" or not seed.action:
            return [seed]
        action_steps = split_explicit_motion_steps(seed.action)
        if len(action_steps) <= 1:
            return [seed]
        expanded: list[TaskSubgoal] = []
        for offset, action in enumerate(action_steps):
            expanded.append(
                TaskSubgoal(
                    sequence_id=sequence_id_start + offset,
                    route=seed.route,
                    goal_text=seed.goal_text,
                    reason=f"{seed.reason}; segmented explicit motion step {offset + 1}/{len(action_steps)} for visual re-check",
                    confidence=seed.confidence,
                    planner_profile_hint=seed.planner_profile_hint,
                    action=action,
                    action_expression=format_action_call(parse_action_payload(action)),
                )
            )
        return expanded

    def _decompose_goal(self, goal_text: str, points: list[PointOfInterest]) -> list[str]:
        stripped_goal = goal_text.strip()
        if not stripped_goal:
            return [""]
        deterministic = split_ordered_subgoals(stripped_goal)
        if not self._llm_client.enabled:
            self._logger.info(
                "task decomposition fallback: llm disabled, use deterministic clauses count=%s",
                len(deterministic),
            )
            return deterministic
        try:
            parsed = self._llm_client.chat_json(
                system_prompt=build_task_decomposition_system_prompt(),
                user_payload={
                    "workflow_contract": build_task_decomposition_contract(),
                    "goal_text": stripped_goal,
                    "known_points": [point.to_dict() for point in points],
                    "deterministic_fallback_subgoals": deterministic,
                },
                response_label="task_decomposition",
            )
            clauses = self._normalize_subgoals(parsed)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("task decomposition failed, fallback to deterministic clauses: error=%s", exc)
            return deterministic
        if not clauses:
            self._logger.warning("task decomposition returned no valid subgoals; use deterministic clauses")
            return deterministic
        self._logger.info(
            "task decomposition selected: subgoal_count=%s reason=%s confidence=%.3f",
            len(clauses),
            str(parsed.get("reason", "") or "").strip(),
            float(parsed.get("confidence", 0.0) or 0.0),
        )
        return clauses

    def _normalize_subgoals(self, parsed: dict[str, object]) -> list[str]:
        raw_subgoals = parsed.get("subgoals", [])
        if not isinstance(raw_subgoals, list):
            raise PlannerError("task_decomposition_subgoals_missing")
        clauses: list[str] = []
        for item in raw_subgoals:
            if isinstance(item, dict):
                goal_text = str(item.get("goal_text", "") or "").strip()
            else:
                goal_text = str(item or "").strip()
            normalized = goal_text.strip().strip(",，。.;；")
            if normalized and normalized not in clauses:
                clauses.append(normalized)
        return clauses
