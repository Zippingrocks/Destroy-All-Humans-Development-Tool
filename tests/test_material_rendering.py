from __future__ import annotations

import hashlib
import os
import struct
import unittest
from collections import Counter

import numpy as np

from dahdev.dah1.container import BlockReader, ResourceRecord
from dahdev.dah1.materials import (
    MATERIAL_TYPE, argb8_mip_chain_size, decode_material,
    decode_swizzled_argb8_top_level, decode_texture_top_level, NativeMaterial,
    TextureBinding, terrain_material_slots, decode_linear_argb_top_level,
    linear_argb_size,
)
from dahdev.dah1.material_evidence import (
    native_material_evidence, terrain_segment_render_state,
    verify_material_executable_evidence,
)


def _material(flags=0, alpha_mode=0, opacity=1.0, shader=0):
    raw = bytearray(140)
    struct.pack_into("<8f", raw, 0x20, 2, 0, 0, -.5, 0, 2, 0, -.5)
    struct.pack_into("<f", raw, 0x68, opacity)
    struct.pack_into("<I", raw, 0x7C, flags)
    struct.pack_into("<I", raw, 0x84, shader)
    binding = bytearray(52)
    struct.pack_into("<I", binding, 0x10, alpha_mode)
    resource = ResourceRecord(0, MATERIAL_TYPE, 0x1234, 0, 0, 140, 0)
    texture = ResourceRecord(1, 0x3BE99284, 0x5678, 0, 140, 8, 0)
    return NativeMaterial(resource, bytes(raw), flags, 108, TextureBinding(
        0, bytes(binding), 8, 1, 4, 4, texture.name_hash, texture,
    ))


class TerrainMaterialEvidenceTests(unittest.TestCase):
    def test_opaque_base_versus_native_alpha_overlay(self):
        material = _material()
        base = terrain_segment_render_state(material, 0x0003)
        overlay = terrain_segment_render_state(material, 0x0103)
        self.assertEqual(base["alpha_mode"], "opaque")
        self.assertTrue(base["depth_write"])
        self.assertEqual(overlay["alpha_mode"], "blend")
        self.assertFalse(overlay["depth_write"])
        self.assertEqual(overlay["alpha_reference_u8"], 1)
        self.assertEqual(overlay["blend_source"], "src_alpha")
        self.assertEqual(overlay["blend_destination"], "one_minus_src_alpha")
        self.assertEqual(overlay["depth_compare"], "less_equal")
        self.assertEqual(overlay["vertex_rgb_scale"], 2)
        self.assertEqual(overlay["vertex_alpha"], "native")
        self.assertFalse(overlay["shade_faces"])
        self.assertEqual(overlay["evidence_status"], "retail-code-verified")

    def test_native_shadow_overrides_ordinary_alpha_blend(self):
        for flags, equation in ((0x100, "add"), (0x3200, "reverse_subtract"), (0x400, "subtract")):
            state = terrain_segment_render_state(_material(flags), 0x0100)
            self.assertEqual(state["blend_equation"], equation)
            self.assertEqual(state["blend_source"], "one")
            self.assertEqual(state["blend_destination"], "one")
            self.assertEqual(state["evidence_status"], "retail-code-verified")
        self.assertEqual(terrain_segment_render_state(_material(0x700), 0x100)["blend_equation"], "add")

    def test_signed_layer_and_mask_reference(self):
        state = terrain_segment_render_state(_material(0x4000, 2, .8), 0xFF02)
        self.assertEqual(state["terrain_layer"], -1)
        self.assertEqual(state["alpha_reference_u8"], 52)
        self.assertTrue(state["double_sided"])
        self.assertEqual(state["alpha_mode"], "blend")

    def test_runtime_uv_reset_is_not_raw_authoring_matrix(self):
        material = _material(0x800000)
        report = native_material_evidence(material)
        self.assertEqual(report["raw_uv_transform"], [2, 0, 0, -.5, 0, 2, 0, -.5])
        self.assertEqual(report["effective_uv_transform"], [1, 0, 0, 0, 0, 1, 0, 0])
        self.assertFalse(report["basic_pixel_material_opacity_multiply"])
        self.assertFalse(report["basic_pixel_material_color_multiply"])
        self.assertIsNone(native_material_evidence(material, "scenery")["effective_uv_transform"])

    def test_unknown_state_is_explicit(self):
        state = terrain_segment_render_state(_material(0x80000, 7, shader=4), 0)
        self.assertEqual(state["evidence_status"], "unresolved-special-path")
        self.assertEqual(len(state["unresolved"]), 3)
        self.assertEqual(state["vertex_rgb_scale"], 1)
        with self.assertRaises(ValueError):
            terrain_segment_render_state(_material(), 65536)

    @unittest.skipUnless(os.environ.get("DAHDEV_RETAIL_XBE"), "retail executable not configured")
    def test_retail_instruction_and_shader_spans(self):
        report = verify_material_executable_evidence(os.environ["DAHDEV_RETAIL_XBE"])
        self.assertEqual(report["status"], "verified")
        self.assertEqual(len(report["checks"]), 14)

    @unittest.skipUnless(os.environ.get("DAHDEV_AREA42_SOURCE"), "retail fixture not configured")
    def test_independent_native_terrain_layer_coverage(self):
        from dahdev.dah1.terrain import read_terrain_scene
        scene = read_terrain_scene(os.environ["DAHDEV_AREA42_SOURCE"])
        layers = Counter()
        triangles = Counter()
        equations = Counter()
        materials = {}
        for placement in scene.placements:
            for lod in placement.children:
                slots = terrain_material_slots(scene.reader, lod)
                for slot in slots:
                    materials[slot.material.resource.name_hash] = slot.material
                for segment in lod.geometry.segments:
                    material = slots[segment.raw_flags & 255].material
                    state = terrain_segment_render_state(material, segment.raw_flags)
                    layer = state["terrain_layer"]
                    layers[layer] += 1
                    triangles[layer] += len(segment.triangles)
                    equations[state["blend_equation"]] += len(segment.triangles)
                    self.assertEqual(state["evidence_status"], "retail-code-verified")
                    alpha = lod.geometry.vertex_bytes[np.unique(segment.triangles), 23]
                    if layer == 0:
                        self.assertTrue((alpha == 255).all())
                    else:
                        self.assertEqual(state["alpha_mode"], "blend")
        self.assertEqual(dict(layers), {0: 994, 1: 270, 2: 106, 3: 92, 4: 14, 5: 4})
        self.assertEqual(dict(triangles), {0: 89750, 1: 9611, 2: 1947, 3: 9269, 4: 995, 5: 182})
        self.assertEqual(equations["reverse_subtract"], 1071)
        self.assertEqual(len(materials), 91)
        self.assertEqual(sum(not np.allclose(native_material_evidence(m)["raw_uv_transform"], [1, 0, 0, 0, 0, 1, 0, 0], atol=1e-6) for m in materials.values()), 8)


class SwizzledArgbTests(unittest.TestCase):
    def test_rectangular_morton_and_bgra_channels(self):
        # Independently enumerated 4x2 Morton order: (0,0),(1,0),(0,1),
        # (1,1),(2,0),(3,0),(2,1),(3,1). Each pixel identifies its source.
        payload = bytes(v for i in range(8) for v in (i, i + 20, i + 40, i + 100))
        rgba = np.asarray(decode_swizzled_argb8_top_level(payload, 4, 2))
        np.testing.assert_array_equal(rgba[:, :, 0], [[40, 41, 44, 45], [42, 43, 46, 47]])
        np.testing.assert_array_equal(rgba[:, :, 2], [[0, 1, 4, 5], [2, 3, 6, 7]])
        np.testing.assert_array_equal(rgba[:, :, 3], [[100, 101, 104, 105], [102, 103, 106, 107]])

    def test_tall_morton_order(self):
        payload = bytes(v for i in range(8) for v in (i, 0, 0, 255))
        rgba = np.asarray(decode_swizzled_argb8_top_level(payload, 2, 4))
        np.testing.assert_array_equal(rgba[:, :, 2], [[0, 1], [2, 3], [4, 5], [6, 7]])

    def test_mip_chain_and_invalid_inputs(self):
        self.assertEqual(argb8_mip_chain_size(64, 64, 5), 21824)
        self.assertEqual(argb8_mip_chain_size(256, 128, 1), 131072)
        for width, height, levels in ((3, 4, 1), (0, 1, 1), (2, 2, 0), (2, 2, 3)):
            with self.assertRaises(ValueError):
                argb8_mip_chain_size(width, height, levels)
        with self.assertRaisesRegex(ValueError, "truncated"):
            decode_swizzled_argb8_top_level(bytes(15), 2, 2)


class LinearArgbTests(unittest.TestCase):
    def test_linear_bgra8_and_argb4444_channel_order(self):
        rgba = np.asarray(decode_linear_argb_top_level(bytes([11, 22, 33, 44]) * 16, 16, 1, 4))
        self.assertEqual(rgba[0, 0].tolist(), [33, 22, 11, 44])
        rgba = np.asarray(decode_linear_argb_top_level(struct.pack("<H", 0xA123) * 32, 32, 1, 5))
        self.assertEqual(rgba[0, 0].tolist(), [17, 34, 51, 170])

    def test_pitch_and_mips_fail_closed(self):
        self.assertEqual(linear_argb_size(128, 480, 1, 2), 122880)
        for args in ((3, 2, 1, 4), (128, 2, 2, 4), (0, 2, 1, 4)):
            with self.assertRaises(ValueError):
                linear_argb_size(*args)
        with self.assertRaisesRegex(ValueError, "truncated"):
            decode_linear_argb_top_level(bytes(1), 16, 1, 4)


@unittest.skipUnless(os.environ.get("DAHDEV_AREA42_SOURCE"), "retail fixture not configured")
class RetailSwizzledArgbTests(unittest.TestCase):
    def test_all_material_texture_formats_resolve_exact_payload_sizes(self):
        reader = BlockReader(os.environ["DAHDEV_AREA42_SOURCE"])
        counts = Counter()
        linear_textures = set()
        for resource in reader.directory.resources:
            if resource.type_hash != MATERIAL_TYPE:
                continue
            material = decode_material(reader, resource)
            if material.binding:
                counts[material.binding.format_word] += 1
                if material.binding.format_word in (4, 5):
                    image = decode_texture_top_level(reader, material.binding)
                    self.assertEqual(image.size, (material.binding.width, material.binding.height))
                    linear_textures.add(material.binding.texture_hash)
        self.assertEqual(dict(counts), {8: 1347, 9: 576, 0: 10, 4: 12, 5: 3})
        self.assertEqual(len(linear_textures), 15)

    def test_all_native_format0_bindings_and_pixel_hashes(self):
        reader = BlockReader(os.environ["DAHDEV_AREA42_SOURCE"])
        bindings = {}
        occurrences = 0
        for resource in reader.directory.resources:
            if resource.type_hash != MATERIAL_TYPE:
                continue
            group = reader.load_group(resource.load_group)
            pointer = group.relocations.pointers.get(resource.offset + 0x6C)
            if pointer is None or group.data[pointer : pointer + 4] != bytes(4):
                continue
            material = decode_material(reader, resource)
            occurrences += 1
            bindings[material.binding.texture_hash] = material.binding
        self.assertEqual(occurrences, 10)
        expected = {
            0x899460B3: "5efbd4026f8772b3c3a25554562ebb71c4925355de4f40efcf36a91e80a8ff46",
            0x302258BD: "b0dbd7a288cfa5dc19a5508ca1aa52b90d31727e042b722bd0036996e8d77c4c",
            0x2E041BF3: "75e4ecda5ffa8d482716c10f15a937770670a7c552f8f8630eee4daad364f7f5",
            0xB5AD66A7: "f6ae93723d52189d697984b337b85335ca44a2af18c8e945c79b5925c8bba5f7",
            0xEB27D855: "089375608b4b67ca84c650ded27adab52b970a91e90fccc46c60c5fc40bc0df2",
        }
        self.assertEqual(set(bindings), set(expected))
        for identity, binding in bindings.items():
            image = decode_texture_top_level(reader, binding)
            self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(), expected[identity])


if __name__ == "__main__":
    unittest.main()
