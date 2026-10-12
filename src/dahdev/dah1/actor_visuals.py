"""Native read-only rigid/segmented actor visuals and completeness inventory.

The segmented table is an attachment list, NOT a destruction-state list.
Retail loader 0xF71D0 copies all pairs; draw 0xF7390 indexes skeleton nodes with
each pair's first word and submits every visible child's mesh. This module
constructs the authored reference pose, without simulating runtime animation,
destruction, or per-node visibility. Original geometry and transforms survive.
"""
from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .container import BlockReader, ResourceRecord
from .humans import BONE_STRIDE, SkinBone, SkinSkeleton
from .materials import MATERIAL_TYPE, NativeMaterial, decode_material
from .transforms import instance_matrix
from .world import (
    LOD_TYPE, MATERIAL_TABLE_TYPE, SEGMENTED_VISUAL_TYPE, STATIC_VISUAL_TYPE,
    ActorArchetype, StaticMesh, StaticVisual, WorldPlacement, WorldScene,
    _decode_static_visual, _material_table,
)


@dataclass(frozen=True)
class ActorVisualPart:
    index: int
    visual: StaticVisual
    bone_index: int | None
    table_offset: int | None
    raw_table_record: bytes
    matrix: NDArray[np.float32]


@dataclass(frozen=True)
class ActorVisualLOD:
    index: int
    resource: ResourceRecord
    parts: tuple[ActorVisualPart, ...]
    skeleton: SkinSkeleton | None
    variant_hash: int
    variant_table_offset: int | None
    selected_variant_offset: int | None
    pose_status: str = "native-reference-pose; runtime-animation-and-visibility-not-applied"


@dataclass(frozen=True)
class ActorVisualReference:
    pointer_field: int
    resource: ResourceRecord
    selected: bool


@dataclass(frozen=True)
class ActorVisualArchetype:
    actor: ActorArchetype
    visual_resource: ResourceRecord
    references: tuple[ActorVisualReference, ...]
    lods: tuple[ActorVisualLOD, ...]


@dataclass(frozen=True)
class ActorVisualInventory:
    actor: ActorArchetype
    placement_count: int
    status: str
    detail: str | None
    references: tuple[ActorVisualReference, ...]


@dataclass(frozen=True)
class ActorVisualScene:
    placements: tuple[WorldPlacement, ...]
    archetypes: dict[int, ActorVisualArchetype]
    inventory: tuple[ActorVisualInventory, ...]


def _range(limit: int, offset: int, count: int, label: str) -> None:
    if offset < 0 or count < 0 or offset + count > limit:
        raise ValueError(f"{label} exceeds native resource data")


def decode_segment_skeleton(reader: BlockReader, visual: ResourceRecord) -> SkinSkeleton:
    """Read the same shared bone56 representation used by native skin visuals."""
    if visual.type_hash != SEGMENTED_VISUAL_TYPE or visual.size < 0xCC:
        raise ValueError("segmented skeleton owner has an unsupported layout")
    group = reader.load_group(visual.load_group)
    data, pointers = group.data, group.relocations.pointers
    shared = pointers.get(visual.offset + 0xC0)
    if shared is None:
        raise ValueError("primary segmented visual lacks its shared skeleton")
    _range(group.resource_limit, shared, 0x3C, "segmented shared header")
    table = pointers.get(shared + 8)
    count = struct.unpack_from("<H", data, shared + 0xC)[0]
    if table is None or not count:
        raise ValueError("segmented shared skeleton lacks its bone table")
    _range(group.resource_limit, table, count * BONE_STRIDE, "segmented bones")
    records = [data[table + i * BONE_STRIDE: table + (i + 1) * BONE_STRIDE] for i in range(count)]
    names = [struct.unpack_from("<I", raw, 4)[0] for raw in records]
    if 0 in names or len(set(names)) != count:
        raise ValueError("segmented skeleton contains duplicate/null bone identities")
    by_name = {name: i for i, name in enumerate(names)}
    bones = []
    for index, raw in enumerate(records):
        name, parent, child, sibling = struct.unpack_from("<4I", raw, 4)
        if any(h and h not in by_name for h in (parent, child, sibling)):
            raise ValueError("segmented skeleton hierarchy references a missing bone")
        values = struct.unpack_from("<8f", raw, 0x14)
        if not np.isfinite(values).all() or not np.isclose(np.linalg.norm(values[3:7]), 1.0, atol=1e-3):
            raise ValueError("segmented bone has a non-finite transform or non-unit quaternion")
        bones.append(SkinBone(index, table + index * BONE_STRIDE, raw, name, parent, child, sibling,
                              by_name.get(parent), values[:3], values[3:7], values[7]))
    skeleton = SkinSkeleton(visual.load_group, shared, data[shared:shared + 0x3C], table, tuple(bones))
    skeleton_reference_matrices(skeleton)  # Validate parent cycles before export.
    return skeleton


def skeleton_reference_matrices(skeleton: SkinSkeleton) -> tuple[NDArray[np.float32], ...]:
    """Compose bone-local transforms in row-vector order (local @ parent)."""
    count = len(skeleton.bones)
    result: dict[int, NDArray[np.float32]] = {}
    visiting: set[int] = set()

    def matrix(index: int) -> NDArray[np.float32]:
        if index in visiting:
            raise ValueError("segmented skeleton parent chain contains a cycle")
        if not 0 <= index < count:
            raise ValueError("segmented skeleton parent index exceeds bone table")
        if index not in result:
            visiting.add(index)
            bone = skeleton.bones[index]
            value = instance_matrix(np.asarray(bone.local_quaternion_xyzw, dtype=np.float32),
                                    np.asarray(bone.local_position, dtype=np.float32), np.float32(bone.local_scale))
            if bone.parent_index is not None:
                value = (value @ matrix(bone.parent_index)).astype(np.float32)
            if not np.isfinite(value).all():
                raise ValueError("segmented reference transform is non-finite")
            result[index] = value
            visiting.remove(index)
        return result[index]

    return tuple(matrix(index) for index in range(count))


def _materials(reader: BlockReader, load_group: int, table: int, count: int) -> tuple[NativeMaterial, ...]:
    group = reader.load_group(load_group)
    _range(group.resource_limit, table, count * 4, "native material array")
    if not count:
        raise ValueError("native material array is empty")
    result = []
    for index in range(count):
        target = group.relocations.pointers.get(table + index * 4)
        resource = reader.resource_by_location(load_group, target) if target is not None else None
        if resource is None or resource.type_hash != MATERIAL_TYPE:
            raise ValueError("native material pointer does not resolve to a material")
        result.append(decode_material(reader, resource))
    return tuple(result)


def decode_actor_visual_lods(
    reader: BlockReader, lod_resource: ResourceRecord, *, variant_hash: int = 0,
    mesh_cache: dict[int, StaticMesh] | None = None,
) -> tuple[ActorVisualLOD, ...]:
    """Decode every rigid component in each LOD, with native material inheritance.

Accepts a LOD wrapper or a direct static/segmented visual. Lower LODs reuse the
primary shared skeleton and materials, as they do not duplicate those resources.
"""
    cache = {} if mesh_cache is None else mesh_cache
    group = reader.load_group(lod_resource.load_group)
    data, pointers = group.data, group.relocations.pointers
    if lod_resource.type_hash == LOD_TYPE:
        if lod_resource.size < 0xCC:
            raise ValueError("native LOD header is truncated")
        table = pointers.get(lod_resource.offset + 0xC0)
        count = struct.unpack_from("<I", data, lod_resource.offset + 0xC8)[0]
        if table is None or not count:
            raise ValueError("native LOD lacks its child pointer table")
        _range(group.resource_limit, table, count * 4, "native LOD children")
        children = []
        for index in range(count):
            target = pointers.get(table + index * 4)
            child = reader.resource_by_location(lod_resource.load_group, target) if target is not None else None
            if child is None:
                raise ValueError("native LOD child does not resolve to a resource")
            children.append(child)
    elif lod_resource.type_hash in {STATIC_VISUAL_TYPE, SEGMENTED_VISUAL_TYPE}:
        children = [lod_resource]
    else:
        raise ValueError(f"unsupported primary visual type 0x{lod_resource.type_hash:08X}")

    variant_table = pointers.get(lod_resource.offset + 0xB4)
    variant_count = struct.unpack_from("<I", data, lod_resource.offset + 0xB8)[0]
    selected_variant = None
    selected_materials = None
    if variant_count:
        if variant_table is None:
            raise ValueError("native material variant table lacks a relocation")
        _range(group.resource_limit, variant_table, variant_count * 8, "native material variants")
        matches = [variant_table + i * 8 for i in range(variant_count)
                   if struct.unpack_from("<I", data, variant_table + i * 8)[0] == variant_hash]
        if len(matches) != 1:
            raise ValueError(f"native material variant 0x{variant_hash:08X} is absent or ambiguous")
        selected_variant = pointers.get(matches[0] + 4)
        if selected_variant is None:
            raise ValueError("native selected variant has no material-list relocation")
        _range(group.resource_limit, selected_variant, 8, "native selected material list")
        material_table = pointers.get(selected_variant)
        material_count = struct.unpack_from("<I", data, selected_variant + 4)[0]
        if material_table is None:
            raise ValueError("native selected variant has no material table")
        selected_materials = _materials(reader, lod_resource.load_group, material_table, material_count)

    skeleton = None
    skeleton_matrices = None
    inherited_materials = None
    lods = []
    for index, child in enumerate(children):
        if child.load_group != lod_resource.load_group:
            raise ValueError("native relocated LOD child crosses load groups")
        if child.type_hash == STATIC_VISUAL_TYPE:
            material_target = pointers.get(child.offset + 0xE8)
            if material_target is not None:
                table_resource = reader.resource_by_location(child.load_group, material_target)
                if table_resource is None or table_resource.type_hash != MATERIAL_TABLE_TYPE:
                    raise ValueError("static material table has unexpected resource type")
                inherited_materials = _material_table(reader, table_resource)
            materials = selected_materials if selected_materials is not None else inherited_materials
            if materials is None:
                raise ValueError("static LOD lacks primary/inherited materials")
            visual = _decode_static_visual(reader, child, cache, material_override=materials)
            parts = (ActorVisualPart(0, visual, None, None, b"", np.eye(4, dtype=np.float32)),)
        elif child.type_hash == SEGMENTED_VISUAL_TYPE:
            if child.size < 0xCC:
                raise ValueError("segmented visual header is truncated")
            shared = pointers.get(child.offset + 0xC0)
            if shared is not None:
                skeleton = decode_segment_skeleton(reader, child)
                skeleton_matrices = skeleton_reference_matrices(skeleton)
                material_table = pointers.get(shared + 0x30)
                material_count = struct.unpack_from("<I", data, shared + 0x34)[0]
                if material_table is None:
                    raise ValueError("segmented shared data lacks material table")
                inherited_materials = _materials(reader, child.load_group, material_table, material_count)
            materials = selected_materials if selected_materials is not None else inherited_materials
            if skeleton is None or skeleton_matrices is None or materials is None:
                raise ValueError("segmented LOD lacks primary/inherited skeleton or materials")
            table = pointers.get(child.offset + 0xC4)
            count = struct.unpack_from("<I", data, child.offset + 0xC8)[0]
            if table is None or not count:
                raise ValueError("segmented LOD lacks component table")
            _range(group.resource_limit, table, count * 8, "segmented component table")
            parts_list = []
            seen_bones = set()
            for part_index in range(count):
                entry = table + part_index * 8
                bone_index = struct.unpack_from("<I", data, entry)[0]
                if bone_index >= len(skeleton.bones) or bone_index in seen_bones:
                    raise ValueError("segmented component has invalid/duplicate skeleton bone index")
                seen_bones.add(bone_index)
                target = pointers.get(entry + 4)
                component = reader.resource_by_location(child.load_group, target) if target is not None else None
                if component is None or component.type_hash != STATIC_VISUAL_TYPE:
                    raise ValueError("segmented component does not resolve to a static visual")
                visual = _decode_static_visual(reader, component, cache,
                                               source_visual_type=SEGMENTED_VISUAL_TYPE,
                                               material_override=materials)
                parts_list.append(ActorVisualPart(part_index, visual, bone_index, entry,
                                                  data[entry:entry + 8], skeleton_matrices[bone_index]))
            parts = tuple(parts_list)
        else:
            raise ValueError(f"unsupported LOD child type 0x{child.type_hash:08X}")
        lods.append(ActorVisualLOD(index, child, parts, skeleton, variant_hash, variant_table, selected_variant))
    return tuple(lods)


def read_actor_visuals(world: WorldScene) -> ActorVisualScene:
    """Inventory every placed actor; decode static/segmented families by resources.

Pedestrian skins remain delegated to read_humans, including cows and powersuits.
Unresolved actors remain inventory records with their native links and reason.
"""
    reader = world.reader
    counts = Counter(p.actor.resource.name_hash for p in world.placements if p.actor)
    cache: dict[int, StaticMesh] = {}
    archetypes = {}
    inventory = []
    for key, actor in world.archetypes.items():
        group = reader.load_group(actor.resource.load_group)
        candidates = []
        for field, target in sorted(group.relocations.pointers.items()):
            if actor.resource.offset <= field < actor.resource.offset + actor.resource.size:
                resource = reader.resource_by_location(actor.resource.load_group, target)
                if resource is not None and resource.type_hash in {LOD_TYPE, STATIC_VISUAL_TYPE, SEGMENTED_VISUAL_TYPE, 0x7C6FF505}:
                    candidates.append((field - actor.resource.offset, resource))
        selected = actor.lod_resource
        if selected is None:
            direct = {r.index: r for _, r in candidates if r.type_hash in {STATIC_VISUAL_TYPE, SEGMENTED_VISUAL_TYPE}}
            selected = next(iter(direct.values())) if len(direct) == 1 else None
        references = tuple(ActorVisualReference(field, resource, resource == selected) for field, resource in candidates)
        status, detail = "decoded", None
        if actor.native_class == "ActorTypePedestrian":
            status = "pedestrian-skin-reader"
        elif selected is None:
            status, detail = "no-resolved-primary-visual", "No unique relocated primary visual; actor retained as native placement."
        else:
            variant_hash = struct.unpack_from("<I", group.data, actor.resource.offset + 0x1C)[0]
            try:
                lods = decode_actor_visual_lods(reader, selected, variant_hash=variant_hash, mesh_cache=cache)
                archetypes[key] = ActorVisualArchetype(actor, selected, references, lods)
            except ValueError as exc:
                status, detail = "unresolved-visual", str(exc)
        inventory.append(ActorVisualInventory(actor, counts.get(key, 0), status, detail, references))
    placements = tuple(p for p in world.placements if p.actor and p.actor.resource.name_hash in archetypes)
    return ActorVisualScene(placements, archetypes, tuple(inventory))
