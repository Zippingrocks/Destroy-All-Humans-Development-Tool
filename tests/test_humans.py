from __future__ import annotations

import os
import copy
import struct
import unittest
from collections import Counter
from types import SimpleNamespace

import numpy as np

from dahdev.dah1.container import ResourceRecord
from dahdev.dah1.humans import (
    SKIN_MESH_TYPE,
    SKIN_VISUAL_TYPE,
    decode_skin_mesh,
    decode_skin_skeleton,
    read_humans,
    validate_human_archetype_reports,
)
from dahdev.dah1.world import read_world_scene


class FakeReader:
    def __init__(self, data: bytearray, pointers: dict[int, int]):
        self.group = SimpleNamespace(
            data=bytes(data), resource_limit=len(data),
            relocations=SimpleNamespace(pointers=pointers),
        )

    def load_group(self, index: int):
        return self.group


def two_batch_skin() -> tuple[bytearray, dict[int, int], ResourceRecord]:
    data = bytearray(0x400)
    resource = ResourceRecord(0, SKIN_MESH_TYPE, 0xABC, 0, 0, 32, 0)
    pointers = {0: 0x100, 4: 0x80, 0x1C: 0x200}
    struct.pack_into("<3I", data, 0x10, 6, 6, (2 << 16) | 40)
    struct.pack_into("<6H", data, 0x80, 0, 1, 2, 3, 4, 5)
    for vertex, position in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (0, 1, 1))):
        offset = 0x100 + vertex * 40
        struct.pack_into("<6f", data, offset, *position, 0, 0, 1)
        struct.pack_into("<8B", data, offset + 24, 255, 0, 0, 0, 0, 255, 255, 255)
        struct.pack_into("<2f", data, offset + 32, vertex / 6, 0.75)
    for batch in range(2):
        offset = 0x200 + batch * 0x8E
        struct.pack_into("<3H", data, offset, 1, batch + 1, 0)
        # These six words are vertex start/count, index start/count, triangle
        # count, material slot. The final two differ from static-mesh ordering.
        struct.pack_into("<6H", data, offset + 0x82, batch * 3, 3, batch * 3, 3, 1, batch)
    return data, pointers, resource


class HumanReportTests(unittest.TestCase):
    def fixture(self):
        raw = bytearray(56)
        struct.pack_into("<I", raw, 4, 0xABC)
        struct.pack_into("<8f", raw, 0x14, 1, 2, 3, 0, 0, 0, 1, 1)
        shared = bytearray(60)
        struct.pack_into("<H", shared, 12, 1)
        resource = {"index": 0, "type_hash": "0x1D9D48EC", "name_hash": "0x12345678",
                    "load_group": 0, "offset": 100, "size": 2048, "flags": "0x00000000"}
        report = {"resource": resource, "variant_hash": "0x00000000", "lod_pointer_offset": 32,
                  "variant_table_offset": None, "selected_variant_offset": None,
                  "pose_status": "native-reference-mesh; animation-not-applied",
                  "skeleton": {"load_group": 0, "shared_offset": 200, "bone_buffer_offset": 300,
                    "raw_shared_header_hex": shared.hex(), "bones": [{"index": 0, "offset": 300,
                    "name_hash": "0x00000ABC", "parent_index": None, "raw_hex": raw.hex(),
                    "local_position_f32": [1, 2, 3], "local_quaternion_xyzw_f32": [0, 0, 0, 1],
                    "local_scale_f32": 1}]}}
        placement = {"editor_collection": "humans", "archetype": {
            "native_class": "ActorTypePedestrian", "resource": resource}}
        return [report], [placement]

    def test_native_report_links_and_bytes_agree(self):
        reports, placements = self.fixture()
        validate_human_archetype_reports(reports, placements)
        validate_human_archetype_reports([], [])

    def test_changed_transform_and_missing_report_fail(self):
        reports, placements = self.fixture()
        changed = copy.deepcopy(reports)
        changed[0]["skeleton"]["bones"][0]["local_position_f32"][0] = 10
        with self.assertRaisesRegex(ValueError, "native bytes"):
            validate_human_archetype_reports(changed, placements)
        with self.assertRaisesRegex(ValueError, "missing archetype"):
            validate_human_archetype_reports([], placements)


class SkinMeshTests(unittest.TestCase):
    def test_batches_keep_native_palette_and_different_materials(self) -> None:
        data, pointers, resource = two_batch_skin()
        mesh = decode_skin_mesh(FakeReader(data, pointers), resource)
        np.testing.assert_array_equal(mesh.triangles, ((0, 1, 2), (3, 4, 5)))
        self.assertEqual(tuple(s.material_slot for s in mesh.segments), (0, 1))
        self.assertEqual(mesh.segments[1].palette, ((2, 0),))
        self.assertEqual(mesh.vertex_bytes.tobytes(), bytes(data[0x100:0x1F0]))
        np.testing.assert_array_equal(mesh.local_uvs[:, 1], np.full(6, 0.75))
        self.assertTrue((mesh.bone_index_bytes[:, 1:] == 255).all())

    def test_active_palette_index_must_resolve_but_inactive_bytes_are_preserved(self) -> None:
        data, pointers, resource = two_batch_skin()
        data[0x100 + 28] = 6
        with self.assertRaisesRegex(ValueError, "active influence"):
            decode_skin_mesh(FakeReader(data, pointers), resource)

    def test_bad_batch_triangle_count_is_rejected(self) -> None:
        data, pointers, resource = two_batch_skin()
        struct.pack_into("<H", data, 0x200 + 0x8A, 2)
        with self.assertRaisesRegex(ValueError, "triangle count"):
            decode_skin_mesh(FakeReader(data, pointers), resource)

    def test_buffer_pointer_cannot_escape_load_group(self) -> None:
        data, pointers, resource = two_batch_skin()
        pointers[0] = 0x3F0
        with self.assertRaisesRegex(ValueError, "vertex buffer"):
            decode_skin_mesh(FakeReader(data, pointers), resource)


class SkinSkeletonTests(unittest.TestCase):
    def skeleton(self, cycle: bool = False):
        data = bytearray(0x300)
        visual = ResourceRecord(0, SKIN_VISUAL_TYPE, 0xABC, 0, 0, 0xD4, 0)
        pointers = {0xC0: 0x100, 0x108: 0x140}
        struct.pack_into("<H", data, 0x10C, 2)
        for index, name in enumerate((11, 22)):
            offset = 0x140 + index * 0x38
            parent = (22 if cycle else 0) if index == 0 else 11
            struct.pack_into("<4I", data, offset + 4, name, parent, 22 if index == 0 else 0, 0)
            struct.pack_into("<8f", data, offset + 0x14, 0, 0, index, 0, 0, 0, 1, 1)
        return FakeReader(data, pointers), visual

    def test_parent_is_native_hash_link_without_guessed_bone_names(self) -> None:
        reader, visual = self.skeleton()
        skeleton = decode_skin_skeleton(reader, visual)
        self.assertEqual(tuple(b.parent_index for b in skeleton.bones), (None, 0))
        self.assertEqual(skeleton.bones[1].name_hash, 22)
        self.assertEqual(skeleton.bones[1].local_position, (0, 0, 1))

    def test_bone_cycle_is_rejected(self) -> None:
        reader, visual = self.skeleton(cycle=True)
        with self.assertRaisesRegex(ValueError, "cycle"):
            decode_skin_skeleton(reader, visual)


@unittest.skipUnless(os.environ.get("DAH_AREA42_SOURCE"), "set DAH_AREA42_SOURCE for the retail golden fixture")
class RetailHumanFixtureTests(unittest.TestCase):
    def test_actual_humans_and_material_variants(self) -> None:
        world = read_world_scene(os.environ["DAH_AREA42_SOURCE"], include_visuals=False)
        humans = read_humans(world)
        self.assertEqual(len(humans.placements), 295)
        self.assertEqual(len(humans.pedestrian_placements), 371)
        self.assertEqual(len(humans.archetypes), 15)
        self.assertEqual(Counter(p.actor.asset_name for p in humans.placements)["h_male_soldier_rifle"], 136)
        by_name = {a.actor.asset_name: a for a in humans.archetypes.values()}
        for archetype in humans.archetypes.values():
            self.assertEqual(tuple(l.index for l in archetype.lods), (0, 1, 2))
        scientist = by_name["h_male_scientist_labcoat"]
        plaid = by_name["h_male_scientist_plaidjacket"]
        self.assertEqual(scientist.lods[0].visual.mesh.resource, plaid.lods[0].visual.mesh.resource)
        self.assertNotEqual(scientist.variant_hash, plaid.variant_hash)
        self.assertNotEqual(
            tuple(m.resource.name_hash for m in scientist.lods[0].visual.materials),
            tuple(m.resource.name_hash for m in plaid.lods[0].visual.materials),
        )
        soldier = by_name["h_male_soldier_rifle"]
        self.assertEqual(len(soldier.lods[0].visual.mesh.triangles), 1433)
        self.assertEqual(len(soldier.lods[0].visual.skeleton.bones), 21)
        self.assertEqual(by_name["a_cow"].actor.native_class, "ActorTypePedestrian")
        self.assertFalse(any(p.actor.native_class == "RandomType" for p in humans.placements))


if __name__ == "__main__":
    unittest.main()
