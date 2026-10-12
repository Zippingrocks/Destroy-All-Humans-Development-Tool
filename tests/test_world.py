from __future__ import annotations

import unittest
import os
import struct
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dahdev.dah1.world import (
    SEGMENTED_VISUAL_TYPE, STATIC_VISUAL_TYPE, _default_static_visual,
    classify_editor_collection,
    _semantic_actor_strings,
    decode_packed_yaw_quaternion,
    decode_packed_quaternion,
    instance_list_records,
    read_world_scene,
)
from dahdev.dah1.container import ResourceRecord


class PackedWorldRotationTests(unittest.TestCase):
    def test_preserves_prop_pitch_and_roll(self) -> None:
        raw = struct.pack("<4h", 1234, -5678, 90, 30000)
        xy, zw = struct.unpack("<II", raw)
        np.testing.assert_array_equal(decode_packed_quaternion(xy, zw),
                                      np.asarray([1234, -5678, 90, 30000], dtype=np.float32) / np.float32(32767))
    def test_decodes_identity_and_half_turn(self) -> None:
        np.testing.assert_array_equal(
            decode_packed_yaw_quaternion(0x80000000),
            np.asarray((0.0, 0.0, 0.0, -1.0), dtype=np.float32),
        )
        np.testing.assert_array_equal(
            decode_packed_yaw_quaternion(0x00008000),
            np.asarray((0.0, 0.0, -1.0, 0.0), dtype=np.float32),
        )

    def test_decodes_area42_quarter_turn_pair(self) -> None:
        value = decode_packed_yaw_quaternion(0x5AD1A5D1)
        self.assertAlmostEqual(float(value[2]), -0.70458, places=4)
        self.assertAlmostEqual(float(value[3]), 0.70952, places=4)


class LegacyVisualSafetyTests(unittest.TestCase):
    def test_segmented_visual_cannot_silently_become_first_component(self):
        resource = ResourceRecord(0, SEGMENTED_VISUAL_TYPE, 123, 0, 0, 0xD0, 0)
        # No data needs to be read to know that this legacy return type would
        # discard the attachment graph, even if the visual has only one part.
        with self.assertRaisesRegex(ValueError, "actor_visuals.read_actor_visuals"):
            _default_static_visual(SimpleNamespace(), resource, {})

    def test_proven_simple_static_preview_remains_supported(self):
        resource = ResourceRecord(0, STATIC_VISUAL_TYPE, 123, 0, 0, 0xF8, 0)
        group = SimpleNamespace(relocations=SimpleNamespace(pointers={0xE8: 500}))
        reader = SimpleNamespace(load_group=lambda _: group)
        visual = object()
        cache = {}
        with patch("dahdev.dah1.world._decode_static_visual", return_value=visual) as decode:
            self.assertIs(_default_static_visual(reader, resource, cache), visual)
            decode.assert_called_once_with(reader, resource, cache)


class EditorCollectionTests(unittest.TestCase):
    def test_pickup_names_are_native_objects_not_unnamed_catalog_entries(self):
        self.assertEqual(_semantic_actor_strings(b"\0PowerupType\0pu_probe\0"),
                         ("PowerupType", "pu_probe"))
        self.assertEqual(_semantic_actor_strings(b"\0PowerupType\0pu_ufo_debris\0"),
                         ("PowerupType", "pu_ufo_debris"))
        self.assertEqual(classify_editor_collection("PowerupType", "pu_probe"), "pickups")
        self.assertEqual(classify_editor_collection("PowerupType", None), "pickups")

    def test_preserves_native_classification_as_editor_grouping(self) -> None:
        self.assertEqual(
            classify_editor_collection("WorldObjectType", "b_sml_barracks_small"),
            "buildings",
        )
        self.assertEqual(
            classify_editor_collection("ActorTypeEmplacement", "e_guard_tower"),
            "buildings",
        )
        self.assertEqual(
            classify_editor_collection("ActorTypePedestrian", "h_male_soldier_rifle"),
            "humans",
        )
        self.assertEqual(
            classify_editor_collection("ActorTypeCarDrive", "v_tank"),
            "vehicles",
        )
        self.assertEqual(classify_editor_collection("PropType", "p_ufo_crate"), "props")

    def test_native_random_and_pedestrian_types_do_not_imply_humans(self) -> None:
        self.assertEqual(
            classify_editor_collection("RandomType", "h_random_ambience_area42"),
            "population",
        )
        self.assertEqual(
            classify_editor_collection("ActorTypePedestrian", "a_cow"), "animals"
        )
        self.assertEqual(
            classify_editor_collection("ActorTypePedestrian", "r_powersuit"), "gameplay"
        )


class InstanceListTests(unittest.TestCase):
    def fixture(self):
        data = bytearray(512)
        struct.pack_into("<I", data, 32, 1)
        struct.pack_into("<I", data, 48, 2)
        return data, {12: 32, 16: 48, 36: 128, 52: 192, 56: 256}

    def test_both_lists_are_traversed_even_if_one_is_inline(self):
        data, relocations = self.fixture()
        self.assertEqual(instance_list_records(data, relocations, 0, len(data)),
                         ((12, 32, 0, 128), (16, 48, 0, 192), (16, 48, 1, 256)))

    def test_second_list_only_is_supported(self):
        data, relocations = self.fixture()
        del relocations[12]
        self.assertEqual(len(instance_list_records(data, relocations, 0, len(data))), 2)

    def test_missing_pointer_and_duplicate_record_fail(self):
        for bad in ({12: 32, 16: 48, 36: 128, 52: 192},
                    {12: 32, 16: 48, 36: 128, 52: 128, 56: 256}):
            with self.assertRaises(ValueError):
                instance_list_records(self.fixture()[0], bad, 0, 512)

    def test_count_cannot_escape_group(self):
        data, relocations = self.fixture()
        struct.pack_into("<I", data, 48, 999)
        with self.assertRaisesRegex(ValueError, "truncated"):
            instance_list_records(data, relocations, 0, len(data))


@unittest.skipUnless(os.environ.get("DAH_AREA42_SOURCE"), "retail fixture not configured")
class CompleteWorldFixtureTests(unittest.TestCase):
    def test_every_counted_list_and_named_missing_setpiece(self):
        world = read_world_scene(os.environ["DAH_AREA42_SOURCE"])
        self.assertTrue(all(not actor.lods for actor in world.archetypes.values()))
        self.assertEqual(len(world.buckets), 41)
        self.assertEqual(len(world.placements), 2674)
        self.assertEqual(len({p.offset for p in world.placements}), 2674)
        self.assertEqual(Counter(p.list_pointer_field for p in world.placements), {12: 808, 16: 1866})
        names = Counter(p.actor.asset_name for p in world.placements if p.actor)
        for name, expected in {"e_teslacoil": 4, "p_crypto_cage": 2, "p_ufo_remains_01": 1,
                               "p_ufo_remains_02": 1, "p_ufo_remains_03": 1,
                               "p_autopsy_table_crypto": 1, "r_powersuit": 6,
                               "b_sml_hanger": 3, "b_sml_majestic_hq_01": 1}.items():
            self.assertEqual(names[name], expected, name)
        self.assertTrue(any(p.packed_rotation_xy for p in world.placements))

    def test_explicit_legacy_preview_rejects_segmented_retail_actor(self):
        with self.assertRaisesRegex(ValueError, "actor_visuals.read_actor_visuals"):
            read_world_scene(os.environ["DAH_AREA42_SOURCE"], include_visuals=True)


if __name__ == "__main__":
    unittest.main()
