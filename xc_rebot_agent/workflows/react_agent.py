from __future__ import annotations

from datetime import datetime

from ..clients.llm_client import OpenAICompatibleClient
from ..clients.robot_api import RobotApiClient
from ..clients.vision_service import VisionUnderstandingClient
from ..errors import ActionExecutionError
from ..errors import ObservationError
from ..errors import PlannerError
from ..errors import PointResolutionError
from ..errors import RobotApiError
from ..errors import RobotProtocolError
from ..errors import XcRebotError
from ..logging_utils import get_component_logger
from ..logging_utils import SessionTraceWriter
from ..models import TaskPlan
from ..planner.action_parser import parse_action_payload
from ..planner.point_resolver import PointResolver
from ..planner.react_planner import ReactiveScenePlanner
from ..planner.router import GoalRouter
from ..runtime.executor import SynchronousActionExecutor
from ..runtime.observer import ObservationProvider


class ReactAgent:
    def __init__(self, *, settings, logger):
        self._settings = settings
        self._logger = get_component_logger(logger, "workflow.react_agent")
        self._trace_writer = SessionTraceWriter(
            (settings.project_root / settings.logging.directory / "session_trace.jsonl")
            if settings.logging.session_trace_enabled
            else None
        )
        self._robot_client = RobotApiClient(
            settings=settings,
            logger=get_component_logger(logger, "client.robot_api"),
        )
        self._llm_client = OpenAICompatibleClient(
            settings=settings,
            logger=get_component_logger(logger, "client.llm"),
        )
        self._vision_client = VisionUnderstandingClient(
            settings=settings,
            logger=get_component_logger(logger, "client.vision"),
        )
        self._point_resolver = PointResolver(
            settings=settings,
            llm_client=self._llm_client,
            logger=get_component_logger(logger, "planner.point_resolver"),
        )
        self._router = GoalRouter(
            settings=settings,
            point_resolver=self._point_resolver,
            llm_client=self._llm_client,
            logger=get_component_logger(logger, "planner.router"),
        )
        self._planner = ReactiveScenePlanner(
            settings=settings,
            llm_client=self._llm_client,
            logger=get_component_logger(logger, "planner.scene"),
        )
        self._executor = SynchronousActionExecutor(
            settings=settings,
            robot_client=self._robot_client,
            logger=get_component_logger(logger, "runtime.executor"),
        )
        self._observer = ObservationProvider(
            settings=settings,
            robot_client=self._robot_client,
            vision_client=self._vision_client,
            logger=get_component_logger(logger, "runtime.observer"),
        )

    def run(
        self,
        goal_text: str,
        *,
        session_context: list[dict[str, object]] | None = None,
        external_session_id: str = "",
    ) -> dict[str, object]:
        session_id = external_session_id or f"{self._settings.session_prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        session_context = list(session_context or [])
        self._logger.info(
            "react session start: session_id=%s goal=%s session_context_items=%s",
            session_id,
            goal_text,
            len(session_context),
        )
        history: list[dict[str, object]] = []
        try:
            points = self._point_resolver.enrich_points(self._robot_client.get_points())
        except (RobotApiError, RobotProtocolError, PointResolutionError) as exc:
            self._logger.error("session bootstrap failed: session_id=%s error=%s", session_id, exc)
            self._trace_writer.write(
                "session_bootstrap_error",
                {
                    "session_id": session_id,
                    "goal_text": goal_text,
                    "stage": "bootstrap_points",
                    "error": str(exc),
                },
            )
            return {
                "session_id": session_id,
                "goal_text": goal_text,
                "session_context_count": len(session_context),
                "steps": [],
                "completed": False,
                "error": str(exc),
                "stage": "bootstrap_points",
            }
        self._trace_writer.write(
            "session_start",
            {
                "session_id": session_id,
                "goal_text": goal_text,
                "session_context_count": len(session_context),
            },
        )

        final_summary: dict[str, object] = {
            "session_id": session_id,
            "goal_text": goal_text,
            "session_context_count": len(session_context),
            "steps": [],
            "completed": False,
        }

        try:
            task_plan = self._router.route(goal_text, points)
        except (RobotApiError, RobotProtocolError, PointResolutionError, PlannerError) as exc:
            self._logger.error("task planning bootstrap failed: session_id=%s error=%s", session_id, exc)
            final_summary["error"] = str(exc)
            final_summary["stage"] = "task_planning"
            return final_summary

        final_summary["task_plan"] = task_plan.to_trace()
        self._trace_writer.write(
            "task_plan_selected",
            {
                "session_id": session_id,
                "goal_text": goal_text,
                "task_plan": task_plan.to_trace(),
            },
        )
        self._logger.info(
            "task plan selected: session_id=%s route=%s subgoal_count=%s reason=%s",
            session_id,
            task_plan.route,
            len(task_plan.subgoals),
            task_plan.reason,
        )

        global_step_index = 0
        halt_session = False
        completed_subgoal_count = 0

        for subgoal in task_plan.subgoals:
            latest_execution: dict[str, object] = {}
            subgoal_completed = False

            while not subgoal_completed and not halt_session:
                if global_step_index >= self._settings.planner.max_steps:
                    final_summary["error"] = "max_steps_exceeded_before_all_subgoals_completed"
                    final_summary["stage"] = "task_plan"
                    halt_session = True
                    break

                step_index = global_step_index + 1
                try:
                    status = self._robot_client.get_status()
                except (RobotApiError, RobotProtocolError) as exc:
                    self._logger.error(
                        "step bootstrap failed: session_id=%s step=%s error=%s",
                        session_id,
                        step_index,
                        exc,
                    )
                    final_summary["steps"].append(
                        {
                            "step_index": step_index,
                            "subgoal": subgoal.to_trace(),
                            "error": str(exc),
                            "stage": "step_bootstrap",
                        }
                    )
                    final_summary["error"] = str(exc)
                    halt_session = True
                    break

                try:
                    profile_name = self._planner.select_profile(
                        goal_text=goal_text,
                        current_subgoal=subgoal,
                        status=status,
                        points=points,
                        history=history[-self._settings.planner.history_window :],
                        session_context=session_context,
                    )
                    observation = self._capture_observation_for_planning(
                        profile_name=profile_name,
                        subgoal=subgoal,
                        session_id=session_id,
                        step_index=step_index,
                    )
                    self._logger.info(
                        "subgoal turn start: session_id=%s step=%s sequence_id=%s goal=%s selected_profile=%s hint_profile=%s status=%s",
                        session_id,
                        step_index,
                        subgoal.sequence_id,
                        subgoal.goal_text,
                        profile_name,
                        subgoal.planner_profile_hint,
                        status.short_dict(),
                    )
                    self._trace_writer.write(
                        "subgoal_turn_start",
                        {
                            "session_id": session_id,
                            "step_index": step_index,
                            "subgoal": subgoal.to_trace(),
                            "status": status.short_dict(),
                            "observation": observation.short_dict() if observation is not None else {},
                        },
                    )
                    decision = self._planner.plan(
                        goal_text=goal_text,
                        current_subgoal=subgoal,
                        task_plan=task_plan,
                        status=status,
                        observation=observation,
                        points=points,
                        history=history[-self._settings.planner.history_window :],
                        session_context=session_context,
                        latest_execution=latest_execution,
                        profile_name=profile_name,
                    )
                except (ObservationError, PlannerError, RobotApiError, RobotProtocolError, XcRebotError) as exc:
                    self._logger.error(
                        "planner phase failed: session_id=%s step=%s sequence_id=%s error=%s",
                        session_id,
                        step_index,
                        subgoal.sequence_id,
                        exc,
                    )
                    final_summary["steps"].append(
                        {
                            "step_index": step_index,
                            "subgoal": subgoal.to_trace(),
                            "error": str(exc),
                            "stage": "planner_phase",
                        }
                    )
                    final_summary["error"] = str(exc)
                    self._trace_writer.write(
                        "planner_phase_error",
                        {
                            "session_id": session_id,
                            "step_index": step_index,
                            "subgoal": subgoal.to_trace(),
                            "error": str(exc),
                        },
                    )
                    halt_session = True
                    break

                self._trace_writer.write(
                    "planner_decision",
                    {
                        "session_id": session_id,
                        "step_index": step_index,
                        "subgoal": subgoal.to_trace(),
                        "decision": decision.to_trace(),
                        "status": status.short_dict(),
                    },
                )

                try:
                    result = self._executor.execute(
                        decision.action,
                        session_id=session_id,
                        step_index=step_index,
                        observation=observation,
                    )
                except (ActionExecutionError, ObservationError, RobotApiError, RobotProtocolError, XcRebotError) as exc:
                    self._logger.error(
                        "step execution failed: session_id=%s step=%s action=%s error=%s",
                        session_id,
                        step_index,
                        decision.action_expression,
                        exc,
                    )
                    final_summary["steps"].append(
                        {
                            "step_index": step_index,
                            "subgoal": subgoal.to_trace(),
                            "decision": decision.to_trace(),
                            "error": str(exc),
                            "stage": "execution",
                        }
                    )
                    final_summary["error"] = str(exc)
                    self._trace_writer.write(
                        "execution_error",
                        {
                            "session_id": session_id,
                            "step_index": step_index,
                            "subgoal": subgoal.to_trace(),
                            "action": decision.action,
                            "action_expression": decision.action_expression,
                            "error": str(exc),
                        },
                    )
                    halt_session = True
                    break

                step_record = {
                    "step_index": step_index,
                    "subgoal": subgoal.to_trace(),
                    "decision": decision.to_trace(),
                    "execution": result.to_dict(),
                }
                final_summary["steps"].append(step_record)
                history.append(
                    {
                        "step_index": step_index,
                        "subgoal": subgoal.to_trace(),
                        "action": decision.action,
                        "action_expression": decision.action_expression,
                        "reason": decision.reason,
                        "summary": result.summary,
                        "status_after": result.status_after,
                    }
                )
                latest_execution = result.to_dict()
                self._trace_writer.write(
                    "execution_result",
                    {
                        "session_id": session_id,
                        "step_index": step_index,
                        "subgoal": subgoal.to_trace(),
                        "result": result.to_dict(),
                    },
                )
                global_step_index = step_index

                action = parse_action_payload(decision.action)

                if action.name == "finish_task":
                    subgoal_completed = True
                    completed_subgoal_count += 1
                    self._logger.info(
                        "subgoal complete by planner finish: session_id=%s step=%s sequence_id=%s goal=%s",
                        session_id,
                        step_index,
                        subgoal.sequence_id,
                        subgoal.goal_text,
                    )
                    continue

                if action.name == "stop" and subgoal.route == "stop_request" and not decision.stop:
                    subgoal_completed = True
                    completed_subgoal_count += 1
                    self._logger.info(
                        "subgoal complete by explicit stop request: session_id=%s step=%s sequence_id=%s goal=%s",
                        session_id,
                        step_index,
                        subgoal.sequence_id,
                        subgoal.goal_text,
                    )
                    continue

                if action.name == "stop":
                    final_summary["error"] = f"planner_requested_stop:{decision.reason}"
                    final_summary["stage"] = "task_plan"
                    halt_session = True
                    self._logger.warning(
                        "session halt requested: session_id=%s step=%s sequence_id=%s action=%s subgoal_state=%s",
                        session_id,
                        step_index,
                        subgoal.sequence_id,
                        decision.action_expression,
                        decision.subgoal_state,
                    )
                    break

                self._logger.info(
                    "subgoal continue after action: session_id=%s step=%s sequence_id=%s action=%s",
                    session_id,
                    step_index,
                    subgoal.sequence_id,
                    decision.action_expression,
                )

            if halt_session:
                break

        if not final_summary.get("error"):
            final_summary["completed"] = completed_subgoal_count == len(task_plan.subgoals)

        try:
            final_summary["final_status"] = self._robot_client.get_status().raw
        except (RobotApiError, RobotProtocolError) as exc:
            final_summary["final_status_error"] = str(exc)
            self._logger.error("final status fetch failed: session_id=%s error=%s", session_id, exc)
        self._trace_writer.write("session_end", final_summary)
        self._logger.info("react session end: session_id=%s completed=%s", session_id, final_summary["completed"])
        return final_summary

    def _capture_observation_for_planning(self, *, profile_name: str, subgoal, session_id: str, step_index: int):
        requires_visual_observation = profile_name in {"scene_exploration", "motion_sequence"}
        if not requires_visual_observation:
            return None
        if profile_name == "scene_exploration" and not self._settings.planner.allow_vlm_exploration:
            raise ObservationError("scene_exploration_requires_visual_observation_but_capture_disabled")
        if profile_name == "motion_sequence" and not self._settings.planner.allow_vlm_motion:
            raise ObservationError("motion_sequence_requires_visual_observation_but_capture_disabled")
        observation = self._observer.capture_scene()
        self._trace_writer.write(
            "observation_ready",
            {
                "session_id": session_id,
                "step_index": step_index,
                "subgoal": subgoal.to_trace(),
                "observation": observation.short_dict(),
            },
        )
        return observation
