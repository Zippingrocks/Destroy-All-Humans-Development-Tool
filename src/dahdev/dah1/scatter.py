"""Read the DAH1 US Xbox static scatter renderer's native resources.

The directory type 0xA3C936AF contains the 0x43BBDAB4 renderer payload. Its
eight-way draw buffers are templates, not eight additional world instances.
See docs/area42-scatter.md for the retail executable evidence and limitations.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .container import BlockReader, ResourceRecord
from .executable_evidence import DAH1_RETAIL_XBOX_US_SHA256, XbeImage
from .materials import MATERIAL_TYPE, NativeMaterial, decode_material
from .terrain import INSTANCE_TYPE, TerrainScene, _scene_sibling_chain, _strip_triangles
from .transforms import instance_matrix


SCATTER_TYPE = 0xA3C936AF
SCATTER_PAYLOAD_TYPE = 0x43BBDAB4
SCATTER_VERTEX_STRIDE = 24
SCATTER_INSTANCE_STRIDE = 80
SCATTER_DRAW_CAPACITY = 8


@dataclass(frozen=True)
class ScatterTemplate:
    load_group: int
    vertex_buffer_offset: int
    index_buffer_offset: int
    vertex_bytes: NDArray[np.uint8]
    local_positions: NDArray[np.float32]
    local_uvs: NDArray[np.float32]
    indices: NDArray[np.uint16]
    triangles: NDArray[np.int32]
    native_draw_vertex_bytes: bytes
    native_draw_index_bytes: bytes


@dataclass(frozen=True)
class ScatterInstance:
    index: int
    offset: int
    raw_bytes: bytes
    raw_matrix: NDArray[np.float32]
    matrix: NDArray[np.float32]
    color_rgb: NDArray[np.float32]
    unknown_word: int


@dataclass(frozen=True)
class ScatterBatch:
    resource: ResourceRecord
    source_name_hash: int
    raw_bytes: bytes
    material_pointer_field: int
    instance_buffer_offset: int
    fade_distances: tuple[float, float]
    unknown_tail_f32: tuple[float, float, float, float]
    material: NativeMaterial
    template: ScatterTemplate
    instances: tuple[ScatterInstance, ...]


@dataclass(frozen=True)
class ScatterPlacement:
    offset: int
    name_hash: int
    raw_header: bytes
    parent_offset: int | None
    next_sibling_offset: int | None
    position: NDArray[np.float32]
    quaternion_xyzw: NDArray[np.float32]
    scale: np.float32
    flags: int
    matrix: NDArray[np.float32]
    batch: ScatterBatch

    def world_matrix(self, instance: ScatterInstance) -> NDArray[np.float32]:
        """Compose row-vector local instance -> batch scene placement."""
        return np.asarray(instance.matrix @ self.matrix, dtype=np.float32)


@dataclass(frozen=True)
class ScatterScene:
    reader: BlockReader
    templates: tuple[ScatterTemplate, ...]
    batches: tuple[ScatterBatch, ...]
    placements: tuple[ScatterPlacement, ...]

    @property
    def instance_count(self) -> int:
        return sum(len(placement.batch.instances) for placement in self.placements)


def material_render_state(material: NativeMaterial) -> dict[str, object]:
    """Decode the supported base material path; preserve exceptional flags.

    0x4000 disables culling. Alpha behavior comes from binding+0x10 and
    material+0x68, not from the BC texture format or the double-sided flag.
    """
    binding_mode = (
        struct.unpack_from("<I", material.binding.raw_bytes, 0x10)[0]
        if material.binding is not None else None
    )
    opacity = struct.unpack_from("<f", material.raw_bytes, 0x68)[0]
    if not np.isfinite(opacity):
        raise ValueError("material opacity is not finite")
    flags = material.render_flags
    unresolved: list[str] = []
    if flags & 0x80F00:
        unresolved.append("special material blend/shadow flags require their own render path")
    if binding_mode not in {None, 0, 1, 2, 3}:
        unresolved.append(f"unknown texture alpha mode {binding_mode}")
    blend = (binding_mode == 3 and not flags & 0x40000) or (
        opacity < 1.0 and binding_mode != 2
    )
    mode = "blend" if blend else "mask" if binding_mode == 2 else "opaque"
    # Masked textures select their own alpha reference in 0xE5D65. The
    # ordinary blend pass selects reference 1 in 0xEABE3; opaque uses 64.
    reference = (
        max(0, min(255, 256 - int(np.float32(opacity) * np.float32(255))))
        if binding_mode == 2 else 1 if blend else 64
    )
    return {
        "alpha_mode": mode,
        "alpha_reference_u8": reference,
        "alpha_compare": "greater",
        "blend_source": "src_alpha" if blend else "one",
        "blend_destination": "one_minus_src_alpha" if blend else "zero",
        "blend_equation": "add",
        "depth_write": not blend,
        "double_sided": bool(flags & 0x4000),
        "native_texture_alpha_mode": binding_mode,
        "native_opacity": opacity,
        "evidence_status": "unresolved-special-path" if unresolved else "retail-code-verified",
        "evidence_addresses": ["0xE5C00", "0xE5D65", "0xE6224", "0xE62E0", "0xE6310", "0xE9FAD", "0xEA010", "0xEAAE0", "0xEAF19", "0xEB438"],
        "unresolved": unresolved,
    }


def _require_range(offset: int, size: int, limit: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > limit:
        raise ValueError(f"scatter {label} is outside load-group resource data")


def decode_scatter_template(reader: BlockReader, resource: ResourceRecord) -> ScatterTemplate:
    group = reader.load_group(resource.load_group)
    data, relocations = group.data, group.relocations.pointers
    vertex_count, index_count = struct.unpack_from("<HH", data, resource.offset + 0xC8)
    if vertex_count < 3 or index_count < 3:
        raise ValueError("scatter template has insufficient vertices or indices")
    vertices = relocations.get(resource.offset + 0xCC)
    indices = relocations.get(resource.offset + 0xD0)
    if vertices is None or indices is None:
        raise ValueError("scatter template lacks relocated vertex/index buffers")
    native_vertex_size = vertex_count * SCATTER_VERTEX_STRIDE * SCATTER_DRAW_CAPACITY
    native_index_count = (index_count + 2) * SCATTER_DRAW_CAPACITY - 2
    _require_range(vertices, native_vertex_size, group.resource_limit, "draw vertex buffer")
    _require_range(indices, native_index_count * 2, group.resource_limit, "draw index buffer")
    native_vertices = data[vertices : vertices + native_vertex_size]
    native_indices = data[indices : indices + native_index_count * 2]
    vertex_values = np.frombuffer(native_vertices, dtype="<f4").reshape(
        SCATTER_DRAW_CAPACITY, vertex_count, 6
    )
    if not np.isfinite(vertex_values).all():
        raise ValueError("scatter template vertices contain non-finite values")
    # Retail submits eight matrix/color constant sets per draw. Each copy
    # preserves position/UV and selects the next group of four constants.
    for draw in range(SCATTER_DRAW_CAPACITY):
        if not np.array_equal(vertex_values[draw, :, :5], vertex_values[0, :, :5]):
            raise ValueError("scatter repeated draw vertices do not match their template")
        if not np.all(vertex_values[draw, :, 5] == np.float32(draw * 4)):
            raise ValueError("scatter vertex constant selector is not four times its draw slot")
    index_values = np.frombuffer(native_indices, dtype="<u2")
    template_indices = index_values[:index_count].copy()
    if int(template_indices.max()) >= vertex_count:
        raise ValueError("scatter template index references a vertex outside its buffer")
    expected: list[int] = []
    for draw in range(SCATTER_DRAW_CAPACITY):
        shifted = [int(value) + draw * vertex_count for value in template_indices]
        if expected:
            expected.extend((expected[-1], shifted[0]))
        expected.extend(shifted)
    if not np.array_equal(index_values, np.asarray(expected, dtype=np.uint16)):
        raise ValueError("scatter native draw strip does not replicate its template with degenerate joins")
    template_bytes = np.frombuffer(
        native_vertices[: vertex_count * SCATTER_VERTEX_STRIDE], dtype=np.uint8
    ).reshape(vertex_count, SCATTER_VERTEX_STRIDE).copy()
    return ScatterTemplate(
        load_group=resource.load_group,
        vertex_buffer_offset=vertices,
        index_buffer_offset=indices,
        vertex_bytes=template_bytes,
        local_positions=vertex_values[0, :, :3].copy(),
        local_uvs=vertex_values[0, :, 3:5].copy(),
        indices=template_indices,
        triangles=_strip_triangles(template_indices),
        native_draw_vertex_bytes=native_vertices,
        native_draw_index_bytes=native_indices,
    )


def decode_scatter_batch(
    reader: BlockReader,
    resource: ResourceRecord,
    template_cache: dict[tuple[int, int, int, int], ScatterTemplate] | None = None,
) -> ScatterBatch:
    if resource.type_hash != SCATTER_TYPE or resource.size != 0xE4:
        raise ValueError("scatter descriptor has an unexpected type or size")
    group = reader.load_group(resource.load_group)
    data, relocations = group.data, group.relocations.pointers
    _require_range(resource.offset, resource.size, group.resource_limit, "descriptor")
    raw = data[resource.offset : resource.offset + resource.size]
    payload_type, source_name = struct.unpack_from("<II", raw)
    if payload_type != SCATTER_PAYLOAD_TYPE:
        raise ValueError("scatter descriptor has an unexpected payload renderer type")
    material_field = resource.offset + 0xB4
    material_target = relocations.get(material_field)
    material_resource = (
        reader.resource_by_location(resource.load_group, material_target)
        if material_target is not None else None
    )
    if material_resource is None or material_resource.type_hash != MATERIAL_TYPE:
        raise ValueError("scatter material pointer does not resolve to a material")
    count = struct.unpack_from("<I", raw, 0xC0)[0]
    instance_buffer = relocations.get(resource.offset + 0xC4)
    if instance_buffer is None:
        raise ValueError("scatter instance table lacks a relocation")
    _require_range(instance_buffer, count * SCATTER_INSTANCE_STRIDE, group.resource_limit, "instance table")
    instances: list[ScatterInstance] = []
    for index in range(count):
        offset = instance_buffer + index * SCATTER_INSTANCE_STRIDE
        record = data[offset : offset + SCATTER_INSTANCE_STRIDE]
        raw_matrix = np.frombuffer(record, dtype="<f4", count=16).reshape(4, 4).copy()
        rgb = np.frombuffer(record, dtype="<f4", count=3, offset=0x40).copy()
        if not np.isfinite(raw_matrix).all() or not np.isfinite(rgb).all():
            raise ValueError("scatter instance matrix or RGB contains non-finite values")
        if not np.array_equal(raw_matrix[3], np.asarray((0, 0, 0, 1), dtype=np.float32)):
            raise ValueError("scatter instance matrix is not a column-vector affine transform")
        if np.linalg.det(raw_matrix[:3, :3]) == 0:
            raise ValueError("scatter instance matrix is singular")
        instances.append(ScatterInstance(
            index=index,
            offset=offset,
            raw_bytes=record,
            raw_matrix=raw_matrix,
            matrix=raw_matrix.T.copy(),
            color_rgb=rgb,
            unknown_word=struct.unpack_from("<I", record, 0x4C)[0],
        ))
    vertex_target = relocations.get(resource.offset + 0xCC)
    index_target = relocations.get(resource.offset + 0xD0)
    if vertex_target is None or index_target is None:
        raise ValueError("scatter template lacks relocated vertex/index buffers")
    key = (resource.load_group, vertex_target, index_target, struct.unpack_from("<I", raw, 0xC8)[0])
    template = template_cache.get(key) if template_cache is not None else None
    if template is None:
        template = decode_scatter_template(reader, resource)
        if template_cache is not None:
            template_cache[key] = template
    distances = struct.unpack_from("<2f", raw, 0xB8)
    parameters = struct.unpack_from("<4f", raw, 0xD4)
    if not np.isfinite(distances).all() or not np.isfinite(parameters).all():
        raise ValueError("scatter distance or trailing parameters contain non-finite values")
    return ScatterBatch(
        resource=resource,
        source_name_hash=source_name,
        raw_bytes=raw,
        material_pointer_field=material_field,
        instance_buffer_offset=instance_buffer,
        fade_distances=distances,
        unknown_tail_f32=parameters,
        material=decode_material(reader, material_resource),
        template=template,
        instances=tuple(instances),
    )


def read_scatter_scene(source: str | Path | BlockReader | TerrainScene) -> ScatterScene:
    reader = source.reader if isinstance(source, TerrainScene) else (
        source if isinstance(source, BlockReader) else BlockReader(source)
    )
    template_cache: dict[tuple[int, int, int, int], ScatterTemplate] = {}
    batches = tuple(
        decode_scatter_batch(reader, resource, template_cache)
        for resource in reader.resources_of_type(SCATTER_TYPE)
    )
    by_location = {(batch.resource.load_group, batch.resource.offset): batch for batch in batches}
    if any(batch.resource.load_group != 0 for batch in batches):
        raise ValueError("scatter placement reader currently requires the verified resident scene")
    if not batches:
        return ScatterScene(reader, (), (), ())
    resident = reader.load_group(0)
    data, relocations = resident.data, resident.relocations.pointers
    candidates = []
    for field, target in relocations.items():
        offset = field - 0x14
        if (0, target) not in by_location or offset < 0 or offset + 64 > resident.resource_limit:
            continue
        if struct.unpack_from("<I", data, offset)[0] == INSTANCE_TYPE:
            candidates.append(offset)
    if not candidates:
        raise ValueError("scatter resources have no typed scene placements")
    chain = source.scene_chain if isinstance(source, TerrainScene) else _scene_sibling_chain(
        data, relocations, candidates[0]
    )
    if not set(candidates).issubset(chain):
        raise ValueError("scatter placements do not belong to the same resident scene chain")
    placements: list[ScatterPlacement] = []
    referenced: set[tuple[int, int]] = set()
    for offset in chain:
        target = relocations.get(offset + 0x14)
        batch = by_location.get((0, target))
        if batch is None:
            continue
        raw = data[offset : offset + 64]
        parent = relocations.get(offset + 8)
        if parent is not None or raw[8:12] != bytes(4):
            raise ValueError("scatter parent hierarchy has not been verified for this build")
        position = np.asarray(struct.unpack_from("<3f", raw, 0x1C), dtype=np.float32)
        quaternion = np.asarray(struct.unpack_from("<4f", raw, 0x28), dtype=np.float32)
        scale, flags = struct.unpack_from("<fI", raw, 0x38)
        if not np.isfinite(position).all() or not np.isfinite(quaternion).all() or not np.isfinite(scale):
            raise ValueError("scatter scene transform contains non-finite values")
        placements.append(ScatterPlacement(
            offset=offset,
            name_hash=struct.unpack_from("<I", raw, 4)[0],
            raw_header=raw,
            parent_offset=parent,
            next_sibling_offset=relocations.get(offset + 0x10),
            position=position,
            quaternion_xyzw=quaternion,
            scale=np.float32(scale),
            flags=flags,
            matrix=instance_matrix(quaternion, position, np.float32(scale)),
            batch=batch,
        ))
        referenced.add((0, target))
    if referenced != set(by_location):
        raise ValueError("resident scene does not account for every native scatter resource")
    return ScatterScene(reader, tuple(template_cache.values()), batches, tuple(placements))


def scatter_report(scene: ScatterScene) -> dict[str, object]:
    materials = {batch.material.resource.name_hash: batch.material for batch in scene.batches}
    templates = []
    for template in scene.templates:
        members = [batch for batch in scene.batches if batch.template is template]
        templates.append({
            "load_group": template.load_group,
            "vertex_buffer_offset": f"0x{template.vertex_buffer_offset:X}",
            "index_buffer_offset": f"0x{template.index_buffer_offset:X}",
            "vertices": len(template.local_positions),
            "strip_indices": len(template.indices),
            "triangles": len(template.triangles),
            "batches": len(members),
            "instances": sum(len(batch.instances) for batch in members),
            "vertex_sha256": hashlib.sha256(template.vertex_bytes.tobytes()).hexdigest(),
            "index_sha256": hashlib.sha256(template.indices.tobytes()).hexdigest(),
            "native_draw_vertices_sha256": hashlib.sha256(template.native_draw_vertex_bytes).hexdigest(),
            "native_draw_indices_sha256": hashlib.sha256(template.native_draw_index_bytes).hexdigest(),
            "source_name_hashes": sorted({f"0x{batch.source_name_hash:08X}" for batch in members}),
            "material_hashes": sorted({f"0x{batch.material.resource.name_hash:08X}" for batch in members}),
            "texture_hashes": sorted({f"0x{batch.material.binding.texture_hash:08X}" for batch in members if batch.material.binding}),
        })
    return {
        "status": "structurally-decoded-read-only",
        "resource_type": f"0x{SCATTER_TYPE:08X}",
        "payload_type": f"0x{SCATTER_PAYLOAD_TYPE:08X}",
        "batch_resources": len(scene.batches),
        "scene_placements": len(scene.placements),
        "instances": scene.instance_count,
        "templates": templates,
        "materials": [{"name_hash": f"0x{key:08X}", **material_render_state(value)} for key, value in sorted(materials.items())],
        "structural_checks": [
            "every scatter resource is reached from the resident scene sibling chain",
            "all material pointers resolve to typed native materials and textures",
            "all eight native draw copies match the template position and UV channels",
            "native vertex selector equals four times its draw slot",
            "full native index buffer matches shifted template strips and degenerate joins",
            "all instance matrices are finite affine transforms; RGB and raw trailing words retained",
        ],
        "unresolved": [
            "original authoring names for the five source hashes have not been recovered",
            "the final instance dword at +0x4C is preserved without semantic interpretation",
            "runtime wind deformation and camera-distance fade are retained as parameters but not simulated",
            "the resource tail at +0xD4..+0xE3 is preserved; its semantics are not assigned",
            "this is native static placement reconstruction; no unchanged-package round trip or mutation is provided",
        ],
    }


def verify_scatter_executable_evidence(path: str | Path) -> dict[str, object]:
    payload = Path(path).read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != DAH1_RETAIL_XBOX_US_SHA256:
        raise ValueError("scatter executable evidence requires the pinned DAH1 US Xbox retail build")
    image = XbeImage(payload)
    checks = {
        "load_instance_count_and_pointer": (0xE8C4E, "8b87c00000008946448b8fc4000000894e48"),
        "load_draw_buffers": (0xE8C60, "8b97d00000008b87cc00000052508bcee85bffffff"),
        "load_template_counts": (0xE8C99, "668b97c800000066895656668b87ca0000005f66894654"),
        "draw_vertex_stride_24": (0xE87C6, "66c740141800"),
        "eight_instances_per_draw": (0xE8940, "83f9087e0bbb08000000895c2414"),
        "instance_stride_80": (0xE89D8, "8d0c80c1e104"),
        "three_matrix_constants": (0xE8A49, "c744910c03000000"),
        "color_constant_at_64": (0xE8A5C, "8b56488b44241c8d4c0240"),
        "template_strip_draw_formula": (0xE8B17, "668b7e568b4c2418660faffb33c0668b465466050200"),
        "double_sided_material_flag": (0xE5C07, "8b467cf6c44074046a00"),
        "alpha_greater": (0xE6224, "ba04020000b93c030400"),
        "texture_alpha_mask_mode": (0xE632A, "8b511033c083fa020f94c0"),
        "texture_alpha_blend_mode": (0xE62FA, "8b511033c083fa030f94c0"),
        "mask_reference_formula": (0xE5D65, "f30f104668f30f5905b8422300b800010000f30f2cc82bc1"),
        "alpha_scale_255": (0x2342B8, "00007f43"),
        "blend_source_alpha": (0xEB447, "ba02030000b944030400"),
        "blend_one_minus_source_alpha": (0xEB460, "ba03030000b948030400"),
        "draw_mode_depth_flag_table": (0xEA000, "00010202010202020303"),
        "depth_mask_inverts_flag_bit_1": (0xEAF1E, "33db8a5d1cd0ebf6d3b95c03040083e3018bd3"),
    }
    result = []
    for name, (address, expected_hex) in checks.items():
        expected = bytes.fromhex(expected_hex)
        if image.read_virtual(address, len(expected)) != expected:
            raise ValueError(f"scatter executable evidence mismatch: {name} at 0x{address:X}")
        result.append({"name": name, "virtual_address": f"0x{address:X}", "byte_length": len(expected)})
    return {"status": "verified", "sha256": digest, "checks": result}
