from __future__ import annotations

import ast
from dataclasses import dataclass

from ..errors import PlannerError

SUPPORTED_ACTION_NAMES = {
    "move_forward",
    "move_backward",
    "turn_left",
    "turn_right",
    "navigate",
    "stop",
    "finish_task",
}

ACTION_LIBRARY_TEXT = (
    "Available executable atomic actions: "
    "Return one structured action object with keys name and args. "
    "Allowed action names are move_forward, move_backward, turn_left, turn_right, navigate, stop, and finish_task. "
    "For move_forward/move_backward/turn_left/turn_right, args must contain profile_name using a configured manual profile "
    "such as explore_forward, explore_backward, explore_left, or explore_right. "
    "For navigate, args must contain point_id from /points. "
    "For stop, args may contain reason_key. "
    "For finish_task, args should be an empty object."
)


@dataclass(frozen=True)
class ActionCall:
    name: str
    arguments: tuple[object, ...]


def action_call_to_payload(action: ActionCall) -> dict[str, object]:
    if action.name in {"move_forward", "move_backward", "turn_left", "turn_right"}:
        return {"name": action.name, "args": {"profile_name": str(action.arguments[0])}}
    if action.name == "navigate":
        return {"name": action.name, "args": {"point_id": str(action.arguments[0])}}
    if action.name == "stop":
        if action.arguments:
            return {"name": action.name, "args": {"reason_key": str(action.arguments[0])}}
        return {"name": action.name, "args": {}}
    if action.name == "finish_task":
        return {"name": action.name, "args": {}}
    raise PlannerError(f"unsupported_action:{action.name}")


def parse_action_expression(expression: str) -> ActionCall:
    try:
        parsed = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise PlannerError(f"invalid_action_expression:{expression}") from exc
    call = parsed.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.keywords:
        raise PlannerError(f"invalid_action_expression:{expression}")
    arguments = tuple(ast.literal_eval(arg) for arg in call.args)
    action = ActionCall(name=call.func.id, arguments=arguments)
    validate_action_call(action)
    return action


def format_action_call(action: ActionCall) -> str:
    if not action.arguments:
        return f"{action.name}()"
    rendered = ", ".join(repr(argument) for argument in action.arguments)
    return f"{action.name}({rendered})"


def validate_action_call(action: ActionCall) -> None:
    if action.name not in SUPPORTED_ACTION_NAMES:
        raise PlannerError(f"unsupported_action:{action.name}")
    if action.name in {"move_forward", "move_backward", "turn_left", "turn_right"}:
        if len(action.arguments) != 1 or not isinstance(action.arguments[0], str) or not action.arguments[0].strip():
            raise PlannerError(f"invalid_motion_action_arguments:{action.name}")
        return
    if action.name == "navigate":
        if len(action.arguments) != 1 or not isinstance(action.arguments[0], str) or not action.arguments[0].strip():
            raise PlannerError("invalid_navigate_action_arguments")
        return
    if action.name == "stop":
        if len(action.arguments) > 1:
            raise PlannerError("invalid_stop_action_arguments")
        if action.arguments and (not isinstance(action.arguments[0], str) or not action.arguments[0].strip()):
            raise PlannerError("invalid_stop_action_arguments")
        return
    if action.name == "finish_task" and action.arguments:
        raise PlannerError("invalid_finish_task_arguments")


def parse_action_payload(payload: object) -> ActionCall:
    if isinstance(payload, str):
        return parse_action_expression(payload)
    if not isinstance(payload, dict):
        raise PlannerError("planner_action_missing")
    name = str(payload.get("name", "") or "").strip()
    args = payload.get("args", {})
    action = ActionCall(name=name, arguments=_parse_action_args(name, args))
    validate_action_call(action)
    return action


def _parse_action_args(name: str, args: object) -> tuple[object, ...]:
    if name in {"move_forward", "move_backward", "turn_left", "turn_right"}:
        if not isinstance(args, dict):
            raise PlannerError(f"invalid_motion_action_arguments:{name}")
        profile_name = str(args.get("profile_name", "") or "").strip()
        return (profile_name,)
    if name == "navigate":
        if not isinstance(args, dict):
            raise PlannerError("invalid_navigate_action_arguments")
        point_id = str(args.get("point_id", "") or "").strip()
        return (point_id,)
    if name == "stop":
        if args in ({}, None):
            return ()
        if not isinstance(args, dict):
            raise PlannerError("invalid_stop_action_arguments")
        reason_key = str(args.get("reason_key", "") or "").strip()
        return (reason_key,) if reason_key else ()
    if name == "finish_task":
        if args not in ({}, None):
            raise PlannerError("invalid_finish_task_arguments")
        return ()
    raise PlannerError(f"unsupported_action:{name}")
