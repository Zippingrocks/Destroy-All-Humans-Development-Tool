from __future__ import annotations

import os
import struct
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dahdev.dah1.container import ResourceRecord
from dahdev.dah1.interiors import (
    DESTROYED_STATE, WORLD_OBJECT_CLASS, decode_building_fixture_archetype,
    decode_fixture_selection, fixture_matrix, read_building_fixtures,
    verify_fixture_executable_evidence,
)
from dahdev.dah1.transforms import instance_matrix, transform_points
from dahdev.dah1.world import ACTOR_ARCHETYPE_TYPE, LOD_TYPE, ActorArchetype, read_world_scene


class FakeReader:
    def __init__(self, data, pointers, resources):
        self.group = SimpleNamespace(data=bytes(data), resource_limit=len(data),
                                    relocations=SimpleNamespace(pointers=pointers))
        self.resources = {(r.type_hash, r.name_hash): r for r in resources}

    def load_group(self, index):
        return self.group

    def resource_by_identity(self, type_hash, name_hash):
        return self.resources.get((type_hash, name_hash))


def fixture_world(*, selected=(11,), parameter_flags=0x400, bad_block=False):
    data = bytearray(0x1000)
    resource = ResourceRecord(0, ACTOR_ARCHETYPE_TYPE, 100, 0, 0x100, 0x600, 0)
    visual = ResourceRecord(1, LOD_TYPE, 101, 0, 0xC00, 0xE8, 0)
    alternate = ResourceRecord(2, LOD_TYPE, 102, 0, 0xD00, 0xE8, 0)
    actor = ActorArchetype(resource, "WorldObjectType", "b_test", None, ())
    struct.pack_into("<I", data, 0x100, WORLD_OBJECT_CLASS)
    pointers = {0x17C: 0x300, 0x184: 0x400, 0x354: 0x600, 0x850: 0x900}
    struct.pack_into("<I", data, 0x300, 1)
    struct.pack_into("<IIi7f", data, 0x304, 1, 0, -1,
                     1 if bad_block else 0, 0, 0, 0, 0, 0, 1)
    struct.pack_into("<IIi7f", data, 0x600, DESTROYED_STATE, 102, -1, 0, 0, 0, 0, 0, 0, 1)
    struct.pack_into("<I", data, 0x400, 2)
    for index in range(2):
        offset = 0x404 + index * 0x64
        struct.pack_into("<IIi7f", data, offset, 11 + index, 101, -1, 2 + index, 0, 0, 0, 0, 0, 1)
        struct.pack_into("<i", data, offset + 0x48, 0)
        # Opaque unknown tail bytes must survive decoding.
        struct.pack_into("<I", data, offset + 0x60, 0xABCDEF01 + index)
    struct.pack_into("<I", data, 0x84C, parameter_flags)
    relative = sum(size for bit, size in enumerate((4, 16, 4, 16, 20, 20, 20, 20, 4, 4))
                   if parameter_flags & (1 << bit))
    pointers[0x900 + relative] = 0xA00
    struct.pack_into(f"<I{len(selected)}I", data, 0xA00, len(selected), *selected)
    reader = FakeReader(data, pointers, (resource, visual, alternate))
    q = np.asarray((0, 0, np.sqrt(0.5), np.sqrt(0.5)), dtype=np.float32)
    parent = SimpleNamespace(actor=actor, offset=0x800, scale=np.float32(1),
                             matrix=instance_matrix(q, np.asarray((10, 20, 30), dtype=np.float32), np.float32(1)))
    world = SimpleNamespace(reader=reader, archetypes={100: actor}, placements=(parent,))
    return reader, actor, parent, world


class BuildingFixtureTests(unittest.TestCase):
    def test_fixture_selection_follows_variable_parameters_not_fixed_offset(self):
        reader, _, parent, _ = fixture_world(parameter_flags=0x400 | 1 | 8 | 0x100)
        selection = decode_fixture_selection(reader, parent)
        self.assertEqual(selection.pointer_field, 0x918)
        self.assertEqual(selection.table_offset, 0xA00)
        self.assertEqual(selection.selected_hashes, (11,))
        self.assertEqual(selection.raw_table, struct.pack("<II", 1, 11))

    def test_absent_selection_flag_means_no_fixtures_not_all_fixtures(self):
        reader, _, parent, _ = fixture_world(parameter_flags=0)
        self.assertEqual(decode_fixture_selection(reader, parent).selected_hashes, ())

    def test_missing_parameter_relocation_rejected(self):
        reader, _, parent, _ = fixture_world()
        del reader.group.relocations.pointers[0x850]
        with self.assertRaisesRegex(ValueError, "variable parameters"):
            decode_fixture_selection(reader, parent)

    def test_truncated_and_duplicate_selections_rejected(self):
        reader, _, parent, _ = fixture_world(selected=(11, 11))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            decode_fixture_selection(reader, parent)
        reader, _, parent, _ = fixture_world()
        data = bytearray(reader.group.data)
        struct.pack_into("<I", data, 0xA00, 0x1000)
        reader.group.data = bytes(data)
        with self.assertRaisesRegex(ValueError, "outside"):
            decode_fixture_selection(reader, parent)

    def test_only_selected_fixture_is_composed_local_then_world(self):
        reader, actor, _, world = fixture_world()
        with patch("dahdev.dah1.interiors.decode_actor_visual_lods", return_value=("decoded-lod",)) as decode:
            scene = read_building_fixtures(world)
        self.assertEqual(len(scene.placements), 1)
        selected = scene.placements[0]
        self.assertEqual(selected.fixture.tag_hash, 11)
        np.testing.assert_allclose(selected.matrix[3, :3], (10, 22, 30), atol=1e-5)
        np.testing.assert_allclose(transform_points(np.asarray([[1, 0, 0]], dtype=np.float32), selected.matrix),
                                   [[10, 23, 30]], atol=1e-5)
        self.assertEqual(Counter(i["status"] for i in scene.inventory),
                         {"decoded": 1, "not-selected-by-native-instance": 1})
        archetype = scene.archetypes[actor.resource.name_hash]
        self.assertEqual(archetype.raw_actor, reader.group.data[0x100:0x700])
        self.assertEqual(archetype.fixtures[0].raw_record, reader.group.data[0x404:0x468])
        self.assertEqual(struct.unpack_from("<I", archetype.fixtures[0].raw_record, 0x60)[0], 0xABCDEF01)
        self.assertEqual(archetype.blocks[0].alternate_state_hash, DESTROYED_STATE)
        self.assertEqual(archetype.blocks[0].alternate_visual_resource.name_hash, 102)
        self.assertTrue(all(call.args[1].name_hash == 101 for call in decode.call_args_list))

    def test_selected_unknown_tag_rejected(self):
        _, _, _, world = fixture_world(selected=(99,))
        with patch("dahdev.dah1.interiors.decode_actor_visual_lods", return_value=()), \
             self.assertRaisesRegex(ValueError, "absent from"):
            read_building_fixtures(world)

    def test_nonidentity_block_is_reported_not_guessed(self):
        reader, actor, _, world = fixture_world(bad_block=True)
        with patch("dahdev.dah1.interiors.decode_actor_visual_lods") as decode:
            scene = read_building_fixtures(world)
        decode.assert_not_called()
        self.assertEqual(scene.placements, ())
        self.assertEqual(scene.archetypes[actor.resource.name_hash].fixtures[0].status,
                         "unsupported-block-transform")

    def test_bad_transforms_and_fixture_array_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            fixture_matrix((float("nan"), 0, 0), (0, 0, 0, 1))
        with self.assertRaisesRegex(ValueError, "unit"):
            fixture_matrix((0, 0, 0), (0, 0, 0, 2))
        reader, actor, _, _ = fixture_world()
        data = bytearray(reader.group.data)
        struct.pack_into("<I", data, 0x400, 0xFFFFFFFF)
        reader.group.data = bytes(data)
        with self.assertRaisesRegex(ValueError, "outside"):
            decode_building_fixture_archetype(reader, actor)

    @unittest.skipUnless(os.environ.get("DAH_AREA42_BLOCK"), "requires pinned Area42 retail fixture")
    def test_retail_selected_fixtures_restore_lab_and_preserve_native_selection(self):
        world = read_world_scene(os.environ["DAH_AREA42_BLOCK"], include_visuals=False)
        scene = read_building_fixtures(world)
        self.assertEqual(len(world.placements), 2674)
        self.assertEqual(len(scene.archetypes), 18)
        self.assertEqual(sum(len(a.fixtures) for a in scene.archetypes.values()), 33)
        self.assertEqual(len(scene.placements), 76)
        self.assertEqual(len({p.parent.offset for p in scene.placements}), 61)
        self.assertEqual(Counter(i["status"] for i in scene.inventory),
                         {"decoded": 76, "not-selected-by-native-instance": 5})
        self.assertFalse(any(p.parent.actor.asset_name == "b_sml_hanger" for p in scene.placements))
        lab = [p for p in scene.placements if p.parent.actor.asset_name == "b_sml_majestic_hq_03"]
        self.assertEqual([p.fixture.visual_hash for p in lab], [0x595EF2B4, 0xACF94D6F])
        self.assertEqual([p.fixture.tag_hash for p in lab], [0xCCC50364, 0xB46D85C5])
        self.assertEqual([sum(len(part.visual.mesh.triangles) for part in p.fixture.lods[0].parts) for p in lab],
                         [1424, 230])
        for placement in lab:
            self.assertEqual(placement.parent.offset, 0x65F38C)
            self.assertEqual(placement.selection.pointer_field, 0x65F3E8)
            self.assertEqual(placement.selection.table_offset, 0x65F3EC)
            np.testing.assert_array_equal(placement.matrix, placement.parent.matrix)
            group = world.reader.load_group(placement.parent.actor.resource.load_group)
            self.assertEqual(placement.fixture.raw_record,
                             group.data[placement.fixture.offset:placement.fixture.offset + 0x64])
            self.assertTrue(all(m.binding is not None for lod in placement.fixture.lods
                                for part in lod.parts for m in part.visual.materials))
        lab_archetype = scene.archetypes[lab[0].parent.actor.resource.name_hash]
        self.assertEqual(lab_archetype.blocks[0].alternate_state_hash, DESTROYED_STATE)
        self.assertEqual(lab_archetype.blocks[0].alternate_visual_hash, 0x800E7FC1)
        self.assertFalse(any(p.fixture.visual_hash == 0x800E7FC1 for p in scene.placements))
        xbe = Path(os.environ["DAH_AREA42_BLOCK"]).parents[2] / "default.xbe"
        if xbe.is_file():
            evidence = verify_fixture_executable_evidence(xbe)
            self.assertEqual(evidence["status"], "verified")
            self.assertEqual(len(evidence["checks"]), 7)


if __name__ == "__main__":
    unittest.main()
