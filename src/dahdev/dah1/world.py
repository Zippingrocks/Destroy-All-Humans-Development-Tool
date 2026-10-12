from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .container import BlockReader, ResourceRecord
from .materials import MATERIAL_TYPE, NativeMaterial, decode_material
from .transforms import instance_matrix


INSTANCE_DATABASE_TYPE = 0xFE55FFAF
WORLD_DATABASE_NAME = 0x59CE7BCB
ACTOR_ARCHETYPE_TYPE = 0x1D9D48EC
LOD_TYPE = 0x6FC132F2
STATIC_VISUAL_TYPE = 0x9805A0A6
SEGMENTED_VISUAL_TYPE = 0x2C84BEFF
STATIC_MESH_TYPE = 0x5B99558D
MATERIAL_TABLE_TYPE = 0x48C2FD5F


@dataclass(frozen=True)
class StaticMeshSegment:
    index: int
    vertex_start: int
    vertex_count: int
    index_start: int
    index_count: int
    material_slot: int
    triangle_count: int
    triangles: NDArray[np.int32]

    @property
    def raw_record(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.vertex_start,
            self.vertex_count,
            self.index_start,
            self.index_count,
            self.material_slot,
            self.triangle_count,
        )


@dataclass(frozen=True)
class StaticMesh:
    resource: ResourceRecord
    vertex_buffer_offset: int
    index_buffer_offset: int
    normal_buffer_offset: int
    segment_buffer_offset: int
    stride: int
    layout_flags: int
    vertex_bytes: NDArray[np.uint8]
    local_positions: NDArray[np.float32]
    local_uvs: NDArray[np.float32]
    packed_normals: NDArray[np.int16]
    indices: NDArray[np.uint16]
    triangles: NDArray[np.int32]
    segments: tuple[StaticMeshSegment, ...]


@dataclass(frozen=True)
class StaticVisual:
    resource: ResourceRecord
    mesh: StaticMesh
    materials: tuple[NativeMaterial, ...]
    source_visual_type: int
    # Historical interchange compatibility only. A segmented component key is
    # a bone attachment index, never evidence for this legacy field.
    destruction_state: int | None


@dataclass(frozen=True)
class WorldObjectLOD:
    index: int
    visual: StaticVisual


@dataclass(frozen=True)
class ActorArchetype:
    resource: ResourceRecord
    native_class: str | None
    asset_name: str | None
    lod_resource: ResourceRecord | None
    lods: tuple[WorldObjectLOD, ...]


@dataclass(frozen=True)
class WorldPlacement:
    bucket_index: int
    bucket_hash: int
    index: int
    offset: int
    raw_header: bytes
    actor: ActorArchetype | None
    position: NDArray[np.float32]
    packed_rotation: int
    quaternion_xyzw: NDArray[np.float32]
    scale: np.float32
    matrix: NDArray[np.float32]
    list_pointer_field: int = 0
    list_offset: int = 0
    list_index: int = 0
    packed_rotation_xy: int = 0


@dataclass(frozen=True)
class InstanceBucket:
    index: int
    offset: int
    type_hash: int
    pointer_table_offset: int
    placements: tuple[WorldPlacement, ...]
    list_offsets: tuple[int, ...] = ()


@dataclass(frozen=True)
class WorldScene:
    reader: BlockReader
    database: ResourceRecord
    buckets: tuple[InstanceBucket, ...]
    placements: tuple[WorldPlacement, ...]
    archetypes: dict[int, ActorArchetype]


def _strip_triangles(strip: NDArray[np.uint16]) -> NDArray[np.int32]:
    faces: list[tuple[int, int, int]] = []
    for index in range(len(strip) - 2):
        a, b, c = (int(value) for value in strip[index : index + 3])
        triangle = (b, a, c) if index & 1 else (a, b, c)
        if len(set(triangle)) == 3:
            faces.append(triangle)
    return np.asarray(faces, dtype=np.int32).reshape((-1, 3))


def decode_packed_yaw_quaternion(value: int) -> NDArray[np.float32]:
    """Decode the retail instance database's signed-16 Z/W quaternion pair."""

    z_raw, w_raw = struct.unpack("<hh", struct.pack("<I", value))

    def snorm(component: int) -> np.float32:
        return np.float32(max(-1.0, component / 32767.0))

    return np.asarray((0.0, 0.0, snorm(z_raw), snorm(w_raw)), dtype=np.float32)


def decode_packed_quaternion(xy: int, zw: int) -> NDArray[np.float32]:
    """Decode all four signed-16 components; props may pitch and roll as well as yaw."""
    return np.asarray(
        [max(-1.0, value / 32767.0) for value in struct.unpack("<4h", struct.pack("<II", xy, zw))],
        dtype=np.float32,
    )


def instance_list_records(data: bytes, relocations: dict[int, int], bucket_offset: int,
                          limit: int) -> tuple[tuple[int, int, int, int], ...]:
    """Traverse BOTH native counted lists at +0x0C/+0x10, never an inline shortcut.

    Return (pointer field, list header, list index, placement offset). Native list
    purposes remain separate from semantic/editor categories until runtime verified.
    """
    result = []
    seen_lists: set[int] = set()
    seen_records: set[int] = set()
    for relative in (0x0C, 0x10):
        field = bucket_offset + relative
        if field + 4 > limit:
            raise ValueError("instance bucket header is truncated")
        table = relocations.get(field)
        if table is None:
            if data[field:field + 4] != bytes(4):
                raise ValueError("instance list pointer is nonzero without relocation")
            continue
        if table in seen_lists or table < 0 or table + 4 > limit:
            raise ValueError("instance list is duplicated or outside resource data")
        seen_lists.add(table)
        count = struct.unpack_from("<I", data, table)[0]
        if table + 4 + count * 4 > limit:
            raise ValueError("instance list pointer array is truncated")
        for index in range(count):
            target = relocations.get(table + 4 + index * 4)
            if target is None or target < 0 or target + 0x40 > limit:
                raise ValueError("instance placement record is missing or truncated")
            if target in seen_records:
                raise ValueError("instance placement is duplicated across native lists")
            seen_records.add(target)
            result.append((relative, table, index, target))
    return tuple(result)


def classify_editor_collection(native_class: str | None, asset_name: str | None) -> str:
    """Add a reversible editor grouping without replacing the native class."""

    if asset_name and (asset_name.startswith("b_") or asset_name == "e_guard_tower"):
        return "buildings"
    if native_class == "RandomType":
        return "population"
    if asset_name and asset_name.startswith("a_"):
        return "animals"
    if asset_name and asset_name.startswith("h_"):
        return "humans"
    if native_class in {"ActorTypeCarDrive", "ActorTypeCarParked"} or (
        asset_name and asset_name.startswith("v_")
    ):
        return "vehicles"
    if native_class == "PropType" or (asset_name and asset_name.startswith("p_")):
        return "props"
    if native_class and native_class.startswith("ActorType"):
        return "gameplay"
    return "other"


def _semantic_actor_strings(raw: bytes) -> tuple[str | None, str | None]:
    strings = [
        match.group().decode("ascii")
        for match in re.finditer(rb"[A-Za-z][A-Za-z0-9_]{2,}", raw)
    ]
    native_class = next(
        (
            value
            for value in strings
            if value.endswith("Type") or value.startswith("ActorType")
        ),
        None,
    )
    asset_name = next(
        (
            value
            for value in strings
            if value.startswith(("a_", "b_", "e_", "h_", "m_", "p_", "r_", "v_"))
        ),
        None,
    )
    return native_class, asset_name


def decode_static_mesh(reader: BlockReader, resource: ResourceRecord) -> StaticMesh:
    if resource.type_hash != STATIC_MESH_TYPE or resource.size != 32:
        raise ValueError("static mesh descriptor has an unexpected type or size")
    group = reader.load_group(resource.load_group)
    data = group.data
    relocations = group.relocations.pointers
    base = resource.offset
    required_fields = (base, base + 8, base + 0x1C)
    missing = [field - base for field in required_fields if field not in relocations]
    if missing:
        raise ValueError(
            f"static mesh 0x{resource.name_hash:08X} lacks relocations at "
            + ", ".join(f"+0x{field:X}" for field in missing)
        )
    vertex_buffer = relocations[base]
    normal_buffer = relocations.get(base + 4, 0)
    index_buffer = relocations[base + 8]
    segment_buffer = relocations[base + 0x1C]
    vertex_count, index_count, packed_layout = struct.unpack_from("<3I", data, base + 0xC)
    # Retail E6F46/E6F86/E7390 read this as signed16, not a uint32.
    segment_count = struct.unpack_from("<h", data, base + 0x18)[0]
    if segment_count < 0:
        raise ValueError("static mesh has a negative segment count")
    stride = packed_layout & 0xFFFF
    layout_flags = packed_layout >> 16
    if stride != 28:
        raise ValueError(f"unsupported static mesh vertex stride {stride}")
    if vertex_buffer + vertex_count * stride > group.resource_limit:
        raise ValueError("static mesh vertex buffer is out of range")
    if index_buffer + index_count * 2 > group.resource_limit:
        raise ValueError("static mesh index buffer is out of range")
    if segment_buffer + segment_count * 12 > group.resource_limit:
        raise ValueError("static mesh segment table is out of range")

    vertex_bytes = np.frombuffer(
        data, dtype=np.uint8, count=vertex_count * stride, offset=vertex_buffer
    ).reshape((vertex_count, stride)).copy()
    positions = np.ndarray(
        (vertex_count, 3),
        dtype="<f4",
        buffer=data,
        offset=vertex_buffer,
        strides=(stride, 4),
    ).copy()
    uvs = np.ndarray(
        (vertex_count, 2),
        dtype="<f4",
        buffer=data,
        offset=vertex_buffer + 0xC,
        strides=(stride, 4),
    ).copy()
    packed_normals = np.ndarray(
        (vertex_count, 4),
        dtype="<i2",
        buffer=data,
        offset=vertex_buffer + 0x14,
        strides=(stride, 2),
    ).copy()
    indices = np.frombuffer(
        data, dtype="<u2", count=index_count, offset=index_buffer
    ).copy()
    if not np.isfinite(positions).all() or not np.isfinite(uvs).all():
        raise ValueError("static mesh position or UV channels contain non-finite values")
    if index_count and int(indices.max()) >= vertex_count:
        raise ValueError("static mesh index references a vertex outside the buffer")

    segments: list[StaticMeshSegment] = []
    faces: list[NDArray[np.int32]] = []
    cursor = 0
    for index in range(segment_count):
        record = struct.unpack_from("<6H", data, segment_buffer + index * 12)
        vertex_start, vertices, index_start, count, material_slot, triangle_count = record
        if index_start != cursor or index_start + count > index_count:
            raise ValueError("static mesh segments do not cover the index buffer contiguously")
        strip = indices[index_start : index_start + count]
        if count < 3 or int(strip.min()) < vertex_start or int(strip.max()) >= vertex_start + vertices:
            raise ValueError("static mesh segment has an invalid vertex or index range")
        triangles = _strip_triangles(strip)
        if len(triangles) != triangle_count:
            raise ValueError("static mesh segment triangle count mismatch")
        segments.append(
            StaticMeshSegment(
                index=index,
                vertex_start=vertex_start,
                vertex_count=vertices,
                index_start=index_start,
                index_count=count,
                material_slot=material_slot,
                triangle_count=triangle_count,
                triangles=triangles,
            )
        )
        faces.append(triangles)
        cursor += count
    if cursor != index_count:
        raise ValueError("static mesh segments leave indices unaccounted for")
    return StaticMesh(
        resource=resource,
        vertex_buffer_offset=vertex_buffer,
        index_buffer_offset=index_buffer,
        normal_buffer_offset=normal_buffer,
        segment_buffer_offset=segment_buffer,
        stride=stride,
        layout_flags=layout_flags,
        vertex_bytes=vertex_bytes,
        local_positions=positions,
        local_uvs=uvs,
        packed_normals=packed_normals,
        indices=indices,
        triangles=np.concatenate(faces) if faces else np.empty((0, 3), dtype=np.int32),
        segments=tuple(segments),
    )


def _material_table(reader: BlockReader, resource: ResourceRecord) -> tuple[NativeMaterial, ...]:
    if resource.type_hash != MATERIAL_TABLE_TYPE or resource.size < 16:
        raise ValueError("visual material table has an unexpected type or size")
    group = reader.load_group(resource.load_group)
    relocations = group.relocations.pointers
    table = relocations.get(resource.offset + 8)
    count = struct.unpack_from("<I", group.data, resource.offset + 0xC)[0]
    if table is None and count:
        raise ValueError("visual material table lacks its relocated pointer array")
    materials = []
    for index in range(count):
        target = relocations.get(table + index * 4)
        resolved = (
            reader.resource_by_location(resource.load_group, target)
            if target is not None
            else None
        )
        if resolved is None or resolved.type_hash != MATERIAL_TYPE:
            raise ValueError("visual material slot does not resolve to a material")
        materials.append(decode_material(reader, resolved))
    return tuple(materials)


def _decode_static_visual(
    reader: BlockReader,
    resource: ResourceRecord,
    mesh_cache: dict[int, StaticMesh],
    *,
    source_visual_type: int | None = None,
    destruction_state: int | None = None,
    material_override: tuple[NativeMaterial, ...] | None = None,
) -> StaticVisual:
    if resource.type_hash != STATIC_VISUAL_TYPE or resource.size < 0xF0:
        raise ValueError("static visual has an unexpected type or size")
    group = reader.load_group(resource.load_group)
    mesh_hash = struct.unpack_from("<I", group.data, resource.offset + 0xEC)[0]
    mesh_resource = reader.resource_by_identity(STATIC_MESH_TYPE, mesh_hash)
    if mesh_resource is None:
        raise ValueError("static visual references a missing static mesh")
    mesh = mesh_cache.get(mesh_hash)
    if mesh is None:
        mesh = decode_static_mesh(reader, mesh_resource)
        mesh_cache[mesh_hash] = mesh
    if material_override is None:
        material_target = group.relocations.pointers.get(resource.offset + 0xE8)
        material_table = (
            reader.resource_by_location(resource.load_group, material_target)
            if material_target is not None
            else None
        )
        if material_table is None or material_table.type_hash != MATERIAL_TABLE_TYPE:
            raise ValueError(
                f"static visual 0x{resource.name_hash:08X} lacks its native material table"
            )
        materials = _material_table(reader, material_table)
    else:
        materials = material_override
    if any(segment.material_slot >= len(materials) for segment in mesh.segments):
        raise ValueError("static mesh segment material slot is out of range")
    return StaticVisual(
        resource=resource,
        mesh=mesh,
        materials=materials,
        source_visual_type=source_visual_type or resource.type_hash,
        destruction_state=destruction_state,
    )


def _default_static_visual(
    reader: BlockReader, resource: ResourceRecord, mesh_cache: dict[int, StaticMesh]
) -> StaticVisual | None:
    """Legacy static-only preview; never flatten a segmented attachment graph."""
    if resource.type_hash == STATIC_VISUAL_TYPE:
        group = reader.load_group(resource.load_group)
        if resource.offset + 0xE8 not in group.relocations.pointers:
            return None
        return _decode_static_visual(reader, resource, mesh_cache)
    if resource.type_hash == SEGMENTED_VISUAL_TYPE:
        raise ValueError(
            "segmented visuals contain bone-attached components, not a default "
            "destruction state; use read_world_scene(..., include_visuals=False) "
            "and actor_visuals.read_actor_visuals(world) to preserve every component"
        )
    return None


def _decode_actor(
    reader: BlockReader, resource: ResourceRecord, mesh_cache: dict[int, StaticMesh],
    *, include_visuals: bool = False,
) -> ActorArchetype:
    group = reader.load_group(resource.load_group)
    raw = group.data[resource.offset : resource.offset + resource.size]
    native_class, asset_name = _semantic_actor_strings(raw)
    lod_resource = None
    direct_visual_resource = None
    for field, target in group.relocations.pointers.items():
        if not resource.offset <= field < resource.offset + resource.size:
            continue
        candidate = reader.resource_by_location(resource.load_group, target)
        if candidate is not None and candidate.type_hash == LOD_TYPE:
            lod_resource = candidate
        elif (
            candidate is not None
            and candidate.type_hash in {STATIC_VISUAL_TYPE, SEGMENTED_VISUAL_TYPE}
            and direct_visual_resource is None
        ):
            direct_visual_resource = candidate
    lods: list[WorldObjectLOD] = []
    # Promote placed buildings and structural/set-dressing props. Dynamic actors
    # remain represented by their native archetypes until their animated layouts
    # are decoded independently.
    decode_visuals = include_visuals and classify_editor_collection(native_class, asset_name) in {
        "buildings",
        "props",
    }
    if decode_visuals and lod_resource is not None and lod_resource.size >= 0xCC:
        child_table = group.relocations.pointers.get(lod_resource.offset + 0xC0)
        child_count = struct.unpack_from("<I", group.data, lod_resource.offset + 0xC8)[0]
        if child_count and child_table is None:
            raise ValueError("actor LOD lacks its relocated visual table")
        for index in range(child_count):
            target = group.relocations.pointers.get(child_table + index * 4)
            child = (
                reader.resource_by_location(resource.load_group, target)
                if target is not None
                else None
            )
            if child is None:
                raise ValueError("actor LOD child does not resolve to a resource")
            visual = _default_static_visual(reader, child, mesh_cache)
            if visual is not None:
                lods.append(WorldObjectLOD(index=index, visual=visual))
    elif decode_visuals and direct_visual_resource is not None:
        visual = _default_static_visual(reader, direct_visual_resource, mesh_cache)
        if visual is not None:
            lods.append(WorldObjectLOD(index=0, visual=visual))
    return ActorArchetype(
        resource=resource,
        native_class=native_class,
        asset_name=asset_name,
        lod_resource=lod_resource,
        lods=tuple(lods),
    )


def read_world_scene(source_dir: str | Path | BlockReader, *, include_visuals: bool = False) -> WorldScene:
    """Read every native placement and archetype without selecting visual parts.

    Use actor_visuals.read_actor_visuals(world) and humans.read_humans(world)
    for complete rigid-component and skin reconstruction. The optional legacy
    include_visuals=True path supports simple static previews only and raises
    when a segmented visual would otherwise lose bone attachments.
    """
    reader = source_dir if isinstance(source_dir, BlockReader) else BlockReader(source_dir)
    database = reader.resource_by_identity(INSTANCE_DATABASE_TYPE, WORLD_DATABASE_NAME)
    if database is None:
        raise ValueError("world.instance-database resource was not found")
    group = reader.load_group(database.load_group)
    data = group.data
    relocations = group.relocations.pointers
    bucket_count = struct.unpack_from("<I", data, database.offset + 4)[0]
    if bucket_count <= 0:
        raise ValueError("world.instance-database has no buckets")

    archetypes: dict[int, ActorArchetype] = {}
    mesh_cache: dict[int, StaticMesh] = {}
    buckets: list[InstanceBucket] = []
    placements: list[WorldPlacement] = []
    for bucket_index in range(bucket_count):
        bucket_offset = relocations.get(database.offset + 8 + bucket_index * 4)
        if bucket_offset is None:
            raise ValueError("instance database bucket pointer lacks a relocation")
        bucket_hash = struct.unpack_from("<I", data, bucket_offset)[0]
        records = instance_list_records(data, relocations, bucket_offset, group.resource_limit)
        bucket_placements: list[WorldPlacement] = []
        for index, (list_field, list_offset, list_index, record_offset) in enumerate(records):
            actor_target = relocations.get(record_offset + 0x3C)
            actor_resource = (
                reader.resource_by_location(database.load_group, actor_target)
                if actor_target is not None
                else None
            )
            actor = None
            if actor_resource is not None:
                if actor_resource.type_hash != ACTOR_ARCHETYPE_TYPE:
                    raise ValueError("instance placement archetype has an unexpected type")
                actor = archetypes.get(actor_resource.name_hash)
                if actor is None:
                    actor = _decode_actor(reader, actor_resource, mesh_cache, include_visuals=include_visuals)
                    archetypes[actor_resource.name_hash] = actor
            position = np.asarray(
                struct.unpack_from("<3f", data, record_offset + 0x10), dtype=np.float32
            )
            packed_rotation = struct.unpack_from("<I", data, record_offset + 0x20)[0]
            packed_rotation_xy = struct.unpack_from("<I", data, record_offset + 0x1C)[0]
            quaternion = decode_packed_quaternion(packed_rotation_xy, packed_rotation)
            scale = np.float32(struct.unpack_from("<f", data, record_offset + 0x24)[0])
            if not np.isfinite(position).all() or not np.isfinite(scale):
                raise ValueError("world placement has non-finite position or scale")
            placement = WorldPlacement(
                bucket_index=bucket_index,
                bucket_hash=bucket_hash,
                index=index,
                offset=record_offset,
                raw_header=data[record_offset : record_offset + 0x40],
                actor=actor,
                position=position,
                packed_rotation=packed_rotation,
                quaternion_xyzw=quaternion,
                scale=scale,
                matrix=instance_matrix(quaternion, position, scale),
                list_pointer_field=list_field,
                list_offset=list_offset,
                list_index=list_index,
                packed_rotation_xy=packed_rotation_xy,
            )
            bucket_placements.append(placement)
            placements.append(placement)
        buckets.append(
            InstanceBucket(
                index=bucket_index,
                offset=bucket_offset,
                type_hash=bucket_hash,
                pointer_table_offset=records[0][1] if records else 0,
                placements=tuple(bucket_placements),
                list_offsets=tuple(dict.fromkeys(record[1] for record in records)),
            )
        )
    return WorldScene(
        reader=reader,
        database=database,
        buckets=tuple(buckets),
        placements=tuple(placements),
        archetypes=archetypes,
    )
