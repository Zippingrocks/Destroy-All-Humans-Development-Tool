from __future__ import annotations

import hashlib
import os
import struct
import unittest
from collections import Counter
from dataclasses import replace

import numpy as np

from dahdev.dah1.container import BlockReader, ResourceRecord
from dahdev.dah1.materials import (
    MATERIAL_TYPE, argb8_mip_chain_size, decode_material,
    decode_swizzled_argb8_top_level, decode_texture_top_level, NativeMaterial,
    TextureBinding, terrain_material_slots, decode_linear_argb_top_level,
    linear_argb_size,
)
from dahdev.dah1.material_evidence import (
    actor_material_render_state, native_material_evidence, native_sampler_state, terrain_segment_render_state,
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
        self.assertEqual(state["vertex_rgb_scale"], 2)
        with self.assertRaises(ValueError):
            terrain_segment_render_state(_material(), 65536)

    @unittest.skipUnless(os.environ.get("DAHDEV_RETAIL_XBE"), "retail executable not configured")
    def test_retail_instruction_and_shader_spans(self):
        report = verify_material_executable_evidence(os.environ["DAHDEV_RETAIL_XBE"])
        self.assertEqual(report["status"], "verified")
        self.assertEqual(len(report["checks"]), 36)

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


class ActorMaterialEvidenceTests(unittest.TestCase):
    def test_opaque_still_uses_native_alpha_test(self):
        for native_mode in (1, 4):
            state = actor_material_render_state(_material(), default_draw_mode=native_mode)
            self.assertEqual(state["native_draw_mode"], native_mode)
            self.assertEqual(state["alpha_mode"], "opaque")
            self.assertTrue(state["alpha_test_enabled"])
            self.assertEqual(state["alpha_reference_u8"], 64)
            self.assertEqual(state["alpha_compare"], "greater")
            self.assertTrue(state["depth_write"])
            self.assertEqual(state["depth_compare"], "less_equal")
            self.assertEqual(state["evidence_status"], "retail-code-verified")
            self.assertTrue(state["visual_unresolved"])

    def test_glow_flags_force_additive_even_without_texture_alpha(self):
        state = actor_material_render_state(_material(flags=0x4100))
        self.assertEqual(state["native_draw_mode"], 6)
        self.assertEqual(state["alpha_mode"], "blend")
        self.assertEqual(state["alpha_reference_u8"], 1)
        self.assertEqual(state["blend_source"], "one")
        self.assertEqual(state["blend_destination"], "one")
        self.assertEqual(state["blend_equation"], "add")
        self.assertFalse(state["depth_write"])
        self.assertTrue(state["double_sided"])
        self.assertFalse(state["native_cull_enabled"])

    def test_subtract_priority_and_texture_alpha_suppression(self):
        for flags, equation in ((0x700, "add"), (0x600, "reverse_subtract"), (0x400, "subtract")):
            state = actor_material_render_state(_material(flags=flags))
            self.assertEqual(state["blend_equation"], equation)
            self.assertEqual(state["blend_source"], "one")
            self.assertEqual(state["blend_destination"], "one")
        transparent = actor_material_render_state(_material(alpha_mode=3))
        self.assertEqual(transparent["blend_source"], "src_alpha")
        self.assertEqual(transparent["blend_destination"], "one_minus_src_alpha")
        suppressed = actor_material_render_state(_material(flags=0x40000, alpha_mode=3))
        self.assertEqual(suppressed["alpha_mode"], "opaque")

    def test_masked_opacity_threshold_is_not_ordinary_transparency(self):
        state = actor_material_render_state(_material(alpha_mode=2, opacity=.8))
        self.assertEqual(state["alpha_mode"], "mask")
        self.assertEqual(state["alpha_reference_u8"], 52)
        self.assertTrue(state["depth_write"])
        glow = actor_material_render_state(_material(flags=0x100, alpha_mode=2, opacity=.8))
        self.assertEqual(glow["alpha_mode"], "blend")
        self.assertEqual(glow["alpha_reference_u8"], 52)
        ordinary = actor_material_render_state(_material(opacity=.8))
        self.assertEqual(ordinary["alpha_mode"], "blend")

    def test_per_draw_fade_is_independent_from_material_opacity(self):
        state = actor_material_render_state(_material(alpha_mode=2), draw_opacity=.5)
        self.assertEqual(state["native_draw_mode"], 6)
        self.assertEqual(state["native_draw_opacity"], .5)
        self.assertEqual(state["native_opacity"], 1)
        self.assertEqual(state["material_opacity"], 1)
        self.assertEqual(state["alpha_reference_u8"], 1)
        self.assertFalse(state["depth_write"])
        self.assertEqual(state["evidence_status"], "unresolved-special-path")
        self.assertTrue(any("draw flags" in reason for reason in state["unresolved"]))

    def test_culling_is_not_assumed_disabled_for_static_actors(self):
        state = actor_material_render_state(_material())
        self.assertTrue(state["native_cull_enabled"])
        self.assertEqual(state["native_cull_winding"], "per-draw-orientation")
        self.assertTrue(state["native_draw_enabled"])
        self.assertFalse(actor_material_render_state(_material(flags=0x80))["native_draw_enabled"])

    def test_raster_evidence_does_not_claim_shader_or_lighting_parity(self):
        state = actor_material_render_state(_material(flags=0x14000, shader=3))
        self.assertEqual(state["evidence_status"], "retail-code-verified")
        self.assertFalse(state["unresolved"])
        self.assertTrue(any("secondary reflection" in value for value in state["visual_unresolved"]))
        self.assertEqual(state["native_base_rgb_scale"], 2)
        self.assertEqual(state["native_base_alpha"], "texture0.a * vertex.a")
        shader4 = actor_material_render_state(_material(shader=4))
        self.assertEqual(shader4["native_base_rgb_scale"], 2)
        self.assertEqual(shader4["native_base_alpha"], "vertex.a")
        special = native_material_evidence(_material(shader=4), "actor")
        self.assertEqual(special["basic_pixel_rgb"], "clamp(2 * texture0.rgb * vertex.rgb, 0, 1)")
        self.assertEqual(special["basic_pixel_alpha"], "vertex.a")
        self.assertIn("texture0.a", special["secondary_pixel_term"])
        evidence = native_material_evidence(_material(opacity=.5), "actor")
        self.assertEqual(evidence["effective_uv_transform"], evidence["raw_uv_transform"])
        self.assertEqual(evidence["uv_transform_status"], "retail-initial-material-load-preserved")
        self.assertEqual(evidence["rigid_vertex_color_evidence"]["alpha"], "material.opacity")
        self.assertIn("material.additive", evidence["rigid_vertex_color_evidence"]["rgb"])
        self.assertFalse(evidence["basic_pixel_material_opacity_multiply"])

    def test_only_proven_initial_uv_contracts_are_applied(self):
        for kind in ("actor", "sky", "static-world-object", "human-reference-pose"):
            evidence = native_material_evidence(_material(), kind)
            self.assertEqual(evidence["effective_uv_transform"], [2, 0, 0, -.5, 0, 2, 0, -.5])
            self.assertEqual(evidence["rigid_vertex_color_evidence"]["null_tint_multiplier_c20"], [1, 1, 1, 1])
            self.assertEqual(evidence["rigid_vertex_color_evidence"]["null_tint_additive_c19"], [0, 0, 0, 0])
            self.assertTrue(any("animation" in reason for reason in evidence["unresolved"]))
        self.assertIsNone(native_material_evidence(_material(), "unknown") ["effective_uv_transform"])

    def test_native_sampler_preserves_mip_bias_and_authored_chain(self):
        material = _material(flags=0x3000)
        raw = bytearray(material.binding.raw_bytes)
        struct.pack_into("<f", raw, 0x24, -1.0)
        material = replace(material, binding=replace(material.binding, mip_levels=3, raw_bytes=bytes(raw)))
        state = native_sampler_state(material)
        self.assertEqual(state["min_filter"], "linear")
        self.assertEqual(state["mag_filter"], "linear")
        self.assertEqual(state["mip_filter"], "linear")
        self.assertEqual(state["lod_bias"], -1)
        self.assertEqual(state["address_u"], "clamp")
        self.assertEqual(state["address_v"], "clamp")
        self.assertEqual(state["max_level"], 2)
        self.assertFalse(state["generate_mipmaps"])
        special = native_sampler_state(replace(material, render_flags=0x100000))
        self.assertEqual(special["mag_filter"], "nearest")
        self.assertEqual(special["min_filter"], "linear")
        self.assertEqual(special["mip_filter"], "none")
        self.assertEqual(native_sampler_state(_material())["mip_filter"], "none")

    def test_sampler_binding_bounds_and_nonfinite_bias_fail_closed(self):
        material = _material()
        with self.assertRaisesRegex(ValueError, "LOD bias"):
            native_sampler_state(replace(material, binding=replace(material.binding, raw_bytes=bytes(16))))
        raw = bytearray(material.binding.raw_bytes)
        struct.pack_into("<f", raw, 0x24, float("nan"))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            native_sampler_state(replace(material, binding=replace(material.binding, raw_bytes=bytes(raw))))

    def test_special_routes_fail_closed_and_irrelevant_flag_does_not(self):
        for flags, expected_mode in ((0x80000, 8), (0x900, 0)):
            state = actor_material_render_state(_material(flags=flags))
            self.assertEqual(state["native_draw_mode"], expected_mode)
            self.assertEqual(state["evidence_status"], "unresolved-special-path")
        state = actor_material_render_state(_material(flags=0x800))
        self.assertEqual(state["native_draw_mode"], 1)
        self.assertEqual(state["evidence_status"], "retail-code-verified")
        self.assertEqual(actor_material_render_state(_material(alpha_mode=7))["evidence_status"], "unresolved-special-path")

    def test_invalid_inputs_and_mask_reference_bounds(self):
        for opacity in (float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "non-finite"):
                actor_material_render_state(_material(opacity=opacity))
            with self.assertRaisesRegex(ValueError, "non-finite"):
                actor_material_render_state(_material(), draw_opacity=opacity)
        with self.assertRaisesRegex(ValueError, "default_draw_mode"):
            actor_material_render_state(_material(), default_draw_mode=0)
        with self.assertRaisesRegex(ValueError, "native actor fields"):
            actor_material_render_state(replace(_material(), raw_bytes=bytes(4)))
        for opacity, reference in ((-.5, 255), (2, 0), (1e20, 0)):
            self.assertEqual(actor_material_render_state(_material(alpha_mode=2, opacity=opacity))["alpha_reference_u8"], reference)

    @unittest.skipUnless(os.environ.get("DAHDEV_AREA42_SOURCE"), "retail fixture not configured")
    def test_authentic_autopsy_cage_and_tesla_glow_materials(self):
        reader = BlockReader(os.environ["DAHDEV_AREA42_SOURCE"])
        expected = {0x1E6FDAFA: "autopsy", 0xE60448A1: "cage", 0xF8241AC7: "tesla"}
        found = set()
        for resource in reader.directory.resources:
            if resource.type_hash != MATERIAL_TYPE or resource.name_hash not in expected:
                continue
            material = decode_material(reader, resource)
            state = actor_material_render_state(material)
            found.add(resource.name_hash)
            self.assertEqual(material.render_flags, 0x4100)
            self.assertEqual(state["native_texture_alpha_mode"], 0)
            self.assertEqual(state["native_opacity"], 1)
            self.assertEqual(state["native_pixel_shader_index"], 0)
            self.assertEqual(state["blend_source"], "one")
            self.assertEqual(state["blend_destination"], "one")
            self.assertEqual(state["blend_equation"], "add")
            self.assertFalse(state["depth_write"])
            self.assertTrue(state["double_sided"])
            self.assertEqual(state["evidence_status"], "retail-code-verified")
        self.assertEqual(found, set(expected))


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
