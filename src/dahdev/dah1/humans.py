"""Read-only retail pedestrian skin geometry and native material variants.

Preview vertices are the authored reference mesh; no animation or invented pose
is applied. The skeleton, batch palettes and weight bytes remain available for
a future verified animation implementation.
"""
from __future__ import annotations

import struct
import math
import re
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .container import BlockReader, ResourceRecord
from .materials import MATERIAL_TYPE, NativeMaterial, decode_material
from .world import ACTOR_ARCHETYPE_TYPE, ActorArchetype, WorldPlacement, WorldScene, _strip_triangles


SKIN_VISUAL_TYPE = 0x7C6FF505
SKIN_MESH_TYPE = 0x72C7D314
SKIN_BATCH_STRIDE = 0x8E
BONE_STRIDE = 0x38


@dataclass(frozen=True)
class SkinSegment:
    index: int
    offset: int
    raw_bytes: bytes
    vertex_start: int
    vertex_count: int
    index_start: int
    index_count: int
    material_slot: int
    triangle_count: int
    palette: tuple[tuple[int, int], ...]
    triangles: NDArray[np.int32]

    @property
    def raw_record(self) -> tuple[int, ...]:
        return struct.unpack_from("<6H", self.raw_bytes, 0x82)


@dataclass(frozen=True)
class SkinMesh:
    resource: ResourceRecord
    raw_descriptor: bytes
    vertex_buffer_offset: int
    index_buffer_offset: int
    segment_buffer_offset: int
    stride: int
    layout_flags: int
    vertex_bytes: NDArray[np.uint8]
    local_positions: NDArray[np.float32]
    local_uvs: NDArray[np.float32]
    normals: NDArray[np.float32]
    weight_bytes: NDArray[np.uint8]
    bone_index_bytes: NDArray[np.uint8]
    indices: NDArray[np.uint16]
    triangles: NDArray[np.int32]
    segments: tuple[SkinSegment, ...]

    @property
    def batch_count(self) -> int:
        return len(self.segments)


@dataclass(frozen=True)
class SkinBone:
    index: int
    offset: int
    raw_bytes: bytes
    name_hash: int
    parent_hash: int
    child_hash: int
    sibling_hash: int
    parent_index: int | None
    local_position: tuple[float, float, float]
    local_quaternion_xyzw: tuple[float, float, float, float]
    local_scale: float


@dataclass(frozen=True)
class SkinSkeleton:
    load_group: int
    shared_offset: int
    raw_shared_header: bytes
    bone_buffer_offset: int
    bones: tuple[SkinBone, ...]


@dataclass(frozen=True)
class HumanVisual:
    resource: ResourceRecord
    mesh: SkinMesh
    materials: tuple[NativeMaterial, ...]
    skeleton: SkinSkeleton
    source_visual_type: int = SKIN_VISUAL_TYPE
    destruction_state: None = None
    pose_status: str = "native-reference-mesh; animation-not-applied"


@dataclass(frozen=True)
class HumanLOD:
    index: int
    visual: HumanVisual


@dataclass(frozen=True)
class HumanArchetype:
    actor: ActorArchetype
    lod_pointer_offset: int
    variant_hash: int
    variant_table_offset: int | None
    selected_variant_offset: int | None
    lods: tuple[HumanLOD, ...]


@dataclass(frozen=True)
class HumanScene:
    placements: tuple[WorldPlacement, ...]
    pedestrian_placements: tuple[WorldPlacement, ...]
    archetypes: dict[int, HumanArchetype]


def _require_range(data_limit: int, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > data_limit:
        raise ValueError(f"{label} is outside resource data")


def decode_skin_mesh(reader: BlockReader, resource: ResourceRecord) -> SkinMesh:
    if resource.type_hash != SKIN_MESH_TYPE or resource.size != 32:
        raise ValueError("skin mesh descriptor has an unexpected type or size")
    group = reader.load_group(resource.load_group)
    data, base = group.data, resource.offset
    pointers = group.relocations.pointers
    if any(base + field not in pointers for field in (0, 4, 0x1C)):
        raise ValueError("skin mesh descriptor lacks a required buffer relocation")
    vertices, indices_offset, segments_offset = (pointers[base + f] for f in (0, 4, 0x1C))
    vc, ic, packed_layout = struct.unpack_from("<3I", data, base + 0x10)
    stride, segment_count = packed_layout & 0xFFFF, packed_layout >> 16
    if stride != 40 or not vc or not ic or not segment_count:
        raise ValueError("skin mesh has an unsupported or empty layout")
    _require_range(group.resource_limit, vertices, vc * stride, "skin vertex buffer")
    _require_range(group.resource_limit, indices_offset, ic * 2, "skin index buffer")
    _require_range(group.resource_limit, segments_offset, segment_count * SKIN_BATCH_STRIDE, "skin batches")
    vertex_bytes = np.frombuffer(data, dtype=np.uint8, count=vc * stride, offset=vertices).reshape(vc, stride).copy()

    def floats(offset: int, width: int) -> NDArray[np.float32]:
        return np.ndarray((vc, width), dtype="<f4", buffer=data, offset=vertices + offset, strides=(stride, 4)).copy()

    positions, normals, uvs = floats(0, 3), floats(12, 3), floats(32, 2)
    if any(not np.isfinite(channel).all() for channel in (positions, normals, uvs)):
        raise ValueError("skin mesh contains non-finite vertex channels")
    weights = vertex_bytes[:, 24:28].copy()
    bone_bytes = vertex_bytes[:, 28:32].copy()
    # Quantized influence sums need not be exactly 255; preserve their bytes.
    if np.any(weights.sum(axis=1) == 0):
        raise ValueError("skin vertex has no non-zero influence")
    indices = np.frombuffer(data, dtype="<u2", count=ic, offset=indices_offset).copy()
    if int(indices.max()) >= vc:
        raise ValueError("skin index references a vertex outside its buffer")
    segments = []
    cursor = 0
    vertex_cursor = 0
    for index in range(segment_count):
        offset = segments_offset + index * SKIN_BATCH_STRIDE
        raw = data[offset : offset + SKIN_BATCH_STRIDE]
        palette_count = struct.unpack_from("<H", raw)[0]
        if not 1 <= palette_count <= 32:
            raise ValueError("skin batch has an invalid bone palette size")
        palette = tuple(struct.unpack_from("<HH", raw, 2 + i * 4) for i in range(palette_count))
        if tuple(local for _, local in palette) != tuple(range(palette_count)):
            raise ValueError("skin batch palette slots are not contiguous")
        vs, count, start, length, triangle_count, material_slot = struct.unpack_from("<6H", raw, 0x82)
        if vs != vertex_cursor or start != cursor or not count or length < 3 or start + length > ic or vs + count > vc:
            raise ValueError("skin batch ranges do not cover buffers contiguously")
        strip = indices[start : start + length]
        if int(strip.min()) < vs or int(strip.max()) >= vs + count:
            raise ValueError("skin batch indices exceed its vertex range")
        active = weights[vs : vs + count] != 0
        active_indices = bone_bytes[vs : vs + count][active]
        if np.any(active_indices % 6) or np.any(active_indices // 6 >= palette_count):
            raise ValueError("skin active influence does not resolve through its batch palette")
        triangles = _strip_triangles(strip)
        if len(triangles) != triangle_count:
            raise ValueError("skin batch triangle count mismatch")
        segments.append(SkinSegment(index, offset, raw, vs, count, start, length, material_slot, triangle_count, palette, triangles))
        cursor += length
        vertex_cursor += count
    if cursor != ic or vertex_cursor != vc:
        raise ValueError("skin batches leave buffer data unaccounted for")
    return SkinMesh(resource, data[base:base + 32], vertices, indices_offset, segments_offset, stride, segment_count, vertex_bytes, positions, uvs, normals, weights, bone_bytes, indices, np.concatenate([s.triangles for s in segments]), tuple(segments))


def decode_skin_skeleton(reader: BlockReader, visual: ResourceRecord) -> SkinSkeleton:
    if visual.type_hash != SKIN_VISUAL_TYPE or visual.size != 0xD4:
        raise ValueError("skin skeleton owner has an unsupported visual layout")
    group = reader.load_group(visual.load_group)
    data, pointers = group.data, group.relocations.pointers
    shared = pointers.get(visual.offset + 0xC0)
    if shared is None:
        raise ValueError("primary skin visual lacks shared data")
    _require_range(group.resource_limit, shared, 0x3C, "skin shared header")
    bones_offset = pointers.get(shared + 8)
    count = struct.unpack_from("<H", data, shared + 0xC)[0]
    if bones_offset is None or not count:
        raise ValueError("skin shared data lacks its bone table")
    _require_range(group.resource_limit, bones_offset, count * BONE_STRIDE, "skin bones")
    raw_bones = [data[bones_offset + i * BONE_STRIDE : bones_offset + (i + 1) * BONE_STRIDE] for i in range(count)]
    names = [struct.unpack_from("<I", raw, 4)[0] for raw in raw_bones]
    if len(set(names)) != count or 0 in names:
        raise ValueError("skin skeleton has duplicate or null bone identities")
    by_name = {name: i for i, name in enumerate(names)}
    bones = []
    for index, raw in enumerate(raw_bones):
        name, parent, child, sibling = struct.unpack_from("<4I", raw, 4)
        if any(h and h not in by_name for h in (parent, child, sibling)):
            raise ValueError("skin bone hierarchy references a missing bone")
        values = struct.unpack_from("<8f", raw, 0x14)
        if not np.isfinite(values).all():
            raise ValueError("skin bone transform contains non-finite values")
        # Retail quaternions include approx. 0.9997 norms. Validate their
        # observed near-unit representation without changing the stored values.
        if not np.isclose(np.linalg.norm(values[3:7]), 1.0, atol=1e-3):
            raise ValueError("skin bone quaternion is not near unit length")
        bones.append(SkinBone(index, bones_offset + index * BONE_STRIDE, raw, name, parent, child, sibling, by_name.get(parent), values[:3], values[3:7], values[7]))
    for bone in bones:
        seen = {bone.index}
        parent = bone.parent_index
        while parent is not None:
            if parent in seen:
                raise ValueError("skin bone parent chain contains a cycle")
            seen.add(parent)
            parent = bones[parent].parent_index
    return SkinSkeleton(visual.load_group, shared, data[shared:shared + 0x3C], bones_offset, tuple(bones))


def _materials(reader: BlockReader, load_group: int, table: int, count: int) -> tuple[NativeMaterial, ...]:
    group = reader.load_group(load_group)
    _require_range(group.resource_limit, table, count * 4, "skin material pointer array")
    result = []
    for index in range(count):
        target = group.relocations.pointers.get(table + index * 4)
        material = reader.resource_by_location(load_group, target) if target is not None else None
        if material is None or material.type_hash != MATERIAL_TYPE:
            raise ValueError("skin material pointer does not resolve to a native material")
        result.append(decode_material(reader, material))
    return tuple(result)


def read_humans(world: WorldScene) -> HumanScene:
    reader = world.reader
    pedestrians = tuple(p for p in world.placements if p.actor and p.actor.native_class == "ActorTypePedestrian")
    human_placements = tuple(p for p in pedestrians if p.actor.asset_name and p.actor.asset_name.startswith("h_"))
    archetypes: dict[int, HumanArchetype] = {}
    mesh_cache: dict[int, SkinMesh] = {}
    for placement in pedestrians:
        actor = placement.actor
        if actor.resource.name_hash in archetypes:
            continue
        lod = actor.lod_resource
        if lod is None:
            raise ValueError("pedestrian archetype lacks its primary LOD visual")
        group = reader.load_group(actor.resource.load_group)
        data, pointers = group.data, group.relocations.pointers
        lod_fields = [field - actor.resource.offset for field, target in pointers.items() if actor.resource.offset <= field < actor.resource.offset + actor.resource.size and target == lod.offset]
        if len(lod_fields) != 1 or lod.load_group != actor.resource.load_group:
            raise ValueError("pedestrian primary LOD pointer is ambiguous")
        table = pointers.get(lod.offset + 0xC0)
        count = struct.unpack_from("<I", data, lod.offset + 0xC8)[0]
        if table is None or not count:
            raise ValueError("pedestrian LOD lacks its visual children")
        _require_range(group.resource_limit, table, count * 4, "pedestrian LOD table")
        children = []
        for index in range(count):
            target = pointers.get(table + index * 4)
            child = reader.resource_by_location(lod.load_group, target) if target is not None else None
            if child is None or child.type_hash != SKIN_VISUAL_TYPE or child.size != 0xD4:
                raise ValueError("pedestrian LOD child has an unsupported visual layout")
            children.append(child)
        skeleton = decode_skin_skeleton(reader, children[0])
        shared = skeleton.shared_offset
        material_count = struct.unpack_from("<I", data, shared + 0x34)[0]
        material_table = pointers.get(shared + 0x30)
        if material_table is None or not material_count:
            raise ValueError("primary pedestrian skin lacks its native materials")
        variant_hash = struct.unpack_from("<I", data, actor.resource.offset + 0x1C)[0]
        variant_table = pointers.get(lod.offset + 0xB4)
        variant_count = struct.unpack_from("<I", data, lod.offset + 0xB8)[0]
        selected_variant = None
        if variant_count:
            if variant_table is None:
                raise ValueError("pedestrian variant table lacks a relocation")
            _require_range(group.resource_limit, variant_table, variant_count * 8, "pedestrian variants")
            matches = [variant_table + index * 8 for index in range(variant_count) if struct.unpack_from("<I", data, variant_table + index * 8)[0] == variant_hash]
            if len(matches) != 1:
                raise ValueError("pedestrian material variant is absent or ambiguous")
            selected_variant = pointers.get(matches[0] + 4)
            if selected_variant is None:
                raise ValueError("pedestrian variant lacks its material-list relocation")
            _require_range(group.resource_limit, selected_variant, 8, "pedestrian variant material list")
            material_table = pointers.get(selected_variant)
            material_count = struct.unpack_from("<I", data, selected_variant + 4)[0]
            if material_table is None or not material_count:
                raise ValueError("pedestrian selected variant is empty")
        materials = _materials(reader, lod.load_group, material_table, material_count)
        lods = []
        for index, child in enumerate(children):
            mesh_hash = struct.unpack_from("<I", data, child.offset + 0xC8)[0]
            mesh = mesh_cache.get(mesh_hash)
            if mesh is None:
                resource = reader.resource_by_identity(SKIN_MESH_TYPE, mesh_hash)
                if resource is None:
                    raise ValueError("pedestrian skin references a missing mesh")
                mesh = decode_skin_mesh(reader, resource)
                mesh_cache[mesh_hash] = mesh
            if any(segment.material_slot >= len(materials) for segment in mesh.segments):
                raise ValueError("skin segment material slot exceeds selected variant")
            if any(global_index >= len(skeleton.bones) for segment in mesh.segments for global_index, _ in segment.palette):
                raise ValueError("skin batch palette exceeds its own skeleton")
            lods.append(HumanLOD(index, HumanVisual(child, mesh, materials, skeleton)))
        archetypes[actor.resource.name_hash] = HumanArchetype(actor, lod_fields[0], variant_hash, variant_table, selected_variant, tuple(lods))
    return HumanScene(human_placements, pedestrians, archetypes)


def validate_human_archetype_reports(reports: object, object_placements: object) -> None:
    """Validate the exported human report graph against its preserved bytes.

    This establishes report integrity and linkage, not retail animation parity.
    It intentionally accepts empty reports when the scene contains no humans.
    """

    def uint(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
            raise ValueError(f"human report {label} must be a uint32")
        return value

    def hash32(value: object, label: str) -> int:
        if not isinstance(value, str) or re.fullmatch(r"0x[0-9A-Fa-f]{8}", value) is None:
            raise ValueError(f"human report {label} must be a hexadecimal uint32")
        return int(value, 16)

    def raw_hex(value: object, size: int, label: str) -> bytes:
        if not isinstance(value, str) or len(value) != size * 2:
            raise ValueError(f"human report {label} must preserve exactly {size} bytes")
        try:
            raw = bytes.fromhex(value)
        except ValueError as error:
            raise ValueError(f"human report {label} is not hexadecimal") from error
        if len(raw) != size:
            raise ValueError(f"human report {label} must preserve exactly {size} bytes")
        return raw

    def finite(value: object) -> bool:
        try:
            return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:
            return False

    def resource_identity(value: object) -> tuple[int, int]:
        if not isinstance(value, dict):
            raise ValueError("human report resource must be an object")
        identity = hash32(value.get("type_hash"), "resource type"), hash32(value.get("name_hash"), "resource name")
        if identity[0] != ACTOR_ARCHETYPE_TYPE:
            raise ValueError("human report resource must be a native actor archetype")
        for key in ("index", "load_group", "offset", "size"):
            uint(value.get(key), f"resource {key}")
        hash32(value.get("flags"), "resource flags")
        return identity

    if not isinstance(reports, list) or not isinstance(object_placements, list):
        raise ValueError("human reports and object placements must be arrays")
    by_identity: dict[tuple[int, int], dict] = {}
    for report in reports:
        if not isinstance(report, dict):
            raise ValueError("human archetype report must be an object")
        identity = resource_identity(report.get("resource"))
        if identity in by_identity:
            raise ValueError("duplicate human archetype report identity")
        by_identity[identity] = report
        hash32(report.get("variant_hash"), "variant hash")
        pointer = uint(report.get("lod_pointer_offset"), "LOD pointer offset")
        if pointer + 4 > report["resource"]["size"]:
            raise ValueError("human report LOD pointer is outside its actor resource")
        for key in ("variant_table_offset", "selected_variant_offset"):
            if key not in report:
                raise ValueError(f"human report lacks {key}")
            if report[key] is not None:
                uint(report[key], key)
        if (report["variant_table_offset"] is None) != (report["selected_variant_offset"] is None):
            raise ValueError("human report variant provenance is incomplete")
        if report.get("pose_status") != "native-reference-mesh; animation-not-applied":
            raise ValueError("human report has an unsupported pose status")
        skeleton = report.get("skeleton")
        if not isinstance(skeleton, dict):
            raise ValueError("human report lacks its skeleton")
        if uint(skeleton.get("load_group"), "skeleton load group") != report["resource"]["load_group"]:
            raise ValueError("human skeleton and actor load groups disagree")
        uint(skeleton.get("shared_offset"), "shared offset")
        bone_base = uint(skeleton.get("bone_buffer_offset"), "bone buffer offset")
        shared = raw_hex(skeleton.get("raw_shared_header_hex"), 0x3C, "shared header")
        bones = skeleton.get("bones")
        if not isinstance(bones, list) or not bones or len(bones) != struct.unpack_from("<H", shared, 0xC)[0]:
            raise ValueError("human skeleton bone count disagrees with the native shared header")
        names: dict[int, int] = {}
        hierarchy: list[tuple[int, int, int]] = []
        for index, bone in enumerate(bones):
            if not isinstance(bone, dict):
                raise ValueError("human report bone must be an object")
            if uint(bone.get("index"), "bone index") != index or uint(bone.get("offset"), "bone offset") != bone_base + index * BONE_STRIDE:
                raise ValueError("human bone indices or source offsets are not contiguous")
            raw = raw_hex(bone.get("raw_hex"), BONE_STRIDE, "bone record")
            name, parent, child, sibling = struct.unpack_from("<4I", raw, 4)
            if not name or name in names or name != hash32(bone.get("name_hash"), "bone name"):
                raise ValueError("human bone name disagrees with native bytes or is duplicated")
            names[name] = index
            hierarchy.append((parent, child, sibling))
            position = bone.get("local_position_f32")
            quaternion = bone.get("local_quaternion_xyzw_f32")
            scale = bone.get("local_scale_f32")
            if not isinstance(position, list) or len(position) != 3 or not isinstance(quaternion, list) or len(quaternion) != 4:
                raise ValueError("human bone transform has an invalid shape")
            values = [*position, *quaternion, scale]
            native_values = struct.unpack_from("<8f", raw, 0x14)
            if not all(finite(value) for value in values) or not all(finite(value) for value in native_values):
                raise ValueError("human bone transform contains non-finite values")
            if tuple(values) != native_values:
                raise ValueError("human bone transform disagrees with its native bytes")
            if not math.isclose(math.sqrt(sum(v * v for v in quaternion)), 1.0, abs_tol=0.001):
                raise ValueError("human bone quaternion is not near unit length")
        parents = []
        for bone, (parent, child, sibling) in zip(bones, hierarchy):
            if any(name and name not in names for name in (parent, child, sibling)):
                raise ValueError("human bone hierarchy references a missing native bone")
            expected_parent = names.get(parent)
            supplied_parent = bone.get("parent_index")
            if "parent_index" not in bone or (supplied_parent is not None and uint(supplied_parent, "bone parent index") >= len(bones)) or supplied_parent != expected_parent:
                raise ValueError("human bone parent index disagrees with its native parent hash")
            parents.append(expected_parent)
        for index in range(len(bones)):
            seen = {index}
            parent = parents[index]
            while parent is not None:
                if parent in seen:
                    raise ValueError("human skeleton parent chain contains a cycle")
                seen.add(parent)
                parent = parents[parent]

    referenced: set[tuple[int, int]] = set()
    for placement in object_placements:
        if not isinstance(placement, dict):
            raise ValueError("human report object placement must be an object")
        if placement.get("editor_collection") != "humans":
            continue
        archetype = placement.get("archetype")
        if not isinstance(archetype, dict) or archetype.get("native_class") != "ActorTypePedestrian":
            raise ValueError("human placement lacks its native pedestrian archetype")
        identity = resource_identity(archetype.get("resource"))
        report = by_identity.get(identity)
        if report is None:
            raise ValueError("human placement references a missing archetype report")
        if archetype["resource"] != report["resource"]:
            raise ValueError("human placement and report resource provenance disagree")
        referenced.add(identity)
    if referenced != by_identity.keys():
        raise ValueError("human archetype report is not referenced by a human placement")
