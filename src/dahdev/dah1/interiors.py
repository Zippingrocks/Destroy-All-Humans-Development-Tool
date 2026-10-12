"""Read-only, instance-selected building fixtures (including interior meshes).

This is not a name-based interior finder. Retail 0x446E0 walks archetype+0x84
as counted 0x64-byte fixture records; 0x447C0 selects their tags using the
placement parameter list. 0x4B390 resolves each fixture's +4 visual hash and
0x4B1C0 composes +0x0C position/+0x18 quaternion with its owning block node.
0x4A9E0 initializes a selected fixture to the undamaged state (0xEE107A28).

Area42's placed building archetypes have one identity base block. This reader
supports that verified case explicitly, rather than guessing bone attachment
semantics. Block+0x50 alternate-state links are retained but never submitted as
intact geometry. All raw archetype bytes, fixture records and selection bytes
are preserved; no native writer is provided.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .actor_visuals import ActorVisualLOD, decode_actor_visual_lods
from .container import BlockReader, ResourceRecord
from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .transforms import instance_matrix
from .world import (
    LOD_TYPE, SEGMENTED_VISUAL_TYPE, STATIC_VISUAL_TYPE, ActorArchetype,
    StaticMesh, WorldPlacement, WorldScene,
)

FIXTURE_STRIDE = 0x64
BLOCK_STRIDE = 0x60
WORLD_OBJECT_CLASS = 0x2E682B5C
DESTROYED_STATE = 0xE4A4EAC2
_PARAMETER_SIZES = (4, 16, 4, 16, 20, 20, 20, 20, 4, 4)


@dataclass(frozen=True)
class FixtureSelection:
    flags: int
    parameter_offset: int | None
    pointer_field: int | None
    table_offset: int | None
    selected_hashes: tuple[int, ...]
    raw_table: bytes


@dataclass(frozen=True)
class BuildingBlock:
    index: int
    offset: int
    raw_record: bytes
    alternate_state_offset: int | None
    alternate_state_hash: int | None
    alternate_visual_hash: int | None
    alternate_visual_resource: ResourceRecord | None
    raw_alternate_prefix: bytes


@dataclass(frozen=True)
class BuildingFixture:
    index: int
    offset: int
    raw_record: bytes
    tag_hash: int
    visual_hash: int
    bone_index: int
    block_index: int
    position: tuple[float, float, float]
    quaternion_xyzw: tuple[float, float, float, float]
    matrix: NDArray[np.float32]
    visual_resource: ResourceRecord | None
    lods: tuple[ActorVisualLOD, ...]
    status: str
    detail: str | None


@dataclass(frozen=True)
class BuildingFixtureArchetype:
    actor: ActorArchetype
    raw_actor: bytes
    relocations: tuple[tuple[int, int], ...]
    block_table_offset: int
    fixture_table_offset: int | None
    blocks: tuple[BuildingBlock, ...]
    fixtures: tuple[BuildingFixture, ...]


@dataclass(frozen=True)
class BuildingFixturePlacement:
    parent: WorldPlacement
    fixture: BuildingFixture
    selection: FixtureSelection
    matrix: NDArray[np.float32]


@dataclass(frozen=True)
class BuildingFixtureScene:
    archetypes: dict[int, BuildingFixtureArchetype]
    placements: tuple[BuildingFixturePlacement, ...]
    inventory: tuple[dict[str, object], ...]


def _span(data: bytes, offset: int, size: int, limit: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > min(limit, len(data)):
        raise ValueError(f"{label} is outside native resource data")
    return data[offset:offset + size]


def _visual(reader: BlockReader, name_hash: int) -> ResourceRecord | None:
    # The resource manager selects the LOD wrapper when one exists; do not draw
    # both the wrapper and its same-name primary static child.
    return next((r for t in (LOD_TYPE, SEGMENTED_VISUAL_TYPE, STATIC_VISUAL_TYPE)
                 if (r := reader.resource_by_identity(t, name_hash)) is not None), None)


def decode_fixture_selection(reader: BlockReader, placement: WorldPlacement) -> FixtureSelection:
    """Follow 0x44FBD/0x10DCA0; +0x50 is variable parameters, not the tag list."""
    if placement.actor is None:
        raise ValueError("building fixture selection requires an actor")
    group = reader.load_group(placement.actor.resource.load_group)
    data, pointers = group.data, group.relocations.pointers
    _span(data, placement.offset, 0x54, group.resource_limit, "building placement")
    flags = struct.unpack_from("<I", data, placement.offset + 0x4C)[0]
    if not flags & 0x400:
        return FixtureSelection(flags, None, None, None, (), b"")
    parameters = pointers.get(placement.offset + 0x50)
    if parameters is None:
        raise ValueError("building fixture flag lacks relocated variable parameters")
    relative = sum(size for bit, size in enumerate(_PARAMETER_SIZES) if flags & (1 << bit))
    field = parameters + relative
    raw_pointer = _span(data, field, 4, group.resource_limit, "fixture selection pointer")
    table = pointers.get(field)
    if table is None:
        if raw_pointer != bytes(4):
            raise ValueError("building fixture selection pointer is nonzero without relocation")
        return FixtureSelection(flags, parameters, field, None, (), b"")
    count = struct.unpack("<I", _span(data, table, 4, group.resource_limit, "fixture tag count"))[0]
    raw = _span(data, table, 4 + count * 4, group.resource_limit, "fixture selected tags")
    tags = struct.unpack_from(f"<{count}I", raw, 4)
    if len(set(tags)) != len(tags):
        raise ValueError("building fixture selection contains duplicate tags")
    return FixtureSelection(flags, parameters, field, table, tags, raw)


def fixture_matrix(position: tuple[float, ...], quaternion: tuple[float, ...]) -> NDArray[np.float32]:
    if len(position) != 3 or len(quaternion) != 4 or not np.isfinite((*position, *quaternion)).all():
        raise ValueError("building fixture transform is not a finite position/quaternion")
    if not np.isclose(np.linalg.norm(quaternion), 1.0, atol=1e-3):
        raise ValueError("building fixture quaternion is not unit length")
    return instance_matrix(np.asarray(quaternion, dtype=np.float32),
                           np.asarray(position, dtype=np.float32), np.float32(1.0))


def decode_building_fixture_archetype(
    reader: BlockReader, actor: ActorArchetype, *, mesh_cache: dict[int, StaticMesh] | None = None,
) -> BuildingFixtureArchetype:
    resource = actor.resource
    group = reader.load_group(resource.load_group)
    data, pointers = group.data, group.relocations.pointers
    raw_actor = _span(data, resource.offset, resource.size, group.resource_limit, "building actor")
    if len(raw_actor) < 0x88 or struct.unpack_from("<I", raw_actor)[0] != WORLD_OBJECT_CLASS:
        raise ValueError("building fixture owner is not the verified WorldObjectType layout")
    block_table = pointers.get(resource.offset + 0x7C)
    if block_table is None:
        raise ValueError("building lacks its relocated block table")
    block_count = struct.unpack("<I", _span(data, block_table, 4, group.resource_limit, "block count"))[0]
    _span(data, block_table + 4, block_count * BLOCK_STRIDE, group.resource_limit, "building blocks")
    blocks = []
    for index in range(block_count):
        offset = block_table + 4 + index * BLOCK_STRIDE
        raw = data[offset:offset + BLOCK_STRIDE]
        state_offset = pointers.get(offset + 0x50)
        state_prefix = _span(data, state_offset, 0x28, group.resource_limit, "alternate block state") if state_offset is not None else b""
        state_hash, visual_hash = struct.unpack_from("<II", state_prefix) if state_prefix else (None, None)
        blocks.append(BuildingBlock(index, offset, raw, state_offset, state_hash, visual_hash,
                                    _visual(reader, visual_hash) if visual_hash else None, state_prefix))
    table = pointers.get(resource.offset + 0x84)
    count = struct.unpack("<I", _span(data, table, 4, group.resource_limit, "fixture count"))[0] if table is not None else 0
    if table is not None:
        _span(data, table + 4, count * FIXTURE_STRIDE, group.resource_limit, "building fixtures")
    fixtures = []
    variant = struct.unpack_from("<I", raw_actor, 0x1C)[0]
    cache = {} if mesh_cache is None else mesh_cache
    for index in range(count):
        offset = table + 4 + index * FIXTURE_STRIDE
        raw = data[offset:offset + FIXTURE_STRIDE]
        tag, name, bone = struct.unpack_from("<IIi", raw)
        position = struct.unpack_from("<3f", raw, 0x0C)
        quaternion = struct.unpack_from("<4f", raw, 0x18)
        matrix = fixture_matrix(position, quaternion)
        block = struct.unpack_from("<i", raw, 0x48)[0]
        visual = _visual(reader, name) if name else None
        status, detail, lods = "decoded", None, ()
        if len(blocks) != 1 or block not in (-1, 0):
            status, detail = "unsupported-block-attachment", "Only the verified single base-block attachment is implemented."
        elif block == 0 and (
            struct.unpack_from("<Ii", blocks[0].raw_record, 4) != (0, -1)
            or not np.array_equal(fixture_matrix(struct.unpack_from("<3f", blocks[0].raw_record, 0x0C),
                                                 struct.unpack_from("<4f", blocks[0].raw_record, 0x18)), np.eye(4, dtype=np.float32))
        ):
            status, detail = "unsupported-block-transform", "Base block is not the verified identity/root-visual case."
        elif not name or bone != -1:
            status, detail = "existing-node-attachment", "A referenced existing visual node must not be duplicated."
        elif visual is None:
            status, detail = "unresolved-visual", "Fixture hash does not resolve to a supported visual resource."
        else:
            try:
                lods = decode_actor_visual_lods(reader, visual, variant_hash=variant, mesh_cache=cache)
            except ValueError as exc:
                status, detail = "unresolved-visual", str(exc)
        fixtures.append(BuildingFixture(index, offset, raw, tag, name, bone, block, position,
                                        quaternion, matrix, visual, lods, status, detail))
    return BuildingFixtureArchetype(
        actor, raw_actor,
        tuple((f, t) for f, t in sorted(pointers.items()) if resource.offset <= f < resource.offset + resource.size),
        block_table, table, tuple(blocks), tuple(fixtures),
    )


def read_building_fixtures(world: WorldScene) -> BuildingFixtureScene:
    """Return only explicitly selected intact fixture placements, plus inventory.

Fixture LODs stay independent: an owner with LOD1 must not erase a fixture that
has only LOD0. Renderer integration should select/clamp each fixture separately.
The parent world placement is retained for native layer/list identity; fixtures
are child visuals, not additional world database actors.
"""
    archetypes = {}
    cache: dict[int, StaticMesh] = {}
    for key, actor in world.archetypes.items():
        if actor.native_class == "WorldObjectType":
            archetypes[key] = decode_building_fixture_archetype(world.reader, actor, mesh_cache=cache)
    placements, inventory = [], []
    for parent in world.placements:
        if parent.actor is None or parent.actor.resource.name_hash not in archetypes:
            continue
        archetype = archetypes[parent.actor.resource.name_hash]
        selection = decode_fixture_selection(world.reader, parent)
        by_tag = {fixture.tag_hash: fixture for fixture in archetype.fixtures}
        if len(by_tag) != len(archetype.fixtures):
            raise ValueError("building archetype has duplicate fixture tags")
        missing = set(selection.selected_hashes) - by_tag.keys()
        if missing:
            raise ValueError("selected building fixture tag is absent from its archetype")
        for fixture in archetype.fixtures:
            selected = fixture.tag_hash in selection.selected_hashes
            inventory.append({
                "parent_offset": parent.offset, "actor_hash": parent.actor.resource.name_hash,
                "fixture_index": fixture.index, "tag_hash": fixture.tag_hash,
                "selected": selected, "status": fixture.status if selected else "not-selected-by-native-instance",
                "detail": fixture.detail, "rendered": selected and fixture.status == "decoded",
            })
            if selected and fixture.status == "decoded":
                if not np.isclose(parent.scale, 1.0, atol=1e-6):
                    raise ValueError("building fixture parent scale differs from the verified retail identity-scale case")
                placements.append(BuildingFixturePlacement(parent, fixture, selection,
                                  (fixture.matrix @ parent.matrix).astype(np.float32)))
    return BuildingFixtureScene(archetypes, tuple(placements), tuple(inventory))


def verify_fixture_executable_evidence(path: str | Path) -> dict[str, object]:
    """Pin narrow instruction spans used by this adapter to the verified retail XBE."""
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("fixture evidence requires the pinned DAH1 US Xbox retail executable")
    image = XbeImage(data)
    spans = {
        "fixture_record_stride": (0x44903, "8b91840000008b0a4083c3643bc1"),
        "fixture_visual_hash": (0x4B42F, "8b4f0455518bc8e8c5df0b00"),
        "selected_tag_compare": (0x447DF, "8b96e00000008b92840000008b7c1a048d541a0483c0043938746e"),
        "fixture_local_position": (0x4B1F3, "8b560c8b404c8d4c24285183c20c8d4c240889442438e822ab0800"),
        "fixture_local_quaternion": (0x4B21E, "8b560c8d4424285083c2188d4c243ce8cea70800"),
        "fixture_initial_undamaged": (0x4AA69, "8b4650bf287a10ee3bc77455"),
        "optional_fixture_selection": (0x10DCA0, "8bd18b02f6c4047409e812feffff034204c3"),
    }
    checks = []
    for name, (address, hex_bytes) in spans.items():
        raw = bytes.fromhex(hex_bytes)
        if image.read_virtual(address, len(raw)) != raw:
            raise ValueError(f"fixture executable evidence mismatch: {name}")
        checks.append({"name": name, "virtual_address": f"0x{address:08X}",
                       "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return {"status": "verified", "executable_sha256": digest, "checks": checks}
