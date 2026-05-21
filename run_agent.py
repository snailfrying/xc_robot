from __future__ import annotations

import argparse
import json
from pathlib import Path

from xc_rebot_agent.clients.robot_api import RobotApiClient
from xc_rebot_agent.errors import XcRebotError
from xc_rebot_agent.interactive_cli import InteractiveGoalCli
from xc_rebot_agent.logging_utils import configure_logging
from xc_rebot_agent.settings import load_settings
from xc_rebot_agent.workflows.react_agent import ReactAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="xc_rebot synchronous ReAct chassis agent")
    parser.add_argument("--goal", default="", help="natural-language goal for the robot")
    parser.add_argument("--config", default="config/defaults.toml", help="settings TOML path")
    parser.add_argument("--env-file", default=".env", help="dotenv path")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="explicitly allow live robot or service API access",
    )
    parser.add_argument("--status", action="store_true", help="query robot status and exit")
    parser.add_argument("--list-points", action="store_true", help="list robot points and exit")
    parser.add_argument("--interactive", action="store_true", help="start interactive goal cli")
    parser.add_argument(
        "--session-mode",
        choices=("stateless", "stateful"),
        default="stateless",
        help="interactive cli memory mode; stateless keeps each goal independent",
    )
    parser.add_argument(
        "--session-file",
        default="runtime_logs/interactive_session.jsonl",
        help="interactive cli session memory file",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    settings = load_settings(
        project_root=project_root,
        config_path=project_root / args.config,
        env_path=project_root / args.env_file,
    )
    logger, _ = configure_logging(settings)
    try:
        if not args.allow_live:
            raise SystemExit(
                "Live robot/service access is blocked by default. "
                "Re-run with --allow-live only after explicit operator approval."
            )

        client = RobotApiClient(settings=settings, logger=logger.getChild("client.robot_api.cli"))

        if args.status:
            print(json.dumps(client.get_status().raw, ensure_ascii=False, indent=2))
            return 0

        if args.list_points:
            points = [point.to_dict() for point in client.get_points()]
            print(json.dumps(points, ensure_ascii=False, indent=2))
            return 0

        if args.interactive:
            cli = InteractiveGoalCli(settings=settings, logger=logger)
            agent = ReactAgent(settings=settings, logger=logger)
            session_file = project_root / args.session_file
            return cli.run(
                agent=agent,
                session_mode=args.session_mode,
                session_file=session_file,
            )

        if not args.goal.strip():
            parser.error("--goal is required unless --status or --list-points is used")

        agent = ReactAgent(settings=settings, logger=logger)
        summary = agent.run(args.goal)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary.get("completed", False) else 1
    except XcRebotError as exc:
        logger.exception("xc_rebot agent failed: %s", exc)
        print(
            json.dumps(
                {
                    "completed": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
