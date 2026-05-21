from __future__ import annotations

import json
import time
from pathlib import Path
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from ..constants import CAPTURE_ACK_MSGS
from ..constants import MOVE_ACK_MSGS
from ..constants import NAVIGATE_ACK_MSGS
from ..constants import POINTS_ACK_MSGS
from ..constants import STATUS_ACK_MSGS
from ..constants import STOP_ACK_MSGS
from ..constants import SUPPORTED_ROBOT_ERROR_CODES
from ..errors import ObservationError
from ..errors import RobotApiError
from ..errors import RobotProtocolError
from ..models import CaptureAsset
from ..models import CaptureObservation
from ..models import PointOfInterest
from ..models import RobotApiEnvelope
from ..models import RobotStatus


class RobotApiClient:
    def __init__(self, *, settings, logger):
        self._settings = settings
        self._logger = logger
        self._base_url = settings.robot_api.base_url.rstrip("/")
        self._request_counter = 0

    def get_status(self) -> RobotStatus:
        envelope = self._request_json("GET", "/status", expected_msgs=STATUS_ACK_MSGS)
        data = envelope.data
        return RobotStatus.from_api(data)

    def get_points(self) -> list[PointOfInterest]:
        envelope = self._request_json("GET", "/points", expected_msgs=POINTS_ACK_MSGS)
        data = envelope.data
        points = data.get("points", [])
        if not isinstance(points, list):
            raise RobotProtocolError("points payload is not a list")
        return [PointOfInterest.from_api(item) for item in points if isinstance(item, dict)]

    def move(self, endpoint: str, *, speed_level: str) -> RobotApiEnvelope:
        envelope = self._request_json(
            "POST",
            f"/move/{endpoint}",
            payload={"speed_level": speed_level},
            expected_msgs=MOVE_ACK_MSGS.get(endpoint, set()),
        )
        self._validate_move_envelope(envelope, endpoint=endpoint)
        return envelope

    def stop(self, *, reason: str) -> RobotApiEnvelope:
        envelope = self._request_json("POST", "/stop", payload={"reason": reason}, expected_msgs=STOP_ACK_MSGS)
        self._validate_stop_envelope(envelope)
        return envelope

    def navigate(self, *, point_id: str, nav_mode: str = "normal") -> RobotApiEnvelope:
        envelope = self._request_json(
            "POST",
            "/navigate",
            payload={"point_id": point_id, "nav_mode": nav_mode},
            expected_msgs=NAVIGATE_ACK_MSGS,
        )
        self._validate_navigate_envelope(envelope, point_id=point_id)
        return envelope

    def capture(self) -> CaptureObservation:
        payload = {
            "include_depth": self._settings.robot_api.capture.include_depth,
            "return_mode": self._settings.robot_api.capture.return_mode,
        }
        envelope = self._request_json(
            "POST",
            "/camera/capture",
            payload=payload,
            timeout_sec=self._settings.robot_api.capture.request_timeout_sec,
            expected_msgs=CAPTURE_ACK_MSGS,
        )
        data = envelope.data
        return CaptureObservation(
            image_id=str(data.get("image_id", "") or "").strip(),
            created_at=str(data.get("created_at", "") or "").strip(),
            return_mode=str(data.get("return_mode", "") or "").strip(),
            rgb=CaptureAsset.from_api(data.get("rgb")),
            depth=CaptureAsset.from_api(data.get("depth")),
            rgb_data_url="",
            depth_data_url="",
            raw=data,
        )

    def download_binary(self, location: str) -> bytes:
        if not location:
            raise ObservationError("download location is empty")
        resolved = self._resolve_url(location)
        self._logger.info("robot binary download start: %s", resolved)
        request = urllib_request.Request(resolved, method="GET")
        try:
            with urllib_request.urlopen(request, timeout=self._settings.robot_api.capture.request_timeout_sec) as response:
                return response.read()
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RobotApiError(
                message=f"binary_download_http_error:{body[:300]}",
                http_status=exc.code,
            ) from exc
        except urllib_error.URLError as exc:
            raise RobotApiError(message=f"binary_download_transport_error:{exc}") from exc

    def read_local_file(self, file_path: str) -> bytes:
        if not file_path:
            raise ObservationError("file_path is empty")
        path = Path(file_path)
        if not path.exists():
            raise ObservationError(f"capture_file_not_found:{file_path}")
        return path.read_bytes()

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        timeout_sec: float | None = None,
        expected_msgs: set[str] | None = None,
    ) -> RobotApiEnvelope:
        url = self._resolve_url(path)
        headers = {"Content-Type": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        self._request_counter += 1
        request_id = f"robot-http-{self._request_counter:05d}"
        self._logger.info(
            "robot request start: request_id=%s method=%s url=%s payload=%s",
            request_id,
            method,
            url,
            payload or {},
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_sec or self._settings.robot_api.request_timeout_sec) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            body_preview = exc.read().decode("utf-8", errors="replace")
            elapsed = time.monotonic() - started
            self._logger.error(
                "robot request http error: request_id=%s method=%s url=%s elapsed=%.3fs status=%s body=%s",
                request_id,
                method,
                url,
                elapsed,
                exc.code,
                body_preview[:500],
            )
            raise RobotApiError(
                message=f"http_error:{body_preview[:500]}",
                http_status=exc.code,
            ) from exc
        except urllib_error.URLError as exc:
            elapsed = time.monotonic() - started
            self._logger.error(
                "robot request transport error: request_id=%s method=%s url=%s elapsed=%.3fs error=%s",
                request_id,
                method,
                url,
                elapsed,
                exc,
            )
            raise RobotApiError(message=f"transport_error:{exc}") from exc
        elapsed = time.monotonic() - started
        self._logger.info(
            "robot request done: request_id=%s method=%s url=%s elapsed=%.3fs",
            request_id,
            method,
            url,
            elapsed,
        )
        if not isinstance(body, dict):
            raise RobotProtocolError("robot response is not a JSON object")
        code = int(body.get("code", 0) or 0)
        if code != 0:
            self._logger.error(
                "robot request business error: request_id=%s method=%s url=%s code=%s msg=%s data=%s",
                request_id,
                method,
                url,
                code,
                body.get("msg", ""),
                body.get("data", {}),
            )
            raise RobotApiError(
                message=str(body.get("msg", SUPPORTED_ROBOT_ERROR_CODES.get(code, "UNKNOWN_ERROR"))),
                code=code,
                payload=dict(body.get("data", {})) if isinstance(body.get("data", {}), dict) else {},
            )
        envelope = RobotApiEnvelope(
            code=code,
            msg=str(body.get("msg", "") or "").strip(),
            data=self._response_data(body),
            raw=body,
        )
        self._validate_success_message(
            envelope,
            request_id=request_id,
            method=method,
            url=url,
            expected_msgs=expected_msgs,
        )
        self._logger.info(
            "robot request ok: request_id=%s code=%s msg=%s data_keys=%s",
            request_id,
            envelope.code,
            envelope.msg,
            sorted(envelope.data.keys()),
        )
        return envelope

    def _response_data(self, body: dict[str, object]) -> dict[str, object]:
        data = body.get("data", {})
        if not isinstance(data, dict):
            raise RobotProtocolError("robot response data field is not an object")
        return data

    def _validate_success_message(
        self,
        envelope: RobotApiEnvelope,
        *,
        request_id: str,
        method: str,
        url: str,
        expected_msgs: set[str] | None,
    ) -> None:
        if not expected_msgs:
            return
        msg = envelope.msg.lower()
        normalized_expected = {value.lower() for value in expected_msgs if value}
        if msg in normalized_expected:
            return
        self._logger.error(
            "robot request success msg unexpected: request_id=%s method=%s url=%s code=%s msg=%s expected=%s",
            request_id,
            method,
            url,
            envelope.code,
            envelope.msg,
            sorted(normalized_expected),
        )
        raise RobotProtocolError(
            f"unexpected_success_msg:{method}:{url}:{envelope.msg}:expected={sorted(normalized_expected)}"
        )

    def _validate_move_envelope(self, envelope: RobotApiEnvelope, *, endpoint: str) -> None:
        robot_state = str(envelope.data.get("robot_state", "") or "").strip()
        direction = str(envelope.data.get("direction", "") or "").strip()
        if robot_state != "manual":
            raise RobotProtocolError(f"move_ack_robot_state_invalid:{endpoint}:{robot_state}")
        if direction and direction != endpoint:
            raise RobotProtocolError(f"move_ack_direction_invalid:{endpoint}:{direction}")

    def _validate_stop_envelope(self, envelope: RobotApiEnvelope) -> None:
        robot_state = str(envelope.data.get("robot_state", "") or "").strip()
        if robot_state and robot_state != "idle":
            raise RobotProtocolError(f"stop_ack_robot_state_invalid:{robot_state}")

    def _validate_navigate_envelope(self, envelope: RobotApiEnvelope, *, point_id: str) -> None:
        ack_point_id = str(envelope.data.get("point_id", "") or "").strip()
        nav_state = str(envelope.data.get("nav_state", "") or "").strip()
        if ack_point_id and ack_point_id != point_id:
            raise RobotProtocolError(f"navigate_ack_point_mismatch:{ack_point_id}:expected={point_id}")
        if nav_state and nav_state != "navigating":
            raise RobotProtocolError(f"navigate_ack_nav_state_invalid:{nav_state}")

    def _resolve_url(self, path_or_url: str) -> str:
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        return urllib_parse.urljoin(f"{self._base_url}/", path_or_url.lstrip("/"))
