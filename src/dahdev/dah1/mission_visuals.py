"""Native script-selected visuals, independent of world database actor records.

The landing mesh is the exact asset selected by retail Lua. It is not a decal
invented from a texture name. Placements retain the authored region center;
runtime collision projection, activation and animation are not simulated.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from .container import BlockReader, ResourceRecord
from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .spawns import SpawnScene, _resource, _span, native_name_hash, read_spawn_scene
from .world import ACTOR_ARCHETYPE_TYPE, LOD_TYPE, read_world_scene

if TYPE_CHECKING:
    from .actor_visuals import ActorVisualLOD

MARKER_CLASS = 0x247E4E97
_RUNTIME_EVIDENCE = {
    "landing_set_zone_marker": (0x6ABC0,
        "5356576a01e856e90c008bf8e81fff06008bf08b1e33d28bcfe8c2a8060050"
        "68ec489d1d8bceff531085c05f5e5b74118138974e7e2475098b0da8872400"
        "89413833c0c3"),
    "landing_project_and_construct": (0x6ADE4,
        "d946408b46488b0d68672800d9542420d946446808912200d95424288944242c"
        "d9c98d542414d95c2414528d442430d95c241c8b895030000050e82d580a00"
        "84c00f84d6000000a1a88724008b481c3b48200f84c50000008b403868fc010000"
        "680000803f68cc0925008d54242c526aff68156258b86a00508d4c244ce8daf80300"),
    "landing_identity_quaternion": (0x2509CC, "0000000000000000000000000000803f"),
}


def verify_landing_executable_evidence(path: str | Path) -> dict[str, object]:
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("landing evidence requires the pinned DAH1 US Xbox retail executable")
    image = XbeImage(data)
    checks = []
    for name, (address, hex_bytes) in _RUNTIME_EVIDENCE.items():
        expected = bytes.fromhex(hex_bytes)
        if image.read_virtual(address, len(expected)) != expected:
            raise ValueError(f"landing runtime evidence mismatch: {name}")
        checks.append({"name": name, "virtual_address": f"0x{address:08X}",
                       "byte_length": len(expected), "sha256": hashlib.sha256(expected).hexdigest()})
    return {"status": "verified", "executable_sha256": digest, "checks": checks}


def decode_marker_lod(reader: BlockReader, actor: ResourceRecord) -> tuple[ResourceRecord, dict[str, object]]:
    """Follow the relocated counted visual-reference list of the selected MarkerType."""
    if actor.type_hash != ACTOR_ARCHETYPE_TYPE or actor.size < 0x1C:
        raise ValueError("landing actor has an unexpected resource type or size")
    group = reader.load_group(actor.load_group)
    raw = _span(group.data, actor.offset, actor.size, group.resource_limit, "landing actor")
    if struct.unpack_from("<I", raw)[0] != MARKER_CLASS:
        raise ValueError("landing.SetZoneMarker actor is not native MarkerType")
    field = actor.offset + 0x18
    table = group.relocations.pointers.get(field)
    if table is None:
        raise ValueError("landing MarkerType lacks its relocated visual-reference list")
    count = struct.unpack("<I", _span(group.data, table, 4, group.resource_limit, "marker reference count"))[0]
    # Area42's verified MarkerType has exactly one visual. Do not silently pick a
    # resource if another build changes the list semantics or includes variants.
    if count != 1:
        raise ValueError(f"unsupported landing marker visual-reference count {count}")
    pointer_field = table + 4
    _span(group.data, pointer_field, 4, group.resource_limit, "marker visual pointer")
    target = group.relocations.pointers.get(pointer_field)
    lod = reader.resource_by_location(actor.load_group, target) if target is not None else None
    if lod is None or lod.type_hash != LOD_TYPE:
        raise ValueError("landing marker reference does not resolve to a native LOD")
    return lod, {
        "actor": _resource(reader, actor), "raw_actor_hex": raw.hex(),
        "actor_visual_list_pointer_field": field, "visual_list_offset": table,
        "visual_list_count": count, "lod_pointer_field": pointer_field,
        "lod": _resource(reader, lod), "confidence": "native-relocated-reference-chain",
    }


@dataclass(frozen=True)
class LandingVisualPlacement:
    marker_id: str
    label: str
    matrix: NDArray[np.float32]
    position: NDArray[np.float32]
    provenance: dict[str, object]


@dataclass(frozen=True)
class LandingVisualScene:
    reader: BlockReader
    actor_resource: ResourceRecord
    lod_resource: ResourceRecord
    lods: tuple[ActorVisualLOD, ...]
    placements: tuple[LandingVisualPlacement, ...]
    report: dict[str, object]


def read_landing_visual_scene(
    source: str | Path | BlockReader, spawns: SpawnScene | None = None,
) -> LandingVisualScene:
    from .actor_visuals import decode_actor_visual_lods

    reader = source if isinstance(source, BlockReader) else BlockReader(source)
    if spawns is None:
        spawns = read_spawn_scene(reader, read_world_scene(reader, include_visuals=False))
    elif spawns.reader is not reader:
        raise ValueError("landing scene and spawn evidence must use the same BlockReader")
    calls = [c for c in spawns.script_calls if c["function"] == "landing.SetZoneMarker"]
    if not calls or any(not c["complete_literal_call"] for c in calls):
        raise ValueError("landing visual selection is missing or is not a literal bytecode call")
    names = set()
    for call in calls:
        args = call["literal_prefix_arguments"]
        if len(args) != 1 or not isinstance(args[0], str):
            raise ValueError("landing.SetZoneMarker does not have one literal actor name")
        names.add(args[0])
    if len(names) != 1:
        raise ValueError("multiple landing marker types require mission-state selection")
    name = next(iter(names))
    actor = reader.resource_by_identity(ACTOR_ARCHETYPE_TYPE, native_name_hash(name))
    if actor is None:
        raise ValueError("script-selected landing marker actor is absent")
    lod, chain = decode_marker_lod(reader, actor)
    visual_lods = decode_actor_visual_lods(reader, lod)
    if not visual_lods or not all(lod.parts for lod in visual_lods):
        raise ValueError("landing marker has no decoded native visual parts")
    placements = []
    for marker in spawns.scene_markers:
        if marker["kind"] != "landing-site":
            continue
        position = np.asarray(marker["position_f32"], dtype=np.float32)
        if position.shape != (3,) or not np.isfinite(position).all():
            raise ValueError("landing region center is not a finite float3")
        matrix = np.eye(4, dtype=np.float32)
        matrix[3, :3] = position
        placements.append(LandingVisualPlacement(
            marker_id=marker["id"], label=marker["label"], matrix=matrix,
            position=position.copy(), provenance=dict(marker),
        ))
    materials = {m.resource.name_hash: m for lod in visual_lods for part in lod.parts for m in part.visual.materials}
    textures = {m.binding.texture_hash: m.binding.texture_resource for m in materials.values() if m.binding}
    report = {
        "status": "native-reference-visual-at-authored-region-centers",
        "actor_name": name, "selection_calls": calls, "native_reference_chain": chain,
        "counts": {"placements": len(placements), "lods": len(visual_lods),
                   "parts": sum(len(lod.parts) for lod in visual_lods),
                   "materials": len(materials), "textures": len(textures)},
        "material_resources": [_resource(reader, m.resource) for _, m in sorted(materials.items())],
        "texture_resources": [_resource(reader, texture) for _, texture in sorted(textures.items())],
        "runtime_evidence": {"executable_sha256": DAH1_RETAIL_XBOX_US_SHA256,
                             "set_marker_va": "0x0006ABC0", "placement_va": "0x0006ADE4",
                             "collision_projection_va": "0x00110650"},
        "placement_semantics": "identity rotation and unit scale; authored region center before runtime vertical collision query",
        "limitations": [
            "The collision projection at runtime is not reproduced; these are not final in-game Z coordinates.",
            "Mission activation, rise/fall transforms and pulsing/emissive animation are not simulated.",
            "Native mesh, UVs and material references are preserved; special blend/render paths may remain approximate in the viewer.",
        ],
    }
    return LandingVisualScene(reader, actor, lod, visual_lods, tuple(placements), report)
