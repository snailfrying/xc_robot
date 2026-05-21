from __future__ import annotations

from ..errors import ObservationError
from ..models import CaptureObservation
from ..utils.text_utils import image_bytes_to_data_url


class ObservationProvider:
    def __init__(self, *, settings, robot_client, logger):
        self._settings = settings
        self._robot_client = robot_client
        self._logger = logger

    def capture_scene(self) -> CaptureObservation:
        if not self._settings.robot_api.capture.enabled:
            raise ObservationError("capture disabled by configuration")
        self._logger.info("capture start: include_depth=%s return_mode=%s", self._settings.robot_api.capture.include_depth, self._settings.robot_api.capture.return_mode)
        observation = self._robot_client.capture()
        rgb_data_url = self._materialize_asset_data_url(observation.rgb)
        if not rgb_data_url:
            raise ObservationError("capture returned no usable rgb image")
        depth_data_url = self._materialize_asset_data_url(observation.depth)
        self._logger.info(
            "capture ready: image_id=%s mode=%s rgb=%s depth=%s",
            observation.image_id,
            observation.return_mode,
            bool(rgb_data_url),
            bool(depth_data_url),
        )
        return CaptureObservation(
            image_id=observation.image_id,
            created_at=observation.created_at,
            return_mode=observation.return_mode,
            rgb=observation.rgb,
            depth=observation.depth,
            rgb_data_url=rgb_data_url,
            depth_data_url=depth_data_url,
            raw=observation.raw,
        )

    def _materialize_asset_data_url(self, asset):
        if asset is None:
            return ""
        if asset.inline_data and self._settings.robot_api.capture.prefer_inline_if_available:
            self._logger.info("capture asset materialized from inline_data: content_type=%s", asset.content_type or "image/jpeg")
            content_type = asset.content_type or "image/jpeg"
            return f"data:{content_type};base64,{asset.inline_data}"
        if asset.file_path:
            self._logger.info("capture asset materialized from file_path: path=%s", asset.file_path)
            payload = self._robot_client.read_local_file(asset.file_path)
            return image_bytes_to_data_url(asset.content_type or "image/jpeg", payload)
        if asset.download_url:
            self._logger.info("capture asset materialized from download_url: url=%s", asset.download_url)
            payload = self._robot_client.download_binary(asset.download_url)
            return image_bytes_to_data_url(asset.content_type or "image/jpeg", payload)
        return ""
