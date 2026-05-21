from __future__ import annotations

import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from xc_rebot_agent.models import CaptureObservation
from xc_rebot_agent.models import PointOfInterest
from xc_rebot_agent.models import RobotStatus
from xc_rebot_agent.models import TaskSubgoal
from xc_rebot_agent.planner.action_parser import parse_action_payload
from xc_rebot_agent.planner.react_planner import ReactiveScenePlanner
from xc_rebot_agent.planner.router import GoalRouter
from xc_rebot_agent.session_memory import SessionMemoryStore


class DummyPointResolver:
    def __init__(self, points_by_goal: dict[str, PointOfInterest] | None = None):
        self._points_by_goal = points_by_goal or {}

    def resolve(self, goal_text, points):
        point = self._points_by_goal.get(goal_text)
        if point is None:
            return None
        return SimpleNamespace(
            route="navigate",
            reason="stub point match",
            confidence=0.99,
            point=point,
        )


class DummyLlmClient:
    def __init__(self, *, enabled: bool, responses: dict[str, object] | None = None, error: Exception | None = None):
        self._enabled = enabled
        self._responses = responses or {}
        self._error = error

    @property
    def enabled(self) -> bool:
        return self._enabled

    def chat_json(self, *, response_label: str, **kwargs):
        if self._error is not None:
            raise self._error
        return self._responses[response_label]


def build_settings():
    return SimpleNamespace(
        routing=SimpleNamespace(react_confidence=0.5),
        planner=SimpleNamespace(
            max_steps=12,
            history_window=6,
            confidence_floor=0.25,
            allow_llm_point_resolution=True,
            allow_vlm_exploration=True,
            force_stop_on_low_confidence=True,
        ),
        executor=SimpleNamespace(
            manual_profiles={
                "explore_forward": SimpleNamespace(),
                "explore_backward": SimpleNamespace(),
                "explore_left": SimpleNamespace(),
                "explore_right": SimpleNamespace(),
            }
        ),
        point_resolution=SimpleNamespace(minimum_confidence=0.72),
    )


def build_status(*, robot_state="idle", nav_state="", target_point_id="", localization_valid=True):
    return RobotStatus(
        robot_state=robot_state,
        nav_state=nav_state,
        target_point_id=target_point_id,
        pose={},
        battery={},
        localization={"valid": localization_valid},
        errors=(),
        raw={},
    )


def build_observation(*, raw: dict[str, object] | None = None, rgb_data_url: str = "data:image/png;base64,abc"):
    return CaptureObservation(
        image_id="img-1",
        created_at="2026-05-21T00:00:00+08:00",
        return_mode="inline",
        rgb=None,
        depth=None,
        rgb_data_url=rgb_data_url,
        depth_data_url="",
        raw=raw or {"scene": "test"},
    )


class PromptChainingTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test.prompt_chaining")
        self.settings = build_settings()

    def test_router_uses_deterministic_motion_and_stop_hints(self):
        router = GoalRouter(
            settings=self.settings,
            point_resolver=DummyPointResolver(),
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        plan = router.route("先向前一点 然后停止", [])
        self.assertEqual(len(plan.subgoals), 2)
        self.assertEqual(plan.subgoals[0].planner_profile_hint, "motion_sequence")
        self.assertEqual(
            plan.subgoals[0].action,
            {"name": "move_forward", "args": {"profile_name": "explore_forward"}},
        )
        self.assertEqual(plan.subgoals[0].action_expression, "move_forward('explore_forward')")
        self.assertEqual(plan.subgoals[1].route, "stop_request")
        self.assertEqual(
            plan.subgoals[1].action,
            {"name": "stop", "args": {"reason_key": "explicit_stop_request"}},
        )
        self.assertEqual(plan.subgoals[1].action_expression, "stop('explicit_stop_request')")

    def test_parse_action_payload_accepts_structured_action(self):
        action = parse_action_payload(
            {
                "name": "navigate",
                "args": {"point_id": "work"},
            }
        )
        self.assertEqual(action.name, "navigate")
        self.assertEqual(action.arguments, ("work",))

    def test_router_falls_back_to_deterministic_decomposition_when_llm_fails(self):
        router = GoalRouter(
            settings=self.settings,
            point_resolver=DummyPointResolver(),
            llm_client=DummyLlmClient(enabled=True, error=RuntimeError("bad llm output")),
            logger=self.logger,
        )
        plan = router.route("去 work 然后回 home", [])
        self.assertEqual([subgoal.goal_text for subgoal in plan.subgoals], ["去 work", "回 home"])

    def test_router_attaches_navigation_hint_when_point_matches(self):
        work = PointOfInterest(point_id="work", name="Work")
        router = GoalRouter(
            settings=self.settings,
            point_resolver=DummyPointResolver({"去工位": work}),
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        plan = router.route("去工位", [work])
        self.assertEqual(plan.subgoals[0].route, "navigation_hint")
        self.assertEqual(plan.subgoals[0].planner_profile_hint, "navigation_sequence")
        self.assertEqual(plan.subgoals[0].point.point_id, "work")

    def test_planner_uses_router_profile_hint_without_extra_selector(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(enabled=True, responses={}),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="manual_motion",
            goal_text="向前一点",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward')",
        )
        profile = planner.select_profile(
            goal_text="向前一点",
            current_subgoal=subgoal,
            status=build_status(),
            points=[],
            history=[],
            session_context=[],
        )
        self.assertEqual(profile, "motion_sequence")

    def test_planner_fallback_executes_explicit_motion_without_llm(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="manual_motion",
            goal_text="向前一点",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward')",
        )
        decision = planner.plan(
            goal_text="向前一点",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(to_trace=lambda: {"route": "manual_motion"}),
            status=build_status(),
            observation=None,
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "move_forward('explore_forward')")
        self.assertEqual(
            decision.action,
            {"name": "move_forward", "args": {"profile_name": "explore_forward"}},
        )
        self.assertEqual(decision.subgoal_state, "continue")

    def test_planner_without_profile_name_still_uses_router_hint(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="manual_motion",
            goal_text="向前一点",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward')",
        )
        decision = planner.plan(
            goal_text="向前一点",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(to_trace=lambda: {"route": "manual_motion"}),
            status=build_status(),
            observation=None,
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="",
        )
        self.assertEqual(decision.action_expression, "move_forward('explore_forward')")

    def test_planner_fallback_finishes_stop_when_robot_already_idle(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="stop_request",
            goal_text="停止",
            reason="deterministic stop",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="stop('explicit_stop_request')",
        )
        decision = planner.plan(
            goal_text="停止",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(to_trace=lambda: {"route": "stop_request"}),
            status=build_status(robot_state="idle", nav_state=""),
            observation=None,
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "finish_task()")
        self.assertEqual(decision.action, {"name": "finish_task", "args": {}})
        self.assertEqual(decision.subgoal_state, "completed")

    def test_planner_fallback_executes_explicit_stop_request(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="stop_request",
            goal_text="停止",
            reason="deterministic stop",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="stop('explicit_stop_request')",
        )
        decision = planner.plan(
            goal_text="停止",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(to_trace=lambda: {"route": "stop_request"}),
            status=build_status(robot_state="manual"),
            observation=None,
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "stop('explicit_stop_request')")
        self.assertEqual(decision.action, {"name": "stop", "args": {"reason_key": "explicit_stop_request"}})
        self.assertEqual(decision.subgoal_state, "completed")
        self.assertFalse(decision.stop)

    def test_scene_exploration_blocks_forward_without_visual_evidence(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:scene_exploration": {
                        "response": "I will move forward.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward"}},
                        "reason": "Try a forward pulse.",
                        "confidence": 0.9,
                        "observation_focus": "scene",
                        "target_hint": "",
                        "subgoal_state": "continue",
                        "stop": False,
                    }
                },
            ),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="scene_exploration",
            goal_text="去看看前面是什么",
            reason="needs exploration",
            confidence=0.5,
            planner_profile_hint="scene_exploration",
        )
        decision = planner.plan(
            goal_text="去看看前面是什么",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="scene_exploration", subgoals=(subgoal,)),
            status=build_status(),
            observation=None,
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="scene_exploration",
        )
        self.assertEqual(decision.action_expression, "stop('planner_blocked')")
        self.assertTrue(decision.stop)

    def test_scene_exploration_downgrades_low_confidence_forward_to_scan(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:scene_exploration": {
                        "response": "I will move forward.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward"}},
                        "reason": "Try a forward pulse.",
                        "confidence": 0.4,
                        "observation_focus": "scene",
                        "target_hint": "",
                        "subgoal_state": "continue",
                        "stop": False,
                    }
                },
            ),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="scene_exploration",
            goal_text="向前探索一下",
            reason="needs exploration",
            confidence=0.5,
            planner_profile_hint="scene_exploration",
        )
        decision = planner.plan(
            goal_text="向前探索一下",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="scene_exploration", subgoals=(subgoal,)),
            status=build_status(),
            observation=build_observation(),
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="scene_exploration",
        )
        self.assertEqual(decision.action_expression, "turn_left('explore_left')")
        self.assertEqual(decision.action, {"name": "turn_left", "args": {"profile_name": "explore_left"}})
        self.assertEqual(decision.subgoal_state, "continue")

    def test_llm_payload_uses_compact_task_and_subgoal_summaries(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(enabled=True, responses={}),
            logger=self.logger,
        )
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="navigation_hint",
            goal_text="去工位",
            reason="matched point",
            confidence=0.9,
            planner_profile_hint="navigation_sequence",
            point=PointOfInterest(point_id="work", name="Work"),
        )
        task_plan = SimpleNamespace(
            route="task_plan",
            subgoals=(
                subgoal,
                TaskSubgoal(
                    sequence_id=2,
                    route="navigation_hint",
                    goal_text="回家",
                    reason="matched point",
                    confidence=0.9,
                    planner_profile_hint="navigation_sequence",
                    point=PointOfInterest(point_id="home", name="Home"),
                ),
            ),
        )
        payload = planner._build_llm_payload(
            goal_text="去工位然后回家",
            current_subgoal=subgoal,
            task_plan=task_plan,
            status=build_status(),
            observation=None,
            points=[PointOfInterest(point_id="work", name="Work")],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="navigation_sequence",
        )
        self.assertIn("task_plan_summary", payload)
        self.assertIn("current_subgoal_summary", payload)
        self.assertNotIn("task_plan", payload)
        self.assertNotIn("current_subgoal", payload)
        self.assertEqual(payload["task_plan_summary"]["subgoal_count"], 2)
        self.assertTrue(payload["task_plan_summary"]["has_later_subgoals"])
        self.assertEqual(payload["current_subgoal_summary"]["goal_text"], "去工位")
        self.assertEqual(payload["current_subgoal_summary"]["point_hint_point_id"], "work")

    def test_session_memory_recent_context_is_compact(self):
        store = SessionMemoryStore(path=Path("tests/.tmp_session_memory.jsonl"), logger=self.logger)
        try:
            store.clear()
            store.record_turn(
                shell_session_id="shell-1",
                mode="stateful",
                goal_text="去工位",
                fed_to_agent=True,
                summary={
                    "completed": True,
                    "task_plan": {"route": "task_plan"},
                    "final_status": {
                        "robot_state": "idle",
                        "nav": {"state": "succeeded", "target_point_id": "work"},
                        "localization": {"valid": True},
                    },
                    "steps": [
                        {
                            "step_index": 1,
                            "subgoal": {"goal_text": "去工位"},
                            "decision": {"subgoal_state": "completed"},
                            "execution": {
                                "action": {"name": "navigate", "args": {"point_id": "work"}},
                                "action_expression": "navigate('work')",
                                "summary": "navigation to work succeeded",
                            },
                        }
                    ],
                },
            )
            context = store.recent_context(limit=1)
            self.assertEqual(len(context), 1)
            self.assertEqual(context[0]["step_count"], 1)
            self.assertEqual(context[0]["task_plan_route"], "task_plan")
            self.assertEqual(context[0]["last_step"]["action"], {"name": "navigate", "args": {"point_id": "work"}})
            self.assertEqual(context[0]["last_step"]["action_expression"], "navigate('work')")
            self.assertNotIn("steps", context[0])
        finally:
            store.clear()
            store.path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
