from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .logging_utils import get_component_logger
from .session_memory import SessionMemoryStore

MODEL_SESSION_CONTEXT_LIMIT = 3


class InteractiveGoalCli:
    def __init__(self, *, settings, logger):
        self._settings = settings
        self._logger = get_component_logger(logger, "cli.interactive")

    def run(self, *, agent, session_mode: str, session_file: Path) -> int:
        shell_session_id = f"{self._settings.session_prefix}-shell-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        store = SessionMemoryStore(path=session_file, logger=self._logger)
        current_mode = session_mode
        self._print_banner(shell_session_id=shell_session_id, session_mode=current_mode, session_file=session_file)
        self._logger.info(
            "interactive cli start: shell_session_id=%s mode=%s session_file=%s",
            shell_session_id,
            current_mode,
            session_file,
        )
        turn_index = 0

        while True:
            try:
                raw = input("goal> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                self._logger.info("interactive cli interrupted: shell_session_id=%s", shell_session_id)
                return 0

            if not raw:
                continue

            if raw.startswith("/"):
                command_result = self._handle_command(
                    raw,
                    store=store,
                    shell_session_id=shell_session_id,
                    current_mode=current_mode,
                )
                if command_result["action"] == "exit":
                    self._logger.info("interactive cli exit command: shell_session_id=%s", shell_session_id)
                    return 0
                if command_result["action"] == "switch_mode":
                    current_mode = command_result["mode"]
                continue

            session_context = []
            fed_to_agent = current_mode == "stateful"
            if fed_to_agent:
                session_context = store.recent_context(limit=MODEL_SESSION_CONTEXT_LIMIT)
            self._logger.info(
                "interactive goal submit: shell_session_id=%s mode=%s fed_to_agent=%s goal=%s context_items=%s",
                shell_session_id,
                current_mode,
                fed_to_agent,
                raw,
                len(session_context),
            )
            turn_index += 1
            summary = agent.run(
                raw,
                session_context=session_context,
                external_session_id=f"{shell_session_id}-turn-{turn_index:04d}",
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            store.record_turn(
                shell_session_id=shell_session_id,
                mode=current_mode,
                goal_text=raw,
                summary=summary,
                fed_to_agent=fed_to_agent,
            )

    def _handle_command(
        self,
        raw: str,
        *,
        store: SessionMemoryStore,
        shell_session_id: str,
        current_mode: str,
    ) -> dict[str, object]:
        normalized = raw.strip()
        if normalized in {"/exit", "/quit"}:
            return {"action": "exit"}
        if normalized == "/help":
            self._print_help()
            return {"action": "continue"}
        if normalized == "/history":
            snapshot = store.history_snapshot(limit=20)
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return {"action": "continue"}
        if normalized == "/clear-session":
            store.clear()
            print("session memory cleared")
            return {"action": "continue"}
        if normalized.startswith("/mode "):
            next_mode = normalized.split(" ", 1)[1].strip().lower()
            if next_mode not in {"stateless", "stateful"}:
                print("mode must be stateless or stateful")
                return {"action": "continue"}
            self._logger.info(
                "interactive mode switch: shell_session_id=%s from=%s to=%s",
                shell_session_id,
                current_mode,
                next_mode,
            )
            print(f"mode switched to {next_mode}")
            return {"action": "switch_mode", "mode": next_mode}
        print("unknown command, use /help")
        return {"action": "continue"}

    def _print_banner(self, *, shell_session_id: str, session_mode: str, session_file: Path) -> None:
        print(f"Interactive CLI ready: {shell_session_id}")
        print(f"session mode: {session_mode}")
        print(f"session file: {session_file}")
        print("type /help for commands")

    def _print_help(self) -> None:
        print("/help           show commands")
        print("/mode stateless independent one-shot execution per sentence")
        print("/mode stateful  feed recent session history back into the agent")
        print("/history        show recent stored turns")
        print("/clear-session  clear stored session history file")
        print("/exit           leave interactive cli")
