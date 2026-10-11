from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

from .container import BlockReader, ResourceRecord
from .transforms import transform_points

if TYPE_CHECKING:
    from .terrain import TerrainLOD, TerrainScene


MATERIAL_TYPE = 0x9709513E
TEXTURE_TYPE = 0x3BE99284


@dataclass(frozen=True)
class TextureBinding:
    offset: int
    raw_bytes: bytes
    format_word: int
    mip_levels: int
    width: int
    height: int
    texture_hash: int
    texture_resource: ResourceRecord

    @property
    def format_name(self) -> str:
        return {8: "BC1/DXT1", 9: "BC3/DXT5"}.get(
            self.format_word, f"unknown-0x{self.format_word:X}"
        )


@dataclass(frozen=True)
class NativeMaterial:
    resource: ResourceRecord
    raw_bytes: bytes
    binding_pointer_field: int
    binding: TextureBinding | None


@dataclass(frozen=True)
class TerrainMaterialSlot:
    index: int
    pointer_field: int
    material: NativeMaterial


def dxt1_level_size(width: int, height: int) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 8


def dxt1_mip_chain_size(width: int, height: int, levels: int) -> int:
    total = 0
    for _ in range(levels):
        total += dxt1_level_size(width, height)
        width = max(1, width // 2)
        height = max(1, height // 2)
    return total


def bc3_level_size(width: int, height: int) -> int:
    return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 16


def bc3_mip_chain_size(width: int, height: int, levels: int) -> int:
    total = 0
    for _ in range(levels):
        total += bc3_level_size(width, height)
        width = max(1, width // 2)
        height = max(1, height // 2)
    return total


def decode_material(reader: BlockReader, resource: ResourceRecord) -> NativeMaterial:
    if resource.type_hash != MATERIAL_TYPE:
        raise ValueError(f"resource 0x{resource.name_hash:08X} is not a material")
    if resource.size < 112:
        raise ValueError("material record is too short for its verified binding field")

    group = reader.load_group(resource.load_group)
    raw = group.data[resource.offset : resource.offset + resource.size]
    pointer_field = resource.offset + 108
    binding_offset = group.relocations.pointers.get(pointer_field)
    if binding_offset is None:
        if raw[108:112] != bytes(4):
            raise ValueError("material binding field is non-zero but has no relocation")
        return NativeMaterial(resource, raw, pointer_field, None)

    if binding_offset + 52 > group.resource_limit:
        raise ValueError("material texture binding extends outside resource data")
    binding_raw = group.data[binding_offset : binding_offset + 52]
    format_word, levels, width, height = struct.unpack_from("<4I", binding_raw)
    texture_hash = struct.unpack_from("<I", binding_raw, 24)[0]
    texture = reader.resource_by_identity(TEXTURE_TYPE, texture_hash)
    if texture is None:
        raise ValueError(
            f"material 0x{resource.name_hash:08X} binding names missing texture "
            f"0x{texture_hash:08X}"
        )
    if not width or not height or not levels:
        raise ValueError("material texture binding has zero dimensions or mip levels")
    expected_size = {
        8: dxt1_mip_chain_size(width, height, levels),
        9: bc3_mip_chain_size(width, height, levels),
    }.get(format_word)
    if expected_size is None:
        raise ValueError(f"unsupported terrain texture format word 0x{format_word:X}")
    if expected_size != texture.size:
        raise ValueError(
            f"texture 0x{texture_hash:08X} payload size does not match the binding's "
            "dimensions, format word, and mip count"
        )

    return NativeMaterial(
        resource=resource,
        raw_bytes=raw,
        binding_pointer_field=pointer_field,
        binding=TextureBinding(
            offset=binding_offset,
            raw_bytes=binding_raw,
            format_word=format_word,
            mip_levels=levels,
            width=width,
            height=height,
            texture_hash=texture_hash,
            texture_resource=texture,
        ),
    )


def terrain_material_slots(
    reader: BlockReader, lod: TerrainLOD
) -> tuple[TerrainMaterialSlot, ...]:
    wrapper = lod.wrapper
    group = reader.load_group(wrapper.load_group)
    relocations = group.relocations.pointers
    table_field = wrapper.offset + 0xB4
    table = relocations.get(table_field)
    if table is None:
        raise ValueError("terrain wrapper lacks its relocated material-slot table")
    count = struct.unpack_from("<I", group.data, wrapper.offset + 0xB8)[0]
    slots: list[TerrainMaterialSlot] = []
    for index in range(count):
        pointer_field = table + index * 4
        target = relocations.get(pointer_field)
        if target is None:
            raise ValueError("terrain material slot lacks a relocation")
        resource = reader.resource_by_location(wrapper.load_group, target)
        if resource is None or resource.type_hash != MATERIAL_TYPE:
            raise ValueError("terrain material slot does not resolve to a material resource")
        slots.append(
            TerrainMaterialSlot(
                index=index,
                pointer_field=pointer_field,
                material=decode_material(reader, resource),
            )
        )

    for segment in lod.geometry.segments:
        slot = segment.raw_flags & 0xFF
        if slot >= len(slots):
            raise ValueError("terrain segment material-slot index is out of range")
    return tuple(slots)


def texture_payload(reader: BlockReader, binding: TextureBinding) -> bytes:
    resource = binding.texture_resource
    group = reader.load_group(resource.load_group)
    payload = group.data[resource.offset : resource.offset + resource.size]
    if len(payload) != resource.size:
        raise ValueError("texture payload is truncated")
    return payload


def decode_dxt1_top_level(payload: bytes, width: int, height: int) -> Image.Image:
    required = dxt1_level_size(width, height)
    if len(payload) < required:
        raise ValueError("BC1/DXT1 top-level payload is truncated")
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    cursor = 0
    for block_y in range((height + 3) // 4):
        for block_x in range((width + 3) // 4):
            color0, color1, selectors = struct.unpack_from("<HHI", payload, cursor)
            cursor += 8

            def rgb565(value: int) -> tuple[int, int, int, int]:
                return (
                    ((value >> 11) & 31) * 255 // 31,
                    ((value >> 5) & 63) * 255 // 63,
                    (value & 31) * 255 // 31,
                    255,
                )

            first = rgb565(color0)
            second = rgb565(color1)
            if color0 > color1:
                palette = (
                    first,
                    second,
                    tuple((2 * first[i] + second[i]) // 3 for i in range(3)) + (255,),
                    tuple((first[i] + 2 * second[i]) // 3 for i in range(3)) + (255,),
                )
            else:
                palette = (
                    first,
                    second,
                    tuple((first[i] + second[i]) // 2 for i in range(3)) + (255,),
                    (0, 0, 0, 0),
                )
            for y in range(4):
                for x in range(4):
                    px = block_x * 4 + x
                    py = block_y * 4 + y
                    if px < width and py < height:
                        selector = (selectors >> (2 * (y * 4 + x))) & 3
                        rgba[py, px] = palette[selector]
    return Image.fromarray(rgba, mode="RGBA")


def decode_dxt5_top_level(payload: bytes, width: int, height: int) -> Image.Image:
    required = bc3_level_size(width, height)
    if len(payload) < required:
        raise ValueError("BC3/DXT5 top-level payload is truncated")
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    cursor = 0
    for block_y in range((height + 3) // 4):
        for block_x in range((width + 3) // 4):
            alpha0, alpha1 = struct.unpack_from("<BB", payload, cursor)
            alpha_bits = int.from_bytes(payload[cursor + 2 : cursor + 8], "little")
            color0, color1, selectors = struct.unpack_from("<HHI", payload, cursor + 8)
            cursor += 16

            def rgb565(value: int) -> tuple[int, int, int]:
                return (
                    ((value >> 11) & 31) * 255 // 31,
                    ((value >> 5) & 63) * 255 // 63,
                    (value & 31) * 255 // 31,
                )

            first = rgb565(color0)
            second = rgb565(color1)
            colors = (
                first,
                second,
                tuple((2 * first[i] + second[i]) // 3 for i in range(3)),
                tuple((first[i] + 2 * second[i]) // 3 for i in range(3)),
            )
            if alpha0 > alpha1:
                alphas = [
                    alpha0,
                    alpha1,
                    *[
                        ((7 - step) * alpha0 + step * alpha1) // 7
                        for step in range(1, 7)
                    ],
                ]
            else:
                alphas = [
                    alpha0,
                    alpha1,
                    *[
                        ((5 - step) * alpha0 + step * alpha1) // 5
                        for step in range(1, 5)
                    ],
                    0,
                    255,
                ]
            for y in range(4):
                for x in range(4):
                    px = block_x * 4 + x
                    py = block_y * 4 + y
                    if px < width and py < height:
                        pixel = y * 4 + x
                        color = colors[(selectors >> (2 * pixel)) & 3]
                        alpha = alphas[(alpha_bits >> (3 * pixel)) & 7]
                        rgba[py, px] = (*color, alpha)
    return Image.fromarray(rgba, mode="RGBA")


def decode_texture_top_level(reader: BlockReader, binding: TextureBinding) -> Image.Image:
    payload = texture_payload(reader, binding)
    if binding.format_word == 8:
        return decode_dxt1_top_level(payload, binding.width, binding.height)
    if binding.format_word == 9:
        return decode_dxt5_top_level(payload, binding.width, binding.height)
    raise ValueError(f"unsupported terrain texture format word 0x{binding.format_word:X}")


def material_dependency_report(scene: TerrainScene) -> dict[str, object]:
    material_records: dict[int, NativeMaterial] = {}
    texture_uses: dict[int, int] = {}
    lod_records: list[dict[str, object]] = []
    segment_count = 0
    slot_count = 0
    for placement_index, placement in enumerate(scene.placements):
        for lod in placement.children:
            slots = terrain_material_slots(scene.reader, lod)
            slot_count += len(slots)
            for slot in slots:
                material_records[slot.material.resource.name_hash] = slot.material
            segments = []
            for segment in lod.geometry.segments:
                slot_index = segment.raw_flags & 0xFF
                material = slots[slot_index].material
                binding = material.binding
                if binding is not None:
                    texture_uses[binding.texture_hash] = texture_uses.get(binding.texture_hash, 0) + 1
                segments.append(
                    {
                        "segment": segment.index,
                        "raw_flags": f"0x{segment.raw_flags:04X}",
                        "material_slot_low_byte": slot_index,
                        "material_hash": f"0x{material.resource.name_hash:08X}",
                        "texture_hash": (
                            f"0x{binding.texture_hash:08X}" if binding is not None else None
                        ),
                    }
                )
                segment_count += 1
            lod_records.append(
                {
                    "placement": placement_index,
                    "lod": lod.index,
                    "wrapper_hash": f"0x{lod.wrapper.name_hash:08X}",
                    "material_slot_count": len(slots),
                    "segments": segments,
                }
            )

    materials = []
    for name_hash, material in sorted(material_records.items()):
        binding = material.binding
        materials.append(
            {
                "material_hash": f"0x{name_hash:08X}",
                "resource_offset": f"0x{material.resource.offset:X}",
                "resource_size": material.resource.size,
                "binding_pointer_field": f"0x{material.binding_pointer_field:X}",
                "binding_offset": f"0x{binding.offset:X}" if binding else None,
                "binding_raw_hex": binding.raw_bytes.hex() if binding else None,
                "format_word": f"0x{binding.format_word:08X}" if binding else None,
                "format": binding.format_name if binding else None,
                "mip_levels": binding.mip_levels if binding else None,
                "width": binding.width if binding else None,
                "height": binding.height if binding else None,
                "texture_hash": f"0x{binding.texture_hash:08X}" if binding else None,
                "texture_load_group": (
                    binding.texture_resource.load_group if binding else None
                ),
                "texture_payload_size": (
                    binding.texture_resource.size if binding else None
                ),
            }
        )

    return {
        "status": "structurally_resolved",
        "evidence": {
            "wrapper_material_table_pointer_field": "+0xB4 relocation",
            "wrapper_material_count_field": "+0xB8 uint32",
            "segment_material_slot": "low byte of preserved uint16 raw_flags",
            "material_texture_binding_pointer_field": "+0x6C relocation",
            "binding_texture_identity_field": "+0x18 name hash",
            "texture_resource_type": f"0x{TEXTURE_TYPE:08X}",
            "payload_interpretation": (
                "format word 8 = BC1/DXT1 and 9 = BC3/DXT5; each has an exact native "
                "width, height, mip count, and payload-size match"
            ),
            "retail_executable": {
                "default_xbe_sha256": "b9491b30eaee82af6c805d9b0036ec1119059b0660a0923803ebdb6219fa02eb",
                "wrapper_loader_0xF0490": (
                    "reads +0xB8 count and indexes the +0xB4 pointer table"
                ),
                "texture_resolver_0xE67C1": (
                    "reads binding +0x18 and resolves resource type 0x3BE99284"
                ),
                "texture_creator_0xE64F0": (
                    "uses binding +0x00 as format-table index and +0x04/+0x08/+0x0C "
                    "as creation arguments matching mip count, width, and height"
                ),
            },
        },
        "counts": {
            "terrain_lods": len(lod_records),
            "material_slot_occurrences": slot_count,
            "terrain_segments": segment_count,
            "unique_materials": len(material_records),
            "materials_with_texture_binding": sum(
                material.binding is not None for material in material_records.values()
            ),
            "unique_bound_textures": len(texture_uses),
        },
        "materials": materials,
        "terrain_lods": lod_records,
        "remaining_unknowns": [
            "The high byte of terrain segment raw_flags is preserved without semantics.",
            "Texture addressing and render-state fields are not yet identified.",
        ],
    }


def write_material_outputs(scene: TerrainScene, output_dir: str | Path) -> dict[str, object]:
    destination = Path(output_dir)
    texture_dir = destination / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    report = material_dependency_report(scene)
    exported: set[int] = set()
    for placement in scene.placements:
        for lod in placement.children:
            for slot in terrain_material_slots(scene.reader, lod):
                binding = slot.material.binding
                if binding is None or binding.texture_hash in exported:
                    continue
                image = decode_texture_top_level(scene.reader, binding)
                image.save(texture_dir / f"{binding.texture_hash:08x}.png")
                exported.add(binding.texture_hash)
    report["decoded_texture_previews"] = len(exported)
    report["decoded_texture_directory"] = str(texture_dir.resolve())
    (destination / "area42_terrain_materials.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


def export_textured_section(
    scene: TerrainScene,
    placement_index: int,
    lod_index: int,
    output_dir: str | Path,
) -> dict[str, object]:
    placement = scene.placements[placement_index]
    lod = placement.children[lod_index]
    slots = terrain_material_slots(scene.reader, lod)
    destination = Path(output_dir)
    texture_dir = destination / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    stem = f"area42_tile{placement_index:03d}_lod{lod_index}"
    obj_path = destination / f"{stem}.obj"
    mtl_path = destination / f"{stem}.mtl"

    geometry = lod.geometry
    world = transform_points(geometry.local_positions, placement.matrix)
    candidate_uv = np.ndarray(
        (len(geometry.vertex_bytes), 2),
        dtype="<f4",
        buffer=geometry.vertex_bytes,
        offset=12,
        strides=(24, 4),
    ).copy()

    material_names: dict[int, str] = {}
    mtl_lines = []
    for slot in slots:
        material = slot.material
        name = f"material_{material.resource.name_hash:08x}"
        material_names[slot.index] = name
        mtl_lines.extend([f"newmtl {name}", "Kd 1 1 1"])
        if material.binding is not None:
            binding = material.binding
            filename = f"{binding.texture_hash:08x}.png"
            decode_texture_top_level(scene.reader, binding).save(texture_dir / filename)
            mtl_lines.append(f"map_Kd textures/{filename}")
        mtl_lines.append("")
    mtl_path.write_text("\n".join(mtl_lines), encoding="utf-8")

    obj_lines = [f"mtllib {mtl_path.name}", f"o {stem}"]
    obj_lines.extend(f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in world)
    # Wavefront's V origin is conventionally bottom-left; native BC rows decode
    # top-down, so this is the only coordinate conversion in the inspection view.
    obj_lines.extend(f"vt {u:.9g} {1.0 - v:.9g}" for u, v in candidate_uv)
    for segment in geometry.segments:
        slot_index = segment.raw_flags & 0xFF
        obj_lines.append(f"usemtl {material_names[slot_index]}")
        for a, b, c in segment.triangles:
            one_based = (int(a) + 1, int(b) + 1, int(c) + 1)
            obj_lines.append(
                "f " + " ".join(f"{index}/{index}" for index in one_based)
            )
    obj_path.write_text("\n".join(obj_lines) + "\n", encoding="utf-8")

    manifest = {
        "status": "inspection_export",
        "placement": placement_index,
        "lod": lod_index,
        "obj": str(obj_path.resolve()),
        "mtl": str(mtl_path.resolve()),
        "vertices": len(world),
        "triangles": len(geometry.triangles),
        "segments": len(geometry.segments),
        "material_slots": len(slots),
        "provenance": {
            "positions": "native stride-24 vertex bytes +0x00..+0x0B, retail transform",
            "uv_candidate": "native stride-24 vertex bytes +0x0C..+0x13",
            "material_assignment": "segment raw_flags low byte -> relocated wrapper slot",
            "textures": "material +0x6C relocation -> binding +0x18 hash -> texture resource",
        },
        "limitations": [
            "The UV channel location is structurally compelling but still awaits executable confirmation and same-location retail-game comparison.",
            "OBJ is a read-only inspection projection, not an authoritative DAH representation.",
            "Texture addressing and the segment flag high byte remain unresolved.",
        ],
    }
    (destination / f"{stem}_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
