from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .container import BlockReader, LoadedGroup, ResourceRecord
from .transforms import instance_matrix, transform_points


INSTANCE_TYPE = 0xE3554C44
LOD_TYPE = 0x6FC132F2
TERRAIN_WRAPPER_TYPE = 0xB31C6654
TERRAIN_GEOMETRY_TYPE = 0x5EE233E1


@dataclass(frozen=True)
class TerrainSegment:
    index: int
    vertex_start: int
    vertex_count: int
    index_start: int
    index_count: int
    raw_flags: int
    triangle_count: int
    triangles: NDArray[np.int32]

    @property
    def raw_record(self) -> tuple[int, int, int, int, int, int]:
        return (
            self.vertex_start,
            self.vertex_count,
            self.index_start,
            self.index_count,
            self.raw_flags,
            self.triangle_count,
        )


@dataclass(frozen=True)
class TerrainGeometry:
    resource: ResourceRecord
    vertex_buffer_offset: int
    index_buffer_offset: int
    segment_buffer_offset: int
    vertex_bytes: NDArray[np.uint8]
    local_positions: NDArray[np.float32]
    indices: NDArray[np.uint16]
    triangles: NDArray[np.int32]
    segments: tuple[TerrainSegment, ...]


@dataclass(frozen=True)
class TerrainLOD:
    index: int
    wrapper: ResourceRecord
    geometry: TerrainGeometry


@dataclass(frozen=True)
class TerrainPlacement:
    offset: int
    name_hash: int
    lod_resource: ResourceRecord
    child_array_offset: int
    position: NDArray[np.float32]
    quaternion_xyzw: NDArray[np.float32]
    scale: np.float32
    flags: int
    matrix: NDArray[np.float32]
    lod_parameters: tuple[float, ...]
    children: tuple[TerrainLOD, ...]
    parent_offset: int | None
    next_sibling_offset: int | None


@dataclass(frozen=True)
class TerrainScene:
    reader: BlockReader
    resident: LoadedGroup
    geometries: dict[int, TerrainGeometry]
    placements: tuple[TerrainPlacement, ...]
    scene_chain: tuple[int, ...]


def _strip_triangles(strip: NDArray[np.uint16]) -> NDArray[np.int32]:
    faces: list[tuple[int, int, int]] = []
    for index in range(len(strip) - 2):
        a, b, c = (int(value) for value in strip[index : index + 3])
        triangle = (b, a, c) if index & 1 else (a, b, c)
        if len(set(triangle)) == 3:
            faces.append(triangle)
    return np.asarray(faces, dtype=np.int32).reshape((-1, 3))


def decode_terrain_geometry(
    reader: BlockReader, resource: ResourceRecord
) -> TerrainGeometry:
    group = reader.load_group(resource.load_group)
    data = group.data
    relocations = group.relocations.pointers
    base = resource.offset

    pointer_fields = (base, base + 4, base + 20)
    missing = [field for field in pointer_fields if field not in relocations]
    if missing:
        raise ValueError(
            f"terrain geometry 0x{resource.name_hash:08X} lacks relocation fields "
            + ", ".join(f"0x{field:X}" for field in missing)
        )

    vertex_buffer = relocations[base]
    index_buffer = relocations[base + 4]
    segment_buffer = relocations[base + 20]
    vertex_count, index_count, segment_count = struct.unpack_from("<3I", data, base + 8)

    expected_index_buffer = (base + 24 + 127) & ~127
    expected_vertex_buffer = (index_buffer + index_count * 2 + 127) & ~127
    if index_buffer != expected_index_buffer:
        raise ValueError("terrain index buffer does not match the verified layout")
    if vertex_buffer != expected_vertex_buffer:
        raise ValueError("terrain vertex buffer does not match the verified layout")
    if segment_buffer != vertex_buffer + vertex_count * 24:
        raise ValueError("terrain segment table is not directly after stride-24 vertices")
    if vertex_buffer + vertex_count * 24 > group.resource_limit:
        raise ValueError("terrain vertex buffer extends beyond load-group resource data")
    if index_buffer + index_count * 2 > group.resource_limit:
        raise ValueError("terrain index buffer extends beyond load-group resource data")
    if segment_buffer + segment_count * 12 > group.resource_limit:
        raise ValueError("terrain segment table extends beyond load-group resource data")

    vertex_bytes = np.frombuffer(
        data, dtype=np.uint8, count=vertex_count * 24, offset=vertex_buffer
    ).reshape((vertex_count, 24)).copy()
    local_positions = np.ndarray(
        (vertex_count, 3),
        dtype="<f4",
        buffer=data,
        offset=vertex_buffer,
        strides=(24, 4),
    ).copy()
    indices = np.frombuffer(
        data, dtype="<u2", count=index_count, offset=index_buffer
    ).copy()
    if not np.isfinite(local_positions).all():
        raise ValueError("terrain position channels contain non-finite values")
    if vertex_count and indices.size and int(indices.max()) >= vertex_count:
        raise ValueError("terrain index references a vertex outside the buffer")

    segments: list[TerrainSegment] = []
    all_faces: list[NDArray[np.int32]] = []
    cursor = 0
    for index in range(segment_count):
        record = struct.unpack_from("<6H", data, segment_buffer + index * 12)
        vertex_start, vertices, index_start, count, raw_flags, triangles = record
        if index_start != cursor:
            raise ValueError("terrain segments do not cover the index buffer contiguously")
        if count < 3 or index_start + count > index_count:
            raise ValueError("terrain segment index range is invalid")
        strip = indices[index_start : index_start + count]
        if int(strip.min()) < vertex_start or int(strip.max()) >= vertex_start + vertices:
            raise ValueError("terrain segment references vertices outside its declared range")
        faces = _strip_triangles(strip)
        if len(faces) != triangles:
            raise ValueError(
                f"terrain segment triangle count mismatch: decoded={len(faces)} "
                f"declared={triangles}"
            )
        segments.append(
            TerrainSegment(
                index=index,
                vertex_start=vertex_start,
                vertex_count=vertices,
                index_start=index_start,
                index_count=count,
                raw_flags=raw_flags,
                triangle_count=triangles,
                triangles=faces,
            )
        )
        all_faces.append(faces)
        cursor += count

    if cursor != index_count:
        raise ValueError("terrain segments do not consume the full index buffer")
    triangles = (
        np.concatenate(all_faces).astype(np.int32, copy=False)
        if all_faces
        else np.empty((0, 3), dtype=np.int32)
    )

    return TerrainGeometry(
        resource=resource,
        vertex_buffer_offset=vertex_buffer,
        index_buffer_offset=index_buffer,
        segment_buffer_offset=segment_buffer,
        vertex_bytes=vertex_bytes,
        local_positions=local_positions,
        indices=indices,
        triangles=triangles,
        segments=tuple(segments),
    )


def _scene_sibling_chain(data: bytes, relocations: dict[int, int], member: int) -> tuple[int, ...]:
    marker = struct.pack("<I", INSTANCE_TYPE)
    predecessors: dict[int, int] = {}
    for pointer_field, target in relocations.items():
        if pointer_field >= 16 and data[pointer_field - 16 : pointer_field - 12] == marker:
            source_record = pointer_field - 16
            if target in predecessors:
                raise ValueError(f"scene record 0x{target:X} has multiple predecessors")
            predecessors[target] = source_record

    head = member
    seen_backwards: set[int] = set()
    while head in predecessors:
        if head in seen_backwards:
            raise ValueError("cycle while locating scene sibling-chain head")
        seen_backwards.add(head)
        head = predecessors[head]

    chain: list[int] = []
    node = head
    while True:
        if node in chain:
            raise ValueError("cycle in scene sibling chain")
        if data[node : node + 4] != marker:
            raise ValueError(f"scene sibling 0x{node:X} has an unexpected type")
        chain.append(node)
        pointer_field = node + 16
        if pointer_field not in relocations:
            break
        node = relocations[pointer_field]
    return tuple(chain)


def read_terrain_scene(block_dir: str | Path) -> TerrainScene:
    reader = BlockReader(block_dir)
    resident = reader.load_group(0)
    data = resident.data
    relocations = resident.relocations.pointers

    geometries = {
        resource.name_hash: decode_terrain_geometry(reader, resource)
        for resource in reader.resources_of_type(TERRAIN_GEOMETRY_TYPE)
    }
    if not geometries:
        raise ValueError("block contains no terrain geometry resources")

    placements: list[TerrainPlacement] = []
    marker = struct.pack("<I", INSTANCE_TYPE)
    cursor = 0
    while True:
        offset = data.find(marker, cursor)
        if offset < 0:
            break
        cursor = offset + 4
        if offset % 4 or offset + 64 > resident.resource_limit:
            continue
        lod_pointer_field = offset + 20
        if lod_pointer_field not in relocations:
            continue
        lod_offset = relocations[lod_pointer_field]
        lod_resource = reader.resource_by_location(0, lod_offset)
        if lod_resource is None or lod_resource.type_hash != LOD_TYPE:
            continue
        child_array_field = lod_offset + 192
        if child_array_field not in relocations:
            continue
        child_count = struct.unpack_from("<I", data, lod_offset + 200)[0]
        child_array = relocations[child_array_field]
        child_offsets: list[int] = []
        wrappers: list[ResourceRecord] = []
        valid = True
        for index in range(child_count):
            pointer_field = child_array + index * 4
            if pointer_field not in relocations:
                valid = False
                break
            child_offset = relocations[pointer_field]
            wrapper = reader.resource_by_location(0, child_offset)
            if wrapper is None or wrapper.type_hash != TERRAIN_WRAPPER_TYPE:
                valid = False
                break
            child_offsets.append(child_offset)
            wrappers.append(wrapper)
        if not valid or not wrappers:
            continue

        position = np.array(struct.unpack_from("<3f", data, offset + 28), dtype=np.float32)
        quaternion = np.array(
            struct.unpack_from("<4f", data, offset + 40), dtype=np.float32
        )
        scale, flags = struct.unpack_from("<fI", data, offset + 56)
        matrix = instance_matrix(quaternion, position, np.float32(scale))

        parent_offset = relocations.get(offset + 8)
        if parent_offset is None and data[offset + 8 : offset + 12] != bytes(4):
            raise ValueError(f"instance 0x{offset:X} has a non-zero unrelocated parent")
        next_sibling = relocations.get(offset + 16)

        children: list[TerrainLOD] = []
        for lod_index, wrapper in enumerate(wrappers):
            geometry_hash = struct.unpack_from("<I", data, wrapper.offset + 196)[0]
            geometry = geometries.get(geometry_hash)
            if geometry is None:
                raise ValueError(
                    f"wrapper 0x{wrapper.name_hash:08X} references missing terrain "
                    f"geometry 0x{geometry_hash:08X}"
                )
            children.append(
                TerrainLOD(index=lod_index, wrapper=wrapper, geometry=geometry)
            )

        placements.append(
            TerrainPlacement(
                offset=offset,
                name_hash=struct.unpack_from("<I", data, offset + 4)[0],
                lod_resource=lod_resource,
                child_array_offset=child_array,
                position=position,
                quaternion_xyzw=quaternion,
                scale=np.float32(scale),
                flags=flags,
                matrix=matrix,
                lod_parameters=tuple(
                    float(value)
                    for value in struct.unpack_from("<6f", data, lod_offset + 204)
                ),
                children=tuple(children),
                parent_offset=parent_offset,
                next_sibling_offset=next_sibling,
            )
        )

    if not placements:
        raise ValueError("no typed terrain placement -> LOD -> wrapper chains were found")

    chain = _scene_sibling_chain(data, relocations, placements[0].offset)
    placement_offsets = {placement.offset for placement in placements}
    if not placement_offsets.issubset(set(chain)):
        raise ValueError("not all terrain placements belong to the discovered scene chain")

    referenced_geometry = {
        child.geometry.resource.name_hash
        for placement in placements
        for child in placement.children
    }
    if referenced_geometry != set(geometries):
        raise ValueError(
            "terrain placement LOD children do not account for every terrain geometry resource"
        )

    return TerrainScene(
        reader=reader,
        resident=resident,
        geometries=geometries,
        placements=tuple(placements),
        scene_chain=chain,
    )


def build_reference_arrays(scene: TerrainScene) -> dict[str, NDArray[np.generic]]:
    child_counts = {len(placement.children) for placement in scene.placements}
    if child_counts != {2}:
        raise ValueError(f"Area 42 validation requires two LODs per placement, got {child_counts}")

    arrays: dict[str, NDArray[np.generic]] = {}
    for lod_index in (0, 1):
        world_vertices: list[NDArray[np.float32]] = []
        triangles: list[NDArray[np.int32]] = []
        vertex_base = 0
        vertex_offsets = [0]
        face_offsets = [0]
        for placement_index, placement in enumerate(scene.placements):
            geometry = placement.children[lod_index].geometry
            local = geometry.local_positions.copy()
            world = transform_points(local, placement.matrix)
            arrays[f"lod{lod_index}_tile{placement_index:03d}_local_xyz"] = local
            world_vertices.append(world)
            triangles.append(geometry.triangles + vertex_base)
            vertex_base += len(local)
            vertex_offsets.append(vertex_base)
            face_offsets.append(face_offsets[-1] + len(geometry.triangles))

        arrays[f"lod{lod_index}_world_xyz_f32"] = np.concatenate(world_vertices)
        arrays[f"lod{lod_index}_triangles_i32"] = np.concatenate(triangles)
        arrays[f"lod{lod_index}_vertex_offsets"] = np.array(vertex_offsets)
        arrays[f"lod{lod_index}_face_offsets"] = np.array(face_offsets)

    arrays["matrices_row_major_f32"] = np.array(
        [placement.matrix for placement in scene.placements], dtype=np.float32
    )
    return arrays


def build_native_buffer_arrays(scene: TerrainScene) -> dict[str, NDArray[np.generic]]:
    arrays: dict[str, NDArray[np.generic]] = {}
    arrays["matrices_row_major_f32"] = np.array(
        [placement.matrix for placement in scene.placements], dtype=np.float32
    )
    for placement_index, placement in enumerate(scene.placements):
        for child in placement.children:
            prefix = f"lod{child.index}_tile{placement_index:03d}"
            geometry = child.geometry
            arrays[f"{prefix}_vertex_bytes_u8"] = geometry.vertex_bytes
            arrays[f"{prefix}_local_positions_f32"] = geometry.local_positions
            arrays[f"{prefix}_index_strip_u16"] = geometry.indices
            arrays[f"{prefix}_segment_records_u16"] = np.asarray(
                [segment.raw_record for segment in geometry.segments], dtype=np.uint16
            )
    return arrays


def _array_sha256(array: NDArray[np.generic]) -> str:
    return hashlib.sha256(array.tobytes()).hexdigest()


def compare_reference_arrays(
    actual: dict[str, NDArray[np.generic]], reference_path: str | Path
) -> dict[str, object]:
    reference_file = Path(reference_path)
    with np.load(reference_file) as expected:
        expected_names = set(expected.files)
        actual_names = set(actual)
        comparisons: list[dict[str, object]] = []
        for name in sorted(expected_names | actual_names):
            wanted = expected[name] if name in expected_names else None
            got = actual.get(name)
            same_shape = wanted is not None and got is not None and wanted.shape == got.shape
            same_dtype = wanted is not None and got is not None and wanted.dtype == got.dtype
            byte_equal = (
                same_shape
                and same_dtype
                and wanted is not None
                and got is not None
                and wanted.tobytes() == got.tobytes()
            )
            max_abs_error: float | None = None
            if same_shape and wanted is not None and got is not None and wanted.size:
                max_abs_error = float(
                    np.max(
                        np.abs(
                            wanted.astype(np.float64, copy=False)
                            - got.astype(np.float64, copy=False)
                        )
                    )
                )
            comparisons.append(
                {
                    "name": name,
                    "expected_present": wanted is not None,
                    "actual_present": got is not None,
                    "shape_equal": same_shape,
                    "dtype_equal": same_dtype,
                    "byte_equal": byte_equal,
                    "expected_sha256": _array_sha256(wanted) if wanted is not None else None,
                    "actual_sha256": _array_sha256(got) if got is not None else None,
                    "max_abs_error": max_abs_error,
                }
            )

    return {
        "reference": str(reference_file.resolve()),
        "array_count": len(comparisons),
        "all_arrays_byte_equal": all(item["byte_equal"] for item in comparisons),
        "arrays": comparisons,
    }


def terrain_report(
    scene: TerrainScene,
    comparison: dict[str, object],
    source_hashes_before: dict[str, str],
    source_hashes_after: dict[str, str],
) -> dict[str, object]:
    lod_stats: list[dict[str, int]] = []
    for lod_index in (0, 1):
        lod_stats.append(
            {
                "lod": lod_index,
                "vertices": sum(
                    len(placement.children[lod_index].geometry.local_positions)
                    for placement in scene.placements
                ),
                "triangles": sum(
                    len(placement.children[lod_index].geometry.triangles)
                    for placement in scene.placements
                ),
                "segments": sum(
                    len(placement.children[lod_index].geometry.segments)
                    for placement in scene.placements
                ),
            }
        )

    return {
        "status": "verified" if comparison["all_arrays_byte_equal"] else "mismatch",
        "structural_validation": {
            "terrain_placements": len(scene.placements),
            "geometry_buffers": len(scene.geometries),
            "lod_children_per_placement": sorted(
                {len(placement.children) for placement in scene.placements}
            ),
            "scene_sibling_records": len(scene.scene_chain),
            "all_parent_pointers_null": all(
                placement.parent_offset is None for placement in scene.placements
            ),
            "all_geometry_resources_accounted_for": {
                child.geometry.resource.name_hash
                for placement in scene.placements
                for child in placement.children
            }
            == set(scene.geometries),
            "lod_stats": lod_stats,
        },
        "reference_comparison": comparison,
        "source_sha256_before": source_hashes_before,
        "source_sha256_after": source_hashes_after,
        "source_unchanged": source_hashes_before == source_hashes_after,
        "retail_transform_evidence": {
            "quaternion_order": "xyzw; no normalization",
            "matrix_layout": "row vector; translation row 3",
            "quaternion_routine_va": "0xD58C0",
            "instance_matrix_routine_va": "0xF2850",
            "point_helper_va": "0xD68C0",
        },
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
        "remaining_unknowns": [
            "Vertex bytes +0x0C..+0x13 are exported only as a UV candidate pending executable confirmation and retail visual comparison.",
            "Vertex bytes +0x14..+0x17 and the high byte of segment raw_flags are preserved without guessed semantics.",
            "No collision, scenery, mission, or destruction data is decoded by this milestone.",
        ],
    }


def write_validation_outputs(
    scene: TerrainScene,
    reference_path: str | Path,
    output_dir: str | Path,
    source_hashes_before: dict[str, str],
) -> dict[str, object]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    reference_arrays = build_reference_arrays(scene)
    comparison = compare_reference_arrays(reference_arrays, reference_path)
    source_hashes_after = scene.reader.source_hashes()
    report = terrain_report(
        scene, comparison, source_hashes_before, source_hashes_after
    )

    np.savez_compressed(
        destination / "area42_reference_reproduction.npz", **reference_arrays
    )
    np.savez_compressed(
        destination / "area42_native_terrain_buffers.npz",
        **build_native_buffer_arrays(scene),
    )
    (destination / "area42_terrain_validation.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report

