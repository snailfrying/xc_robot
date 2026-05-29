from __future__ import annotations

import json
from urllib import error as urllib_error
from urllib import request as urllib_request

from ..errors import VisionServiceError
from ..models import CaptureObservation
from ..models import VisualSceneUnderstanding


class VisionUnderstandingClient:
    def __init__(self, *, settings, logger):
        self._settings = settings
        self._logger = logger

    @property
    def enabled(self) -> bool:
        return bool(
            self._settings.vision.enabled
            and self._settings.vision.api_url
            and self._settings.vision.backend
        )

    def understand_scene(self, observation: CaptureObservation) -> VisualSceneUnderstanding | None:
        if not self.enabled:
            return None
        if self._settings.vision.require_depth and not observation.depth_data_url:
            raise VisionServiceError("vision_service_requires_depth_but_capture_has_no_depth")
        payload = {
            "backend": self._settings.vision.backend,
            "image_id": observation.image_id,
            "created_at": observation.created_at,
            "rgb_data_url": observation.rgb_data_url,
            "depth_data_url": observation.depth_data_url,
            "capture_metadata": observation.raw,
        }
        headers = {"Content-Type": "application/json"}
        if self._settings.vision.api_key:
            headers["Authorization"] = f"Bearer {self._settings.vision.api_key}"
        request = urllib_request.Request(
            self._settings.vision.api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        self._logger.info(
            "vision request start: backend=%s image_id=%s has_depth=%s",
            self._settings.vision.backend,
            observation.image_id,
            bool(observation.depth_data_url),
        )
        try:
            with urllib_request.urlopen(request, timeout=self._settings.vision.request_timeout_sec) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise VisionServiceError(f"vision_service_http_error:{exc.code}:{detail[:300]}") from exc
        except urllib_error.URLError as exc:
            raise VisionServiceError(f"vision_service_transport_error:{exc}") from exc
        if not isinstance(body, dict):
            raise VisionServiceError("vision_service_response_not_object")
        if "data" in body and isinstance(body.get("data"), dict):
            payload_body = body["data"]
        else:
            payload_body = body
        scene = VisualSceneUnderstanding.from_api(payload_body)
        if scene is None:
            raise VisionServiceError("vision_service_missing_scene_payload")
        self._logger.info(
            "vision request done: image_id=%s confidence=%.3f safe_to_advance=%s target_count=%s",
            observation.image_id,
            scene.confidence,
            scene.flags.safe_to_advance,
            len(scene.targets),
        )
        return scene
