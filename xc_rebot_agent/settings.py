from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .constants import APP_ENV_OVERRIDES, PPIO_ENV_FALLBACKS


@dataclass(frozen=True)
class CaptureSettings:
    enabled: bool
    include_depth: bool
    return_mode: str
    request_timeout_sec: float
    prefer_inline_if_available: bool


@dataclass(frozen=True)
class RobotApiSettings:
    base_url: str
    request_timeout_sec: float
    status_poll_interval_sec: float
    status_transition_timeout_sec: float
    navigation_timeout_sec: float
    navigation_terminal_grace_sec: float
    capture: CaptureSettings


@dataclass(frozen=True)
class LlmSettings:
    enabled: bool
    api_url: str
    api_key: str
    model_name: str
    request_timeout_sec: float
    temperature: float
    max_retries: int


@dataclass(frozen=True)
class ManualProfile:
    name: str
    endpoint: str
    speed_level: str
    pulse_sec: float
    settle_sec: float
    status_expect_state: str


@dataclass(frozen=True)
class StopSettings:
    reason: str
    transition_timeout_sec: float
    poll_interval_sec: float


@dataclass(frozen=True)
class ExecutorSettings:
    manual_profiles: dict[str, ManualProfile]
    stop: StopSettings


@dataclass(frozen=True)
class PlannerSettings:
    max_steps: int
    history_window: int
    confidence_floor: float
    allow_llm_point_resolution: bool
    allow_vlm_exploration: bool
    force_stop_on_low_confidence: bool


@dataclass(frozen=True)
class PointResolutionSettings:
    local_alias_file: str
    minimum_confidence: float
    max_candidates: int
    exact_match_confidence: float
    substring_match_confidence: float
    reverse_substring_match_confidence: float


@dataclass(frozen=True)
class RoutingSettings:
    react_confidence: float


@dataclass(frozen=True)
class LoggingSettings:
    directory: str
    level: str
    console: bool
    file: bool
    session_trace_enabled: bool
    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class AppSettings:
    project_root: Path
    project_name: str
    session_prefix: str
    robot_api: RobotApiSettings
    llm: LlmSettings
    executor: ExecutorSettings
    planner: PlannerSettings
    point_resolution: PointResolutionSettings
    routing: RoutingSettings
    logging: LoggingSettings


def load_settings(*, project_root: Path, config_path: Path, env_path: Path) -> AppSettings:
    _load_dotenv(env_path)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    robot_api_raw = raw["robot_api"]
    capture_raw = robot_api_raw["capture"]
    llm_raw = raw["llm"]
    planner_raw = raw["planner"]
    point_raw = raw["point_resolution"]
    routing_raw = raw["routing"]
    logging_raw = raw["logging"]
    robot_api = RobotApiSettings(
        base_url=_env_first(
            APP_ENV_OVERRIDES["robot_base_url"],
            default=str(robot_api_raw["base_url"]).strip(),
        ),
        request_timeout_sec=float(robot_api_raw["request_timeout_sec"]),
        status_poll_interval_sec=float(robot_api_raw["status_poll_interval_sec"]),
        status_transition_timeout_sec=float(robot_api_raw["status_transition_timeout_sec"]),
        navigation_timeout_sec=float(robot_api_raw["navigation_timeout_sec"]),
        navigation_terminal_grace_sec=float(robot_api_raw["navigation_terminal_grace_sec"]),
        capture=CaptureSettings(
            enabled=bool(capture_raw["enabled"]),
            include_depth=bool(capture_raw["include_depth"]),
            return_mode=str(capture_raw["return_mode"]).strip(),
            request_timeout_sec=float(capture_raw["request_timeout_sec"]),
            prefer_inline_if_available=bool(capture_raw["prefer_inline_if_available"]),
        ),
    )

    llm = LlmSettings(
        enabled=bool(llm_raw["enabled"]),
        api_url=_env_first(
            APP_ENV_OVERRIDES["llm_api_url"],
            fallback_env=PPIO_ENV_FALLBACKS["llm_api_url"],
            default=str(llm_raw["api_url"]).strip(),
        ),
        api_key=_env_first(
            APP_ENV_OVERRIDES["llm_api_key"],
            fallback_env=PPIO_ENV_FALLBACKS["llm_api_key"],
            default=str(llm_raw["api_key"]).strip(),
        ),
        model_name=_env_first(
            APP_ENV_OVERRIDES["llm_model_name"],
            fallback_env=PPIO_ENV_FALLBACKS["llm_model_name"],
            default=str(llm_raw["model_name"]).strip(),
        ),
        request_timeout_sec=float(llm_raw["request_timeout_sec"]),
        temperature=float(llm_raw["temperature"]),
        max_retries=int(llm_raw["max_retries"]),
    )

    manual_profiles_raw = raw["executor"]["manual_profiles"]
    manual_profiles: dict[str, ManualProfile] = {}
    for name, payload in manual_profiles_raw.items():
        manual_profiles[name] = ManualProfile(
            name=name,
            endpoint=str(payload["endpoint"]).strip(),
            speed_level=str(payload["speed_level"]).strip(),
            pulse_sec=float(payload["pulse_sec"]),
            settle_sec=float(payload["settle_sec"]),
            status_expect_state=str(payload["status_expect_state"]).strip(),
        )

    executor = ExecutorSettings(
        manual_profiles=manual_profiles,
        stop=StopSettings(
            reason=str(raw["executor"]["stop"]["reason"]).strip(),
            transition_timeout_sec=float(raw["executor"]["stop"]["transition_timeout_sec"]),
            poll_interval_sec=float(raw["executor"]["stop"]["poll_interval_sec"]),
        ),
    )

    planner = PlannerSettings(
        max_steps=int(planner_raw["max_steps"]),
        history_window=int(planner_raw["history_window"]),
        confidence_floor=float(planner_raw["confidence_floor"]),
        allow_llm_point_resolution=bool(planner_raw["allow_llm_point_resolution"]),
        allow_vlm_exploration=bool(planner_raw["allow_vlm_exploration"]),
        force_stop_on_low_confidence=bool(planner_raw["force_stop_on_low_confidence"]),
    )

    point_resolution = PointResolutionSettings(
        local_alias_file=str(point_raw["local_alias_file"]).strip(),
        minimum_confidence=float(point_raw["minimum_confidence"]),
        max_candidates=int(point_raw["max_candidates"]),
        exact_match_confidence=float(point_raw["exact_match_confidence"]),
        substring_match_confidence=float(point_raw["substring_match_confidence"]),
        reverse_substring_match_confidence=float(point_raw["reverse_substring_match_confidence"]),
    )

    routing = RoutingSettings(
        react_confidence=float(routing_raw["react_confidence"]),
    )

    logging = LoggingSettings(
        directory=str(logging_raw["directory"]).strip(),
        level=str(logging_raw["level"]).strip(),
        console=bool(logging_raw["console"]),
        file=bool(logging_raw["file"]),
        session_trace_enabled=bool(logging_raw["session_trace_enabled"]),
        max_bytes=int(logging_raw["max_bytes"]),
        backup_count=int(logging_raw["backup_count"]),
    )

    return AppSettings(
        project_root=project_root,
        project_name=str(raw["project"]["name"]).strip(),
        session_prefix=str(raw["project"]["session_prefix"]).strip(),
        robot_api=robot_api,
        llm=llm,
        executor=executor,
        planner=planner,
        point_resolution=point_resolution,
        routing=routing,
        logging=logging,
    )


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def _env_first(primary_env: str, *, fallback_env: str | None = None, default: str = "") -> str:
    primary = os.environ.get(primary_env, "").strip()
    if primary:
        return primary
    if fallback_env:
        fallback = os.environ.get(fallback_env, "").strip()
        if fallback:
            return fallback
    return default
