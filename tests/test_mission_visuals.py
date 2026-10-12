from __future__ import annotations

import os
import struct
import unittest
from types import SimpleNamespace

import numpy as np

from dahdev.dah1.container import BlockReader, ResourceRecord
from dahdev.dah1.mission_visuals import (
    MARKER_CLASS, decode_marker_lod, read_landing_visual_scene,
    verify_landing_executable_evidence,
)
from dahdev.dah1.world import ACTOR_ARCHETYPE_TYPE, LOD_TYPE


class MarkerReferenceTests(unittest.TestCase):
    def fixture(self, count=1, native_class=MARKER_CLASS, target=True):
        data = bytearray(512)
        struct.pack_into("<I", data, 0, native_class)
        struct.pack_into("<I", data, 128, count)
        pointers = {0x18: 128}
        if target:
            pointers[132] = 256
        actor = ResourceRecord(0, ACTOR_ARCHETYPE_TYPE, 1, 0, 0, 128, 0)
        lod = ResourceRecord(1, LOD_TYPE, 2, 0, 256, 232, 0)
        group = SimpleNamespace(data=bytes(data), resource_limit=len(data),
                                relocations=SimpleNamespace(pointers=pointers))
        reader = SimpleNamespace(load_group=lambda _: group,
                                 resource_by_location=lambda _, offset: lod if offset == 256 else None)
        return reader, actor, lod

    def test_counted_relocated_reference_preserves_provenance(self):
        reader, actor, lod = self.fixture()
        decoded, proof = decode_marker_lod(reader, actor)
        self.assertEqual(decoded, lod)
        self.assertEqual(proof["visual_list_offset"], 128)
        self.assertEqual(proof["lod_pointer_field"], 132)
        self.assertEqual(len(proof["raw_actor_hex"]), 256)
        self.assertEqual(len(proof["actor"]["sha256"]), 64)

    def test_rejects_wrong_native_class(self):
        reader, actor, _ = self.fixture(native_class=0x07066922)
        with self.assertRaisesRegex(ValueError, "MarkerType"):
            decode_marker_lod(reader, actor)

    def test_rejects_unproved_multiple_visuals(self):
        reader, actor, _ = self.fixture(count=2)
        with self.assertRaisesRegex(ValueError, "count"):
            decode_marker_lod(reader, actor)

    def test_requires_relocated_target(self):
        reader, actor, _ = self.fixture(target=False)
        with self.assertRaisesRegex(ValueError, "resolve"):
            decode_marker_lod(reader, actor)

    def test_rejects_count_outside_resource_data(self):
        reader, actor, _ = self.fixture()
        reader.load_group(0).resource_limit = 128
        with self.assertRaisesRegex(ValueError, "outside"):
            decode_marker_lod(reader, actor)


@unittest.skipUnless(os.environ.get("DAH_AREA42_SOURCE"), "set DAH_AREA42_SOURCE for retail fixture")
class Area42LandingVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = read_landing_visual_scene(BlockReader(os.environ["DAH_AREA42_SOURCE"]))

    def test_script_actor_lod_component_chain_is_native(self):
        scene = self.scene
        self.assertEqual(scene.actor_resource.name_hash, 0xE57E86D3)
        self.assertEqual(scene.lod_resource.name_hash, 0xA40AAA5B)
        self.assertEqual(scene.report["counts"],
                         {"placements": 3, "lods": 1, "parts": 11, "materials": 17, "textures": 5})
        self.assertEqual([part.bone_index for part in scene.lods[0].parts],
                         [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12])
        self.assertEqual(scene.report["selection_calls"][0]["script"]["sha256"],
                         "fa3175824371fe47abf5d18c2707aed0542d57f6a2a9202c97db15b6aa88b1c9")

    def test_ground_component_uses_its_real_mesh_uvs_and_texture(self):
        visual = self.scene.lods[0].parts[6].visual
        self.assertEqual(visual.mesh.resource.name_hash, 0x03DCA99B)
        self.assertEqual(len(visual.mesh.local_positions), 9)
        self.assertEqual(len(visual.mesh.triangles), 8)
        self.assertEqual(visual.mesh.segments[0].material_slot, 6)
        self.assertEqual(visual.materials[6].binding.texture_hash, 0xC8444C72)
        np.testing.assert_array_equal(visual.mesh.local_positions[:, 2], np.zeros(9))
        self.assertTrue(np.isfinite(visual.mesh.local_uvs).all())

    def test_authored_centers_not_claimed_as_collision_projected(self):
        sites = {p.label: p for p in self.scene.placements}
        self.assertEqual(set(sites), {"m1_zone", "m2_zone", "lz_majestic"})
        np.testing.assert_array_equal(sites["m1_zone"].position,
                                      [185.85836791992188, 82.94969177246094, 10.750364303588867])
        for site in sites.values():
            np.testing.assert_array_equal(site.matrix[:3, :3], np.eye(3))
            np.testing.assert_array_equal(site.matrix[3, :3], site.position)
            self.assertIn("registration_calls", site.provenance)
        self.assertIn("not reproduced", self.scene.report["limitations"][0])


@unittest.skipUnless(os.environ.get("DAH_RETAIL_XBE"), "set DAH_RETAIL_XBE for runtime evidence")
class LandingRuntimeEvidenceTests(unittest.TestCase):
    def test_pinned_marker_selection_projection_and_transform(self):
        result = verify_landing_executable_evidence(os.environ["DAH_RETAIL_XBE"])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(result["checks"]), 3)
