from __future__ import annotations

import time
from datetime import datetime

from ..constants import TERMINAL_NAV_STATES
from ..errors import ActionExecutionError
from ..models import ExecutionResult
from ..planner.action_parser import action_call_to_payload
from ..planner.action_parser import format_action_call
from ..planner.action_parser import parse_action_payload


class SynchronousActionExecutor:
    def __init__(self, *, settings, robot_client, logger):
        self._settings = settings
        self._robot_client = robot_client
        self._logger = logger

    def execute(self, action_payload: object, *, session_id: str = "", step_index: int = 0):
        action = parse_action_payload(action_payload)
        action_expression = format_action_call(action)
        started_at = datetime.now().astimezone()
        status_before = self._robot_client.get_status()
        events: list[dict[str, object]] = [
            {
                "stage": "before",
                "session_id": session_id,
                "step_index": step_index,
                "status": status_before.short_dict(),
            }
        ]
        self._logger.info(
            "executor step start: session_id=%s step=%s action=%s status_before=%s",
            session_id,
            step_index,
            action_expression,
            status_before.short_dict(),
        )
        self._assert_action_allowed(
            action,
            status_before=status_before,
            session_id=session_id,
            step_index=step_index,
        )

        if action.name in {"move_forward", "move_backward", "turn_left", "turn_right"}:
            status_after, summary = self._execute_manual_action(
                action,
                session_id=session_id,
                step_index=step_index,
                events=events,
            )
        elif action.name == "navigate":
            status_after, summary = self._execute_navigation(
                action,
                session_id=session_id,
                step_index=step_index,
                events=events,
            )
        elif action.name == "stop":
            status_after, summary = self._execute_stop(
                action,
                session_id=session_id,
                step_index=step_index,
                events=events,
            )
        elif action.name == "finish_task":
            status_after = status_before
            summary = "planner finished without sending a robot motion command"
        else:
            raise ActionExecutionError(f"unsupported_executor_action:{action.name}")

        events.append(
            {
                "stage": "after",
                "session_id": session_id,
                "step_index": step_index,
                "status": status_after.short_dict(),
            }
        )
        finished_at = datetime.now().astimezone()
        self._logger.info(
            "executor step done: session_id=%s step=%s action=%s summary=%s status_after=%s",
            session_id,
            step_index,
            action_expression,
            summary,
            status_after.short_dict(),
        )
        return ExecutionResult.timestamps(
            action=action_call_to_payload(action),
            action_expression=action_expression,
            ok=True,
            summary=summary,
            status_before=status_before.short_dict(),
            status_after=status_after.short_dict(),
            events=events,
            started_at=started_at,
            finished_at=finished_at,
        )

    def _assert_action_allowed(self, action, *, status_before, session_id: str, step_index: int) -> None:
        if action.name == "finish_task":
            return
        if action.name == "stop":
            return
        if status_before.errors:
            raise ActionExecutionError(
                f"action_blocked_robot_errors:{action.name}:{list(status_before.errors)}"
            )
        if action.name == "navigate":
            if not status_before.localization_valid:
                raise ActionExecutionError("action_blocked_localization_invalid:navigate")
            if status_before.robot_state == "manual":
                raise ActionExecutionError("action_blocked_robot_manual_active:navigate")
            if status_before.is_navigation_active:
                raise ActionExecutionError(
                    f"action_blocked_navigation_already_active:navigate:{status_before.target_point_id}"
                )
            return
        if action.name in {"move_forward", "move_backward", "turn_left", "turn_right"}:
            if status_before.is_navigation_active:
                raise ActionExecutionError(
                    f"action_blocked_navigation_already_active:{action.name}:{status_before.target_point_id}"
                )
            if status_before.robot_state == "manual":
                raise ActionExecutionError(f"action_blocked_robot_manual_active:{action.name}")
            self._logger.info(
                "executor local safety check ok: session_id=%s step=%s action=%s",
                session_id,
                step_index,
                action.name,
            )
            return

    def _execute_manual_action(self, action, *, session_id: str, step_index: int, events: list[dict[str, object]]):
        profile_name = str(action.arguments[0]).strip()
        profile = self._settings.executor.manual_profiles[profile_name]
        self._logger.info(
            "manual action start: session_id=%s step=%s action=%s profile=%s endpoint=%s pulse=%.2fs settle=%.2fs",
            session_id,
            step_index,
            action.name,
            profile_name,
            profile.endpoint,
            profile.pulse_sec,
            profile.settle_sec,
        )
        move_ack = self._robot_client.move(profile.endpoint, speed_level=profile.speed_level)
        events.append(
            {
                "stage": "manual_command_sent",
                "session_id": session_id,
                "step_index": step_index,
                "endpoint": profile.endpoint,
                "speed_level": profile.speed_level,
                "api_ack": move_ack.to_trace(),
            }
        )
        transition_status = self._wait_for_status(
            description=f"manual state {profile.status_expect_state}",
            timeout_sec=self._settings.robot_api.status_transition_timeout_sec,
            poll_interval_sec=self._settings.robot_api.status_poll_interval_sec,
            predicate=lambda status: status.robot_state == profile.status_expect_state,
            session_id=session_id,
            step_index=step_index,
            events=events,
        )
        self._logger.info("manual transition confirmed: %s", transition_status.short_dict())
        time.sleep(profile.pulse_sec)
        events.append(
            {
                "stage": "manual_pulse_elapsed",
                "session_id": session_id,
                "step_index": step_index,
                "pulse_sec": profile.pulse_sec,
            }
        )
        stop_ack = self._robot_client.stop(reason=self._settings.executor.stop.reason)
        events.append(
            {
                "stage": "manual_stop_sent",
                "session_id": session_id,
                "step_index": step_index,
                "reason": self._settings.executor.stop.reason,
                "api_ack": stop_ack.to_trace(),
            }
        )
        if profile.settle_sec > 0.0:
            time.sleep(profile.settle_sec)
            events.append(
                {
                    "stage": "manual_settle_elapsed",
                    "session_id": session_id,
                    "step_index": step_index,
                    "settle_sec": profile.settle_sec,
                }
            )
        final_status = self._wait_for_status(
            description="manual clear",
            timeout_sec=self._settings.executor.stop.transition_timeout_sec,
            poll_interval_sec=self._settings.executor.stop.poll_interval_sec,
            predicate=lambda status: status.robot_state != "manual" and not status.is_navigation_active,
            session_id=session_id,
            step_index=step_index,
            events=events,
        )
        return final_status, f"manual action {action.name} completed synchronously"

    def _execute_navigation(self, action, *, session_id: str, step_index: int, events: list[dict[str, object]]):
        point_id = str(action.arguments[0]).strip()
        self._logger.info(
            "navigation start: session_id=%s step=%s point_id=%s",
            session_id,
            step_index,
            point_id,
        )
        navigate_ack = self._robot_client.navigate(point_id=point_id)
        events.append(
            {
                "stage": "navigate_command_sent",
                "session_id": session_id,
                "step_index": step_index,
                "point_id": point_id,
                "api_ack": navigate_ack.to_trace(),
            }
        )
        first_status = self._wait_for_status(
            description="navigation accepted",
            timeout_sec=self._settings.robot_api.status_transition_timeout_sec,
            poll_interval_sec=self._settings.robot_api.status_poll_interval_sec,
            predicate=lambda status: status.nav_state in TERMINAL_NAV_STATES or status.is_navigation_active,
            session_id=session_id,
            step_index=step_index,
            events=events,
        )
        if first_status.nav_state == "succeeded":
            return first_status, f"navigation to {point_id} completed immediately"
        final_status = self._wait_for_status(
            description="navigation terminal state",
            timeout_sec=self._settings.robot_api.navigation_timeout_sec,
            poll_interval_sec=self._settings.robot_api.status_poll_interval_sec,
            predicate=lambda status: status.nav_state in TERMINAL_NAV_STATES,
            session_id=session_id,
            step_index=step_index,
            events=events,
        )
        if final_status.nav_state != "succeeded":
            raise ActionExecutionError(f"navigation_failed:{point_id}:{final_status.nav_state}")
        if self._settings.robot_api.navigation_terminal_grace_sec > 0.0:
            time.sleep(self._settings.robot_api.navigation_terminal_grace_sec)
            events.append(
                {
                    "stage": "navigation_terminal_grace_elapsed",
                    "session_id": session_id,
                    "step_index": step_index,
                    "grace_sec": self._settings.robot_api.navigation_terminal_grace_sec,
                }
            )
        return final_status, f"navigation to {point_id} succeeded"

    def _execute_stop(self, action, *, session_id: str, step_index: int, events: list[dict[str, object]]):
        reason = str(action.arguments[0]).strip() if action.arguments else self._settings.executor.stop.reason
        self._logger.info(
            "stop action start: session_id=%s step=%s reason=%s",
            session_id,
            step_index,
            reason,
        )
        stop_ack = self._robot_client.stop(reason=reason)
        events.append(
            {
                "stage": "stop_command_sent",
                "session_id": session_id,
                "step_index": step_index,
                "reason": reason,
                "api_ack": stop_ack.to_trace(),
            }
        )
        final_status = self._wait_for_status(
            description="stop settled",
            timeout_sec=self._settings.executor.stop.transition_timeout_sec,
            poll_interval_sec=self._settings.executor.stop.poll_interval_sec,
            predicate=lambda status: status.robot_state != "manual" and not status.is_navigation_active,
            session_id=session_id,
            step_index=step_index,
            events=events,
        )
        return final_status, "stop action confirmed"

    def _wait_for_status(
        self,
        *,
        description: str,
        timeout_sec: float,
        poll_interval_sec: float,
        predicate,
        session_id: str,
        step_index: int,
        events: list[dict[str, object]],
    ):
        started = time.monotonic()
        last_status = self._robot_client.get_status()
        events.append(
            {
                "stage": "wait_status_begin",
                "session_id": session_id,
                "step_index": step_index,
                "description": description,
                "status": last_status.short_dict(),
            }
        )
        self._logger.info(
            "wait status begin: session_id=%s step=%s description=%s timeout=%.2fs poll=%.2fs status=%s",
            session_id,
            step_index,
            description,
            timeout_sec,
            poll_interval_sec,
            last_status.short_dict(),
        )
        last_logged_signature = repr(last_status.short_dict())
        while True:
            if predicate(last_status):
                events.append(
                    {
                        "stage": "wait_status_done",
                        "session_id": session_id,
                        "step_index": step_index,
                        "description": description,
                        "status": last_status.short_dict(),
                        "elapsed_sec": round(time.monotonic() - started, 3),
                    }
                )
                self._logger.info(
                    "wait status done: session_id=%s step=%s description=%s elapsed=%.3fs status=%s",
                    session_id,
                    step_index,
                    description,
                    time.monotonic() - started,
                    last_status.short_dict(),
                )
                return last_status
            if time.monotonic() - started >= timeout_sec:
                events.append(
                    {
                        "stage": "wait_status_timeout",
                        "session_id": session_id,
                        "step_index": step_index,
                        "description": description,
                        "status": last_status.short_dict(),
                        "elapsed_sec": round(time.monotonic() - started, 3),
                    }
                )
                raise ActionExecutionError(
                    f"status_wait_timeout:{description}:{last_status.short_dict()}"
                )
            time.sleep(poll_interval_sec)
            last_status = self._robot_client.get_status()
            current_signature = repr(last_status.short_dict())
            if current_signature != last_logged_signature:
                last_logged_signature = current_signature
                events.append(
                    {
                        "stage": "wait_status_progress",
                        "session_id": session_id,
                        "step_index": step_index,
                        "description": description,
                        "status": last_status.short_dict(),
                        "elapsed_sec": round(time.monotonic() - started, 3),
                    }
                )
                self._logger.info(
                    "wait status progress: session_id=%s step=%s description=%s elapsed=%.3fs status=%s",
                    session_id,
                    step_index,
                    description,
                    time.monotonic() - started,
                    last_status.short_dict(),
                )
