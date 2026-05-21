from __future__ import annotations

from ..errors import PlannerError
from ..models import CaptureObservation
from ..models import PlannerDecision
from ..models import PointOfInterest
from ..models import RobotStatus
from ..models import TaskPlan
from ..models import TaskSubgoal
from ..planner.action_parser import ACTION_LIBRARY_TEXT
from ..planner.action_parser import ActionCall
from ..planner.action_parser import action_call_to_payload
from ..planner.action_parser import format_action_call
from ..planner.action_parser import parse_action_payload
from ..planner.action_parser import parse_action_expression
from ..planner.contracts import build_step_planner_workflow_contract
from ..planner.prompts import build_step_planner_system_prompt

PLANNER_OUTPUT_TEMPLATE = {
    "response": "I will execute one safe atomic step now.",
    "action": {"name": "finish_task", "args": {}},
    "reason": "No further safe action is justified.",
    "confidence": 0.0,
    "observation_focus": "scene",
    "target_hint": "",
    "subgoal_state": "blocked",
    "stop": True,
}

VALID_PLANNER_PROFILES = {"navigation_sequence", "motion_sequence", "scene_exploration"}


class ReactiveScenePlanner:
    def __init__(self, *, settings, llm_client, logger):
        self._settings = settings
        self._llm_client = llm_client
        self._logger = logger

    def select_profile(
        self,
        *,
        goal_text: str,
        current_subgoal: TaskSubgoal,
        status: RobotStatus,
        points: list[PointOfInterest],
        history: list[dict[str, object]],
        session_context: list[dict[str, object]] | None = None,
    ) -> str:
        if current_subgoal.planner_profile_hint in VALID_PLANNER_PROFILES:
            self._logger.info(
                "planner profile selected by router hint: profile=%s subgoal=%s route=%s",
                current_subgoal.planner_profile_hint,
                current_subgoal.goal_text,
                current_subgoal.route,
            )
            return current_subgoal.planner_profile_hint
        return self._fallback_profile(current_subgoal)

    def plan(
        self,
        *,
        goal_text: str,
        current_subgoal: TaskSubgoal,
        task_plan: TaskPlan,
        status: RobotStatus,
        observation: CaptureObservation | None,
        points: list[PointOfInterest],
        history: list[dict[str, object]],
        session_context: list[dict[str, object]] | None = None,
        latest_execution: dict[str, object] | None = None,
        profile_name: str = "",
    ) -> PlannerDecision:
        profile_name = profile_name or self.select_profile(
            goal_text=goal_text,
            current_subgoal=current_subgoal,
            status=status,
            points=points,
            history=history,
            session_context=session_context or [],
        )
        if not self._llm_client.enabled:
            decision = self._fallback_decision(
                profile_name=profile_name,
                current_subgoal=current_subgoal,
                status=status,
                points=points,
                latest_execution=latest_execution or {},
            )
            decision = self._apply_profile_guardrail(
                decision=decision,
                profile_name=profile_name,
                current_subgoal=current_subgoal,
                status=status,
                observation=observation,
                points=points,
                latest_execution=latest_execution or {},
            )
            decision = self._normalize_stop_semantics(decision, current_subgoal=current_subgoal)
            self._validate_business_constraints(decision, points, current_subgoal=current_subgoal)
            return decision
        self._logger.info(
            "step planner start: profile=%s subgoal=%s status=%s history_items=%s session_context_items=%s observation=%s",
            profile_name,
            current_subgoal.goal_text,
            status.short_dict(),
            len(history),
            len(session_context or []),
            observation.short_dict() if observation is not None else {},
        )
        parsed = self._llm_client.chat_json(
            system_prompt=build_step_planner_system_prompt(profile_name),
            user_payload=self._build_llm_payload(
                goal_text=goal_text,
                current_subgoal=current_subgoal,
                task_plan=task_plan,
                status=status,
                observation=observation,
                points=points,
                history=history,
                session_context=session_context or [],
                latest_execution=latest_execution or {},
                profile_name=profile_name,
            ),
            image_data_urls=self._observation_image_urls(observation),
            response_label=f"step_planner:{profile_name}",
        )
        decision = self._normalize_decision(parsed, profile_name=profile_name)
        decision = self._apply_profile_guardrail(
            decision=decision,
            profile_name=profile_name,
            current_subgoal=current_subgoal,
            status=status,
            observation=observation,
            points=points,
            latest_execution=latest_execution or {},
        )
        decision = self._normalize_stop_semantics(decision, current_subgoal=current_subgoal)
        self._validate_business_constraints(decision, points, current_subgoal=current_subgoal)
        decision_action = parse_action_payload(decision.action or decision.action_expression)
        if (
            decision.confidence < self._settings.planner.confidence_floor
            and self._settings.planner.force_stop_on_low_confidence
            and decision_action.name != "stop"
        ):
            self._logger.warning("planner confidence below floor: %.3f", decision.confidence)
            return self._safe_finish("planner confidence below configured floor", profile_name=profile_name)
        self._logger.info(
            "step planner decision: profile=%s action=%s confidence=%.3f stop=%s reason=%s",
            profile_name,
            decision.action_expression,
            decision.confidence,
            decision.stop,
            decision.reason,
        )
        return decision

    def _build_llm_payload(
        self,
        *,
        goal_text: str,
        current_subgoal: TaskSubgoal,
        task_plan: TaskPlan,
        status: RobotStatus,
        observation: CaptureObservation | None,
        points: list[PointOfInterest],
        history: list[dict[str, object]],
        session_context: list[dict[str, object]],
        latest_execution: dict[str, object],
        profile_name: str,
    ) -> dict[str, object]:
        matched_point_hint = current_subgoal.point.to_dict() if current_subgoal.point is not None else {}
        return {
            "format_template": PLANNER_OUTPUT_TEMPLATE,
            "workflow_contract": build_step_planner_workflow_contract(profile_name),
            "action_library": ACTION_LIBRARY_TEXT,
            "overall_goal": goal_text,
            "task_plan_summary": self._build_task_plan_summary(
                task_plan=task_plan,
                current_subgoal=current_subgoal,
            ),
            "current_subgoal_summary": self._build_current_subgoal_summary(current_subgoal),
            "subgoal_runtime_context": self._build_subgoal_runtime_context(
                current_subgoal=current_subgoal,
                status=status,
                latest_execution=latest_execution,
            ),
            "execution_contract": self._build_execution_contract(
                current_subgoal=current_subgoal,
                profile_name=profile_name,
            ),
            "current_navigation_context": {
                "robot_state": status.robot_state,
                "nav_state": status.nav_state,
                "current_target_point_id": status.target_point_id,
                "localization_valid": status.localization_valid,
            },
            "fresh_runtime_evidence": {
                "robot_status_digest": status.short_dict(),
                "latest_execution_digest": self._compact_execution_result(latest_execution),
                "observation_digest": observation.short_dict() if observation is not None else {},
            },
            "robot_status": status.raw,
            "matched_point_hint": matched_point_hint,
            "known_points": [point.to_dict() for point in points],
            "point_candidates": self._build_point_candidates(
                current_subgoal=current_subgoal,
                points=points,
            ),
            "action_space": self._build_action_space(
                profile_name=profile_name,
                current_subgoal=current_subgoal,
            ),
            "memory_digest": {
                "recent_history": self._compact_history(history),
                "session_memory": self._compact_session_context(session_context),
            },
            "observation_metadata": observation.raw if observation is not None else {},
            "freshness_contract": {
                "status_truth": "robot_status is the newest API-confirmed state for this turn",
                "execution_truth": "latest_execution_result is the newest confirmed result from the previous synchronous action",
                "image_truth": "if an image is provided, it is the newest visual evidence for this turn",
                "conflict_rule": "prefer the newest status/result/image over older memory whenever they conflict",
            },
            "specialist_roles": {
                "navigation_sequence": "complete one ordered map-point navigation subgoal",
                "motion_sequence": "complete one ordered direct chassis-motion or stop subgoal",
                "scene_exploration": "explore, inspect, or recover visibility until the newest evidence justifies the next action",
            },
        }

    def _build_task_plan_summary(
        self,
        *,
        task_plan: TaskPlan,
        current_subgoal: TaskSubgoal,
    ) -> dict[str, object]:
        remaining_subgoals = [
            {
                "sequence_id": subgoal.sequence_id,
                "goal_text": subgoal.goal_text,
                "route": subgoal.route,
            }
            for subgoal in task_plan.subgoals
            if subgoal.sequence_id >= current_subgoal.sequence_id
        ]
        return {
            "route": task_plan.route,
            "subgoal_count": len(task_plan.subgoals),
            "current_sequence_id": current_subgoal.sequence_id,
            "has_later_subgoals": any(
                subgoal.sequence_id > current_subgoal.sequence_id for subgoal in task_plan.subgoals
            ),
            "remaining_subgoals": remaining_subgoals,
        }

    def _build_current_subgoal_summary(self, current_subgoal: TaskSubgoal) -> dict[str, object]:
        return {
            "sequence_id": current_subgoal.sequence_id,
            "goal_text": current_subgoal.goal_text,
            "route": current_subgoal.route,
            "planner_profile_hint": current_subgoal.planner_profile_hint,
            "has_point_hint": current_subgoal.point is not None,
            "point_hint_point_id": current_subgoal.point.point_id if current_subgoal.point is not None else "",
            "point_hint_point_name": current_subgoal.point.name if current_subgoal.point is not None else "",
            "router_action_hint": current_subgoal.action,
            "router_action_expression": current_subgoal.action_expression,
        }

    def _build_point_candidates(
        self,
        *,
        current_subgoal: TaskSubgoal,
        points: list[PointOfInterest],
    ) -> list[dict[str, object]]:
        if current_subgoal.point is not None:
            preferred = [current_subgoal.point]
            others = [point for point in points if point.point_id != current_subgoal.point.point_id]
            ordered_points = preferred + others
        else:
            ordered_points = list(points)
        return [
            {
                "point_id": point.point_id,
                "name": point.name,
                "aliases": list(point.aliases),
                "search_terms": list(point.search_terms()),
                "is_matched_hint": bool(current_subgoal.point is not None and point.point_id == current_subgoal.point.point_id),
            }
            for point in ordered_points
        ]

    def _build_subgoal_runtime_context(
        self,
        *,
        current_subgoal: TaskSubgoal,
        status: RobotStatus,
        latest_execution: dict[str, object],
    ) -> dict[str, object]:
        point_id = current_subgoal.point.point_id if current_subgoal.point is not None else ""
        latest_action = self._latest_execution_action(latest_execution)
        latest_action_expression = format_action_call(latest_action) if latest_action is not None else ""
        return {
            "subgoal_goal_text": current_subgoal.goal_text,
            "subgoal_reason": current_subgoal.reason,
            "subgoal_confidence": current_subgoal.confidence,
            "point_hint_available": bool(current_subgoal.point is not None),
            "point_hint_point_id": point_id,
            "point_hint_point_name": current_subgoal.point.name if current_subgoal.point is not None else "",
            "target_already_reached": bool(point_id and status.target_point_id == point_id and status.nav_state == "succeeded"),
            "latest_execution_ok": bool(latest_execution.get("ok", False)),
            "latest_execution_action": action_call_to_payload(latest_action) if latest_action is not None else {},
            "latest_execution_action_expression": latest_action_expression,
            "latest_execution_matches_point_hint": bool(
                point_id
                and latest_action is not None
                and latest_action.name == "navigate"
                and str(latest_action.arguments[0]).strip() == point_id
            ),
            "router_action_hint": current_subgoal.action,
            "router_action_expression": current_subgoal.action_expression,
            "latest_execution_summary": str(latest_execution.get("summary", "") or ""),
        }

    def _build_action_space(self, *, profile_name: str, current_subgoal: TaskSubgoal) -> dict[str, object]:
        motion_profiles = sorted(self._settings.executor.manual_profiles.keys())
        allowed_actions_by_profile = {
            "navigation_sequence": ["navigate(point_id)", "stop(reason_key)", "finish_task()"],
            "motion_sequence": [
                "move_forward(profile_name)",
                "move_backward(profile_name)",
                "turn_left(profile_name)",
                "turn_right(profile_name)",
                "stop(reason_key)",
                "finish_task()",
            ],
            "scene_exploration": [
                "navigate(point_id)",
                "move_forward(profile_name)",
                "move_backward(profile_name)",
                "turn_left(profile_name)",
                "turn_right(profile_name)",
                "stop(reason_key)",
                "finish_task()",
            ],
        }
        return {
            "allowed_profile_names": motion_profiles,
            "allowed_actions_for_profile": allowed_actions_by_profile.get(profile_name, []),
            "router_action_hint": current_subgoal.action,
            "router_action_expression": current_subgoal.action_expression,
            "exact_action_library": ACTION_LIBRARY_TEXT,
        }

    def _build_execution_contract(self, *, current_subgoal: TaskSubgoal, profile_name: str) -> dict[str, object]:
        return {
            "route": current_subgoal.route,
            "planner_profile_hint": current_subgoal.planner_profile_hint,
            "explicit_action_hint": current_subgoal.action,
            "explicit_action_expression": current_subgoal.action_expression,
            "profile_name": profile_name,
            "finish_rule": "finish_task() is allowed only when the current ordered subgoal is already complete from the newest evidence",
            "stop_rule": (
                "stop(reason_key) is fail-closed for blocked situations; for stop_request it may also be the completing action"
            ),
            "serial_rule": "never skip the current subgoal and never plan more than one atomic action",
        }

    def _compact_execution_result(self, latest_execution: dict[str, object]) -> dict[str, object]:
        if not latest_execution:
            return {}
        action = self._latest_execution_action(latest_execution)
        return {
            "action": action_call_to_payload(action) if action is not None else {},
            "action_expression": str(latest_execution.get("action_expression", "") or ""),
            "ok": bool(latest_execution.get("ok", False)),
            "summary": str(latest_execution.get("summary", "") or ""),
            "status_after": latest_execution.get("status_after", {}),
        }

    def _compact_history(self, history: list[dict[str, object]]) -> list[dict[str, object]]:
        compact: list[dict[str, object]] = []
        for item in history[-self._settings.planner.history_window :]:
            subgoal = item.get("subgoal", {})
            compact.append(
                {
                    "step_index": item.get("step_index", 0),
                    "subgoal_goal_text": subgoal.get("goal_text", "") if isinstance(subgoal, dict) else "",
                    "action": item.get("action", {}),
                    "action_expression": item.get("action_expression", ""),
                    "reason": item.get("reason", ""),
                    "summary": item.get("summary", ""),
                    "status_after": item.get("status_after", {}),
                }
            )
        return compact

    def _compact_session_context(self, session_context: list[dict[str, object]]) -> list[dict[str, object]]:
        compact: list[dict[str, object]] = []
        for item in session_context[-self._settings.planner.history_window :]:
            compact.append(
                {
                    "ts": item.get("ts", ""),
                    "goal_text": item.get("goal_text", ""),
                    "completed": bool(item.get("completed", False)),
                    "error": item.get("error", ""),
                    "stage": item.get("stage", ""),
                    "task_plan_route": item.get("task_plan_route", ""),
                    "final_status": item.get("final_status", {}),
                    "last_step": item.get("last_step", {}),
                    "step_count": int(item.get("step_count", 0) or 0),
                }
            )
        return compact

    def _fallback_profile(self, current_subgoal: TaskSubgoal) -> str:
        if current_subgoal.action or current_subgoal.action_expression:
            return "motion_sequence"
        if current_subgoal.point is not None:
            return "navigation_sequence"
        return "scene_exploration"

    def _fallback_decision(
        self,
        *,
        profile_name: str,
        current_subgoal: TaskSubgoal,
        status: RobotStatus,
        points: list[PointOfInterest],
        latest_execution: dict[str, object],
    ) -> PlannerDecision:
        latest_action = self._latest_execution_action(latest_execution)
        if profile_name == "navigation_sequence" and current_subgoal.point is not None:
            point_id = current_subgoal.point.point_id
            if status.target_point_id == point_id and status.nav_state == "succeeded":
                return self._finish_with_reason(
                    "navigation subgoal already satisfied by newest status",
                    profile_name=profile_name,
                    target_hint=current_subgoal.point.name,
                )
            if (
                latest_action is not None
                and latest_action.name == "navigate"
                and str(latest_action.arguments[0]).strip() == point_id
                and latest_execution.get("ok", False)
            ):
                return self._finish_with_reason(
                    "previous synchronous navigation already completed this subgoal",
                    profile_name=profile_name,
                    target_hint=current_subgoal.point.name,
                )
            return self._decision_from_action_call(
                ActionCall("navigate", (point_id,)),
                response="I will navigate to the matched map point now.",
                reason="fallback navigation decision from matched point hint",
                confidence=0.92,
                observation_focus="map_point",
                target_hint=current_subgoal.point.name,
                subgoal_state="continue",
                stop=False,
                raw={"profile_name": profile_name, "source": "fallback"},
            )
        explicit_action = self._subgoal_action(current_subgoal)
        if profile_name == "motion_sequence" and explicit_action is not None:
            if explicit_action.name == "stop" and status.robot_state != "manual" and not status.is_navigation_active:
                return self._finish_with_reason(
                    "stop subgoal is already satisfied by the newest idle status",
                    profile_name=profile_name,
                    target_hint="",
                )
            if self._actions_match(latest_action, explicit_action) and latest_execution.get("ok", False):
                return self._finish_with_reason(
                    "previous synchronous motion already completed this explicit subgoal",
                    profile_name=profile_name,
                    target_hint="",
                )
            if explicit_action.name == "stop":
                return self._decision_from_action_call(
                    explicit_action,
                    response="I will execute the explicit stop request now.",
                    reason="fallback motion decision from deterministic stop-request semantics",
                    confidence=0.98,
                    observation_focus="status",
                    target_hint="",
                    subgoal_state="completed",
                    stop=False,
                    raw={"profile_name": profile_name, "source": "fallback"},
                )
            return self._decision_from_action_call(
                explicit_action,
                response="I will execute the deterministic explicit chassis action now.",
                reason="fallback motion decision from deterministic subgoal semantics",
                confidence=0.95,
                observation_focus="status",
                target_hint="",
                subgoal_state="continue",
                stop=False,
                raw={"profile_name": profile_name, "source": "fallback"},
            )
        return self._safe_finish(
            "llm backend disabled; fail closed instead of degrading to keyword-based action routing",
            profile_name=profile_name,
        )

    def _normalize_decision(self, parsed: dict[str, object], *, profile_name: str) -> PlannerDecision:
        action_call = parse_action_payload(parsed.get("action"))
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0) or 0.0)))
        return self._decision_from_action_call(
            action_call,
            response=str(parsed.get("response", "") or "").strip() or "I will execute the next safe step.",
            reason=str(parsed.get("reason", "") or "").strip() or "planner produced an action",
            confidence=confidence,
            observation_focus=str(parsed.get("observation_focus", "") or "scene").strip(),
            target_hint=str(parsed.get("target_hint", "") or "").strip(),
            subgoal_state=str(parsed.get("subgoal_state", "") or "").strip() or "blocked",
            stop=bool(parsed.get("stop", False)),
            raw={"profile_name": profile_name, "llm_output": parsed},
        )

    def _normalize_stop_semantics(
        self,
        decision: PlannerDecision,
        *,
        current_subgoal: TaskSubgoal,
    ) -> PlannerDecision:
        action = parse_action_payload(decision.action or decision.action_expression)
        derived_stop = action.name == "stop" and not (
            current_subgoal.route == "stop_request" and decision.subgoal_state == "completed"
        )
        if decision.stop == derived_stop:
            return decision
        return PlannerDecision(
            response=decision.response,
            action=dict(decision.action),
            action_expression=decision.action_expression,
            reason=decision.reason,
            confidence=decision.confidence,
            observation_focus=decision.observation_focus,
            target_hint=decision.target_hint,
            subgoal_state=decision.subgoal_state,
            stop=derived_stop,
            raw=dict(decision.raw),
        )

    def _validate_business_constraints(
        self,
        decision: PlannerDecision,
        points: list[PointOfInterest],
        current_subgoal: TaskSubgoal | None = None,
    ) -> None:
        action = parse_action_payload(decision.action or decision.action_expression)
        if decision.subgoal_state not in {"continue", "completed", "blocked"}:
            raise PlannerError(f"invalid_subgoal_state:{decision.subgoal_state}")
        if action.name == "finish_task" and decision.subgoal_state != "completed":
            raise PlannerError(f"finish_task_requires_completed_state:{decision.subgoal_state}")
        if action.name == "stop":
            if current_subgoal is not None and current_subgoal.route == "stop_request":
                if decision.subgoal_state != "completed":
                    raise PlannerError(f"stop_request_requires_completed_state:{decision.subgoal_state}")
            elif decision.subgoal_state != "blocked":
                raise PlannerError(f"stop_requires_blocked_state:{decision.subgoal_state}")
        if action.name not in {"finish_task", "stop"} and decision.subgoal_state != "continue":
            raise PlannerError(f"action_requires_continue_state:{action.name}:{decision.subgoal_state}")
        if action.name == "navigate":
            target_point_id = str(action.arguments[0]).strip()
            point_ids = {point.point_id for point in points}
            if target_point_id not in point_ids:
                raise PlannerError(f"planner_returned_unknown_point:{target_point_id}")
        if action.name in {"move_forward", "move_backward", "turn_left", "turn_right"}:
            profile_name = str(action.arguments[0]).strip()
            if profile_name not in self._settings.executor.manual_profiles:
                raise PlannerError(f"planner_returned_unknown_profile:{profile_name}")

    def _safe_finish(self, reason: str, *, profile_name: str) -> PlannerDecision:
        return self._decision_from_action_call(
            ActionCall("stop", ("planner_blocked",)),
            response="I cannot get a reliable next step, so I will stop instead of guessing.",
            reason=reason,
            confidence=0.0,
            observation_focus="scene",
            target_hint="",
            subgoal_state="blocked",
            stop=True,
            raw={"profile_name": profile_name, "fallback_reason": reason},
        )

    def _finish_with_reason(self, reason: str, *, profile_name: str, target_hint: str) -> PlannerDecision:
        return self._decision_from_action_call(
            ActionCall("finish_task", ()),
            response="The current ordered subgoal is already complete, so I will finish it here.",
            reason=reason,
            confidence=0.95,
            observation_focus="status",
            target_hint=target_hint,
            subgoal_state="completed",
            stop=False,
            raw={"profile_name": profile_name, "source": "completion"},
        )

    def _decision_from_action_call(
        self,
        action_call: ActionCall,
        *,
        response: str,
        reason: str,
        confidence: float,
        observation_focus: str,
        target_hint: str,
        subgoal_state: str,
        stop: bool,
        raw: dict[str, object],
    ) -> PlannerDecision:
        return PlannerDecision(
            response=response,
            action=action_call_to_payload(action_call),
            action_expression=format_action_call(action_call),
            reason=reason,
            confidence=confidence,
            observation_focus=observation_focus,
            target_hint=target_hint,
            subgoal_state=subgoal_state,
            stop=stop,
            raw=raw,
        )

    def _subgoal_action(self, current_subgoal: TaskSubgoal) -> ActionCall | None:
        if current_subgoal.action:
            return parse_action_payload(current_subgoal.action)
        if current_subgoal.action_expression:
            return parse_action_expression(current_subgoal.action_expression)
        return None

    def _latest_execution_action(self, latest_execution: dict[str, object]) -> ActionCall | None:
        if not latest_execution:
            return None
        action_payload = latest_execution.get("action")
        if action_payload not in ({}, None, ""):
            return parse_action_payload(action_payload)
        action_expression = str(latest_execution.get("action_expression", "") or "").strip()
        if action_expression:
            return parse_action_expression(action_expression)
        return None

    def _actions_match(self, left: ActionCall | None, right: ActionCall | None) -> bool:
        if left is None or right is None:
            return False
        return left.name == right.name and left.arguments == right.arguments

    def _scene_completion_supported(
        self,
        *,
        current_subgoal: TaskSubgoal,
        status: RobotStatus,
        observation: CaptureObservation | None,
        latest_execution: dict[str, object],
    ) -> bool:
        if current_subgoal.point is not None:
            point_id = current_subgoal.point.point_id
            if status.target_point_id == point_id and status.nav_state == "succeeded":
                return True
            latest_action = self._latest_execution_action(latest_execution)
            if (
                latest_action is not None
                and latest_action.name == "navigate"
                and str(latest_action.arguments[0]).strip() == point_id
                and latest_execution.get("ok", False)
            ):
                return True
        return observation is not None

    def _has_visual_observation(self, observation: CaptureObservation | None) -> bool:
        if observation is None:
            return False
        return bool(observation.rgb_data_url or observation.depth_data_url or observation.raw)

    def _build_conservative_scan_decision(self, *, profile_name: str, reason: str) -> PlannerDecision:
        manual_profiles = self._settings.executor.manual_profiles
        if "explore_left" in manual_profiles:
            return self._decision_from_action_call(
                ActionCall("turn_left", ("explore_left",)),
                response="I will rotate conservatively to gather better evidence before moving forward.",
                reason=reason,
                confidence=max(self._settings.planner.confidence_floor, 0.6),
                observation_focus="scene",
                target_hint="",
                subgoal_state="continue",
                stop=False,
                raw={"profile_name": profile_name, "source": "guardrail_scan"},
            )
        if "explore_right" in manual_profiles:
            return self._decision_from_action_call(
                ActionCall("turn_right", ("explore_right",)),
                response="I will rotate conservatively to gather better evidence before moving forward.",
                reason=reason,
                confidence=max(self._settings.planner.confidence_floor, 0.6),
                observation_focus="scene",
                target_hint="",
                subgoal_state="continue",
                stop=False,
                raw={"profile_name": profile_name, "source": "guardrail_scan"},
            )
        return self._safe_finish(reason, profile_name=profile_name)

    def _apply_profile_guardrail(
        self,
        *,
        decision: PlannerDecision,
        profile_name: str,
        current_subgoal: TaskSubgoal,
        status: RobotStatus,
        observation: CaptureObservation | None,
        points: list[PointOfInterest],
        latest_execution: dict[str, object],
    ) -> PlannerDecision:
        action = parse_action_payload(decision.action or decision.action_expression)
        latest_action = self._latest_execution_action(latest_execution)
        if profile_name == "navigation_sequence" and current_subgoal.point is not None:
            point_id = current_subgoal.point.point_id
            if action.name == "finish_task":
                already_satisfied = status.target_point_id == point_id and status.nav_state == "succeeded"
                just_finished = (
                    latest_action is not None
                    and latest_action.name == "navigate"
                    and str(latest_action.arguments[0]).strip() == point_id
                    and latest_execution.get("ok", False)
                )
                if not already_satisfied and not just_finished:
                    self._logger.warning(
                        "planner guardrail override: navigation_sequence returned finish_task before target=%s was reached",
                        point_id,
                    )
                    return self._fallback_decision(
                        profile_name=profile_name,
                        current_subgoal=current_subgoal,
                        status=status,
                        points=points,
                        latest_execution=latest_execution,
                    )
        if profile_name == "motion_sequence" and action.name == "finish_task" and not latest_execution.get("ok", False):
            explicit_stop_already_satisfied = (
                current_subgoal.route == "stop_request"
                and status.robot_state != "manual"
                and not status.is_navigation_active
            )
            if not explicit_stop_already_satisfied:
                raise PlannerError("motion_sequence_finished_before_any_confirmed_action")
        if profile_name == "motion_sequence":
            expected_action = self._subgoal_action(current_subgoal)
            if expected_action is not None:
                if expected_action.name == "stop":
                    if action.name not in {"stop", "finish_task"}:
                        self._logger.warning(
                            "planner guardrail override: stop_request returned unexpected action=%s, expected stop",
                            action.name,
                        )
                        return self._fallback_decision(
                            profile_name=profile_name,
                            current_subgoal=current_subgoal,
                            status=status,
                            points=points,
                            latest_execution=latest_execution,
                        )
                elif action.name not in {expected_action.name, "stop", "finish_task"}:
                    self._logger.warning(
                        "planner guardrail override: explicit motion returned action=%s, expected=%s",
                        action.name,
                        expected_action.name,
                    )
                    return self._fallback_decision(
                        profile_name=profile_name,
                        current_subgoal=current_subgoal,
                        status=status,
                        points=points,
                        latest_execution=latest_execution,
                    )
                elif action.name == expected_action.name and not self._actions_match(action, expected_action):
                    self._logger.warning(
                        "planner guardrail override: explicit motion action arguments changed from router hint expected=%s actual=%s",
                        current_subgoal.action_expression,
                        decision.action_expression,
                    )
                    return self._fallback_decision(
                        profile_name=profile_name,
                        current_subgoal=current_subgoal,
                        status=status,
                        points=points,
                        latest_execution=latest_execution,
                    )
        if profile_name == "scene_exploration":
            if action.name == "finish_task" and not self._scene_completion_supported(
                current_subgoal=current_subgoal,
                status=status,
                observation=observation,
                latest_execution=latest_execution,
            ):
                self._logger.warning(
                    "planner guardrail override: scene_exploration returned finish_task without fresh completion evidence"
                )
                return self._safe_finish(
                    "scene exploration completion lacks fresh evidence",
                    profile_name=profile_name,
                )
            if action.name == "move_forward":
                if not self._has_visual_observation(observation):
                    self._logger.warning(
                        "planner guardrail override: scene_exploration blocked forward motion without fresh visual evidence"
                    )
                    return self._safe_finish(
                        "scene exploration forward motion blocked without fresh visual evidence",
                        profile_name=profile_name,
                    )
                minimum_forward_confidence = max(self._settings.planner.confidence_floor, 0.6)
                if decision.confidence < minimum_forward_confidence:
                    self._logger.warning(
                        "planner guardrail override: scene_exploration downgraded low-confidence forward motion to conservative scan"
                    )
                    return self._build_conservative_scan_decision(
                        profile_name=profile_name,
                        reason="scene exploration forward motion downgraded to conservative scan under weak evidence",
                    )
        if profile_name == "navigation_sequence" and action.name not in {"navigate", "stop", "finish_task"}:
            raise PlannerError(f"navigation_sequence_invalid_action:{action.name}")
        if profile_name == "motion_sequence" and action.name not in {
            "move_forward",
            "move_backward",
            "turn_left",
            "turn_right",
            "stop",
            "finish_task",
        }:
            raise PlannerError(f"motion_sequence_invalid_action:{action.name}")
        if profile_name == "navigation_sequence" and current_subgoal.point is not None and action.name == "navigate":
            target_point_id = str(action.arguments[0]).strip()
            if target_point_id != current_subgoal.point.point_id:
                raise PlannerError(
                    f"navigation_sequence_target_mismatch:{target_point_id}:expected={current_subgoal.point.point_id}"
                )
        return decision

    def _observation_image_urls(self, observation: CaptureObservation | None) -> list[str]:
        if observation is None:
            return []
        return [value for value in [observation.rgb_data_url, observation.depth_data_url] if value]
