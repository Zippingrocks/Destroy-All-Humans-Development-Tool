from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from .dah1.container import BlockReader, ResourceRecord
from .dah1.materials import (
    NativeMaterial,
    decode_texture_top_level,
    terrain_material_slots,
)
from .dah1.terrain import (
    TerrainScene,
    build_reference_arrays,
    compare_reference_arrays,
    read_terrain_scene,
)
from .dah1.world import (
    StaticMesh,
    classify_editor_collection,
    read_world_scene,
)
from .dah1.humans import read_humans, validate_human_archetype_reports
from .dah1.scatter import read_scatter_scene, scatter_report, material_render_state
from .dah1.spawns import read_spawn_scene
from .dah1.actor_visuals import read_actor_visuals
from .dah1.mission_visuals import read_landing_visual_scene
from .dah1.material_evidence import native_material_evidence, terrain_segment_render_state
from .dah1.scene_validation import validate_complete_scene


SCHEMA_NAME = "dah-editor-scene"
SCHEMA_VERSION = 4
AREA42_PROFILE = "dah1-retail-xbox-us"
AREA42_MAP = "area42"
AREA42_COUNTS = {
    "terrain_placements": 92,
    "terrain_lods": 184,
    "terrain_geometries": 184,
    "world_buckets": 41,
    "world_placements": 849,
    "building_placements": 18,
    "building_renderable_placements": 18,
    "building_geometries": 8,
    "prop_placements": 23,
    "prop_renderable_placements": 23,
    "prop_geometries": 11,
    "renderable_structure_placements": 41,
    "lod0_vertices": 122956,
    "lod0_triangles": 103634,
    "lod1_vertices": 16240,
    "lod1_triangles": 8120,
}
AREA42_V4_COUNTS = {**AREA42_COUNTS, "world_placements": 2674,
                   "building_placements": 79, "building_renderable_placements": 79,
                   "prop_placements": 1468, "prop_renderable_placements": 1468,
                   "renderable_structure_placements": 1547}
for _key in ("building_geometries", "prop_geometries"):
    del AREA42_V4_COUNTS[_key]


def _parts_lod(lod: Any) -> dict[str, object]:
    parts = [{"geometry": f"geometry:static:{p.visual.mesh.resource.name_hash:08x}",
              "material_slots": [f"material:{m.resource.name_hash:08x}" for m in p.visual.materials],
              "matrix_row_major_f32": p.matrix.reshape(-1).tolist(),
              "visual": _resource(p.visual.resource), "bone_index": p.bone_index,
              "table_offset": p.table_offset, "raw_table_record_hex": p.raw_table_record.hex()}
             for p in lod.parts]
    return {"index": lod.index, "geometry": parts[0]["geometry"],
            "material_slots": parts[0]["material_slots"], "parts": parts,
            "visual": _resource(lod.resource), "preview_status": lod.pose_status}


def _skeleton_report(skeleton: Any) -> dict[str, object] | None:
    if skeleton is None:
        return None
    return {"load_group": skeleton.load_group, "shared_offset": skeleton.shared_offset,
            "raw_shared_header_hex": skeleton.raw_shared_header.hex(),
            "bone_buffer_offset": skeleton.bone_buffer_offset,
            "bones": [{"index": b.index, "offset": b.offset, "name_hash": f"0x{b.name_hash:08X}",
                       "parent_index": b.parent_index, "raw_hex": b.raw_bytes.hex(),
                       "local_position_f32": list(b.local_position),
                       "local_quaternion_xyzw_f32": list(b.local_quaternion_xyzw),
                       "local_scale_f32": b.local_scale} for b in skeleton.bones]}


def _human_geometry_entry(mesh: Any, buffer: _BufferBuilder) -> dict[str, object]:
    return {
        "id": f"geometry:human:{mesh.resource.name_hash:08x}",
        "kind": "human-reference-pose",
        "resource": _resource(mesh.resource),
        "preview_status": "native-reference-mesh; animation-not-applied",
        "raw_descriptor_hex": mesh.raw_descriptor.hex(),
        "counts": {"vertices": len(mesh.local_positions), "indices": len(mesh.indices),
                   "triangles": len(mesh.triangles), "segments": len(mesh.segments)},
        "native_offsets": {"vertex_buffer": mesh.vertex_buffer_offset,
                           "index_buffer": mesh.index_buffer_offset,
                           "segment_buffer": mesh.segment_buffer_offset},
        "vertex_layout": {
            "stride": 40,
            "position": {"offset": 0, "format": "float32x3-little-endian", "status": "verified"},
            "normal": {"offset": 12, "format": "float32x3-little-endian", "status": "verified"},
            "weights": {"offset": 24, "format": "uint8x4", "status": "structurally-verified"},
            "bone_indices": {"offset": 28, "format": "uint8x4-palette-slot-times-six", "status": "structurally-verified"},
            "uv": {"offset": 32, "format": "float32x2-little-endian", "status": "verified"},
        },
        "buffers": {
            "vertices": buffer.add(mesh.vertex_bytes.tobytes()),
            "index_strip": buffer.add(mesh.indices.astype("<u2", copy=False).tobytes()),
            "triangles": buffer.add(mesh.triangles.astype("<u4", copy=False).tobytes()),
            "segments": buffer.add(b"".join(s.raw_bytes for s in mesh.segments)),
        },
        "segments": [{"index": s.index, "vertex_start": s.vertex_start,
                      "vertex_count": s.vertex_count, "index_start": s.index_start,
                      "index_count": s.index_count, "triangle_count": s.triangle_count,
                      "material_slot": s.material_slot, "native_offset": s.offset,
                      "bone_palette": [list(pair) for pair in s.palette]}
                     for s in mesh.segments],
    }


def _scatter_geometry_id(template: Any) -> str:
    return f"geometry:scatter:{template.load_group}:{template.vertex_buffer_offset:08x}"


def _scatter_geometry_entry(template: Any, buffer: _BufferBuilder) -> dict[str, object]:
    return {
        "id": _scatter_geometry_id(template), "kind": "static-scatter",
        "counts": {"vertices": len(template.local_positions), "indices": len(template.indices),
                   "triangles": len(template.triangles), "segments": 1},
        "native_offsets": {"load_group": template.load_group,
                           "vertex_buffer": template.vertex_buffer_offset,
                           "index_buffer": template.index_buffer_offset},
        "vertex_layout": {
            "stride": 24,
            "position": {"offset": 0, "format": "float32x3-little-endian", "status": "verified"},
            "uv": {"offset": 12, "format": "float32x2-little-endian", "status": "verified"},
            "constant_selector": {"offset": 20, "format": "float32-little-endian", "status": "verified"},
        },
        "buffers": {
            "vertices": buffer.add(template.vertex_bytes.tobytes()),
            "index_strip": buffer.add(template.indices.astype("<u2", copy=False).tobytes()),
            "triangles": buffer.add(template.triangles.astype("<u4", copy=False).tobytes()),
            "segments": buffer.add(b""),
            "native_draw_vertices": buffer.add(template.native_draw_vertex_bytes),
            "native_draw_indices": buffer.add(template.native_draw_index_bytes),
        },
        "segment_origin": "one native material per scatter batch; no native segment table",
        "segments": [{"index": 0, "triangle_count": len(template.triangles), "material_slot": 0}],
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resource(resource: ResourceRecord) -> dict[str, object]:
    return {
        "index": resource.index,
        "type_hash": f"0x{resource.type_hash:08X}",
        "name_hash": f"0x{resource.name_hash:08X}",
        "load_group": resource.load_group,
        "offset": resource.offset,
        "size": resource.size,
        "flags": f"0x{resource.flags:08X}",
    }


class _BufferBuilder:
    def __init__(self) -> None:
        self.data = bytearray()

    def add(self, payload: bytes, *, alignment: int = 16) -> dict[str, object]:
        if alignment <= 0 or alignment & (alignment - 1):
            raise ValueError("buffer alignment must be a positive power of two")
        padding = (-len(self.data)) & (alignment - 1)
        self.data.extend(bytes(padding))
        offset = len(self.data)
        self.data.extend(payload)
        return {
            "offset": offset,
            "length": len(payload),
            "sha256": _sha256(payload),
        }


def _static_geometry_entry(mesh: StaticMesh, buffer: _BufferBuilder) -> dict[str, object]:
    segment_bytes = np.asarray(
        [segment.raw_record for segment in mesh.segments], dtype="<u2"
    ).tobytes()
    return {
        "id": f"geometry:static:{mesh.resource.name_hash:08x}",
        "kind": "static-world-object",
        "resource": _resource(mesh.resource),
        "counts": {
            "vertices": len(mesh.local_positions),
            "indices": len(mesh.indices),
            "triangles": len(mesh.triangles),
            "segments": len(mesh.segments),
        },
        "native_offsets": {
            "vertex_buffer": mesh.vertex_buffer_offset,
            "index_buffer": mesh.index_buffer_offset,
            "secondary_buffer": mesh.normal_buffer_offset,
            "segment_buffer": mesh.segment_buffer_offset,
        },
        "vertex_layout": {
            "stride": mesh.stride,
            "position": {
                "offset": 0,
                "format": "float32x3-little-endian",
                "status": "verified",
            },
            "uv": {
                "offset": 12,
                "format": "float32x2-little-endian",
                "status": "verified",
            },
            "normal": {
                "offset": 20,
                "format": "snorm16x4-little-endian",
                "status": "structurally-verified",
            },
        },
        "buffers": {
            "vertices": buffer.add(mesh.vertex_bytes.tobytes()),
            "index_strip": buffer.add(mesh.indices.astype("<u2", copy=False).tobytes()),
            "triangles": buffer.add(mesh.triangles.astype("<u4", copy=False).tobytes()),
            "segments": buffer.add(segment_bytes),
        },
        "segments": [
            {
                "index": segment.index,
                "vertex_start": segment.vertex_start,
                "vertex_count": segment.vertex_count,
                "index_start": segment.index_start,
                "index_count": segment.index_count,
                "triangle_count": segment.triangle_count,
                "material_slot": segment.material_slot,
            }
            for segment in mesh.segments
        ],
    }


def _safe_relative_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} escapes the scene directory")
    resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError(f"{label} escapes the scene directory")
    return resolved


def _require_int(mapping: dict[str, Any], key: str, label: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label}.{key} must be a non-negative integer")
    return value


def validate_editor_scene_directory(path: str | Path) -> dict[str, object]:
    root = Path(path).resolve()
    manifest_path = root / "scene.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"editor scene manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("editor scene manifest root must be an object")

    schema = manifest.get("schema")
    if not isinstance(schema, dict):
        raise ValueError("editor scene schema must be an object")
    if schema.get("name") != SCHEMA_NAME or schema.get("version") not in (2, 3, SCHEMA_VERSION):
        raise ValueError("unsupported editor scene schema")
    if manifest.get("read_only") is not True:
        raise ValueError("editor scene must be explicitly read-only")

    buffers = manifest.get("buffers")
    if not isinstance(buffers, dict):
        raise ValueError("editor scene buffers must be an object")
    buffer_path = _safe_relative_path(root, buffers.get("file"), "buffers.file")
    payload = buffer_path.read_bytes()
    if len(payload) != _require_int(buffers, "byte_length", "buffers"):
        raise ValueError("editor scene buffer length mismatch")
    if _sha256(payload) != buffers.get("sha256"):
        raise ValueError("editor scene buffer SHA-256 mismatch")

    geometries = manifest.get("geometries")
    placements = manifest.get("placements")
    object_placements = manifest.get("object_placements")
    counts = manifest.get("counts")
    if (
        not isinstance(geometries, list)
        or not isinstance(placements, list)
        or not isinstance(object_placements, list)
    ):
        raise ValueError("editor scene geometry and placement collections must be arrays")
    if not isinstance(counts, dict):
        raise ValueError("editor scene counts must be an object")
    if len(geometries) != _require_int(counts, "geometries", "counts"):
        raise ValueError("editor scene geometry count mismatch")
    if len(placements) != _require_int(counts, "terrain_placements", "counts"):
        raise ValueError("editor scene terrain placement count mismatch")
    if len(object_placements) != _require_int(counts, "world_placements", "counts"):
        raise ValueError("editor scene world placement count mismatch")

    geometry_ids: set[str] = set()
    for geometry in geometries:
        if not isinstance(geometry, dict) or not isinstance(geometry.get("id"), str):
            raise ValueError("editor scene geometry lacks a string id")
        geometry_id = geometry["id"]
        if geometry_id in geometry_ids:
            raise ValueError(f"duplicate editor scene geometry id: {geometry_id}")
        geometry_ids.add(geometry_id)
        views = geometry.get("buffers")
        if not isinstance(views, dict):
            raise ValueError(f"geometry {geometry_id} lacks buffer views")
        if not {"vertices", "index_strip", "triangles", "segments"}.issubset(views):
            raise ValueError(f"geometry {geometry_id} lacks required buffer views")
        for semantic, view in views.items():
            if not isinstance(view, dict):
                raise ValueError(f"geometry {geometry_id} lacks {semantic} buffer view")
            offset = _require_int(view, "offset", f"geometry {geometry_id}.{semantic}")
            length = _require_int(view, "length", f"geometry {geometry_id}.{semantic}")
            if offset + length > len(payload):
                raise ValueError(f"geometry {geometry_id} {semantic} buffer view is out of range")
            if _sha256(payload[offset : offset + length]) != view.get("sha256"):
                raise ValueError(f"geometry {geometry_id} {semantic} SHA-256 mismatch")

    lod_count = 0
    for placement in placements:
        if not isinstance(placement, dict):
            raise ValueError("editor scene placement must be an object")
        matrix = placement.get("matrix_row_major_f32")
        if not isinstance(matrix, list) or len(matrix) != 16:
            raise ValueError("editor scene placement matrix must contain 16 values")
        lods = placement.get("lods")
        if not isinstance(lods, list):
            raise ValueError("editor scene placement LODs must be an array")
        lod_count += len(lods)
        for lod in lods:
            if not isinstance(lod, dict) or lod.get("geometry") not in geometry_ids:
                raise ValueError("editor scene LOD references an unknown geometry")
    if lod_count != _require_int(counts, "terrain_lods", "counts"):
        raise ValueError("editor scene terrain LOD count mismatch")

    building_count = 0
    renderable_building_count = 0
    prop_count = 0
    renderable_prop_count = 0
    for placement in object_placements:
        if not isinstance(placement, dict):
            raise ValueError("editor scene object placement must be an object")
        matrix = placement.get("matrix_row_major_f32")
        if not isinstance(matrix, list) or len(matrix) != 16:
            raise ValueError("editor scene object matrix must contain 16 values")
        lods = placement.get("lods")
        if not isinstance(lods, list):
            raise ValueError("editor scene object LODs must be an array")
        if placement.get("editor_collection") == "buildings":
            building_count += 1
            if lods:
                renderable_building_count += 1
        elif placement.get("editor_collection") == "props":
            prop_count += 1
            if lods:
                renderable_prop_count += 1
        for lod in lods:
            if not isinstance(lod, dict) or lod.get("geometry") not in geometry_ids:
                raise ValueError("editor scene object LOD references an unknown geometry")
    if building_count != _require_int(counts, "building_placements", "counts"):
        raise ValueError("editor scene building placement count mismatch")
    if renderable_building_count != _require_int(
        counts, "building_renderable_placements", "counts"
    ):
        raise ValueError("editor scene renderable building placement count mismatch")
    if prop_count != _require_int(counts, "prop_placements", "counts"):
        raise ValueError("editor scene prop placement count mismatch")
    if renderable_prop_count != _require_int(
        counts, "prop_renderable_placements", "counts"
    ):
        raise ValueError("editor scene renderable prop placement count mismatch")

    if schema.get("version") >= 3:
        validate_human_archetype_reports(manifest.get("human_archetypes"), object_placements)
        expansion_arrays = {key: manifest.get(key) for key in
                            ("scatter_placements", "scatter_batches", "scene_markers", "spawn_definitions")}
        for key, items in expansion_arrays.items():
            if not isinstance(items, list) or len(items) != _require_int(counts, key, "counts"):
                raise ValueError(f"editor scene {key} count mismatch")
            ids = [item.get("id") if isinstance(item, dict) else None for item in items]
            if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                raise ValueError(f"editor scene {key} ids must be unique nonempty strings")
        for placement in [*object_placements, *placements, *expansion_arrays["scatter_placements"]]:
            matrix = placement.get("matrix_row_major_f32")
            if not isinstance(matrix, list) or len(matrix) != 16 or any(
                not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in matrix
            ):
                raise ValueError("editor scene transform must contain 16 finite numbers")
            lods = placement.get("lods")
            if not isinstance(lods, list) or any(lod.get("geometry") not in geometry_ids for lod in lods):
                raise ValueError("editor scene expanded placement references an unknown geometry")
        batches = {b["id"]: b for b in expansion_arrays["scatter_batches"]}
        for batch in batches.values():
            view = batch.get("instance_buffer", {})
            offset = _require_int(view, "offset", "scatter instance buffer")
            length = _require_int(view, "length", "scatter instance buffer")
            if (length != _require_int(batch, "instance_count", "scatter batch") * 80
                or offset + length > len(payload)
                or _sha256(payload[offset:offset + length]) != view.get("sha256")):
                raise ValueError("scatter instance buffer size or SHA-256 mismatch")
        seen_instances = set()
        for placement in expansion_arrays["scatter_placements"]:
            native = placement.get("native", {})
            batch = batches.get(native.get("batch"))
            index = _require_int(native, "instance_index", "scatter placement")
            pair = (native.get("batch"), index)
            if batch is None or index >= batch["instance_count"] or pair in seen_instances:
                raise ValueError("scatter placement has duplicate or invalid native instance")
            seen_instances.add(pair)
        if len(seen_instances) != sum(b["instance_count"] for b in batches.values()):
            raise ValueError("scatter placements do not account for every native instance")
        for marker in expansion_arrays["scene_markers"]:
            position = marker.get("position_f32")
            if (not isinstance(position, list) or len(position) != 3
                or any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) for v in position)):
                raise ValueError("scene marker position must contain three finite numbers")
            if any(not isinstance(marker.get(k), str) or not marker[k] for k in ("kind", "label", "status")):
                raise ValueError("scene marker lacks its kind, label or status")
        for collection, key in (("humans", "human"),):
            members = [p for p in object_placements if p.get("editor_collection") == collection]
            if len(members) != _require_int(counts, f"{key}_placements", "counts") or sum(bool(p["lods"]) for p in members) != _require_int(counts, f"{key}_renderable_placements", "counts"):
                raise ValueError(f"editor scene {key} placement count mismatch")
        if sum(g.get("kind") == "static-scatter" for g in geometries) != _require_int(counts, "scatter_geometries", "counts") or sum(g.get("kind") == "human-reference-pose" for g in geometries) != _require_int(counts, "human_geometries", "counts"):
            raise ValueError("editor scene expanded geometry count mismatch")
        if sum(m["kind"] == "landing-site" for m in expansion_arrays["scene_markers"]) != _require_int(counts, "landing_sites", "counts"):
            raise ValueError("editor scene landing site count mismatch")

    textures = manifest.get("textures")
    if not isinstance(textures, list):
        raise ValueError("editor scene textures must be an array")
    for texture in textures:
        if not isinstance(texture, dict):
            raise ValueError("editor scene texture must be an object")
        preview = texture.get("preview")
        if not isinstance(preview, dict):
            raise ValueError("editor scene texture lacks its preview")
        preview_path = _safe_relative_path(root, preview.get("file"), "texture.preview.file")
        if _file_sha256(preview_path) != preview.get("sha256"):
            raise ValueError("editor scene texture preview SHA-256 mismatch")

    if manifest.get("profile_id") == AREA42_PROFILE and manifest.get("map_name") == AREA42_MAP:
        for key, expected in (AREA42_V4_COUNTS if schema["version"] >= 4 else AREA42_COUNTS).items():
            if counts.get(key) != expected:
                raise ValueError(
                    f"Area 42 editor scene count mismatch for {key}: "
                    f"expected {expected}, got {counts.get(key)}"
                )
        if schema.get("version") >= 3:
            for key, expected in {"human_placements": 295, "human_renderable_placements": 295,
                                  "scatter_batches": 654, "scatter_placements": 15121,
                                  "scatter_geometries": 5, "landing_sites": 3}.items():
                if counts.get(key) != expected:
                    raise ValueError(f"Area 42 {key} count mismatch")
        expected_geometries = 203 + counts.get("human_geometries", 0) + counts.get("scatter_geometries", 0)
        if schema["version"] < 4 and counts["geometries"] != expected_geometries:
            raise ValueError("Area 42 geometry count mismatch")

    if schema["version"] >= 4:
        validate_complete_scene(manifest, payload)

    return {
        "schema_version": schema["version"],
        "profile_id": manifest.get("profile_id"),
        "map_name": manifest.get("map_name"),
        "counts": counts,
        "manifest_sha256": _file_sha256(manifest_path),
        "buffer_sha256": buffers["sha256"],
    }


def _material_entry(material: NativeMaterial) -> dict[str, object]:
    binding = material.binding
    return {
        "id": f"material:{material.resource.name_hash:08x}",
        "resource": _resource(material.resource),
        "raw_sha256": _sha256(material.raw_bytes),
        "render_flags": material.render_flags,
        "render_state": material_render_state(material),
        "native_material_evidence": native_material_evidence(material, kind="shared-resource"),
        "raw_hex": material.raw_bytes.hex(),
        "sampler": {
            "address_u": material.address_u,
            "address_v": material.address_v,
            "status": "verified",
            "evidence": (
                "DAH1 US Xbox retail material setup at 0xE5C00: +0x7C bit "
                "0x1000 clamps U and bit 0x2000 clamps V; unset axes wrap"
            ),
        },
        "binding_pointer_field": material.binding_pointer_field,
        "binding": (
            {
                "offset": binding.offset,
                "raw_hex": binding.raw_bytes.hex(),
                "format_word": binding.format_word,
                "format": binding.format_name,
                "mip_levels": binding.mip_levels,
                "width": binding.width,
                "height": binding.height,
                "texture": f"texture:{binding.texture_hash:08x}",
            }
            if binding is not None
            else None
        ),
    }


def _resolve_reference(reference: str | Path) -> Path:
    path = Path(reference).resolve()
    if path.is_dir():
        path = path / "area42_retail_lods_and_transforms.npz"
    if not path.is_file():
        raise FileNotFoundError(f"reference archive not found: {path}")
    return path


def export_area42_editor_scene(
    source_dir: str | Path,
    reference: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    source = Path(source_dir).resolve()
    output = Path(output_dir).resolve()
    reference_file = _resolve_reference(reference)
    if output.suffix.lower() != ".dahscene":
        raise ValueError("editor scene output directory must end in .dahscene")
    if source == output or source in output.parents:
        raise ValueError("editor scene output must not be written inside the retail source")
    if output.exists() and any(output.iterdir()):
        raise ValueError("editor scene output directory must be absent or empty")

    source_hashes_before = BlockReader(source).source_hashes()
    scene = read_terrain_scene(source)
    world = read_world_scene(scene.reader, include_visuals=False)
    humans = read_humans(world)
    actors = read_actor_visuals(world)
    scatter = read_scatter_scene(scene)
    spawns = read_spawn_scene(scene.reader, world)
    landing = read_landing_visual_scene(scene.reader, spawns)
    human_archetypes = {
        p.actor.resource.name_hash: humans.archetypes[p.actor.resource.name_hash]
        for p in humans.placements
    }
    skin_archetypes = humans.archetypes
    comparison = compare_reference_arrays(build_reference_arrays(scene), reference_file)
    if not comparison["all_arrays_byte_equal"]:
        raise ValueError("native terrain does not match the verified reference arrays")

    child_counts = {len(placement.children) for placement in scene.placements}
    if child_counts != {2}:
        raise ValueError(f"Area 42 export requires two LODs per placement, got {child_counts}")

    buffer = _BufferBuilder()
    geometry_entries: list[dict[str, object]] = []
    for name_hash, geometry in sorted(scene.geometries.items()):
        segment_bytes = np.asarray(
            [segment.raw_record for segment in geometry.segments], dtype="<u2"
        ).tobytes()
        triangle_bytes = geometry.triangles.astype("<u4", copy=False).tobytes()
        geometry_entries.append(
            {
                "id": f"geometry:{name_hash:08x}",
                "kind": "terrain",
                "resource": _resource(geometry.resource),
                "counts": {
                    "vertices": len(geometry.local_positions),
                    "indices": len(geometry.indices),
                    "triangles": len(geometry.triangles),
                    "segments": len(geometry.segments),
                },
                "native_offsets": {
                    "vertex_buffer": geometry.vertex_buffer_offset,
                    "index_buffer": geometry.index_buffer_offset,
                    "segment_buffer": geometry.segment_buffer_offset,
                },
                "vertex_layout": {
                    "stride": 24,
                    "position": {
                        "offset": 0,
                        "format": "float32x3-little-endian",
                        "status": "verified",
                    },
                    "uv": {
                        "offset": 12,
                        "format": "float32x2-little-endian",
                        "status": "verified",
                    },
                    "diffuse_color": {
                        "offset": 20,
                        "format": "D3DCOLOR-BGRA8",
                        "status": "verified",
                        "evidence": (
                            "DAH1 US Xbox retail terrain declaration at 0xECA10: "
                            "FLOAT3, FLOAT2, D3DCOLOR in a stride-24 stream"
                        ),
                    },
                },
                "buffers": {
                    "vertices": buffer.add(geometry.vertex_bytes.tobytes()),
                    "index_strip": buffer.add(
                        geometry.indices.astype("<u2", copy=False).tobytes()
                    ),
                    "triangles": buffer.add(triangle_bytes),
                    "segments": buffer.add(segment_bytes),
                },
                "segments": [
                    {
                        "index": segment.index,
                        "vertex_start": segment.vertex_start,
                        "vertex_count": segment.vertex_count,
                        "index_start": segment.index_start,
                        "index_count": segment.index_count,
                        "triangle_count": segment.triangle_count,
                        "raw_flags": f"0x{segment.raw_flags:04X}",
                        "material_slot": segment.raw_flags & 0xFF,
                        "terrain_layer": segment.raw_flags >> 8,
                    }
                    for segment in geometry.segments
                ],
            }
        )

    building_placements_native = [
        placement
        for placement in world.placements
        if placement.actor is not None
        and classify_editor_collection(
            placement.actor.native_class, placement.actor.asset_name
        )
        == "buildings"
    ]
    prop_placements_native = [
        placement
        for placement in world.placements
        if placement.actor is not None
        and classify_editor_collection(
            placement.actor.native_class, placement.actor.asset_name
        )
        == "props"
    ]
    renderable_structure_placements_native = (
        building_placements_native + prop_placements_native
    )
    static_meshes = {
        part.visual.mesh.resource.name_hash: part.visual.mesh
        for actor in actors.archetypes.values()
        for lod in actor.lods for part in lod.parts
    }
    static_meshes.update({part.visual.mesh.resource.name_hash: part.visual.mesh
                          for lod in landing.lods for part in lod.parts})
    for _, mesh in sorted(static_meshes.items()):
        geometry_entries.append(_static_geometry_entry(mesh, buffer))
    human_meshes = {
        lod.visual.mesh.resource.name_hash: lod.visual.mesh
        for actor in skin_archetypes.values() for lod in actor.lods
    }
    for _, mesh in sorted(human_meshes.items()):
        geometry_entries.append(_human_geometry_entry(mesh, buffer))
    for template in scatter.templates:
        geometry_entries.append(_scatter_geometry_entry(template, buffer))

    scatter_batches = []
    scatter_placements = []
    for p in scatter.placements:
        batch = p.batch
        batch_id = f"scatter-batch:{batch.resource.name_hash:08x}"
        scatter_batches.append({
            "id": batch_id, "resource": _resource(batch.resource),
            "raw_descriptor_hex": batch.raw_bytes.hex(),
            "source_name_hash": f"0x{batch.source_name_hash:08X}",
            "scene_instance_offset": p.offset, "raw_scene_header_hex": p.raw_header.hex(),
            "instance_buffer_offset": batch.instance_buffer_offset,
            "instance_count": len(batch.instances), "instance_stride": 80,
            "instance_buffer": buffer.add(b"".join(i.raw_bytes for i in batch.instances)),
            "fade_distances_f32": list(batch.fade_distances),
            "unknown_tail_f32": list(batch.unknown_tail_f32),
            "material_pointer_field": batch.material_pointer_field,
            "geometry": _scatter_geometry_id(batch.template),
        })
        for instance in batch.instances:
            matrix = p.world_matrix(instance)
            scatter_placements.append({
                "id": f"{batch_id}:instance:{instance.index:04d}",
                "editor_collection": "static-scatter",
                "archetype": {"native_class": "native-static-scatter",
                              "asset_name": f"source:0x{batch.source_name_hash:08X}"},
                "preview_status": "native-static-transform; wind-and-distance-fade-not-simulated",
                "native": {"batch": batch_id, "instance_index": instance.index,
                           "record_offset": instance.offset,
                           "unknown_word_4c": f"0x{instance.unknown_word:08X}",
                           "matrix_conversion": "native column-vector matrix transposed then composed with scene placement"},
                "matrix_row_major_f32": [float(v) for v in matrix.reshape(-1)],
                "instance_color_rgb_f32": [float(v) for v in instance.color_rgb],
                "lods": [{"index": 0, "geometry": _scatter_geometry_id(batch.template),
                          "material_slots": [f"material:{batch.material.resource.name_hash:08x}"]}],
            })

    material_records: dict[int, NativeMaterial] = {}
    texture_images: dict[int, tuple[object, object]] = {}
    placement_lod_slots: dict[tuple[int, int], list[str]] = {}
    placement_lod_states: dict[tuple[int, int], list[dict[str, object]]] = {}
    for placement_index, placement in enumerate(scene.placements):
        for lod in placement.children:
            slots = terrain_material_slots(scene.reader, lod)
            placement_lod_slots[(placement_index, lod.index)] = [
                f"material:{slot.material.resource.name_hash:08x}" for slot in slots
            ]
            placement_lod_states[(placement_index, lod.index)] = [
                terrain_segment_render_state(slots[s.raw_flags & 0xFF].material, s.raw_flags)
                for s in lod.geometry.segments]
            for slot in slots:
                material = slot.material
                material_records[material.resource.name_hash] = material
                binding = material.binding
                if binding is not None and binding.texture_hash not in texture_images:
                    texture_images[binding.texture_hash] = (
                        binding,
                        decode_texture_top_level(scene.reader, binding),
                    )

    all_rigid_lods = [lod for actor in actors.archetypes.values() for lod in actor.lods] + list(landing.lods)
    for rigid_lod in all_rigid_lods:
        for lod in rigid_lod.parts:
            for material in lod.visual.materials:
                material_records[material.resource.name_hash] = material
                binding = material.binding
                if binding is not None and binding.texture_hash not in texture_images:
                    texture_images[binding.texture_hash] = (
                        binding,
                        decode_texture_top_level(scene.reader, binding),
                    )

    for actor in skin_archetypes.values():
        for lod in actor.lods:
            for material in lod.visual.materials:
                material_records[material.resource.name_hash] = material
                binding = material.binding
                if binding is not None and binding.texture_hash not in texture_images:
                    texture_images[binding.texture_hash] = (
                        binding, decode_texture_top_level(scene.reader, binding)
                    )
    for batch in scatter.batches:
        material = batch.material
        material_records[material.resource.name_hash] = material
        binding = material.binding
        if binding is not None and binding.texture_hash not in texture_images:
            texture_images[binding.texture_hash] = (binding, decode_texture_top_level(scene.reader, binding))

    output.mkdir(parents=True, exist_ok=True)
    texture_dir = output / "textures"
    texture_dir.mkdir(exist_ok=True)
    texture_entries: list[dict[str, object]] = []
    for texture_hash, (binding, image) in sorted(texture_images.items()):
        encoded = io.BytesIO()
        image.save(encoded, format="PNG", optimize=False)
        png = encoded.getvalue()
        relative_path = Path("textures") / f"{texture_hash:08x}.png"
        (output / relative_path).write_bytes(png)
        texture_entries.append(
            {
                "id": f"texture:{texture_hash:08x}",
                "resource": _resource(binding.texture_resource),
                "native": {
                    "format_word": binding.format_word,
                    "format": binding.format_name,
                    "mip_levels": binding.mip_levels,
                    "width": binding.width,
                    "height": binding.height,
                },
                "preview": {
                    "file": relative_path.as_posix(),
                    "format": "RGBA8 PNG",
                    "sha256": _sha256(png),
                },
            }
        )

    placements: list[dict[str, object]] = []
    for placement_index, placement in enumerate(scene.placements):
        placements.append(
            {
                "id": f"terrain-placement:{placement_index:03d}",
                "index": placement_index,
                "native": {
                    "instance_offset": placement.offset,
                    "name_hash": f"0x{placement.name_hash:08X}",
                    "flags": f"0x{placement.flags:08X}",
                    "lod_resource": _resource(placement.lod_resource),
                    "child_array_offset": placement.child_array_offset,
                    "parent_offset": placement.parent_offset,
                    "next_sibling_offset": placement.next_sibling_offset,
                },
                "position_f32": [float(value) for value in placement.position],
                "quaternion_xyzw_f32": [
                    float(value) for value in placement.quaternion_xyzw
                ],
                "scale_f32": float(placement.scale),
                "matrix_row_major_f32": [
                    float(value) for value in placement.matrix.reshape(-1)
                ],
                "lod_parameters_f32": list(placement.lod_parameters),
                "lods": [
                    {
                        "index": lod.index,
                        "wrapper": _resource(lod.wrapper),
                        "geometry": f"geometry:{lod.geometry.resource.name_hash:08x}",
                        "material_slots": placement_lod_slots[
                            (placement_index, lod.index)
                        ],
                        "segment_render_states": placement_lod_states[(placement_index, lod.index)],
                    }
                    for lod in placement.children
                ],
            }
        )

    object_placements: list[dict[str, object]] = []
    for placement in world.placements:
        actor = placement.actor
        human = skin_archetypes.get(actor.resource.name_hash) if actor else None
        rigid = actors.archetypes.get(actor.resource.name_hash) if actor else None
        collection = classify_editor_collection(
            actor.native_class if actor else None,
            actor.asset_name if actor else None,
        )
        object_placements.append(
            {
                "id": (
                    f"world-placement:{placement.bucket_index:02d}:"
                    f"{placement.index:04d}"
                ),
                "editor_collection": collection,
                "preview_status": ("native-reference-mesh; animation-not-applied" if human
                                   else "native-reference-pose; animation-and-component-visibility-not-applied" if rigid else "population-definition; not-a-fixed-actor"),
                "native": {
                    "bucket_index": placement.bucket_index,
                    "bucket_hash": f"0x{placement.bucket_hash:08X}",
                    "record_index": placement.index,
                    "record_offset": placement.offset,
                    "raw_header_hex": placement.raw_header.hex(),
                    "raw_header_sha256": _sha256(placement.raw_header),
                    "packed_yaw_quaternion_zw": f"0x{placement.packed_rotation:08X}",
                    "packed_quaternion_xy": f"0x{placement.packed_rotation_xy:08X}",
                    "list_pointer_field": placement.list_pointer_field,
                    "list_offset": placement.list_offset,
                    "list_index": placement.list_index,
                },
                "archetype": (
                    {
                        "resource": _resource(actor.resource),
                        "native_class": actor.native_class,
                        "asset_name": actor.asset_name,
                        "lod_resource": (
                            _resource(actor.lod_resource)
                            if actor.lod_resource is not None
                            else None
                        ),
                    }
                    if actor is not None
                    else None
                ),
                "position_f32": [float(value) for value in placement.position],
                "quaternion_xyzw_f32": [
                    float(value) for value in placement.quaternion_xyzw
                ],
                "scale_f32": float(placement.scale),
                "matrix_row_major_f32": [
                    float(value) for value in placement.matrix.reshape(-1)
                ],
                "lods": [
                    {
                        "index": lod.index,
                        "visual": _resource(lod.visual.resource),
                        "source_visual_type": (
                            f"0x{lod.visual.source_visual_type:08X}"
                        ),
                        "destruction_state": lod.visual.destruction_state,
                        "geometry": (
                            ("geometry:human:" if human else "geometry:static:")
                            +
                            f"{lod.visual.mesh.resource.name_hash:08x}"
                        ),
                        "material_slots": [
                            f"material:{material.resource.name_hash:08x}"
                            for material in lod.visual.materials
                        ],
                    }
                    for lod in (human.lods if human else ())
                ]
                if actor is not None
                else [],
            }
        )
        if rigid is not None:
            object_placements[-1]["lods"] = [_parts_lod(lod) for lod in rigid.lods]

    landing_placements = [{"id": "landing-visual:" + p.label,
                           "editor_collection": "landing-sites",
                           "archetype": {"native_class": "MarkerType", "asset_name": "marker_landing_zone",
                                         "resource": _resource(landing.actor_resource)},
                           "preview_status": landing.report["status"],
                           "native": p.provenance,
                           "position_f32": p.position.tolist(),
                           "matrix_row_major_f32": p.matrix.reshape(-1).tolist(),
                           "lods": [_parts_lod(lod) for lod in landing.lods]}
                          for p in landing.placements]
    rendered_ids = {p["id"] for p in object_placements if p["lods"]}
    scene_markers = [m for m in spawns.scene_markers
                     if not m.get("world_placement") or
                     f"world-placement:{m['world_placement']['bucket_index']:02d}:{m['world_placement']['index']:04d}" not in rendered_ids]

    def collection_meshes(collection: str) -> set[str]:
        return {part["geometry"] for p in object_placements if p["editor_collection"] == collection
                for lod in p["lods"] for part in lod.get("parts", [lod])}

    lod0_vertices = sum(
        len(placement.children[0].geometry.local_positions)
        for placement in scene.placements
    )
    lod1_vertices = sum(
        len(placement.children[1].geometry.local_positions)
        for placement in scene.placements
    )
    lod0_triangles = sum(
        len(placement.children[0].geometry.triangles)
        for placement in scene.placements
    )
    lod1_triangles = sum(
        len(placement.children[1].geometry.triangles)
        for placement in scene.placements
    )
    counts = {
        "terrain_placements": len(placements),
        "terrain_lods": sum(len(placement.children) for placement in scene.placements),
        "terrain_geometries": len(scene.geometries),
        "world_buckets": len(world.buckets),
        "world_placements": len(object_placements),
        "building_placements": len(building_placements_native),
        "building_renderable_placements": sum(
            bool(p["lods"]) for p in object_placements if p["editor_collection"] == "buildings"
        ),
        "building_geometries": len(collection_meshes("buildings")),
        "prop_placements": len(prop_placements_native),
        "prop_renderable_placements": sum(
            bool(p["lods"]) for p in object_placements if p["editor_collection"] == "props"
        ),
        "prop_geometries": len(collection_meshes("props")),
        "renderable_structure_placements": sum(
            bool(p["lods"]) for p in object_placements if p["editor_collection"] in {"buildings", "props"}
        ),
        "human_placements": len(humans.placements),
        "human_renderable_placements": sum(bool(p["lods"]) for p in object_placements if p["editor_collection"] == "humans"),
        "human_geometries": len(human_meshes),
        "scatter_batches": len(scatter_batches),
        "scatter_placements": len(scatter_placements),
        "scatter_geometries": len(scatter.templates),
        "scene_markers": len(scene_markers),
        "landing_sites": sum(m["kind"] == "landing-site" for m in spawns.scene_markers),
        "spawn_definitions": len(spawns.spawn_definitions),
        "landing_visual_placements": len(landing_placements),
        "renderable_world_placements": len(rendered_ids),
        "geometries": len(geometry_entries),
        "materials": len(material_records),
        "textures": len(texture_entries),
        "scene_sibling_records": len(scene.scene_chain),
        "lod0_vertices": lod0_vertices,
        "lod0_triangles": lod0_triangles,
        "lod1_vertices": lod1_vertices,
        "lod1_triangles": lod1_triangles,
    }
    for key, expected in AREA42_V4_COUNTS.items():
        if counts[key] != expected:
            raise ValueError(
                f"Area 42 editor scene count mismatch for {key}: "
                f"expected {expected}, got {counts[key]}"
            )

    buffer_bytes = bytes(buffer.data)
    buffer_path = output / "buffers.bin"
    buffer_path.write_bytes(buffer_bytes)
    source_hashes_after = scene.reader.source_hashes()
    if source_hashes_before != source_hashes_after:
        raise ValueError("retail source hashes changed during editor scene export")

    manifest = {
        "schema": {"name": SCHEMA_NAME, "version": SCHEMA_VERSION},
        "profile_id": AREA42_PROFILE,
        "map_name": AREA42_MAP,
        "read_only": True,
        "authority": "native DAH resources and preserved unknown bytes",
        "source": {
            "files_sha256": dict(sorted(source_hashes_before.items())),
            "directory_version": scene.reader.directory.version,
            "directory_unknown0": scene.reader.directory.unknown0,
            "scene_chain_offsets": list(scene.scene_chain),
            "world_instance_database": _resource(world.database),
            "load_groups": [
                {
                    "index": group.index,
                    "archive": group.archive,
                    "package_offset": group.package_offset,
                    "mode": group.mode,
                    "decoded_sha256": group.decoded_sha256,
                    "resource_limit": group.resource_limit,
                    "relocation_count": len(group.relocations.pointers),
                    "relocation_sha256": group.relocations.sha256,
                    "relocation_location": group.relocation_location,
                }
                for group in scene.reader.loaded_groups
            ],
        },
        "reference_validation": {
            "file": reference_file.name,
            "sha256": _file_sha256(reference_file),
            "array_count": comparison["array_count"],
            "all_arrays_byte_equal": comparison["all_arrays_byte_equal"],
            "arrays": comparison["arrays"],
        },
        "coordinate_system": {
            "matrix_layout": "row-major storage; row-vector multiplication",
            "translation": "matrix row 3",
            "quaternion_order": "xyzw; native values are not normalized",
            "unit": "native DAH world unit",
            "axis_handedness": "preserved; not reinterpreted by the interchange",
        },
        "buffers": {
            "file": "buffers.bin",
            "byte_length": len(buffer_bytes),
            "sha256": _sha256(buffer_bytes),
        },
        "counts": counts,
        "geometries": geometry_entries,
        "materials": [
            _material_entry(material)
            for _, material in sorted(material_records.items())
        ],
        "textures": texture_entries,
        "placements": placements,
        "object_placements": object_placements,
        "landing_placements": landing_placements,
        "landing_visual_validation": landing.report,
        "actor_visual_inventory": [{"resource": _resource(i.actor.resource), "asset_name": i.actor.asset_name,
                                    "native_class": i.actor.native_class, "placements": i.placement_count,
                                    "status": i.status, "detail": i.detail,
                                    "references": [{"pointer_field": r.pointer_field, "resource": _resource(r.resource),
                                                    "selected": r.selected} for r in i.references]}
                                   for i in actors.inventory],
        "actor_visual_skeletons": [{"archetype": _resource(a.actor.resource),
                                    "lods": [{"index": lod.index, "skeleton": _skeleton_report(lod.skeleton)} for lod in a.lods]}
                                   for a in actors.archetypes.values()],
        "landing_skeletons": [_skeleton_report(lod.skeleton) for lod in landing.lods],
        "nonhuman_skin_skeletons": [{"archetype": _resource(a.actor.resource),
                                     "skeleton": _skeleton_report(a.lods[0].visual.skeleton)}
                                    for key, a in skin_archetypes.items() if key not in human_archetypes],
        "staging": {"mode": "all-authored-content", "mission_activation": "not-evaluated",
                    "native_lists": [{"bucket": b.index, "layer_hash": f"0x{b.type_hash:08X}",
                                      "list_offsets": list(b.list_offsets), "placements": len(b.placements)} for b in world.buckets],
                    "authority": "native identities and mission layers; editor categories do not change runtime ownership"},
        "scatter_placements": scatter_placements,
        "scatter_batches": scatter_batches,
        "scatter_validation": scatter_report(scatter),
        "scene_markers": scene_markers,
        "spawn_definitions": list(spawns.spawn_definitions),
        "spawn_layers": list(spawns.spawn_layers),
        "regions": list(spawns.regions),
        "entity_tags": list(spawns.tags),
        "script_calls": list(spawns.script_calls),
        "spawn_validation": spawns.report,
        "human_archetypes": [
            {
                "resource": _resource(actor.actor.resource),
                "variant_hash": f"0x{actor.variant_hash:08X}",
                "lod_pointer_offset": actor.lod_pointer_offset,
                "variant_table_offset": actor.variant_table_offset,
                "selected_variant_offset": actor.selected_variant_offset,
                "pose_status": actor.lods[0].visual.pose_status,
                "skeleton": {
                    "load_group": actor.lods[0].visual.skeleton.load_group,
                    "shared_offset": actor.lods[0].visual.skeleton.shared_offset,
                    "raw_shared_header_hex": actor.lods[0].visual.skeleton.raw_shared_header.hex(),
                    "bone_buffer_offset": actor.lods[0].visual.skeleton.bone_buffer_offset,
                    "bones": [{"index": b.index, "offset": b.offset,
                               "name_hash": f"0x{b.name_hash:08X}",
                               "parent_index": b.parent_index,
                               "raw_hex": b.raw_bytes.hex(),
                               "local_position_f32": list(b.local_position),
                               "local_quaternion_xyzw_f32": list(b.local_quaternion_xyzw),
                               "local_scale_f32": b.local_scale}
                              for b in actor.lods[0].visual.skeleton.bones],
                },
            } for _, actor in sorted(human_archetypes.items())
        ],
        "unknowns": [
            "Terrain layer, alpha, RGB scale and blend equations carry executable evidence; special shader paths and dynamic UV scaling remain incomplete.",
            "Viewport orientation and remaining render states still require a same-location retail comparison.",
            "Native double-sided flags are preserved; the preview disables culling until front-face parity is verified.",
            "Skinned and segmented actors show their authored reference pose; runtime animation, component visibility and held attachments are not applied.",
            "Scatter wind and distance fade are preserved but not simulated; translucent instances are not sorted individually in the preview.",
            "Native landing visuals use authored region centers; runtime collision projection, activation and animation are not applied.",
            "Authored placements and script registrations do not imply simultaneous runtime activation.",
            "Environment lighting, fog, runtime shadows and same-location visual parity are not yet verified.",
            "Collision, mission execution, destruction-state editing, streaming simulation and native writes are outside schema v4.",
        ],
    }
    manifest_path = output / "scene.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    validation = validate_editor_scene_directory(output)
    return {
        "status": "verified",
        "scene": str(output),
        "manifest": str(manifest_path),
        "counts": counts,
        "source_unchanged": True,
        "reference_arrays_byte_equal": True,
        "manifest_sha256": validation["manifest_sha256"],
        "buffer_sha256": validation["buffer_sha256"],
    }
