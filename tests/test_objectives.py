from __future__ import annotations

import hashlib
import copy
import os
import struct
import unittest
from collections import Counter

from dahdev.dah1.objectives import (
    _resolved_target, objective_calls, objective_definitions, read_objectives,
    verify_objective_executable_evidence,
    validate_objective_report,
)
from dahdev.dah1.spawns import LuaPrototype, native_name_hash
from dahdev.dah1.world import read_world_scene


def proto(strings=(), code=(), children=()):
    return LuaPrototype("fixture.lua", 0, tuple(strings), (), tuple(children), tuple(code), 100)


def ins(op, operand=0):
    return (operand << 6) | op


def definition_fixture():
    # b1.EnterBuilding={type='TagRegion',tag='player_alien',region='lab',Init=function...}
    strings = ["b1", "EnterBuilding", "type", "TagRegion", "tag", "player_alien",
               "region", "lab", "Init"]
    code = [ins(12), ins(7, 1), ins(17, 4),
            ins(7, 2), ins(7, 3), ins(7, 4), ins(7, 5),
            ins(7, 6), ins(7, 7), ins(7, 8), ins(48),
            ins(22, 4), (3 << 15) | (3 << 6) | 20, 0]
    return proto(strings, code, [proto(code=[0])])


class ObjectiveBytecodeTests(unittest.TestCase):
    def test_definition_requires_table_and_assignment_not_string_constants(self):
        source = definition_fixture()
        self.assertEqual(objective_definitions(proto(source.strings, [0])), [])
        result = objective_definitions(source)
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual((item["mission_table"], item["name"], item["type"]),
                         ("b1", "EnterBuilding", "TagRegion"))
        self.assertEqual(item["fields"]["Init"], {"prototype": "0.0"})
        self.assertEqual(item["fields"]["region"], "lab")
        self.assertEqual(item["field_provenance"]["region"]["value"]["instruction_span"]["code_offset"], 132)
        raw = bytes.fromhex(item["instruction_span"]["raw_instructions_hex"])
        self.assertEqual(raw, struct.pack("<13I", *source.instructions[:13]))
        self.assertEqual(item["instruction_span"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_missing_assignment_cannot_become_objective(self):
        source = definition_fixture()
        self.assertEqual(objective_definitions(proto(source.strings, source.instructions[:-2], source.children)), [])

    def test_missing_closure_is_not_invented(self):
        source = definition_fixture()
        self.assertEqual(objective_definitions(proto(source.strings, source.instructions)), [])

    def test_calls_keep_self_fields_symbolic_until_callback_context_is_known(self):
        source = proto(["mission", "AddTagMarker", "tag", "marker_tag"],
                       [ins(12), ins(14, 1), ins(11), ins(14, 2), ins(7, 3), ins(2), 0])
        result = objective_calls(source)[0]
        self.assertTrue(result["complete_call"])
        self.assertEqual(result["arguments"], [{"local_reference": 0, "fields": ["tag"]}, "marker_tag"])

    def test_arithmetic_is_explicitly_unresolved(self):
        source = proto(["mission", "StartObjective", "key"],
                       [ins(12), ins(14, 1), ins(7, 2), ins(25), ins(2), 0])
        result = objective_calls(source)[0]
        self.assertFalse(result["complete_call"])
        self.assertEqual(result["status"], "unresolved-call-expression")

    def test_inner_call_is_not_proof_of_outer_call(self):
        source = proto(["mission", "AddTagMarker", "GetTag", "key"],
                       [ins(12), ins(14, 1), ins(12), ins(14, 2), ins(7, 3), ins(2), 0])
        outer, inner = objective_calls(source)
        self.assertFalse(outer["complete_call"])
        self.assertTrue(inner["complete_call"])

    def test_data_members_are_not_reported_as_calls(self):
        self.assertEqual(objective_calls(proto(["mission", "currentObjective"],
                                              [ins(12), ins(14, 1), 0])), [])


class NativeTargetTests(unittest.TestCase):
    def test_old_manifest_without_extension_remains_valid(self):
        validate_objective_report({})

    def test_missing_runtime_targets_never_acquire_a_position(self):
        target = _resolved_target("tag", "player_alien", {}, {}, {})
        self.assertEqual(target["status"], "unresolved-runtime-or-absent-native-target")
        self.assertNotIn("position", target)

    def test_tag_preserves_each_authored_member_and_unresolved_ids(self):
        key = f"0x{native_name_hash('cages'):08X}"
        raw = struct.pack("<3I", 2, 100, 101)
        tags = {key: {"id": "tag:" + key, "entity_ids": [100, 101], "offset": 16,
                      "load_group": 0, "raw_record_hex": raw.hex()}}
        entries = [{"world_placement_id": "world-placement:00:0001"},
                   {"world_placement_id": "world-placement:04:0002"}]
        result = _resolved_target("tag", "cages", {}, tags, {100: entries})
        self.assertEqual(result["placement_refs"], entries)
        self.assertEqual(result["unresolved_entity_ids"], [101])
        self.assertEqual(result["status"], "partially-resolved-native-tag")


@unittest.skipUnless(os.environ.get("DAH_AREA42_SOURCE"), "set DAH_AREA42_SOURCE for retail fixture")
class Area42ObjectiveFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = read_world_scene(os.environ["DAH_AREA42_SOURCE"], include_visuals=False)
        cls.scene = read_objectives(cls.world)
        cls.manifest = {
            "objective_validation": cls.scene.report,
            "objective_definitions": list(cls.scene.objectives),
            "collectible_placements": list(cls.scene.collectibles),
            "counts": {"objective_definitions": len(cls.scene.objectives),
                       "authored_pickups": len(cls.scene.collectibles)},
            "object_placements": [{"id": f"world-placement:{p.bucket_index:02d}:{p.index:04d}",
                "archetype": {"native_class": p.actor.native_class, "asset_name": p.actor.asset_name,
                              "resource": {"name_hash": f"0x{p.actor.resource.name_hash:08X}"}},
                "position_f32": p.position.tolist(),
                "native": {"raw_header_hex": p.raw_header.hex(), "bucket_hash": f"0x{p.bucket_hash:08X}"}}
                for p in cls.world.placements if p.actor]}

    def test_pickups_reference_existing_world_positions_without_duplicates(self):
        records = {f"world-placement:{p.bucket_index:02d}:{p.index:04d}": p for p in self.world.placements}
        items = self.scene.collectibles
        self.assertEqual(len(items), 264)
        self.assertEqual(len({item["world_placement_id"] for item in items}), 264)
        self.assertEqual(Counter(item["kind"] for item in items),
                         {"pickup": 235, "probe": 24, "mission-debris": 5})
        for item in items:
            p = records[item["world_placement_id"]]
            self.assertEqual(item["position"], p.position.tolist())
            self.assertEqual(item["raw_header_hex"], p.raw_header.hex())
            self.assertEqual(item["native_layer_hash"], f"0x{p.bucket_hash:08X}")
            if item["kind"] == "probe":
                self.assertEqual(item["native_layer_hash"], "0x8F651465")
                self.assertEqual(item["actor_resource"]["name_hash"], "0x22F488FC")
            if item["kind"] == "mission-debris":
                self.assertEqual(item["native_layer_hash"], "0xDBD1D036")

    def test_objective_tables_and_callback_targets_are_reconstructed(self):
        self.assertEqual(len(self.scene.objectives), 146)
        objective = next(d for d in self.scene.objectives if d["mission_table"] == "b1" and d["name"] == "kill_mutants")
        self.assertEqual(objective["type"], "TagDestroyed")
        target = objective["targets"][0]
        self.assertEqual(target["name"], "b1_psimutants")
        self.assertEqual(len(target["placement_refs"]), 2)
        self.assertTrue(all(p["native_layer_hash"] for p in target["placement_refs"]))
        calls = [c for c in self.scene.script_calls if c["id"] in objective["callback_call_ids"]]
        marker = next(c for c in calls if c["function"] == "mission.AddTagMarker")
        self.assertEqual(marker["resolved_arguments"][:2], ["b1_psimutants", "marker_tag"])
        self.assertEqual(marker["targets"][0]["placement_refs"], target["placement_refs"])

    def test_region_markers_have_raw_native_sources_not_guessed_positions(self):
        self.assertEqual({m["name"] for m in self.scene.scene_markers},
                         {"b1_building", "b2_cows", "b2_cows2", "cr_zone", "m1_crashsite", "m2_testing_site"})
        for marker in self.scene.scene_markers:
            raw = bytes.fromhex(marker["source"]["raw_record_hex"])
            self.assertEqual(marker["position"], list(struct.unpack_from("<3f", raw, 0x40)))
            self.assertEqual(marker["matrix"][3][:3], marker["position"])
            self.assertIn("not a runtime projected", marker["placement_status"])

    def test_no_guess_for_missing_mission_zone(self):
        mission = next(m for m in self.scene.mission_registrations if m.get("name") == "b1")
        self.assertEqual(mission["targets"][0]["status"], "unresolved-runtime-or-absent-native-target")
        self.assertNotIn("position", mission["targets"][0])

    def test_every_decoded_call_and_definition_matches_original_resource_bytes(self):
        for item in (*self.scene.script_calls, *self.scene.objectives):
            resource, span = item["script"], item["instruction_span"]
            raw = bytes.fromhex(span["raw_instructions_hex"])
            actual = self.world.reader.load_group(resource["load_group"]).data
            offset = item["instruction_offset"]
            self.assertEqual(actual[offset:offset + len(raw)], raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), span["sha256"])

    def test_report_reconstructs_structure_not_just_hashes_and_counts(self):
        validate_objective_report(self.manifest)
        changed = copy.deepcopy(self.manifest)
        changed["objective_definitions"][0]["type"] = "InventedType"
        with self.assertRaisesRegex(ValueError, "reconstructed Lua table"):
            validate_objective_report(changed)

    def test_omitted_objective_or_duplicate_pickup_is_rejected(self):
        changed = copy.deepcopy(self.manifest)
        changed["objective_definitions"].pop()
        changed["counts"]["objective_definitions"] -= 1
        with self.assertRaisesRegex(ValueError, "do not cover"):
            validate_objective_report(changed)
        changed = copy.deepcopy(self.manifest)
        changed["collectible_placements"][1] = changed["collectible_placements"][0]
        with self.assertRaisesRegex(ValueError, "duplicate or missing"):
            validate_objective_report(changed)


@unittest.skipUnless(os.environ.get("DAH_RETAIL_XBE"), "set DAH_RETAIL_XBE for runtime evidence")
class ObjectiveRuntimeEvidenceTests(unittest.TestCase):
    def test_exact_runtime_marker_registration_and_projection_evidence(self):
        result = verify_objective_executable_evidence(os.environ["DAH_RETAIL_XBE"])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(result["checks"]), 5)
