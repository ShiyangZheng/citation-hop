"""Allow ``python -m citation_hop`` to launch the tray app, or use
``python -m citation_hop <text>`` to run a single lookup from the
command line (no GUI required).

The CLI mode is invaluable for debugging lookup behaviour without
restarting the menu-bar process.  Each lookup prints a structured
record to stdout::

    $ python -m citation_hop "Smith et al. (2020)"
    [citationHop] CLI mode — no tray started
    status:  in_text
    author:  Smith
    year:    2020
    engine:  scholar
    url:     https://scholar.google.com/scholar?q=+Smith+2020

Exit code is 0 on success (any status other than ``empty`` or
``not_citation``), 2 on bad input, 3 on an unexpected error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import __version__
from .engines import default_engines, engines_from_dicts
from .main import lookup


def _print_cli_result(text: str, *, json_mode: bool) -> int:
    """Run a single lookup on *text* and print the result."""
    from .config import load_config

    cfg = load_config()
    # Prefer user-configured engines if present, else use defaults.
    raw = cfg.get("engines") if isinstance(cfg, dict) else None
    engines = (
        engines_from_dicts(raw)
        if raw
        else default_engines()
    )
    mailto = cfg.get("mailto") if isinstance(cfg, dict) else None

    result = lookup(text, engines=engines, mailto=mailto)

    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"status:    {result.get('status')}")
        print(f"engine:    {result.get('engine_used')}")
        print(f"doi:       {result.get('doi')}")
        print(f"title:     {result.get('title')}")
        print(f"url:       {result.get('url')}")
        in_text = result.get("in_text") or {}
        if in_text:
            print(f"author:    {in_text.get('author')}")
            print(f"year:      {in_text.get('year')}")

    status = result.get("status")
    if status in (None, "empty", "not_citation"):
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citation_hop",
        description=(
            "citationHop — select a citation, get the paper.  "
            "Run without arguments to launch the menu-bar / tray app.  "
            "Pass a citation as the first argument to run a one-shot "
            "lookup from the command line (useful for debugging)."
        ),
    )
    p.add_argument(
        "text",
        nargs=argparse.REMAINDER,
        help="Citation text to look up.  If omitted, the tray app starts.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print the raw lookup result as JSON (CLI mode only).",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"citationHop {__version__}",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    # CLI mode: any positional text passed.
    if args.text:
        # argparse.REMAINDER can capture a leading "--" if the user
        # wrote `python -m citation_hop -- "Smith (2020)"`.  Strip it.
        text_parts = [t for t in args.text if t != "--"]
        text = " ".join(text_parts)
        try:
            return _print_cli_result(text, json_mode=args.json)
        except Exception as e:  # noqa: BLE001
            print(f"[citationHop] lookup failed: {e}", file=sys.stderr)
            return 3

    # Tray mode.
    from .tray import main as tray_main
    return tray_main()


if __name__ == "__main__":
    sys.exit(main())