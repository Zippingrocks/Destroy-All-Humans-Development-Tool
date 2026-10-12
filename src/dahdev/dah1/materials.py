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
        return {0: "Xbox swizzled A8R8G8B8", 4: "Xbox linear A8R8G8B8",
                5: "Xbox linear A4R4G4B4", 8: "BC1/DXT1", 9: "BC3/DXT5"}.get(
            self.format_word, f"unknown-0x{self.format_word:X}"
        )


@dataclass(frozen=True)
class NativeMaterial:
    resource: ResourceRecord
    raw_bytes: bytes
    render_flags: int
    binding_pointer_field: int
    binding: TextureBinding | None

    @property
    def address_u(self) -> str:
        return "clamp" if self.render_flags & 0x1000 else "wrap"

    @property
    def address_v(self) -> str:
        return "clamp" if self.render_flags & 0x2000 else "wrap"


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


def argb8_mip_chain_size(width: int, height: int, levels: int) -> int:
    """Exact compact mip sizes for native swizzled Xbox A8R8G8B8."""
    if width <= 0 or height <= 0 or width & (width - 1) or height & (height - 1):
        raise ValueError("swizzled A8R8G8B8 dimensions must be positive powers of two")
    if not 1 <= levels <= max(width, height).bit_length():
        raise ValueError("swizzled A8R8G8B8 mip count is invalid")
    total = 0
    for _ in range(levels):
        total += width * height * 4
        width, height = max(1, width // 2), max(1, height // 2)
    return total


def decode_swizzled_argb8_top_level(payload: bytes, width: int, height: int) -> Image.Image:
    """Decode format-table entry 0 (D3DFMT_A8R8G8B8 = 6).

    Source pixels are little-endian ARGB dwords (BGRA bytes), in Xbox compact
    Morton order. Only dimensions with a verified power-of-two layout are accepted.
    Native compressed formats are not passed through this unswizzler.
    """
    required = argb8_mip_chain_size(width, height, 1)
    if len(payload) < required:
        raise ValueError("swizzled A8R8G8B8 top-level payload is truncated")
    x = np.arange(width, dtype=np.uint32)
    y = np.arange(height, dtype=np.uint32)
    x_offsets = np.zeros(width, dtype=np.uint32)
    y_offsets = np.zeros(height, dtype=np.uint32)
    source_bit, address_bit = 1, 1
    while source_bit < width or source_bit < height:
        if source_bit < width:
            x_offsets |= ((x & source_bit) != 0).astype(np.uint32) * address_bit
            address_bit <<= 1
        if source_bit < height:
            y_offsets |= ((y & source_bit) != 0).astype(np.uint32) * address_bit
            address_bit <<= 1
        source_bit <<= 1
    native = np.frombuffer(payload, dtype=np.uint8, count=required).reshape(-1, 4)
    bgra = native[y_offsets[:, None] | x_offsets[None, :]]
    return Image.fromarray(bgra[:, :, [2, 1, 0, 3]], mode="RGBA")


def linear_argb_size(width: int, height: int, levels: int, bytes_per_pixel: int) -> int:
    """Verified tight-row subset of Xbox linear textures; no guessed pitch."""
    if width <= 0 or height <= 0 or bytes_per_pixel not in (2, 4):
        raise ValueError("linear ARGB dimensions or pixel size are invalid")
    if levels != 1:
        raise ValueError("only one-level native linear ARGB textures are verified")
    if width * bytes_per_pixel % 64:
        raise ValueError("linear ARGB row pitch is not native 64-byte aligned")
    return width * height * bytes_per_pixel


def decode_linear_argb_top_level(payload: bytes, width: int, height: int, format_word: int) -> Image.Image:
    """Binding format4 -> native0x12 BGRA8; format5 -> native0x1D ARGB4444."""
    if format_word not in (4, 5):
        raise ValueError("unsupported linear ARGB binding format")
    required = linear_argb_size(width, height, 1, 4 if format_word == 4 else 2)
    if len(payload) < required:
        raise ValueError("linear ARGB top-level payload is truncated")
    if format_word == 4:
        bgra = np.frombuffer(payload, dtype=np.uint8, count=required).reshape(height, width, 4)
        rgba = bgra[:, :, [2, 1, 0, 3]]
    else:
        argb = np.frombuffer(payload, dtype="<u2", count=width * height).reshape(height, width)
        rgba = np.stack([((argb >> shift) & 15) * 17 for shift in (8, 4, 0, 12)], axis=2).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def decode_material(reader: BlockReader, resource: ResourceRecord) -> NativeMaterial:
    group, raw = _material_data(reader, resource)
    render_flags = struct.unpack_from("<I", raw, 0x7C)[0]
    return NativeMaterial(resource, raw, render_flags, resource.offset + 0x6C,
                          _decode_material_texture_binding(reader, resource, group, raw, 0))


def _material_data(reader: BlockReader, resource: ResourceRecord):
    if resource.type_hash != MATERIAL_TYPE:
        raise ValueError(f"resource 0x{resource.name_hash:08X} is not a material")
    if resource.size < 128:
        raise ValueError("material record is too short for its verified fields")
    group = reader.load_group(resource.load_group)
    if resource.offset < 0 or resource.offset + resource.size > group.resource_limit:
        raise ValueError("material record extends outside resource data")
    raw = group.data[resource.offset : resource.offset + resource.size]
    if len(raw) != resource.size:
        raise ValueError("material record is truncated")
    return group, raw


def decode_material_texture_bindings(
    reader: BlockReader, resource: ResourceRecord,
) -> tuple[TextureBinding | None, ...]:
    """All four native texture stages, preserving unbound slots and identities.

    Retail material initialization 0xE60BC resolves exactly four consecutive
    pointers at +0x6C/+0x70/+0x74/+0x78. Stage 1 is not an alternative diffuse
    texture: reflection and other shaders sample it independently.
    """
    group, raw = _material_data(reader, resource)
    return tuple(_decode_material_texture_binding(reader, resource, group, raw, stage)
                 for stage in range(4))


def _decode_material_texture_binding(reader, resource, group, raw, stage):
    field_offset = 0x6C + stage * 4
    pointer_field = resource.offset + field_offset
    binding_offset = group.relocations.pointers.get(pointer_field)
    if binding_offset is None:
        if raw[field_offset:field_offset + 4] != bytes(4):
            raise ValueError(f"material stage {stage} binding field is non-zero but has no relocation")
        return None

    if binding_offset < 0 or binding_offset + 52 > group.resource_limit:
        raise ValueError("material texture binding extends outside resource data")
    binding_raw = group.data[binding_offset : binding_offset + 52]
    if len(binding_raw) != 52:
        raise ValueError("material texture binding is truncated")
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
    size_function = {
        0: argb8_mip_chain_size,
        4: lambda w, h, n: linear_argb_size(w, h, n, 4),
        5: lambda w, h, n: linear_argb_size(w, h, n, 2),
        8: dxt1_mip_chain_size,
        9: bc3_mip_chain_size,
    }.get(format_word)
    expected_size = size_function(width, height, levels) if size_function else None
    if expected_size is None:
        raise ValueError(f"unsupported terrain texture format word 0x{format_word:X}")
    if expected_size != texture.size:
        raise ValueError(
            f"texture 0x{texture_hash:08X} payload size does not match the binding's "
            "dimensions, format word, and mip count"
        )

    return TextureBinding(
        offset=binding_offset,
        raw_bytes=binding_raw,
        format_word=format_word,
        mip_levels=levels,
        width=width,
        height=height,
        texture_hash=texture_hash,
        texture_resource=texture,
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
    if binding.format_word == 0:
        return decode_swizzled_argb8_top_level(payload, binding.width, binding.height)
    if binding.format_word in (4, 5):
        return decode_linear_argb_top_level(payload, binding.width, binding.height, binding.format_word)
    if binding.format_word == 8:
        return decode_dxt1_top_level(payload, binding.width, binding.height)
    if binding.format_word == 9:
        return decode_dxt5_top_level(payload, binding.width, binding.height)
    raise ValueError(f"unsupported terrain texture format word 0x{binding.format_word:X}")


def texture_mip_layout(binding: TextureBinding) -> tuple[dict[str, int], ...]:
    """Offsets of authored levels, largest first; no synthesized mip tail.

    The verified compressed and swizzled resources store compact, contiguous
    levels. Linear textures have one native level and a verified tight pitch.
    A partial native chain is valid: its last level is not required to be 1x1.
    """
    width, height, count = binding.width, binding.height, binding.mip_levels
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height, count)):
        raise ValueError("native mip dimensions and count must be positive integers")
    if count > max(width, height).bit_length():
        raise ValueError("native mip count exceeds the dimension chain")
    if binding.format_word == 0:
        argb8_mip_chain_size(width, height, count)
        level_size = lambda w, h: w * h * 4
    elif binding.format_word in (4, 5):
        bpp = 4 if binding.format_word == 4 else 2
        linear_argb_size(width, height, count, bpp)
        level_size = lambda w, h: w * h * bpp
    elif binding.format_word in (8, 9):
        level_size = dxt1_level_size if binding.format_word == 8 else bc3_level_size
    else:
        raise ValueError(f"unsupported native mip texture format word 0x{binding.format_word:X}")
    layout = []
    cursor = 0
    for index in range(count):
        size = level_size(width, height)
        layout.append({"level": index, "width": width, "height": height,
                       "byte_offset": cursor, "byte_length": size})
        cursor += size
        width, height = max(1, width // 2), max(1, height // 2)
    if cursor != binding.texture_resource.size:
        raise ValueError("native mip chain size does not match the texture resource")
    return tuple(layout)


def decode_texture_mip_levels(reader: BlockReader, binding: TextureBinding) -> tuple[Image.Image, ...]:
    """Decode every original mip without resizing, filtering or upscaling.

    Each RGBA image comes from that level's own native payload, including
    authored alpha coverage. GPU-generated replacements are not equivalent.
    """
    layout = texture_mip_layout(binding)
    payload = texture_payload(reader, binding)
    images = []
    for level in layout:
        offset, size = level["byte_offset"], level["byte_length"]
        data = payload[offset:offset + size]
        width, height = level["width"], level["height"]
        if binding.format_word == 0:
            image = decode_swizzled_argb8_top_level(data, width, height)
        elif binding.format_word in (4, 5):
            image = decode_linear_argb_top_level(data, width, height, binding.format_word)
        elif binding.format_word == 8:
            image = decode_dxt1_top_level(data, width, height)
        else:
            image = decode_dxt5_top_level(data, width, height)
        images.append(image)
    return tuple(images)


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
                "render_flags": f"0x{material.render_flags:08X}",
                "address_u": material.address_u,
                "address_v": material.address_v,
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
                "material_gpu_setup_0xE5C00": (
                    "reads material +0x7C: bit 0x1000 selects U clamp and bit "
                    "0x2000 selects V clamp; unset selects wrap for stages 0, 2, and 3"
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
            "Material flags other than 0x1000 and 0x2000 remain preserved without semantics.",
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
    native_uv = np.ndarray(
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
    obj_lines.extend(f"vt {u:.9g} {1.0 - v:.9g}" for u, v in native_uv)
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
            "uv": "verified native stride-24 vertex bytes +0x0C..+0x13",
            "material_assignment": "segment raw_flags low byte -> relocated wrapper slot",
            "textures": "material +0x6C relocation -> binding +0x18 hash -> texture resource",
            "texture_addressing": (
                "material +0x7C flags: 0x1000 clamps U and 0x2000 clamps V; "
                "unset axes wrap"
            ),
        },
        "limitations": [
            "A same-location retail-game comparison is still required to validate viewport orientation and remaining render states.",
            "OBJ is a read-only inspection projection, not an authoritative DAH representation.",
            "The segment flag high byte remains unresolved.",
        ],
    }
    (destination / f"{stem}_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
