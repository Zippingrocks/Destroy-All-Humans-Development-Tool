from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .dah1.container import BlockReader
from .dah1.materials import export_textured_section, write_material_outputs
from .dah1.terrain import read_terrain_scene, write_validation_outputs
from .editor_scene import export_area42_editor_scene
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

    terrain = subparsers.add_parser(
        "terrain-validate",
        help="Reconstruct Area 42 terrain from native structures and compare every reference array.",
    )
    terrain.add_argument("--source", required=True, help="Retail Area 42 block directory.")
    terrain.add_argument(
        "--reference",
        required=True,
        help="Verified reference NPZ or its containing directory.",
    )
    terrain.add_argument(
        "--out",
        required=True,
        help="Generated validation output directory; must not be inside the source.",
    )
    terrain.add_argument("--json", action="store_true", help="Emit the full report as JSON.")

    section = subparsers.add_parser(
        "terrain-section-export",
        help="Export one native Area 42 terrain placement as textured OBJ inspection data.",
    )
    section.add_argument("--source", required=True, help="Retail Area 42 block directory.")
    section.add_argument("--placement", required=True, type=int, help="Zero-based terrain placement.")
    section.add_argument("--lod", type=int, choices=(0, 1), default=0, help="LOD child to export.")
    section.add_argument("--out", required=True, help="Generated output directory.")
    section.add_argument("--json", action="store_true", help="Emit the manifest as JSON.")

    editor_scene = subparsers.add_parser(
        "editor-scene-export",
        help="Export verified Area 42 native terrain as a read-only CryptoEditor scene.",
    )
    editor_scene.add_argument("--source", required=True, help="Retail Area 42 block directory.")
    editor_scene.add_argument(
        "--reference",
        required=True,
        help="Verified reference NPZ or its containing directory.",
    )
    editor_scene.add_argument(
        "--out",
        required=True,
        help="New or empty output directory ending in .dahscene.",
    )
    editor_scene.add_argument("--json", action="store_true", help="Emit the export report as JSON.")

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


def _terrain_validate(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    output = Path(args.out).resolve()
    if source == output or source in output.parents:
        raise ValueError("validation output must not be written inside the retail source")

    reference = Path(args.reference)
    if reference.is_dir():
        reference = reference / "area42_retail_lods_and_transforms.npz"
    if not reference.is_file():
        raise FileNotFoundError(f"reference archive not found: {reference}")

    source_hashes_before = BlockReader(source).source_hashes()
    scene = read_terrain_scene(source)
    report = write_validation_outputs(
        scene, reference, output, source_hashes_before
    )
    materials = write_material_outputs(scene, output)
    source_hashes_after_materials = scene.reader.source_hashes()
    report["material_reconstruction"] = {
        "status": materials["status"],
        "counts": materials["counts"],
        "decoded_texture_previews": materials["decoded_texture_previews"],
        "report": str((output / "area42_terrain_materials.json").resolve()),
    }
    report["source_sha256_after"] = source_hashes_after_materials
    report["source_unchanged"] = source_hashes_before == source_hashes_after_materials
    (output / "area42_terrain_validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if args.json:
        _print_json(report)
    else:
        structural = report["structural_validation"]
        lods = structural["lod_stats"]
        print(f"Terrain placements:   {structural['terrain_placements']}")
        print(f"Geometry buffers:     {structural['geometry_buffers']}")
        print(f"Scene chain records:  {structural['scene_sibling_records']}")
        for lod in lods:
            print(
                f"LOD{lod['lod']}:                 "
                f"{lod['vertices']} vertices, {lod['triangles']} triangles, "
                f"{lod['segments']} segments"
            )
        print(
            "Reference arrays:    "
            + (
                "byte-for-byte match"
                if report["reference_comparison"]["all_arrays_byte_equal"]
                else "MISMATCH"
            )
        )
        print(f"Source unchanged:     {'yes' if report['source_unchanged'] else 'NO'}")
        print(
            "Native materials:     "
            f"{materials['counts']['unique_materials']} unique, "
            f"{materials['counts']['unique_bound_textures']} bound textures"
        )
        print(f"Report:               {output / 'area42_terrain_validation.json'}")

    passed = (
        report["reference_comparison"]["all_arrays_byte_equal"]
        and report["source_unchanged"]
    )
    return 0 if passed else 1


def _terrain_section_export(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    output = Path(args.out).resolve()
    if source == output or source in output.parents:
        raise ValueError("inspection output must not be written inside the retail source")
    scene = read_terrain_scene(source)
    if not 0 <= args.placement < len(scene.placements):
        raise ValueError(
            f"placement must be between 0 and {len(scene.placements) - 1}"
        )
    report = export_textured_section(scene, args.placement, args.lod, output)
    if args.json:
        _print_json(report)
    else:
        print(f"Placement/LOD:        {args.placement}/{args.lod}")
        print(f"Vertices:             {report['vertices']}")
        print(f"Triangles:            {report['triangles']}")
        print(f"Material slots:       {report['material_slots']}")
        print(f"OBJ inspection view:  {report['obj']}")
    return 0


def _editor_scene_export(args: argparse.Namespace) -> int:
    report = export_area42_editor_scene(args.source, args.reference, args.out)
    if args.json:
        _print_json(report)
    else:
        counts = report["counts"]
        print(f"Scene:                {report['scene']}")
        print(f"Terrain placements:   {counts['placements']}")
        print(f"Geometry/LOD records: {counts['geometries']}/{counts['lods']}")
        print(f"Materials/textures:   {counts['materials']}/{counts['textures']}")
        print("Reference arrays:     byte-for-byte match")
        print("Source unchanged:     yes")
        print(f"Manifest SHA-256:     {report['manifest_sha256']}")
        print(f"Buffer SHA-256:       {report['buffer_sha256']}")
    return 0


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
        if args.command == "terrain-validate":
            return _terrain_validate(args)
        if args.command == "terrain-section-export":
            return _terrain_section_export(args)
        if args.command == "editor-scene-export":
            return _editor_scene_export(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        parser.exit(2, f"error: {error}\n")
    parser.error(f"Unknown command: {args.command}")
    return 2

