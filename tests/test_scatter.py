from __future__ import annotations

import os
import struct
import unittest
from types import SimpleNamespace

import numpy as np

from dahdev.dah1.container import ResourceRecord
from dahdev.dah1.materials import MATERIAL_TYPE, NativeMaterial, TextureBinding
from dahdev.dah1.scatter import (
    SCATTER_PAYLOAD_TYPE,
    SCATTER_TYPE,
    ScatterPlacement,
    decode_scatter_batch,
    material_render_state,
    read_scatter_scene,
)
from dahdev.dah1.transforms import transform_points


def synthetic_batch() -> tuple[object, ResourceRecord]:
    """A tiny native eight-copy draw buffer with one independently placed instance."""
    resource = ResourceRecord(0, SCATTER_TYPE, 0x12345678, 0, 0, 0xE4, 0)
    material = ResourceRecord(1, MATERIAL_TYPE, 0x87654321, 0, 0x200, 0x8C, 0)
    data = bytearray(0xC00)
    struct.pack_into("<II", data, 0, SCATTER_PAYLOAD_TYPE, 0xDEADC0DE)
    struct.pack_into("<2fI", data, 0xB8, 30.0, 40.0, 1)
    struct.pack_into("<HH", data, 0xC8, 3, 4)
    struct.pack_into("<f", data, 0x268, 1.0)
    struct.pack_into("<16f", data, 0x300, 0, -2, 0, 7, 2, 0, 0, 8, 0, 0, 2, 9, 0, 0, 0, 1)
    struct.pack_into("<3fI", data, 0x340, .2, .4, .6, 0xDEADBEEF)
    base_vertices = ((0, 0, 0, 0, 0), (1, 0, 0, 1, 0), (0, 1, 0, 0, 1))
    indices = []
    for copy in range(8):
        for index, vertex in enumerate(base_vertices):
            struct.pack_into("<6f", data, 0x500 + (copy * 3 + index) * 24, *vertex, copy * 4)
        if copy:
            indices.extend((copy * 3 - 1, copy * 3))
        indices.extend((copy * 3, copy * 3 + 1, copy * 3 + 2, copy * 3 + 2))
    struct.pack_into(f"<{len(indices)}H", data, 0xB00, *indices)
    group = SimpleNamespace(
        data=data,
        resource_limit=len(data),
        relocations=SimpleNamespace(pointers={0xB4: 0x200, 0xC4: 0x300, 0xCC: 0x500, 0xD0: 0xB00}),
    )
    reader = SimpleNamespace(
        load_group=lambda index: group,
        resource_by_location=lambda group, offset: material if offset == 0x200 else None,
    )
    return reader, resource


class ScatterDecoderTests(unittest.TestCase):
    def test_draw_copies_do_not_create_extra_world_instances(self) -> None:
        reader, resource = synthetic_batch()
        batch = decode_scatter_batch(reader, resource)
        self.assertEqual(len(batch.instances), 1)
        self.assertEqual(batch.template.local_positions.shape, (3, 3))
        self.assertEqual(batch.template.triangles.tolist(), [[0, 1, 2]])
        self.assertEqual(len(batch.template.native_draw_vertex_bytes), 8 * 3 * 24)
        self.assertEqual(len(batch.template.native_draw_index_bytes), (8 * 6 - 2) * 2)
        self.assertEqual(batch.instances[0].unknown_word, 0xDEADBEEF)
        np.testing.assert_allclose(batch.instances[0].color_rgb, (.2, .4, .6))

    def test_column_matrix_is_transposed_before_scene_composition(self) -> None:
        reader, resource = synthetic_batch()
        batch = decode_scatter_batch(reader, resource)
        parent = np.eye(4, dtype=np.float32)
        parent[3, :3] = (100, 200, 300)
        placement = ScatterPlacement(
            0, 0, bytes(64), None, None, parent[3, :3],
            np.asarray((0, 0, 0, 1), dtype=np.float32), np.float32(1), 0, parent, batch,
        )
        # The source column matrix rotates (1,0,0) to (0,2,0), then translates.
        result = transform_points(np.asarray(((1, 0, 0),), dtype=np.float32), placement.world_matrix(batch.instances[0]))
        np.testing.assert_array_equal(result, ((107, 210, 309),))

    def test_rejects_incorrect_native_copy_and_index_join(self) -> None:
        reader, resource = synthetic_batch()
        struct.pack_into("<f", reader.load_group(0).data, 0x500 + 3 * 24 + 20, 8)
        with self.assertRaisesRegex(ValueError, "constant selector"):
            decode_scatter_batch(reader, resource)
        reader, resource = synthetic_batch()
        struct.pack_into("<H", reader.load_group(0).data, 0xB00 + 4 * 2, 0)
        with self.assertRaisesRegex(ValueError, "degenerate joins"):
            decode_scatter_batch(reader, resource)

    def test_rejects_invalid_affine_matrix(self) -> None:
        reader, resource = synthetic_batch()
        struct.pack_into("<f", reader.load_group(0).data, 0x330, 1)
        with self.assertRaisesRegex(ValueError, "affine transform"):
            decode_scatter_batch(reader, resource)

    def test_trailing_word_is_preserved_even_when_float_interpretation_is_nan(self) -> None:
        reader, resource = synthetic_batch()
        struct.pack_into("<I", reader.load_group(0).data, 0x34C, 0xFFFFFFFF)
        batch = decode_scatter_batch(reader, resource)
        self.assertEqual(batch.instances[0].unknown_word, 0xFFFFFFFF)
        self.assertEqual(batch.instances[0].raw_bytes[-4:], b"\xff" * 4)

    def test_rejects_missing_material_relocation(self) -> None:
        reader, resource = synthetic_batch()
        del reader.load_group(0).relocations.pointers[0xB4]
        with self.assertRaisesRegex(ValueError, "material pointer"):
            decode_scatter_batch(reader, resource)


class ScatterMaterialTests(unittest.TestCase):
    def material(self, mode: int, opacity: float = 1, flags: int = 0x7000) -> NativeMaterial:
        raw = bytearray(0x8C)
        struct.pack_into("<f", raw, 0x68, opacity)
        binding_raw = bytearray(52)
        struct.pack_into("<I", binding_raw, 0x10, mode)
        resource = ResourceRecord(0, MATERIAL_TYPE, 1, 0, 0, len(raw), 0)
        binding = TextureBinding(0, bytes(binding_raw), 9, 1, 128, 128, 2, resource)
        return NativeMaterial(resource, bytes(raw), flags, 0x6C, binding)

    def test_mask_threshold_comes_from_native_opacity(self) -> None:
        state = material_render_state(self.material(2, .8))
        self.assertEqual(state["alpha_mode"], "mask")
        self.assertEqual(state["alpha_reference_u8"], 52)
        self.assertEqual(state["alpha_compare"], "greater")
        self.assertTrue(state["depth_write"])

    def test_double_sided_does_not_imply_alpha_mask(self) -> None:
        state = material_render_state(self.material(0))
        self.assertEqual(state["alpha_mode"], "opaque")
        self.assertTrue(state["double_sided"])

    def test_blended_foliage_has_native_blend_and_depth_state(self) -> None:
        state = material_render_state(self.material(3))
        self.assertEqual(state["alpha_mode"], "blend")
        self.assertEqual(state["alpha_reference_u8"], 1)
        self.assertEqual(state["blend_source"], "src_alpha")
        self.assertEqual(state["blend_destination"], "one_minus_src_alpha")
        self.assertFalse(state["depth_write"])


@unittest.skipUnless(os.environ.get("DAHDEV_AREA42_SOURCE"), "set DAHDEV_AREA42_SOURCE to the read-only retail fixture")
class Area42ScatterGoldenTests(unittest.TestCase):
    def test_complete_scene_and_independent_native_draw_repetitions(self) -> None:
        scene = read_scatter_scene(os.environ["DAHDEV_AREA42_SOURCE"])
        self.assertEqual((len(scene.batches), len(scene.placements), scene.instance_count), (654, 654, 15121))
        self.assertEqual(
            sorted((len(t.local_positions), len(t.indices), len(t.triangles)) for t in scene.templates),
            [(12, 16, 6), (14, 18, 8), (18, 24, 10), (21, 32, 14), (36, 80, 35)],
        )
        self.assertEqual(sum(len(p.batch.instances) * len(p.batch.template.triangles) for p in scene.placements), 240610)
        self.assertEqual({b.material.binding.texture_hash for b in scene.batches}, {0xF1778599, 0xB45CD522, 0x45D79D83, 0x6F4B7DB5, 0xFD51B2AF})


if __name__ == "__main__":
    unittest.main()
