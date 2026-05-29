from __future__ import annotations


def build_task_decomposition_contract() -> dict[str, object]:
    return {
        "goal": "Decompose the user goal into an ordered list of semantic subgoals.",
        "subgoal_rule": "Each subgoal must represent one stage of execution and preserve user order.",
        "minimality_rule": "If the goal is already atomic, return one subgoal instead of splitting aggressively.",
        "fallback_rule": "deterministic_fallback_subgoals is the conservative baseline decomposition to preserve when no better semantic split is justified.",
        "parsing_rule": "Return valid JSON only.",
    }


def build_navigation_sequence_workflow_contract() -> dict[str, object]:
    return {
        "goal_priority": "Complete the current ordered navigation subgoal only.",
        "react_rule": "Observe the newest status/result, choose one atomic action, execute, then inspect the next real result.",
        "router_hint_rule": "current_subgoal.route and planner_profile_hint are deterministic upstream constraints and should be respected unless the newest status makes them unsafe.",
        "evidence_rule": "Use fresh_runtime_evidence as the first-pass compact evidence layer, then consult robot_status for detail only when needed.",
        "point_rule": "If a known point already satisfies the current subgoal, prefer navigate(point_id).",
        "map_truth_rule": "Treat known_points and current_navigation_context as the authoritative map/navigation context for this turn.",
        "completion_signal_rule": "Use subgoal_runtime_context.target_already_reached and latest_execution_matches_point_hint before sending another navigation command.",
        "action_constraint_rule": "Allowed actions are navigate, stop, or finish_task only.",
        "state_constraint_rule": "Use subgoal_state=continue for navigate, subgoal_state=completed for finish_task, and subgoal_state=blocked for stop.",
        "completion_rule": "If the newest status shows the current target already succeeded, return finish_task() instead of navigating again.",
        "serial_rule": "Do not jump to a later ordered subgoal before the current one is complete.",
        "parsing_rule": "Return valid JSON only and keep action as an object with keys name and args.",
    }


def build_motion_sequence_workflow_contract() -> dict[str, object]:
    return {
        "goal_priority": "Complete the current explicit chassis subgoal only.",
        "react_rule": "Observe the newest image and result, choose one conservative atomic chassis action, execute it synchronously, then evaluate again.",
        "router_hint_rule": "If current_subgoal.action or router_action_hint is present, it is the preferred deterministic action for this subgoal unless the newest evidence makes it unsafe.",
        "evidence_rule": "Use fresh_runtime_evidence as the first-pass compact evidence layer, treat structured scene understanding as the primary safety evidence when available, and treat the newest visual observation as required safety evidence before non-stop motion.",
        "motion_rule": "Use configured semantic move_* or turn_* actions instead of inventing raw physical constants, and keep each explicit move or turn conservative enough to re-check safety on the next turn.",
        "action_constraint_rule": "Allowed actions are move_forward, move_backward, turn_left, turn_right, stop, or finish_task only.",
        "state_constraint_rule": "Use subgoal_state=continue for move_*/turn_* actions, subgoal_state=completed for finish_task or an explicit stop_request stop, and subgoal_state=blocked for fail-closed stop.",
        "completion_rule": "If the previous synchronous motion already completed the current subgoal, return finish_task().",
        "safety_rule": "Use stop(reason_key) when structured scene understanding or fresh visual evidence is missing, weak, or suggests that continuing the motion could be unsafe.",
        "parsing_rule": "Return valid JSON only and keep action as an object with keys name and args.",
    }


def build_scene_exploration_workflow_contract() -> dict[str, object]:
    return {
        "goal_priority": "Use the newest real observation and result to improve visibility, approach, or navigation progress safely.",
        "react_rule": "Operate one strict serial step at a time: observe, choose one action, execute, then observe again.",
        "freshness_rule": "The newest status, newest execution result, and newest image are the source of truth for this turn.",
        "evidence_rule": "Use fresh_runtime_evidence as the compact primary evidence layer, prioritize structured scene understanding when available, and use memory_digest only as background context.",
        "completion_rule": "Only finish when the newest evidence confirms the current subgoal is complete; otherwise stop if blocked.",
        "navigation_rule": "If a map point can now satisfy the current subgoal, use navigate(point_id) instead of more blind search.",
        "map_truth_rule": "known_points and current_navigation_context are available every turn and must be consulted before deciding that navigation is impossible or unnecessary.",
        "state_constraint_rule": "Use subgoal_state=continue while still exploring, subgoal_state=completed only with finish_task, and subgoal_state=blocked with stop.",
        "safety_rule": "If the evidence is weak or unsafe before completion, choose stop(reason_key) instead of guessing; do not use move_forward without fresh visual evidence, structured scene approval when available, and a clear confidence margin.",
        "serial_rule": "Every action must depend on the newest confirmed result from the immediately previous action.",
        "parsing_rule": "Return valid JSON only and keep action as an object with keys name and args.",
    }


def build_step_planner_workflow_contract(prompt_profile: str) -> dict[str, object]:
    if prompt_profile == "navigation_sequence":
        return build_navigation_sequence_workflow_contract()
    if prompt_profile == "motion_sequence":
        return build_motion_sequence_workflow_contract()
    return build_scene_exploration_workflow_contract()


def build_point_resolution_contract() -> dict[str, object]:
    return {
        "goal": "Select at most one point_id that best matches the user goal.",
        "rule": "Prefer exact or near-exact semantic matches. If nothing is reliable, return null.",
        "parsing_rule": "Return valid JSON only.",
    }
