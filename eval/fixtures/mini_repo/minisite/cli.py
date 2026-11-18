"""Command line interface for minisite."""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from minisite.config import load_config
from minisite.engine import BuildEngine
from minisite.exceptions import MiniSiteError


def create_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="minisite",
        description="Lightweight static site generator.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Path to JSON configuration file (default: minisite.json if present)",
    )
    parser.add_argument(
        "--include-drafts",
        action="store_true",
        default=False,
        help="Include draft posts in build output",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate file discovery without writing HTML files",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose output logging",
    )
    return parser


def run_cli(argv: Optional[List[str]] = None) -> int:
    """Execute CLI runner with argument parsing and exception control."""
    parser = create_parser()
    args = parser.parse_args(argv)

    config_path = Path(args.config) if args.config else None

    try:
        config = load_config(config_path)
        if args.include_drafts:
            config.include_drafts = True

        engine = BuildEngine(config=config, base_dir=Path.cwd())

        if args.dry_run:
            docs = engine.discover_documents()
            print(f"[DRY-RUN] Discovered {len(docs)} document(s).")
            return 0

        count = engine.build()
        print(f"Successfully generated {count} page(s) into '{config.output_dir}'.")
        return 0

    except MiniSiteError as err:
        print(f"Error [{err.code}]: {err.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Unexpected crash: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    """Entrypoint function for console scripts."""
    sys.exit(run_cli())


if __name__ == "__main__":
    main()
