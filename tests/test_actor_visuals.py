from __future__ import annotations

import os
import struct
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dahdev.dah1.actor_visuals import (
    decode_actor_visual_lods, decode_segment_skeleton, read_actor_visuals,
    skeleton_reference_matrices,
)
from dahdev.dah1.container import ResourceRecord
from dahdev.dah1.humans import read_humans
from dahdev.dah1.transforms import transform_points
from dahdev.dah1.world import (
    LOD_TYPE, SEGMENTED_VISUAL_TYPE, STATIC_VISUAL_TYPE, read_world_scene,
)


class FakeReader:
    def __init__(self, data, pointers, resources):
        self.group = SimpleNamespace(data=bytes(data), resource_limit=len(data),
                                     relocations=SimpleNamespace(pointers=pointers))
        self.resources = {r.offset: r for r in resources}

    def load_group(self, index):
        return self.group

    def resource_by_location(self, group, offset):
        return self.resources.get(offset)


def two_lod_segments(*, bad_bone=False, cycle=False, duplicate=False):
    data = bytearray(0xC00)
    lod = ResourceRecord(0, LOD_TYPE, 100, 0, 0, 0xCC, 0)
    high = ResourceRecord(1, SEGMENTED_VISUAL_TYPE, 101, 0, 0x100, 0xD0, 0)
    low = ResourceRecord(2, SEGMENTED_VISUAL_TYPE, 102, 0, 0x200, 0xD0, 0)
    visual = ResourceRecord(3, STATIC_VISUAL_TYPE, 103, 0, 0x300, 0xF8, 0)
    pointers = {0xC0: 0x400, 0x400: 0x100, 0x404: 0x200,
                0x1C0: 0x500, 0x508: 0x600, 0x530: 0xA00,
                0x1C4: 0x700, 0x2C4: 0x720,
                0x704: 0x300, 0x70C: 0x300, 0x724: 0x300}
    struct.pack_into("<I", data, 0xC8, 2)
    struct.pack_into("<H", data, 0x50C, 3)
    struct.pack_into("<I", data, 0x534, 1)
    struct.pack_into("<I", data, 0x1C8, 2)
    struct.pack_into("<I", data, 0x2C8, 1)
    struct.pack_into("<I", data, 0x700, 1)
    struct.pack_into("<I", data, 0x708, 3 if bad_bone else (1 if duplicate else 2))
    struct.pack_into("<I", data, 0x720, 2)
    # Root rotates +90 around Z and scales by two. Both children instance the
    # same source mesh but attach at different local positions and retain bytes.
    z = np.float32(np.sqrt(0.5))
    for index, pos in enumerate(((10, 0, 0), (1, 0, 0), (-1, 0, 0))):
        offset = 0x600 + index * 0x38
        parent = (11 if cycle else 0) if index == 0 else 10
        struct.pack_into("<4I", data, offset + 4, 10 + index, parent, 11 if index == 0 else 0, 12 if index == 1 else 0)
        q = (0, 0, z, z) if index == 0 else (0, 0, 0, 1)
        struct.pack_into("<8f", data, offset + 0x14, *pos, *q, 2 if index == 0 else 1)
        struct.pack_into("<I", data, offset + 0x34, 0x80000000 | index)
    return FakeReader(data, pointers, (lod, high, low, visual)), lod, high


class ActorVisualTests(unittest.TestCase):
    def test_skeleton_transform_is_local_then_parent_and_preserves_raw(self):
        reader, _, visual = two_lod_segments()
        skeleton = decode_segment_skeleton(reader, visual)
        matrices = skeleton_reference_matrices(skeleton)
        np.testing.assert_allclose(matrices[1][3, :3], (10, 2, 0), atol=1e-6)
        np.testing.assert_allclose(matrices[2][3, :3], (10, -2, 0), atol=1e-6)
        np.testing.assert_allclose(transform_points(np.array([[1, 0, 0]], dtype=np.float32), matrices[1]), [[10, 4, 0]], atol=1e-6)
        self.assertEqual(skeleton.raw_shared_header, reader.group.data[0x500:0x53C])
        self.assertEqual(skeleton.bones[2].raw_bytes, reader.group.data[0x670:0x6A8])
        self.assertEqual(struct.unpack_from("<I", skeleton.bones[2].raw_bytes, 0x34)[0], 0x80000002)

    def test_skeleton_cycle_rejected(self):
        reader, _, visual = two_lod_segments(cycle=True)
        with self.assertRaisesRegex(ValueError, "cycle"):
            decode_segment_skeleton(reader, visual)

    def test_all_components_and_lower_lod_inherit_shared_skeleton(self):
        reader, lod, _ = two_lod_segments()
        calls = []

        def decode(reader, resource, cache, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(resource=resource)

        with patch("dahdev.dah1.actor_visuals._materials", return_value=("material",)), \
             patch("dahdev.dah1.actor_visuals._decode_static_visual", side_effect=decode):
            lods = decode_actor_visual_lods(reader, lod)
        self.assertEqual([len(l.parts) for l in lods], [2, 1])
        self.assertEqual([p.bone_index for p in lods[0].parts], [1, 2])
        self.assertEqual(lods[0].parts[0].visual.resource, lods[0].parts[1].visual.resource)
        self.assertIs(lods[0].skeleton, lods[1].skeleton)
        self.assertEqual(lods[0].parts[1].raw_table_record, reader.group.data[0x708:0x710])
        self.assertTrue(all(c["material_override"] == ("material",) for c in calls))
        self.assertTrue(all("destruction_state" not in c for c in calls))

    def test_bad_or_duplicate_attachment_bone_rejected(self):
        for option in ("bad_bone", "duplicate"):
            reader, lod, _ = two_lod_segments(**{option: True})
            with patch("dahdev.dah1.actor_visuals._materials", return_value=("material",)), \
                 patch("dahdev.dah1.actor_visuals._decode_static_visual", return_value=SimpleNamespace()):
                with self.assertRaisesRegex(ValueError, "bone index"):
                    decode_actor_visual_lods(reader, lod)

    def test_component_requires_relocation_not_raw_pointer_value(self):
        reader, lod, _ = two_lod_segments()
        del reader.group.relocations.pointers[0x70C]
        with patch("dahdev.dah1.actor_visuals._materials", return_value=("material",)), \
             patch("dahdev.dah1.actor_visuals._decode_static_visual", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, "resolve to a static"):
                decode_actor_visual_lods(reader, lod)

    def test_actor_variant_selects_native_material_list_not_default(self):
        reader, lod, _ = two_lod_segments()
        data = bytearray(reader.group.data)
        pointers = reader.group.relocations.pointers
        pointers.update({0xB4: 0x800, 0x804: 0x840, 0x80C: 0x850, 0x840: 0x880, 0x850: 0x890})
        struct.pack_into("<I", data, 0xB8, 2)
        struct.pack_into("<I", data, 0x800, 0xAAAA)
        struct.pack_into("<I", data, 0x808, 0xBBBB)
        struct.pack_into("<I", data, 0x844, 1)
        struct.pack_into("<I", data, 0x854, 1)
        reader.group.data = bytes(data)

        def materials(reader, group, table, count):
            return (f"material-at-{table:X}",)

        with patch("dahdev.dah1.actor_visuals._materials", side_effect=materials), \
             patch("dahdev.dah1.actor_visuals._decode_static_visual", return_value=SimpleNamespace()) as decode:
            lods = decode_actor_visual_lods(reader, lod, variant_hash=0xBBBB)
            self.assertTrue(all(c.kwargs["material_override"] == ("material-at-890",) for c in decode.call_args_list))
            self.assertEqual(lods[0].selected_variant_offset, 0x850)
            self.assertEqual(lods[1].variant_table_offset, 0x800)
            with self.assertRaisesRegex(ValueError, "absent or ambiguous"):
                decode_actor_visual_lods(reader, lod, variant_hash=0xCCCC)


@unittest.skipUnless(os.environ.get("DAH_AREA42_SOURCE"), "set DAH_AREA42_SOURCE for retail fixture")
class RetailActorVisualTests(unittest.TestCase):
    def test_every_placed_family_is_accounted_for_without_inventing_spawns(self):
        world = read_world_scene(os.environ["DAH_AREA42_SOURCE"], include_visuals=False)
        scene = read_actor_visuals(world)
        self.assertEqual(len(world.placements), 2674)
        self.assertEqual(len(scene.inventory), 150)
        self.assertEqual(Counter(i.status for i in scene.inventory),
                         {"decoded": 132, "pedestrian-skin-reader": 15, "no-resolved-primary-visual": 3})
        self.assertEqual(len(scene.placements), 2164)
        humans = read_humans(world)
        self.assertEqual(len(humans.pedestrian_placements), 371)
        unresolved = [i for i in scene.inventory if i.status == "no-resolved-primary-visual"]
        self.assertEqual({i.actor.native_class for i in unresolved}, {"RandomType"})
        self.assertEqual(sum(i.placement_count for i in unresolved), 139)
        self.assertEqual(len(scene.placements) + len(humans.pedestrian_placements) + 139, len(world.placements))
        by_name = {a.actor.asset_name: a for a in scene.archetypes.values()}
        for name in ("p_ufo_remains_01", "p_ufo_remains_02", "p_ufo_remains_03", "p_crypto_cage",
                     "p_autopsy_table_crypto", "e_teslacoil", "p_experimental_ufo", "p_roboprez_foot"):
            self.assertIn(name, by_name)
        self.assertEqual([len(l.parts) for l in by_name["v_tank"].lods], [6, 5, 4])
        self.assertEqual([len(l.parts) for l in by_name["v_truck_army_drive"].lods], [13, 7, 6])
        self.assertEqual([len(l.parts) for l in by_name["v_car_gman_drive_easy"].lods], [17, 9, 1])
        self.assertEqual([len(l.parts) for l in by_name["e_guard_tower"].lods], [3, 1])
        self.assertEqual(len(by_name["p_force_field_fence_doorway_small"].lods[0].parts), 9)
        truck = by_name["v_truck_army_drive"].lods[0]
        by_bone = {p.bone_index: p for p in truck.parts}
        self.assertEqual(by_bone[2].visual.mesh.resource, by_bone[10].visual.mesh.resource)
        self.assertFalse(np.array_equal(by_bone[2].matrix, by_bone[10].matrix))
        np.testing.assert_allclose(by_bone[2].matrix[3, :3], (-1.240358, -3.997813, 0.682489), atol=1e-5)
        np.testing.assert_allclose(by_bone[10].matrix[3, :3], (1.202298, -3.997813, 0.682489), atol=1e-5)
        # Native mesh bytes were not baked/transformed to achieve completeness.
        mesh = by_bone[2].visual.mesh
        group = world.reader.load_group(mesh.resource.load_group)
        self.assertEqual(mesh.vertex_bytes.tobytes(), group.data[mesh.vertex_buffer_offset:mesh.vertex_buffer_offset + mesh.vertex_bytes.size])
        self.assertTrue(all(p.visual.destruction_state is None for a in scene.archetypes.values() for lod in a.lods for p in lod.parts))
        self.assertTrue(all(np.isfinite(p.matrix).all() for a in scene.archetypes.values() for lod in a.lods for p in lod.parts))


if __name__ == "__main__":
    unittest.main()
