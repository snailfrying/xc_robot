from __future__ import annotations

import json
from pathlib import Path

from ..errors import PointResolutionError
from ..models import GoalResolution
from ..models import PointOfInterest
from ..utils.text_utils import normalize_text
from .contracts import build_point_resolution_contract
from .prompts import build_point_resolution_system_prompt


class PointResolver:
    def __init__(self, *, settings, llm_client, logger):
        self._settings = settings
        self._llm_client = llm_client
        self._logger = logger

    def enrich_points(self, points: list[PointOfInterest]) -> list[PointOfInterest]:
        alias_map = self._load_local_aliases()
        enriched: list[PointOfInterest] = []
        for point in points:
            aliases = list(point.aliases)
            aliases.extend(alias_map.get(point.point_id, ()))
            unique_aliases = tuple(dict.fromkeys(alias for alias in aliases if alias))
            enriched.append(PointOfInterest.from_api(point.raw, aliases=unique_aliases))
        self._logger.info(
            "point resolver enrich complete: api_points=%s local_alias_points=%s enriched_points=%s",
            len(points),
            len(alias_map),
            len(enriched),
        )
        return enriched

    def resolve(self, goal_text: str, points: list[PointOfInterest]) -> GoalResolution | None:
        if not points:
            return None
        deterministic = self._deterministic_match(goal_text, points)
        if deterministic is not None:
            self._logger.info(
                "point resolver deterministic hit: point_id=%s confidence=%.3f reason=%s",
                deterministic.point.point_id if deterministic.point is not None else "",
                deterministic.confidence,
                deterministic.reason,
            )
            return deterministic
        if not (self._settings.planner.allow_llm_point_resolution and self._llm_client.enabled):
            self._logger.info("point resolver skipped llm route: allow_llm=%s llm_enabled=%s", self._settings.planner.allow_llm_point_resolution, self._llm_client.enabled)
            return None
        return self._llm_match(goal_text, points)

    def _deterministic_match(self, goal_text: str, points: list[PointOfInterest]) -> GoalResolution | None:
        normalized_goal = normalize_text(goal_text)
        if not normalized_goal:
            return None
        best_point = None
        best_score = 0.0
        best_position = 10**9
        best_reason = ""
        ambiguous_points: set[str] = set()
        for point in points:
            for term in point.search_terms():
                normalized_term = normalize_text(term)
                if not normalized_term:
                    continue
                if normalized_goal == normalized_term:
                    return GoalResolution(
                        route="navigate",
                        reason=f"deterministic exact point match: {term}",
                        confidence=self._settings.point_resolution.exact_match_confidence,
                        point=point,
                    )
                if normalized_term in normalized_goal:
                    score = self._settings.point_resolution.substring_match_confidence
                    position = normalized_goal.find(normalized_term)
                    reason = f"goal contains point term: {term}"
                elif normalized_goal in normalized_term:
                    score = self._settings.point_resolution.reverse_substring_match_confidence
                    position = normalized_term.find(normalized_goal)
                    reason = f"point term contains goal: {term}"
                else:
                    continue
                if score > best_score or (score == best_score and position < best_position):
                    best_point = point
                    best_score = score
                    best_position = position
                    best_reason = reason
                    ambiguous_points = {point.point_id}
                elif (
                    best_point is not None
                    and score == best_score
                    and position == best_position
                    and point.point_id != best_point.point_id
                ):
                    ambiguous_points.add(point.point_id)
                    ambiguous_points.add(best_point.point_id)
        if len(ambiguous_points) > 1:
            self._logger.warning(
                "point resolver deterministic ambiguous hit: goal=%s candidates=%s score=%.3f position=%s",
                goal_text,
                sorted(ambiguous_points),
                best_score,
                best_position,
            )
            return None
        if best_point is not None and best_score >= self._settings.point_resolution.minimum_confidence:
            return GoalResolution(
                route="navigate",
                reason=best_reason,
                confidence=best_score,
                point=best_point,
            )
        return None

    def _llm_match(self, goal_text: str, points: list[PointOfInterest]) -> GoalResolution | None:
        payload = {
            "goal_text": goal_text,
            "workflow_contract": build_point_resolution_contract(),
            "points": [point.to_dict() for point in points[: self._settings.point_resolution.max_candidates]],
        }
        parsed = self._llm_client.chat_json(
            system_prompt=build_point_resolution_system_prompt(),
            user_payload=payload,
            response_label="point_resolution",
        )
        point_id = parsed.get("point_id")
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
        reason = str(parsed.get("reason", "") or "").strip()
        if point_id in (None, ""):
            return None
        point_id = str(point_id).strip()
        for point in points:
            if point.point_id == point_id:
                if confidence < self._settings.point_resolution.minimum_confidence:
                    self._logger.warning(
                        "point resolver llm hit below threshold: point_id=%s confidence=%.3f threshold=%.3f",
                        point_id,
                        confidence,
                        self._settings.point_resolution.minimum_confidence,
                    )
                    return None
                self._logger.info(
                    "point resolver llm hit: point_id=%s confidence=%.3f reason=%s",
                    point_id,
                    confidence,
                    reason or "llm point match",
                )
                return GoalResolution(
                    route="navigate",
                    reason=reason or "llm point match",
                    confidence=confidence,
                    point=point,
                )
        raise PointResolutionError(f"llm returned unknown point_id:{point_id}")

    def _load_local_aliases(self) -> dict[str, tuple[str, ...]]:
        path = self._settings.project_root / self._settings.point_resolution.local_alias_file
        if not path.exists():
            self._logger.info("point resolver local alias file missing: %s", path)
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PointResolutionError(f"invalid_local_alias_json:{path}") from exc
        points = payload.get("points", [])
        if not isinstance(points, list):
            return {}
        aliases: dict[str, tuple[str, ...]] = {}
        for item in points:
            if not isinstance(item, dict):
                continue
            point_id = str(item.get("point_id", "")).strip()
            if not point_id:
                continue
            alias_values = item.get("aliases", [])
            if isinstance(alias_values, list):
                aliases[point_id] = tuple(str(value).strip() for value in alias_values if str(value).strip())
        self._logger.info("point resolver local alias file loaded: %s entries=%s", path, len(aliases))
        return aliases
