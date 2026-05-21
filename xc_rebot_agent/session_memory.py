from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class SessionMemoryStore:
    def __init__(self, *, path: Path, logger):
        self._path = path
        self._logger = logger
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[dict[str, object]] = []
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def record_turn(
        self,
        *,
        shell_session_id: str,
        mode: str,
        goal_text: str,
        summary: dict[str, object],
        fed_to_agent: bool,
    ) -> None:
        record = {
            "ts": datetime.now().astimezone().isoformat(),
            "shell_session_id": shell_session_id,
            "mode": mode,
            "goal_text": goal_text,
            "fed_to_agent": fed_to_agent,
            "summary": summary,
        }
        self._records.append(record)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")
        self._logger.info(
            "session memory record appended: shell_session_id=%s mode=%s fed_to_agent=%s file=%s",
            shell_session_id,
            mode,
            fed_to_agent,
            self._path,
        )

    def recent_context(self, *, limit: int) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        recent = self._records[-limit:]
        context: list[dict[str, object]] = []
        for record in recent:
            summary = record.get("summary", {})
            if not isinstance(summary, dict):
                continue
            context.append(
                {
                    "ts": record.get("ts", ""),
                    "shell_session_id": record.get("shell_session_id", ""),
                    "mode": record.get("mode", ""),
                    "goal_text": record.get("goal_text", ""),
                    "completed": bool(summary.get("completed", False)),
                    "error": summary.get("error", ""),
                    "stage": summary.get("stage", ""),
                    "task_plan_route": self._task_plan_route(summary),
                    "final_status": self._compact_status(summary.get("final_status", {})),
                    "last_step": self._last_step_digest(summary.get("steps", [])),
                    "step_count": self._step_count(summary.get("steps", [])),
                }
            )
        return context

    def history_snapshot(self, *, limit: int = 20) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        return self._records[-limit:]

    def clear(self) -> None:
        self._records = []
        self._path.write_text("", encoding="utf-8")
        self._logger.info("session memory cleared: %s", self._path)

    def _load(self) -> None:
        if not self._path.exists():
            self._logger.info("session memory file does not exist yet: %s", self._path)
            return
        loaded = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                self._records.append(record)
                loaded += 1
        self._logger.info("session memory loaded: file=%s records=%s", self._path, loaded)

    def _task_plan_route(self, summary: dict[str, object]) -> str:
        task_plan = summary.get("task_plan", {})
        if isinstance(task_plan, dict):
            return str(task_plan.get("route", "") or "")
        return ""

    def _compact_status(self, raw_status: object) -> dict[str, object]:
        if not isinstance(raw_status, dict):
            return {}
        nav = raw_status.get("nav", {})
        nav_dict = nav if isinstance(nav, dict) else {}
        localization = raw_status.get("localization", {})
        localization_dict = localization if isinstance(localization, dict) else {}
        return {
            "robot_state": raw_status.get("robot_state", ""),
            "nav_state": nav_dict.get("state", ""),
            "target_point_id": nav_dict.get("target_point_id", ""),
            "localization_valid": bool(localization_dict.get("valid", False)),
        }

    def _last_step_digest(self, raw_steps: object) -> dict[str, object]:
        if not isinstance(raw_steps, list) or not raw_steps:
            return {}
        last = raw_steps[-1]
        if not isinstance(last, dict):
            return {}
        subgoal = last.get("subgoal", {})
        execution = last.get("execution", {})
        decision = last.get("decision", {})
        return {
            "step_index": last.get("step_index", 0),
            "subgoal_goal_text": subgoal.get("goal_text", "") if isinstance(subgoal, dict) else "",
            "action": execution.get("action", {}) if isinstance(execution, dict) else {},
            "action_expression": execution.get("action_expression", "") if isinstance(execution, dict) else "",
            "summary": execution.get("summary", "") if isinstance(execution, dict) else "",
            "decision_state": decision.get("subgoal_state", "") if isinstance(decision, dict) else "",
        }

    def _step_count(self, raw_steps: object) -> int:
        if not isinstance(raw_steps, list):
            return 0
        return len(raw_steps)
