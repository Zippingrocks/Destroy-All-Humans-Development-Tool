from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .profiles import PROFILES, profiles_as_dicts
from .source import inspect_source
from .verify import verification_report


DEFAULT_FIXTURE = Path(__file__).resolve().parent / "data" / "area42-retail-xbox-us.json"


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dahdev",
        description="Read-only inspection of Destroy All Humans! map sources.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    profiles = subparsers.add_parser("profiles", help="List planned and implemented edition profiles.")
    profiles.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    inspect = subparsers.add_parser("inspect", help="Inspect a map source directory without modifying it.")
    inspect.add_argument("path", help="Directory containing the map .dir and package files.")
    inspect.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    verify = subparsers.add_parser("verify", help="Verify the Area 42 golden fixture by size and SHA-256.")
    verify.add_argument("--source", required=True, help="Retail Area 42 source directory.")
    verify.add_argument("--reference", help="Corrected LOD/transform reference directory.")
    verify.add_argument("--fixture", default=str(DEFAULT_FIXTURE), help="Fixture manifest JSON.")
    verify.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    return parser


def _profiles(json_output: bool) -> int:
    if json_output:
        _print_json(profiles_as_dicts())
        return 0
    for profile in PROFILES:
        print(f"{profile.id:24} {profile.status:22} {profile.title}")
        print(f"  {profile.notes}")
    return 0


def _inspect(path: str, json_output: bool) -> int:
    result = inspect_source(path)
    if json_output:
        _print_json(result.to_dict())
    else:
        print(f"Path:             {result.path}")
        print(f"Container family: {result.container_family}")
        print(f"Map name:         {result.map_name or 'unknown'}")
        print(f"Complete shape:   {'yes' if result.complete else 'no'}")
        print(f"DIR files:        {', '.join(result.dir_files) or 'none'}")
        print(f"Packages:         {', '.join(result.present_packages) or 'none'}")
        for warning in result.warnings:
            print(f"Warning:          {warning}")
        print("Edition:          not inferred from filenames; verify a build-pinned fixture")
    return 0 if result.complete else 1


def _verify(args: argparse.Namespace) -> int:
    report = verification_report(args.fixture, args.source, args.reference)
    if args.json:
        _print_json(report)
    else:
        print(f"Fixture: {report['fixture_id']} ({report['profile_id']})")
        for section in ("source", "reference"):
            for check in report[section]:
                status = "OK" if check["ok"] else "FAIL"
                print(f"{status:4} {section:9} {check['name']}")
                if not check["ok"]:
                    print(f"     expected size={check['expected_size']} sha256={check['expected_sha256']}")
                    print(f"     actual   size={check['actual_size']} sha256={check['actual_sha256']}")
        if not report["reference_requested"]:
            print("Reference: not requested (source files only)")
        print(f"Verified: {'yes' if report['verified'] else 'no'}")
    return 0 if report["verified"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "profiles":
            return _profiles(args.json)
        if args.command == "inspect":
            return _inspect(args.path, args.json)
        if args.command == "verify":
            return _verify(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")
    parser.error(f"Unknown command: {args.command}")
    return 2

