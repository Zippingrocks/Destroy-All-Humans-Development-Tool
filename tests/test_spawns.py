from __future__ import annotations

import os
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace

from dahdev.dah1.container import BlockReader, ResourceRecord
from dahdev.dah1.world import read_world_scene
from dahdev.dah1.spawns import (
    ACTOR_ARCHETYPE_TYPE, RANDOM_CLASS, LuaPrototype, _hash_entries,
    decode_constructor_tables, decode_population_definition, literal_calls,
    native_name_hash, parse_lua4, read_spawn_scene, verify_spawn_executable_evidence,
)


def _prototype(strings=(), instructions=()):
    return LuaPrototype("test.lua", 0, tuple(strings), (), (), tuple(instructions), 100)


class NativeNameHashTests(unittest.TestCase):
    def test_verified_retail_resource_identities(self):
        for text, expected in {"world": 0x59CE7BCB, "regions": 0xD0894AB3,
                               "lua": 0x42EF1FD7, "type": 0x1D9D48EC,
                               "m1_zone": 0xA7692E96}.items():
            self.assertEqual(native_name_hash(text), expected)
            self.assertEqual(native_name_hash(text.upper()), expected)
        self.assertEqual(native_name_hash("world\0ignored"), 0x59CE7BCB)


class LuaEvidenceTests(unittest.TestCase):
    def test_call_requires_real_instructions_and_literal_argument(self):
        proto = _prototype(["landing", "RegisterZone", "zone"],
                           [12, (1 << 6) | 14, (2 << 6) | 7, 2, 0])
        call = literal_calls(proto)[0]
        self.assertEqual(call["function"], "landing.RegisterZone")
        self.assertEqual(call["literal_prefix_arguments"], ["zone"])
        self.assertTrue(call["complete_literal_call"])
        # A string constant alone does not establish a registration.
        self.assertEqual(literal_calls(_prototype(proto.strings, [0])), [])

    def test_dynamic_lookup_is_not_a_literal_call(self):
        proto = _prototype(["instance", "CreateObject", "h_human"],
                           [12, (1 << 6) | 14, (2 << 6) | 7, 11, 2, 0])
        self.assertFalse(literal_calls(proto)[0]["complete_literal_call"])

    def test_constructor_rejects_calls(self):
        with self.assertRaisesRegex(ValueError, "non-constructor"):
            decode_constructor_tables(_prototype([], [2]))

    def test_constructor_decodes_arrays(self):
        proto = _prototype(["values", "a", "b"], [17, (1 << 6) | 7,
                           (2 << 6) | 7, (2 << 6) | 21, 19, 0])
        self.assertEqual(decode_constructor_tables(proto), {"values": ["a", "b"]})

    def test_truncated_and_wrong_lua_headers(self):
        for raw in (b"", b"\x1bLua", bytes(50)):
            with self.assertRaises(ValueError):
                parse_lua4(raw)


class PopulationDecoderTests(unittest.TestCase):
    def fixture(self, threshold=0.75, include_second=True):
        data = bytearray(224)
        struct.pack_into("<I", data, 0, RANDOM_CLASS)
        struct.pack_into("<I", data, 128, 2)
        struct.pack_into("<f", data, 136, threshold)
        struct.pack_into("<f", data, 144, 1.0)
        actor = ResourceRecord(0, ACTOR_ARCHETYPE_TYPE, 1, 0, 0, 120, 0)
        choice1 = ResourceRecord(1, ACTOR_ARCHETYPE_TYPE, 2, 0, 160, 16, 0)
        choice2 = ResourceRecord(2, ACTOR_ARCHETYPE_TYPE, 3, 0, 176, 16, 0)
        pointers = {0x70: 128, 132: 160}
        if include_second:
            pointers[140] = 176
        group = SimpleNamespace(data=bytes(data), resource_limit=len(data),
                                relocations=SimpleNamespace(pointers=pointers))
        reader = SimpleNamespace(load_group=lambda _: group,
                                 resource_by_location=lambda _, offset: {160: choice1, 176: choice2}.get(offset))
        return reader, actor

    def test_cumulative_choice_weights(self):
        reader, actor = self.fixture()
        result = decode_population_definition(reader, actor)
        self.assertEqual([c["probability"] for c in result["choices"]], [0.75, 0.25])

    def test_out_of_range_threshold_is_rejected(self):
        reader, actor = self.fixture(threshold=1.2)
        with self.assertRaisesRegex(ValueError, "thresholds"):
            decode_population_definition(reader, actor)

    def test_missing_choice_relocation_is_rejected(self):
        reader, actor = self.fixture(include_second=False)
        with self.assertRaisesRegex(ValueError, "resolve"):
            decode_population_definition(reader, actor)

    def test_out_of_bounds_array_is_rejected(self):
        reader, actor = self.fixture()
        reader.load_group(0).resource_limit = 140
        with self.assertRaisesRegex(ValueError, "outside"):
            decode_population_definition(reader, actor)


@unittest.skipUnless(os.environ.get("DAH_AREA42_SOURCE"), "set DAH_AREA42_SOURCE for retail fixture")
class Area42SpawnFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reader = BlockReader(Path(os.environ["DAH_AREA42_SOURCE"]))
        cls.scene = read_spawn_scene(reader, read_world_scene(reader, include_visuals=False))

    def test_retail_counts_and_dependency_resolution(self):
        counts = self.scene.report["counts"]
        self.assertEqual(counts["parsed_lua_resources"], 223)
        self.assertEqual(counts["regions"], 92)
        self.assertEqual(counts["tags"], 135)
        self.assertEqual(counts["spawn_layers"], 41)
        self.assertEqual(counts["authored_entity_records"], 2674)
        self.assertEqual(counts["population_spawn_placements"], 139)
        self.assertEqual(counts["population_definitions"], 3)
        self.assertEqual(counts["directed_spawn_tables"], 4)
        self.assertEqual(self.scene.report["unresolved"], [])

    def test_landing_calls_resolve_exact_region_centers(self):
        sites = {m["label"]: m for m in self.scene.scene_markers if m["kind"] == "landing-site"}
        self.assertEqual(set(sites), {"m1_zone", "m2_zone", "lz_majestic"})
        self.assertEqual(sites["m1_zone"]["position_f32"],
                         [185.85836791992188, 82.94969177246094, 10.750364303588867])
        self.assertEqual(sites["lz_majestic"]["resource"]["offset"], 0x793C80)
        self.assertEqual(sum(len(site["registration_calls"]) for site in sites.values()), 8)

    def test_recipe_is_reconstructed_from_lua_constructor_instructions(self):
        tables = {d["name"]: d for d in self.scene.spawn_definitions if d["kind"] == "directed-spawn-table"}
        self.assertEqual(tables["area42spawn.defaults"]["levels"][0][0],
                         ["h_male_soldier_rifle", "pedestrian_lane_follow", 2, 0, 20, ["directed", "road"]])
        self.assertTrue(all(len(table["levels"]) == 4 for table in tables.values()))

    def test_spawn_markers_reference_existing_records_without_duplicating_humans(self):
        records = {(b["index"], p["placement_index"]): p for b in self.scene.spawn_layers for p in b["placements"]}
        for marker in self.scene.scene_markers:
            if marker["kind"] == "landing-site":
                continue
            reference = marker["world_placement"]
            record = records[reference["bucket_index"], reference["index"]]
            self.assertEqual(marker["position_f32"], record["position"])
            if record["native_class"] == "ActorTypePedestrian":
                self.assertFalse((record["asset_name"] or "").startswith("h_"))


@unittest.skipUnless(os.environ.get("DAH_RETAIL_XBE"), "set DAH_RETAIL_XBE for runtime evidence")
class SpawnRuntimeEvidenceTests(unittest.TestCase):
    def test_pinned_executable_bytes(self):
        result = verify_spawn_executable_evidence(os.environ["DAH_RETAIL_XBE"])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(result["checks"]), 4)
