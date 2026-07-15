from __future__ import annotations

import argparse
import sys
from pathlib import Path

from inspector.config import RecognitionGlobalConfig
from inspector.replay_store import ReplayStore
from app.utils.logging_utils import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persist replay materials from a detached payload file.")
    parser.add_argument("--handoff", required=True, help="Path to replay handoff json")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging("replay-worker", force=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    store = ReplayStore(RecognitionGlobalConfig())
    store.write_from_handoff(Path(args.handoff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
