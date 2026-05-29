from __future__ import annotations

import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

from xc_rebot_agent.clients.robot_api import RobotApiClient
from xc_rebot_agent.models import CaptureObservation
from xc_rebot_agent.models import CaptureAsset
from xc_rebot_agent.models import PointOfInterest
from xc_rebot_agent.models import RobotStatus
from xc_rebot_agent.models import TaskSubgoal
from xc_rebot_agent.models import VisualSceneUnderstanding
from xc_rebot_agent.errors import ObservationError
from xc_rebot_agent.planner.action_parser import parse_action_payload
from xc_rebot_agent.planner.react_planner import ReactiveScenePlanner
from xc_rebot_agent.planner.router import GoalRouter
from xc_rebot_agent.interactive_cli import InteractiveGoalCli
from xc_rebot_agent.runtime.executor import SynchronousActionExecutor
from xc_rebot_agent.runtime.observer import ObservationProvider
from xc_rebot_agent.session_memory import SessionMemoryStore
from xc_rebot_agent.workflows.react_agent import ReactAgent


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


class DummyObserver:
    def __init__(self):
        self.capture_count = 0

    def capture_scene(self):
        self.capture_count += 1
        return build_observation()


class DummyRobotClient:
    def __init__(self, observation):
        self._observation = observation

    def capture(self):
        return self._observation

    def read_local_file(self, _path):
        raise AssertionError("read_local_file should not be called in this test")

    def download_binary(self, _url):
        raise AssertionError("download_binary should not be called in this test")


class DummyTraceWriter:
    def write(self, *_args, **_kwargs):
        return None


class DummyAgent:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def run(self, goal_text: str, *, session_context=None, external_session_id=""):
        self.calls.append(
            {
                "goal_text": goal_text,
                "session_context": list(session_context or []),
                "external_session_id": external_session_id,
            }
        )
        return {"completed": True, "task_plan": {"route": "task_plan"}, "steps": [], "final_status": {}}


def build_settings():
    return SimpleNamespace(
        session_prefix="xc-rebot",
        robot_api=SimpleNamespace(
            capture=SimpleNamespace(
                enabled=True,
                include_depth=True,
                return_mode="url",
                request_timeout_sec=15.0,
                prefer_inline_if_available=True,
            )
        ),
        routing=SimpleNamespace(react_confidence=0.5),
        planner=SimpleNamespace(
            max_steps=12,
            history_window=6,
            confidence_floor=0.25,
            allow_llm_point_resolution=True,
            allow_vlm_exploration=True,
            allow_vlm_motion=True,
            force_stop_on_low_confidence=True,
        ),
        vision=SimpleNamespace(
            enabled=False,
            api_url="",
            api_key="",
            backend="",
            request_timeout_sec=20.0,
            fail_closed=True,
            require_depth=True,
            prefer_structured_scene=True,
            minimum_confidence=0.55,
            minimum_forward_clearance_m=0.75,
            minimum_backward_clearance_m=0.45,
            maximum_rotation_risk_confidence=0.35,
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
        scene_understanding=None,
        raw=raw or {"scene": "test"},
    )


def build_scene_understanding(
    *,
    safe_to_advance: bool | None = True,
    safe_to_retreat: bool | None = True,
    safe_to_rotate: bool | None = True,
    target_visible: bool | None = True,
    target_centered: bool | None = False,
    depth_reliable: bool | None = True,
    requires_caution: bool | None = False,
    confidence: float = 0.9,
    forward_clearance_m: float | None = 1.2,
    rear_clearance_m: float | None = 0.8,
    recommended_action: str = "",
    recommended_profile_name: str = "",
):
    return {
        "summary": "scene ok",
        "confidence": confidence,
        "targets": [
            {
                "label": "target",
                "confidence": 0.88,
                "track_id": "track-1",
                "bbox": {"x": 0.4, "y": 0.3, "w": 0.2, "h": 0.2},
                "center_depth_m": 1.1,
                "median_depth_m": 1.15,
                "min_depth_m": 1.0,
                "horizontal_offset_norm": 0.05,
                "vertical_offset_norm": 0.02,
            }
        ],
        "obstacles": {
            "forward_clearance_m": forward_clearance_m,
            "left_clearance_m": 0.9,
            "right_clearance_m": 0.85,
            "rear_clearance_m": rear_clearance_m,
        },
        "scene_flags": {
            "safe_to_advance": safe_to_advance,
            "safe_to_retreat": safe_to_retreat,
            "safe_to_rotate": safe_to_rotate,
            "target_visible": target_visible,
            "target_centered": target_centered,
            "depth_reliable": depth_reliable,
            "requires_caution": requires_caution,
        },
        "recommended_action": recommended_action,
        "recommended_profile_name": recommended_profile_name,
        "risk_reasons": ["obstacle_near"] if safe_to_advance is False else [],
    }


def build_structured_observation(**scene_kwargs):
    observation = build_observation()
    return CaptureObservation(
        image_id=observation.image_id,
        created_at=observation.created_at,
        return_mode=observation.return_mode,
        rgb=observation.rgb,
        depth=observation.depth,
        rgb_data_url=observation.rgb_data_url,
        depth_data_url=observation.depth_data_url or "data:image/png;base64,depth",
        scene_understanding=VisualSceneUnderstanding.from_api(build_scene_understanding(**scene_kwargs)),
        raw=observation.raw,
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

    def test_router_prefers_scene_exploration_for_search_goal_with_motion_words(self):
        router = GoalRouter(
            settings=self.settings,
            point_resolver=DummyPointResolver(),
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        plan = router.route("寻找门，必要时小角度转动并缓慢前进，告诉我门在那", [])
        self.assertEqual(len(plan.subgoals), 1)
        self.assertEqual(plan.subgoals[0].route, "scene_exploration")
        self.assertEqual(plan.subgoals[0].planner_profile_hint, "scene_exploration")
        self.assertEqual(plan.subgoals[0].action, {})
        self.assertEqual(plan.subgoals[0].action_expression, "")

    def test_parse_action_payload_accepts_structured_action(self):
        action = parse_action_payload(
            {
                "name": "navigate",
                "args": {"point_id": "work"},
            }
        )
        self.assertEqual(action.name, "navigate")
        self.assertEqual(action.arguments, ("work",))

    def test_parse_action_payload_accepts_motion_distance(self):
        action = parse_action_payload(
            {
                "name": "move_forward",
                "args": {"profile_name": "explore_forward", "distance_m": 0.3},
            }
        )
        self.assertEqual(action.name, "move_forward")
        self.assertEqual(action.arguments, ("explore_forward", 0.3))

    def test_router_preserves_explicit_forward_distance(self):
        router = GoalRouter(
            settings=self.settings,
            point_resolver=DummyPointResolver(),
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        one_meter = router.route("往前1m", [])
        thirty_cm = router.route("往前30cm", [])
        self.assertEqual(len(one_meter.subgoals), 4)
        self.assertEqual(
            [subgoal.action for subgoal in one_meter.subgoals],
            [
                {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
                {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
                {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
                {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.1}},
            ],
        )
        self.assertEqual(
            thirty_cm.subgoals[0].action,
            {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
        )
        self.assertEqual(thirty_cm.subgoals[0].action_expression, "move_forward('explore_forward', 0.3)")

    def test_router_clamps_explicit_turn_angle_to_conservative_step(self):
        router = GoalRouter(
            settings=self.settings,
            point_resolver=DummyPointResolver(),
            llm_client=DummyLlmClient(enabled=False),
            logger=self.logger,
        )
        plan = router.route("右转90度", [])
        self.assertEqual(len(plan.subgoals), 3)
        self.assertEqual(
            [subgoal.action for subgoal in plan.subgoals],
            [
                {"name": "turn_right", "args": {"profile_name": "explore_right", "angle_deg": 30.0}},
                {"name": "turn_right", "args": {"profile_name": "explore_right", "angle_deg": 30.0}},
                {"name": "turn_right", "args": {"profile_name": "explore_right", "angle_deg": 30.0}},
            ],
        )

    def test_executor_converts_requested_distance_to_pulse_seconds(self):
        executor = SynchronousActionExecutor.__new__(SynchronousActionExecutor)
        action = parse_action_payload(
            {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}}
        )
        profile = SimpleNamespace(
            name="explore_forward",
            endpoint="forward",
            pulse_sec=1.0,
            linear_m_per_sec=0.2,
            min_pulse_sec=0.15,
            max_pulse_sec=4.0,
        )
        self.assertAlmostEqual(executor._manual_pulse_sec(action, profile), 1.5)
        self.assertEqual(executor._profile_speed_mps(profile), 0.2)

    def test_executor_allows_normal_speed_ninety_degree_turn(self):
        executor = SynchronousActionExecutor.__new__(SynchronousActionExecutor)
        action = parse_action_payload(
            {"name": "turn_left", "args": {"profile_name": "explore_left", "angle_deg": 90.0}}
        )
        profile = SimpleNamespace(
            name="explore_left",
            endpoint="left",
            pulse_sec=0.7,
            angular_rad_per_sec=0.5,
            min_pulse_sec=0.15,
            max_pulse_sec=4.0,
        )
        self.assertAlmostEqual(executor._manual_pulse_sec(action, profile), 3.141592653589793, places=3)
        self.assertEqual(executor._profile_angular_radps(profile), 0.5)

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
            observation=build_structured_observation(),
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
            observation=build_structured_observation(),
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
            observation=build_structured_observation(),
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
            observation=build_structured_observation(),
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
        self.assertIn("scene_understanding", payload["fresh_runtime_evidence"]["observation_digest"])
        self.assertIn("scene_understanding", payload["observation_metadata"])

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

    def test_interactive_cli_limits_model_session_context_to_three_records(self):
        cli = InteractiveGoalCli(settings=self.settings, logger=self.logger)
        agent = DummyAgent()
        session_file = Path("tests/.tmp_interactive_session.jsonl")
        store = SessionMemoryStore(path=session_file, logger=self.logger)
        try:
            store.clear()
            for index in range(5):
                store.record_turn(
                    shell_session_id="shell-1",
                    mode="stateful",
                    goal_text=f"goal-{index}",
                    fed_to_agent=True,
                    summary={"completed": True, "task_plan": {"route": "task_plan"}, "steps": [], "final_status": {}},
                )

            prompts = iter(["new goal", "/exit"])
            import builtins

            original_input = builtins.input
            builtins.input = lambda _prompt="": next(prompts)
            try:
                cli.run(agent=agent, session_mode="stateful", session_file=session_file)
            finally:
                builtins.input = original_input

            self.assertEqual(len(agent.calls), 1)
            self.assertEqual(len(agent.calls[0]["session_context"]), 3)
            self.assertEqual(
                [item["goal_text"] for item in agent.calls[0]["session_context"]],
                ["goal-2", "goal-3", "goal-4"],
            )
        finally:
            store.clear()

    def test_navigation_profile_does_not_depend_on_camera_capture(self):
        agent = ReactAgent.__new__(ReactAgent)
        observer = DummyObserver()
        agent._settings = self.settings
        agent._observer = observer
        agent._trace_writer = DummyTraceWriter()
        agent._logger = self.logger
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="navigation_hint",
            goal_text="去工位",
            reason="matched point",
            confidence=0.9,
            planner_profile_hint="navigation_sequence",
            point=PointOfInterest(point_id="work", name="Work"),
        )
        observation = agent._capture_observation_for_planning(
            profile_name="navigation_sequence",
            subgoal=subgoal,
            session_id="test-session",
            step_index=1,
        )
        self.assertIsNone(observation)
        self.assertEqual(observer.capture_count, 0)

    def test_motion_sequence_requires_visual_capture(self):
        agent = ReactAgent.__new__(ReactAgent)
        observer = DummyObserver()
        agent._settings = self.settings
        agent._observer = observer
        agent._trace_writer = DummyTraceWriter()
        agent._logger = self.logger
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="manual_motion",
            goal_text="往前30cm",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward', 0.3)",
        )
        observation = agent._capture_observation_for_planning(
            profile_name="motion_sequence",
            subgoal=subgoal,
            session_id="test-session",
            step_index=1,
        )
        self.assertIsNotNone(observation)
        self.assertEqual(observer.capture_count, 1)

    def test_motion_sequence_requires_visual_capture_enabled(self):
        settings = build_settings()
        settings.planner.allow_vlm_motion = False
        agent = ReactAgent.__new__(ReactAgent)
        agent._settings = settings
        agent._observer = DummyObserver()
        agent._trace_writer = DummyTraceWriter()
        agent._logger = self.logger
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="manual_motion",
            goal_text="往前30cm",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward', 0.3)",
        )
        with self.assertRaises(ObservationError):
            agent._capture_observation_for_planning(
                profile_name="motion_sequence",
                subgoal=subgoal,
                session_id="test-session",
                step_index=1,
            )

    def test_motion_sequence_blocks_motion_without_visual_evidence(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:motion_sequence": {
                        "response": "I will move forward.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
                        "reason": "Execute the requested motion.",
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
            route="manual_motion",
            goal_text="往前30cm",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward', 0.3)",
        )
        decision = planner.plan(
            goal_text="往前30cm",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="manual_motion", subgoals=(subgoal,)),
            status=build_status(),
            observation=None,
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "stop('planner_blocked')")
        self.assertTrue(decision.stop)

    def test_motion_sequence_allows_safer_reduced_step_from_router_hint(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:motion_sequence": {
                        "response": "I will take a smaller step first.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.2}},
                        "reason": "Safer to advance conservatively.",
                        "confidence": 0.85,
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
            route="manual_motion",
            goal_text="往前30cm",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward', 0.3)",
        )
        decision = planner.plan(
            goal_text="往前30cm",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="manual_motion", subgoals=(subgoal,)),
            status=build_status(),
            observation=build_structured_observation(forward_clearance_m=1.0),
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "move_forward('explore_forward', 0.2)")

    def test_motion_sequence_allows_vlm_only_motion_when_vision_service_disabled(self):
        settings = build_settings()
        settings.vision.enabled = False
        planner = ReactiveScenePlanner(
            settings=settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:motion_sequence": {
                        "response": "I will move forward.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
                        "reason": "Execute the requested motion.",
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
            route="manual_motion",
            goal_text="往前30cm",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward', 0.3)",
        )
        decision = planner.plan(
            goal_text="往前30cm",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="manual_motion", subgoals=(subgoal,)),
            status=build_status(),
            observation=build_observation(),
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "move_forward('explore_forward', 0.3)")

    def test_scene_exploration_allows_vlm_only_exploration_when_vision_service_disabled(self):
        settings = build_settings()
        settings.vision.enabled = False
        planner = ReactiveScenePlanner(
            settings=settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:scene_exploration": {
                        "response": "I will rotate a little to inspect the scene.",
                        "action": {"name": "turn_left", "args": {"profile_name": "explore_left"}},
                        "reason": "A conservative scan is the safest first step.",
                        "confidence": 0.82,
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
            goal_text="告诉我门在那",
            reason="needs exploration",
            confidence=0.5,
            planner_profile_hint="scene_exploration",
        )
        decision = planner.plan(
            goal_text="告诉我门在那",
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

    def test_scene_exploration_allows_forward_with_visual_only_when_no_structured_scene(self):
        settings = build_settings()
        settings.vision.enabled = False
        planner = ReactiveScenePlanner(
            settings=settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:scene_exploration": {
                        "response": "I will move forward toward the target.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.4}},
                        "reason": "The target appears ahead and the visible path looks open.",
                        "confidence": 0.84,
                        "observation_focus": "scene",
                        "target_hint": "front box",
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
            goal_text="去前方盒子的位置",
            reason="needs exploration",
            confidence=0.5,
            planner_profile_hint="scene_exploration",
        )
        decision = planner.plan(
            goal_text="去前方盒子的位置",
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
        self.assertEqual(decision.action_expression, "move_forward('explore_forward', 0.4)")

    def test_motion_sequence_downgrades_unsafe_forward_to_rotation(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:motion_sequence": {
                        "response": "I will move forward.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward", "distance_m": 0.3}},
                        "reason": "Execute the requested motion.",
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
            route="manual_motion",
            goal_text="往前30cm",
            reason="deterministic motion",
            confidence=1.0,
            planner_profile_hint="motion_sequence",
            action_expression="move_forward('explore_forward', 0.3)",
        )
        decision = planner.plan(
            goal_text="往前30cm",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="manual_motion", subgoals=(subgoal,)),
            status=build_status(),
            observation=build_structured_observation(
                safe_to_advance=False,
                forward_clearance_m=0.3,
                recommended_action="turn_left",
                recommended_profile_name="explore_left",
            ),
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="motion_sequence",
        )
        self.assertEqual(decision.action_expression, "turn_left('explore_left')")

    def test_scene_exploration_blocks_forward_when_structured_scene_is_unsafe(self):
        planner = ReactiveScenePlanner(
            settings=self.settings,
            llm_client=DummyLlmClient(
                enabled=True,
                responses={
                    "step_planner:scene_exploration": {
                        "response": "I will move forward.",
                        "action": {"name": "move_forward", "args": {"profile_name": "explore_forward"}},
                        "reason": "Try a forward pulse.",
                        "confidence": 0.92,
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
            goal_text="向前探索一点",
            reason="needs exploration",
            confidence=0.5,
            planner_profile_hint="scene_exploration",
        )
        decision = planner.plan(
            goal_text="向前探索一点",
            current_subgoal=subgoal,
            task_plan=SimpleNamespace(route="scene_exploration", subgoals=(subgoal,)),
            status=build_status(),
            observation=build_structured_observation(safe_to_advance=False, forward_clearance_m=0.25),
            points=[],
            history=[],
            session_context=[],
            latest_execution={},
            profile_name="scene_exploration",
        )
        self.assertEqual(decision.action_expression, "turn_left('explore_left')")

    def test_scene_exploration_requires_visual_capture_enabled(self):
        settings = build_settings()
        settings.planner.allow_vlm_exploration = False
        agent = ReactAgent.__new__(ReactAgent)
        agent._settings = settings
        agent._observer = DummyObserver()
        agent._trace_writer = DummyTraceWriter()
        agent._logger = self.logger
        subgoal = TaskSubgoal(
            sequence_id=1,
            route="scene_exploration",
            goal_text="观察前方",
            reason="needs scene evidence",
            confidence=0.5,
            planner_profile_hint="scene_exploration",
        )
        with self.assertRaises(ObservationError):
            agent._capture_observation_for_planning(
                profile_name="scene_exploration",
                subgoal=subgoal,
                session_id="test-session",
                step_index=1,
            )

    def test_robot_api_resolves_absolute_api_paths_without_duplicate_prefix(self):
        settings = SimpleNamespace(
            robot_api=SimpleNamespace(base_url="http://robot.local:8080/api/v1")
        )
        client = RobotApiClient(settings=settings, logger=self.logger)
        resolved = client._resolve_url("/api/v1/camera/images/img-1/rgb")
        self.assertEqual(resolved, "http://robot.local:8080/api/v1/camera/images/img-1/rgb")
        status_url = client._resolve_url("/status")
        self.assertEqual(status_url, "http://robot.local:8080/api/v1/status")

    def test_capture_asset_infers_content_types_from_camera_urls(self):
        rgb = CaptureAsset.from_api({"download_url": "/api/v1/camera/images/img-1/rgb"}, default_content_type="image/jpeg")
        depth = CaptureAsset.from_api({"download_url": "/api/v1/camera/images/img-1/depth"}, default_content_type="image/png")
        self.assertEqual(rgb.content_type, "image/jpeg")
        self.assertEqual(depth.content_type, "image/png")

    def test_observer_requires_depth_when_structured_scene_is_enabled(self):
        settings = build_settings()
        settings.vision.enabled = True
        settings.robot_api = SimpleNamespace(
            capture=SimpleNamespace(
                enabled=True,
                include_depth=True,
                return_mode="url",
                prefer_inline_if_available=True,
            )
        )
        raw_capture = CaptureObservation(
            image_id="img-1",
            created_at="2026-05-29T10:00:00+08:00",
            return_mode="url",
            rgb=CaptureAsset(content_type="image/jpeg", inline_data="abc", file_path="", download_url=""),
            depth=None,
            rgb_data_url="",
            depth_data_url="",
            scene_understanding=None,
            raw={},
        )
        observer = ObservationProvider(
            settings=settings,
            robot_client=DummyRobotClient(raw_capture),
            vision_client=SimpleNamespace(enabled=False),
            logger=self.logger,
        )
        with self.assertRaises(ObservationError):
            observer.capture_scene()


if __name__ == "__main__":
    unittest.main()
