from __future__ import annotations

import os
import struct
import unittest
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

from dahdev.dah1.container import BlockReader, ResourceRecord
from dahdev.dah1.materials import (
    MATERIAL_TYPE, TEXTURE_TYPE, TextureBinding, decode_material,
    decode_material_texture_bindings, decode_texture_mip_levels, decode_texture_top_level, texture_mip_layout,
)


def fixture(payload, width, height, levels, fmt):
    resource = ResourceRecord(0, TEXTURE_TYPE, 0x1234, 0, 0, len(payload), 0)
    binding = TextureBinding(0, bytes(52), fmt, levels, width, height, 0x1234, resource)
    reader = SimpleNamespace(load_group=lambda _: SimpleNamespace(data=payload))
    return reader, binding


def two_stage_material_fixture():
    data = bytearray(264)
    material = ResourceRecord(0, MATERIAL_TYPE, 0xA0, 0, 0, 140, 0)
    struct.pack_into("<8f", data, 0x20, 4, 0, 0, -1.5, 0, 4, 0, -1.5)
    struct.pack_into("<f", data, 0x68, 1)
    struct.pack_into("<4I", data, 160, 8, 2, 8, 8)
    struct.pack_into("<I", data, 160 + 24, 0xB0)
    struct.pack_into("<4I", data, 212, 8, 1, 4, 4)
    struct.pack_into("<I", data, 212 + 24, 0xB1)
    resources = {
        0xB0: ResourceRecord(1, TEXTURE_TYPE, 0xB0, 1, 0, 40, 0),
        0xB1: ResourceRecord(2, TEXTURE_TYPE, 0xB1, 2, 0, 8, 0),
    }
    red, green = struct.pack("<HHI", 0xF800, 0, 0), struct.pack("<HHI", 0x07E0, 0, 0)
    groups = {
        0: SimpleNamespace(data=data, resource_limit=len(data), relocations=SimpleNamespace(pointers={0x6C: 160, 0x70: 212})),
        1: SimpleNamespace(data=red * 4 + green),
        2: SimpleNamespace(data=green),
    }
    reader = SimpleNamespace(load_group=groups.__getitem__, resource_by_identity=lambda kind, name: resources.get(name))
    return reader, material


class MaterialStageTests(unittest.TestCase):
    def test_all_four_stages_keep_null_slots_and_relocation_provenance(self):
        reader, resource = two_stage_material_fixture()
        bindings = decode_material_texture_bindings(reader, resource)
        self.assertEqual(len(bindings), 4)
        self.assertEqual([b.texture_hash if b else None for b in bindings], [0xB0, 0xB1, None, None])
        self.assertEqual([b.offset if b else None for b in bindings], [160, 212, None, None])
        self.assertEqual(bindings[0], decode_material(reader, resource).binding)
        self.assertEqual(bindings[1].texture_resource.load_group, 2)

    def test_secondary_missing_relocation_target_and_bounds_fail(self):
        for mutation, expected in (
            (lambda g: g.relocations.pointers.pop(0x70), "no relocation"),
            (lambda g: g.relocations.pointers.__setitem__(0x70, -1), "outside"),
            (lambda g: g.relocations.pointers.__setitem__(0x70, 263), "outside"),
            (lambda g: struct.pack_into("<I", g.data, 212 + 24, 0xBAD), "missing texture"),
        ):
            reader, resource = two_stage_material_fixture()
            group = reader.load_group(0)
            struct.pack_into("<I", group.data, 0x70, 1)
            mutation(group)
            with self.assertRaisesRegex(ValueError, expected):
                decode_material_texture_bindings(reader, resource)


class NativeMipTests(unittest.TestCase):
    def test_bc1_each_level_uses_its_own_authored_pixels(self):
        # Red 8x4, green 4x2, blue 2x1, white 1x1: no resize can produce these.
        colors = (0xF800, 0x07E0, 0x001F, 0xFFFF)
        payload = b"".join(struct.pack("<HHI", color, 0, 0) * (2 if i == 0 else 1)
                           for i, color in enumerate(colors))
        reader, binding = fixture(payload, 8, 4, 4, 8)
        images = decode_texture_mip_levels(reader, binding)
        self.assertEqual([im.size for im in images], [(8, 4), (4, 2), (2, 1), (1, 1)])
        self.assertEqual([im.getpixel((0, 0)) for im in images],
                         [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 255, 255)])
        self.assertEqual([m["byte_offset"] for m in texture_mip_layout(binding)], [0, 16, 24, 32])
        self.assertEqual(images[0].tobytes(), decode_texture_top_level(reader, binding).tobytes())

    def test_bc3_preserves_authored_alpha_per_level(self):
        payload = b"".join(bytes([alpha, 0]) + bytes(6) + struct.pack("<HHI", 0xFFFF, 0, 0)
                           for alpha in (255, 128, 7))
        reader, binding = fixture(payload, 4, 4, 3, 9)
        images = decode_texture_mip_levels(reader, binding)
        self.assertEqual([im.getpixel((0, 0))[3] for im in images], [255, 128, 7])
        self.assertEqual([m["byte_length"] for m in texture_mip_layout(binding)], [16, 16, 16])

    def test_swizzled_levels_use_compact_independent_layout(self):
        payload = bytes([1, 2, 3, 4]) * 8 + bytes([5, 6, 7, 8]) * 2 + bytes([9, 10, 11, 12])
        reader, binding = fixture(payload, 4, 2, 3, 0)
        images = decode_texture_mip_levels(reader, binding)
        self.assertEqual([im.size for im in images], [(4, 2), (2, 1), (1, 1)])
        self.assertEqual([im.getpixel((0, 0)) for im in images], [(3, 2, 1, 4), (7, 6, 5, 8), (11, 10, 9, 12)])
        self.assertEqual([m["byte_offset"] for m in texture_mip_layout(binding)], [0, 32, 40])

    def test_linear_textures_keep_the_native_one_level_subset(self):
        for fmt, payload, width in ((4, bytes([1, 2, 3, 4]) * 16, 16),
                                    (5, struct.pack("<H", 0xA123) * 32, 32)):
            reader, binding = fixture(payload, width, 1, 1, fmt)
            images = decode_texture_mip_levels(reader, binding)
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].tobytes(), decode_texture_top_level(reader, binding).tobytes())
            with self.assertRaisesRegex(ValueError, "one-level"):
                texture_mip_layout(replace(binding, mip_levels=2))

    def test_partial_chain_does_not_generate_a_missing_tail(self):
        reader, binding = fixture(bytes(40), 8, 8, 2, 8)
        self.assertEqual([im.size for im in decode_texture_mip_levels(reader, binding)], [(8, 8), (4, 4)])

    def test_invalid_metadata_and_truncated_payload_fail(self):
        reader, binding = fixture(bytes(8), 4, 4, 1, 8)
        for changes in ({"width": 0}, {"height": -1}, {"mip_levels": 0},
                        {"mip_levels": 4}, {"width": True}, {"format_word": 99}):
            with self.assertRaises(ValueError):
                texture_mip_layout(replace(binding, **changes))
        with self.assertRaisesRegex(ValueError, "chain size"):
            texture_mip_layout(replace(binding, texture_resource=replace(binding.texture_resource, size=9)))
        reader.load_group = lambda _: SimpleNamespace(data=bytes(7))
        with self.assertRaisesRegex(ValueError, "truncated"):
            decode_texture_mip_levels(reader, binding)

    @unittest.skipUnless(os.environ.get("DAHDEV_AREA42_SOURCE"), "retail fixture not configured")
    def test_all_native_area42_chains_and_original_dimensions(self):
        reader = BlockReader(os.environ["DAHDEV_AREA42_SOURCE"])
        bindings = {}
        for resource in reader.directory.resources:
            if resource.type_hash == MATERIAL_TYPE:
                for binding in decode_material_texture_bindings(reader, resource):
                    if binding:
                        bindings[binding.texture_hash] = binding
        self.assertEqual(len(bindings), 638)
        self.assertEqual(Counter(b.format_word for b in bindings.values()), {8: 422, 9: 196, 4: 12, 0: 5, 5: 3})
        for binding in bindings.values():
            layout = texture_mip_layout(binding)
            self.assertEqual(sum(m["byte_length"] for m in layout), binding.texture_resource.size)
            images = decode_texture_mip_levels(reader, binding)
            self.assertEqual(len(images), binding.mip_levels)
            self.assertEqual(images[0].size, (binding.width, binding.height))
            for level, image in enumerate(images):
                self.assertEqual(image.size, (max(1, binding.width >> level), max(1, binding.height >> level)))


if __name__ == "__main__":
    unittest.main()
