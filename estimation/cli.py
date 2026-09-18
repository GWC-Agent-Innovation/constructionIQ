"""Command-line harness for testing material estimation."""
from __future__ import annotations

import argparse
import json
import sys

from agent import spec_store

from .schemas import EstimatePreferences
from .service import estimate_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m estimation",
        description="Run a material-estimation test for a saved ConstructIQ project.",
    )
    parser.add_argument("session_id", help="Saved project session ID")
    parser.add_argument("--location", required=True, help="City or postcode used for price searches")
    parser.add_argument("--currency", default="INR", help="Three-letter currency code (default: INR)")
    parser.add_argument("--brand", action="append", dest="brands", default=[], help="Preferred brand; repeat for multiple brands")
    parser.add_argument("--wholesale", action="store_true", help="Use wholesale pricing")
    parser.add_argument("--include-delivery", action="store_true", help="Include delivery in price lookups")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        session = spec_store.load(args.session_id)
    except Exception as exc:
        print(f"Could not load session: {exc}", file=sys.stderr)
        return 2
    if session.layout is None:
        print(f"Session '{args.session_id}' has no generated layout yet.", file=sys.stderr)
        return 2
    try:
        preferences = EstimatePreferences(
            city_or_postcode=args.location,
            currency=args.currency.upper(),
            preferred_brands=args.brands,
            purchase_type="wholesale" if args.wholesale else "retail",
            include_delivery=args.include_delivery,
        )
        result = estimate_project(session.layout, preferences)
    except Exception as exc:
        print(f"Estimation test failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    print("\nPrice providers are not connected yet; this CLI currently verifies the estimation boundary.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
