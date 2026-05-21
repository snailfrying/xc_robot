from __future__ import annotations

APP_ENV_OVERRIDES = {
    "robot_base_url": "XC_ROBOT_BASE_URL",
    "llm_api_url": "XC_LLM_API_URL",
    "llm_api_key": "XC_LLM_API_KEY",
    "llm_model_name": "XC_LLM_MODEL_NAME",
}

PPIO_ENV_FALLBACKS = {
    "llm_api_url": "PPIO_BASE_URL",
    "llm_api_key": "PPIO_API_KEY",
    "llm_model_name": "PPIO_BASE_MODEL",
}

TERMINAL_NAV_STATES = {"succeeded", "failed", "stopped"}
ACTIVE_NAV_STATES = {"navigating"}

SUPPORTED_ROBOT_ERROR_CODES = {
    0: "OK",
    1001: "INVALID_PARAM",
    1002: "PARSE_ERROR",
    1003: "SERVICE_UNAVAILABLE",
    2004: "POINT_NOT_FOUND",
    2006: "NAV_IN_PROGRESS",
    2007: "LOCALIZATION_INVALID",
    3001: "INTERNAL_FAILED",
    4004: "IMAGE_NOT_FOUND",
}

GENERIC_SUCCESS_MSGS = {"success", "ok"}

MOVE_ACK_MSGS = {
    "forward": {"moving_forward", *GENERIC_SUCCESS_MSGS},
    "backward": {"moving_backward", *GENERIC_SUCCESS_MSGS},
    "left": {"turning_left", *GENERIC_SUCCESS_MSGS},
    "right": {"turning_right", *GENERIC_SUCCESS_MSGS},
}

STOP_ACK_MSGS = {"stopped", *GENERIC_SUCCESS_MSGS}
NAVIGATE_ACK_MSGS = {"accepted", *GENERIC_SUCCESS_MSGS}
STATUS_ACK_MSGS = {"ok", *GENERIC_SUCCESS_MSGS}
POINTS_ACK_MSGS = {"ok", *GENERIC_SUCCESS_MSGS}
CAPTURE_ACK_MSGS = {"ok", *GENERIC_SUCCESS_MSGS}
