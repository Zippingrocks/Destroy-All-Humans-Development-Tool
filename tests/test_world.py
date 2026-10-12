from __future__ import annotations

import unittest

import numpy as np

from dahdev.dah1.world import (
    classify_editor_collection,
    decode_packed_yaw_quaternion,
)


class PackedWorldRotationTests(unittest.TestCase):
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


class EditorCollectionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
