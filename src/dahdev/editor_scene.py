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
    TextureBinding,
    decode_material_texture_bindings,
    decode_texture_mip_levels,
    terrain_material_slots,
    texture_mip_layout,
    texture_payload,
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
from .dah1.objectives import read_objectives, validate_objective_report
from .dah1.actor_visuals import read_actor_visuals
from .dah1.mission_visuals import read_landing_visual_scene
from .dah1.interiors import read_building_fixtures
from .dah1.environment import read_environment_scene, environment_report, visual_light_role
from .dah1.material_evidence import native_material_evidence, native_sampler_state, terrain_segment_render_state, actor_material_render_state
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


def _inherit_native_light_role(placement: dict[str, Any], role: dict[str, object] | None) -> None:
    """The owning visual selects one initial light; child meshes do not reselect it."""
    placement["native_light_role"] = role
    for lod in placement["lods"]:
        lod["native_light_role"] = role
        for part in lod.get("parts", []):
            part["native_light_role"] = role


def _sky_placement_entry(environment: Any, contract: dict[str, Any]) -> dict[str, object]:
    if contract.get("first_visual_enabled") is not True:
        raise ValueError("initial native sky materials have unresolved draw state")
    visual = environment.sky.visuals[0]
    states = {state["name_hash"]: state for state in contract["materials"]}
    segment_states = []
    for segment in visual.mesh.segments:
        material = visual.materials[segment.material_slot]
        name = f"0x{material.resource.name_hash:08X}"
        if name not in states:
            raise ValueError("native sky segment lacks verified material state")
        segment_states.append({**states[name], "sky": True, "depth_compare": "less_equal",
                               "fog_enabled": False, "editor_shade_faces": False})
    return {
        "id": f"sky-visual:{visual.resource.name_hash:08x}",
        "editor_collection": "environment-sky", "preview_status": "retail-initial-sky-verified",
        "native": {"visual": _resource(visual.resource), "controller_offset": environment.sky.span.offset,
                   "visual_index": 0, "placement_status": contract["placement"]},
        "position_f32": [0.0, 0.0, 0.0],
        "matrix_row_major_f32": np.eye(4, dtype=np.float32).reshape(-1).tolist(),
        "native_light_role": None,
        "lods": [{"index": 0, "visual": _resource(visual.resource),
                  "geometry": f"geometry:static:{visual.mesh.resource.name_hash:08x}",
                  "material_slots": [f"material:{m.resource.name_hash:08x}" for m in visual.materials],
                  "sky_render_states": segment_states}],
    }


def _validate_sky_manifest(manifest: dict[str, Any]) -> None:
    """Validate the optional initial-sky extension without changing legacy scenes."""
    if "sky_placements" not in manifest:
        return
    placements = manifest["sky_placements"]
    counts = manifest.get("counts", {})
    if not isinstance(placements, list) or len(placements) != 1 or counts.get("sky_placements") != 1:
        raise ValueError("initial sky must contain exactly its verified first visual")
    contract = manifest.get("environment", {}).get("sky", {}).get("draw_contract", {})
    if (contract.get("first_visual_enabled") is not True or contract.get("second_visual_enabled") is not False
            or contract.get("force_far_depth") is not False or contract.get("depth_write") is not False):
        raise ValueError("sky draw contract is missing or unsupported")
    states = {s["name_hash"]: s for s in contract.get("materials", [])}
    geometries = {g["id"]: g for g in manifest["geometries"]}
    materials = {m["id"]: m for m in manifest["materials"]}
    placement = placements[0]
    if (placement.get("matrix_row_major_f32") != np.eye(4).reshape(-1).tolist()
            or placement.get("native", {}).get("visual_index") != 0):
        raise ValueError("sky placement must preserve identity and first visual selection")
    if placement["native"].get("visual", {}).get("name_hash") != contract.get("first_visual_name_hash"):
        raise ValueError("sky placement disagrees with the native controller reference")
    lods = placement.get("lods")
    if not isinstance(lods, list) or len(lods) != 1 or lods[0].get("index") != 0:
        raise ValueError("initial sky must contain one native visual, not invented LODs")
    lod = lods[0]
    geometry = geometries.get(lod.get("geometry"))
    if geometry is None or geometry.get("kind") != "static-world-object" or counts.get("sky_geometries") != 1:
        raise ValueError("sky references an unknown native static mesh")
    segments = geometry.get("segments", [])
    draw_states, slots = lod.get("sky_render_states"), lod.get("material_slots")
    if not isinstance(draw_states, list) or len(draw_states) != len(segments) or not isinstance(slots, list):
        raise ValueError("sky segment states do not cover the native mesh")
    for segment, actual in zip(segments, draw_states):
        slot = segment.get("material_slot")
        if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < len(slots) or slots[slot] not in materials:
            raise ValueError("sky segment references an unknown material")
        material_name = materials[slots[slot]]["resource"]["name_hash"]
        expected = states.get(material_name)
        if expected is None or any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError("sky segment state disagrees with native material evidence")
        if any(actual.get(k) != v for k, v in {"sky": True, "depth_compare": "less_equal",
                                               "fog_enabled": False, "editor_shade_faces": False}.items()):
            raise ValueError("sky segment changes the verified native pass state")


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
        _validate_native_texture_mips(root, texture)

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
        _validate_sky_manifest(manifest)
        validate_complete_scene(manifest, payload)
        validate_objective_report(manifest)

    return {
        "schema_version": schema["version"],
        "profile_id": manifest.get("profile_id"),
        "map_name": manifest.get("map_name"),
        "counts": counts,
        "manifest_sha256": _file_sha256(manifest_path),
        "buffer_sha256": buffers["sha256"],
    }


def _binding_entry(binding: TextureBinding | None) -> dict[str, object] | None:
    if binding is None:
        return None
    return {
        "offset": binding.offset, "raw_hex": binding.raw_bytes.hex(),
        "format_word": binding.format_word, "format": binding.format_name,
        "mip_levels": binding.mip_levels, "width": binding.width, "height": binding.height,
        "texture": f"texture:{binding.texture_hash:08x}",
    }


def _validate_native_texture_mips(root: Path, texture: dict[str, object]) -> None:
    """New mip sidecars are checked; older top-level-only scenes stay readable."""
    from PIL import Image

    preview = texture["preview"]
    mips = preview.get("native_mips")
    if mips is None:
        return
    native = texture.get("native")
    if not isinstance(native, dict) or not isinstance(mips, list) or not mips:
        raise ValueError("editor scene native mip chain is invalid")
    width = _require_int(native, "width", "texture.native")
    height = _require_int(native, "height", "texture.native")
    count = _require_int(native, "mip_levels", "texture.native")
    if width < 1 or height < 1 or count < 1 or count > max(width, height).bit_length() or len(mips) != count:
        raise ValueError("editor scene native mip dimensions or count are invalid")
    cursor = 0
    for index, mip in enumerate(mips):
        if not isinstance(mip, dict) or mip.get("level") != index:
            raise ValueError("editor scene native mip levels are not consecutive")
        expected = (max(1, width >> index), max(1, height >> index))
        if (mip.get("width"), mip.get("height")) != expected:
            raise ValueError("editor scene native mip dimensions disagree with the original chain")
        path = _safe_relative_path(root, mip.get("file"), "texture.preview.native_mips.file")
        if _file_sha256(path) != mip.get("sha256"):
            raise ValueError("editor scene native mip SHA-256 mismatch")
        with Image.open(path) as image:
            if image.size != expected:
                raise ValueError("editor scene native mip PNG was resized")
        offset = _require_int(mip, "native_byte_offset", "texture.native_mip")
        size = _require_int(mip, "native_byte_length", "texture.native_mip")
        if offset != cursor or size < 1:
            raise ValueError("editor scene native mip provenance has a gap or overlap")
        cursor += size
        if index == 0 and any(mip.get(key) != preview.get(key) for key in ("file", "sha256")):
            raise ValueError("editor scene preview does not use the original highest-resolution mip")
    resource = texture.get("resource")
    if not isinstance(resource, dict) or cursor != resource.get("size"):
        raise ValueError("editor scene native mip provenance does not span the original texture")


def _material_entry(
    material: NativeMaterial, bindings: tuple[TextureBinding | None, ...] | None = None,
) -> dict[str, object]:
    binding = material.binding
    bindings = bindings if bindings is not None else (binding, None, None, None)
    if len(bindings) != 4:
        raise ValueError("native material must preserve exactly four texture stages")
    return {
        "id": f"material:{material.resource.name_hash:08x}",
        "resource": _resource(material.resource),
        "raw_sha256": _sha256(material.raw_bytes),
        "render_flags": material.render_flags,
        "render_state": material_render_state(material),
        "actor_render_state": actor_material_render_state(material),
        "actor_material": native_material_evidence(material, kind="actor"),
        "native_sampler": native_sampler_state(material),
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
        "binding": _binding_entry(binding),
        "texture_stages": [
            {"stage": stage, "binding_pointer_field": material.resource.offset + 0x6C + 4 * stage,
             "binding": _binding_entry(stage_binding)}
            for stage, stage_binding in enumerate(bindings)
        ],
        "texture_stage_status": "native-dependencies-resolved; secondary-shader-sampling-not-yet-rendered",
    }


def _collect_texture_bindings(reader, material_records):
    """Resolve every material stage before any texture export, including sky."""
    material_bindings = {}
    texture_bindings = {}
    for identity, material in material_records.items():
        stages = decode_material_texture_bindings(reader, material.resource)
        material_bindings[identity] = stages
        for binding in stages:
            if binding is None:
                continue
            previous = texture_bindings.get(binding.texture_hash)
            if previous is not None and (
                previous.format_word, previous.width, previous.height, previous.mip_levels,
            ) != (binding.format_word, binding.width, binding.height, binding.mip_levels):
                raise ValueError("one texture identity has conflicting native mip metadata")
            texture_bindings[binding.texture_hash] = binding
    return material_bindings, texture_bindings


def _write_texture_entries(reader, texture_bindings, output: Path):
    """Write authored mip images; keep the old level-0 preview path compatible."""
    (output / "textures").mkdir(parents=True, exist_ok=True)
    entries = []
    for texture_hash, binding in sorted(texture_bindings.items()):
        payload = texture_payload(reader, binding)
        levels = texture_mip_layout(binding)
        images = decode_texture_mip_levels(reader, binding)
        mips = []
        for level, image in zip(levels, images, strict=True):
            index = level["level"]
            filename = f"{texture_hash:08x}.png" if index == 0 else f"{texture_hash:08x}.mip{index}.png"
            path = Path("textures") / filename
            encoded = io.BytesIO()
            image.save(encoded, format="PNG", optimize=False)
            png = encoded.getvalue()
            (output / path).write_bytes(png)
            offset, size = level["byte_offset"], level["byte_length"]
            mips.append({
                "level": index, "width": image.width, "height": image.height,
                "file": path.as_posix(), "format": "RGBA8 PNG", "sha256": _sha256(png),
                "native_byte_offset": offset, "native_byte_length": size,
                "native_sha256": _sha256(payload[offset:offset + size]),
            })
        entries.append({
            "id": f"texture:{texture_hash:08x}", "resource": _resource(binding.texture_resource),
            "native": {"format_word": binding.format_word, "format": binding.format_name,
                       "mip_levels": binding.mip_levels, "width": binding.width, "height": binding.height},
            "preview": {**{key: mips[0][key] for key in ("file", "format", "sha256")}, "native_mips": mips},
            "conversion": {"source": "original-retail-texture-bytes", "base_level": 0,
                           "max_level": len(mips) - 1, "resized": False,
                           "generated_mipmaps": False, "ai_generated": False},
        })
    return entries


def _texture_dependency_report(material_bindings):
    primary = {b[0].texture_hash for b in material_bindings.values() if b[0] is not None}
    textures = {item.texture_hash for bindings in material_bindings.values() for item in bindings if item is not None}
    return {
        "status": "all-four-native-stages-resolved",
        "materials": len(material_bindings), "textures": len(textures),
        "bound_stages": [sum(bindings[stage] is not None for bindings in material_bindings.values()) for stage in range(4)],
        "secondary_only_textures": len(textures - primary),
        "provenance": "material +0x6C/+0x70/+0x74/+0x78 relocations; four-slot initialization at 0xE60BC",
        "remaining_unknowns": ["secondary reflection/shader texture sampling is preserved as a dependency, not yet rendered"],
    }


def _resolve_reference(reference: str | Path) -> Path:
    path = Path(reference).resolve()
    if path.is_dir():
        path = path / "area42_retail_lods_and_transforms.npz"
    if not path.is_file():
        raise FileNotFoundError(f"reference archive not found: {path}")
    return path


def _append_building_fixture_parts(objects: list[dict[str, Any]], fixtures: Any) -> None:
    """Keep native fixture visuals owned by their existing placed building.

    LOD0 uses the decoded intact fixture LOD0. Higher inspection LOD indices
    clamp each independent fixture chain; this is not runtime distance selection.
    """
    by_id = {obj["id"]: obj for obj in objects}
    for placed in fixtures.placements:
        parent_id = f"world-placement:{placed.parent.bucket_index:02d}:{placed.parent.index:04d}"
        owner = by_id[parent_id]
        fixture = placed.fixture
        if not owner["lods"] or not fixture.lods:
            raise ValueError("selected building fixture has no renderable owner or LOD")
        for owner_lod in owner["lods"]:
            selected = fixture.lods[min(owner_lod["index"], len(fixture.lods) - 1)]
            for part in _parts_lod(selected)["parts"]:
                part["matrix_row_major_f32"] = (
                    np.asarray(part["matrix_row_major_f32"], dtype=np.float32).reshape(4, 4)
                    @ fixture.matrix
                ).reshape(-1).tolist()
                part["building_fixture"] = {
                    "index": fixture.index, "tag_hash": f"0x{fixture.tag_hash:08X}",
                    "visual_hash": f"0x{fixture.visual_hash:08X}",
                    "record_offset": fixture.offset, "raw_record_hex": fixture.raw_record.hex(),
                    "selected_lod_index": selected.index,
                    "lod_policy": "independent-chain-clamped-inspection-index; not-runtime-distance-selection",
                    "selection_pointer_field": placed.selection.pointer_field,
                    "selection_flags": placed.selection.flags,
                    "selection_parameter_offset": placed.selection.parameter_offset,
                    "selection_table_offset": placed.selection.table_offset,
                    "raw_selection_table_hex": placed.selection.raw_table.hex(),
                }
                owner_lod["parts"].append(part)
        owner["preview_status"] += "; native-selected-intact-building-fixtures"


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
    fixtures = read_building_fixtures(world)
    environment = read_environment_scene(scene.reader)
    environment_contract = environment_report(environment)
    sky_contract = environment_contract["sky"]["draw_contract"]
    sky_placements = [_sky_placement_entry(environment, sky_contract)]
    scatter = read_scatter_scene(scene)
    spawns = read_spawn_scene(scene.reader, world)
    objectives = read_objectives(world, spawns)
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
    fixture_lods = [lod for placed in fixtures.placements for lod in placed.fixture.lods]
    static_meshes.update({part.visual.mesh.resource.name_hash: part.visual.mesh
                         for lod in fixture_lods for part in lod.parts})
    sky_visual = environment.sky.visuals[0]
    static_meshes[sky_visual.mesh.resource.name_hash] = sky_visual.mesh
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

    all_rigid_lods = [lod for actor in actors.archetypes.values() for lod in actor.lods] + list(landing.lods) + fixture_lods
    for rigid_lod in all_rigid_lods:
        for lod in rigid_lod.parts:
            for material in lod.visual.materials:
                material_records[material.resource.name_hash] = material

    for actor in skin_archetypes.values():
        for lod in actor.lods:
            for material in lod.visual.materials:
                material_records[material.resource.name_hash] = material
    for batch in scatter.batches:
        material = batch.material
        material_records[material.resource.name_hash] = material
    for material in sky_visual.materials:
        material_records[material.resource.name_hash] = material
    material_bindings, texture_bindings = _collect_texture_bindings(scene.reader, material_records)
    texture_entries = _write_texture_entries(scene.reader, texture_bindings, output)

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

    spawn_metadata_by_placement = {
        (layer["index"], record["placement_index"]): record
        for layer in spawns.spawn_layers
        for record in layer["placements"]
    }
    object_placements: list[dict[str, object]] = []
    light_role_cache: dict[tuple[int, int], dict[str, object]] = {}
    for placement in world.placements:
        actor = placement.actor
        human = skin_archetypes.get(actor.resource.name_hash) if actor else None
        rigid = actors.archetypes.get(actor.resource.name_hash) if actor else None
        collection = classify_editor_collection(
            actor.native_class if actor else None,
            actor.asset_name if actor else None,
        )
        spawn_metadata = spawn_metadata_by_placement.get(
            (placement.bucket_index, placement.index)
        )
        if spawn_metadata is None:
            raise ValueError(
                "World placement is missing from the native spawn-layer index: "
                f"{placement.bucket_index}:{placement.index}"
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
                    "entity_id": spawn_metadata["entity_id"],
                    "tag_ids": list(spawn_metadata["tag_ids"]),
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
        owning_visual = rigid.visual_resource if rigid else human.actor.lod_resource if human else None
        role = None
        if owning_visual is not None:
            key = (owning_visual.load_group, owning_visual.offset)
            if key not in light_role_cache:
                light_role_cache[key] = visual_light_role(scene.reader, owning_visual)
            role = light_role_cache[key]
        _inherit_native_light_role(object_placements[-1], role)

    _append_building_fixture_parts(object_placements, fixtures)
    # Fixture subparts still belong to the original native instance light context.
    for placed in object_placements:
        _inherit_native_light_role(placed, placed["native_light_role"])

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
    landing_light_role = visual_light_role(scene.reader, landing.lod_resource)
    for placed in landing_placements:
        _inherit_native_light_role(placed, landing_light_role)
    rendered_ids = {p["id"] for p in object_placements if p["lods"]}
    scene_markers = [m for m in spawns.scene_markers
                     if not m.get("world_placement") or
                     f"world-placement:{m['world_placement']['bucket_index']:02d}:{m['world_placement']['index']:04d}" not in rendered_ids]
    scene_markers.extend({"id": marker["id"], "kind": marker["kind"],
                          "label": marker["name"], "status": marker["placement_status"],
                          "position_f32": marker["position"], "native": marker}
                         for marker in objectives.scene_markers)

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
        "native_regions": len(spawns.regions),
        "native_layers": len(spawns.spawn_layers),
        "entity_tags": len(spawns.tags),
        "script_calls": len(spawns.script_calls),
        "objective_definitions": len(objectives.objectives),
        "authored_pickups": len(objectives.collectibles),
        "collectible_probes": objectives.report["counts"]["probes"],
        "mission_debris_pickups": objectives.report["counts"]["mission_debris"],
        "objective_region_markers": len(objectives.scene_markers),
        "landing_visual_placements": len(landing_placements),
        "sky_placements": len(sky_placements),
        "sky_geometries": 1,
        "renderable_world_placements": len(rendered_ids),
        "selected_building_fixtures": len(fixtures.placements),
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
            _material_entry(material, material_bindings[material.resource.name_hash])
            for _, material in sorted(material_records.items())
        ],
        "textures": texture_entries,
        "texture_dependency_report": _texture_dependency_report(material_bindings),
        "placements": placements,
        "object_placements": object_placements,
        "landing_placements": landing_placements,
        "sky_placements": sky_placements,
        "landing_visual_validation": landing.report,
        "environment": environment_contract,
        "building_fixture_validation": {
            "inventory": list(fixtures.inventory),
            "selected_fixture_count": len(fixtures.placements),
            "archetypes": [{
                "resource": _resource(a.actor.resource), "asset_name": a.actor.asset_name,
                "raw_actor_hex": a.raw_actor.hex(), "relocations": list(a.relocations),
                "block_table_offset": a.block_table_offset, "fixture_table_offset": a.fixture_table_offset,
                "blocks": [{"index": b.index, "offset": b.offset, "raw_record_hex": b.raw_record.hex(),
                            "alternate_state_offset": b.alternate_state_offset,
                            "alternate_state_hash": b.alternate_state_hash,
                            "alternate_visual_hash": b.alternate_visual_hash,
                            "alternate_visual_resource": _resource(b.alternate_visual_resource) if b.alternate_visual_resource else None,
                            "raw_alternate_prefix_hex": b.raw_alternate_prefix.hex(),
                            "alternate_state_rendered": False} for b in a.blocks],
            } for a in fixtures.archetypes.values()],
            "status": "native-instance-selected-intact-fixtures; no-destruction-simulation",
        },
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
        "objective_definitions": list(objectives.objectives),
        "collectible_placements": list(objectives.collectibles),
        "mission_registrations": list(objectives.mission_registrations),
        "objective_script_calls": list(objectives.script_calls),
        "objective_visual_dependencies": list(objectives.visual_dependencies),
        "objective_validation": objectives.report,
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
            "Terrain layer, alpha, RGB scale and blend equations carry executable evidence. Pixel shaders 3/4 preserve their decoded RGB2 base and exact alpha behavior; their additive reflection term still needs generated t1 coordinates and the live c5 constant.",
            "Viewport orientation and remaining render states still require a same-location retail comparison.",
            "Native double-sided flags are preserved; the preview disables culling until front-face parity is verified.",
            "Skinned and segmented actors show their authored reference pose; runtime animation, component visibility and held attachments are not applied.",
            "Scatter wind and distance fade are preserved but not simulated. The viewport depth-sorts translucent instances, not individual triangles; exact retail draw ordering remains unverified.",
            "Native landing visuals use authored region centers; runtime collision projection, activation and animation are not applied.",
            "Authored placements and script registrations do not imply simultaneous runtime activation.",
            "First sky visual initial draw, authored sun constants and the configured alien camera-plane fog endpoint are previewed; UFO profile switching, second sky visual, animated UVs, live camera light directions, per-object effects and runtime shadows remain unresolved.",
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
