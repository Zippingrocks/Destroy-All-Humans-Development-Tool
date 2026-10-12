from __future__ import annotations

import copy
import struct
import unittest

from dahdev.dah1.fixture_validation import validate_building_fixture_report


def fixture_manifest():
    raw_actor = bytearray(0x300)
    struct.pack_into("<I", raw_actor, 0x100, 2)
    struct.pack_into("<II", raw_actor, 0x104, 11, 101)
    struct.pack_into("<II", raw_actor, 0x168, 12, 102)
    actor = {"type_hash": "0x1D9D48EC", "name_hash": "0x00000064",
             "load_group": 0, "offset": 0x100, "size": len(raw_actor)}
    part = {"geometry": "geometry:fixture", "bone_index": None, "table_offset": None,
            "building_fixture": {
                "index": 0, "tag_hash": "0x0000000B", "visual_hash": "0x00000065",
                "record_offset": 0x204, "raw_record_hex": raw_actor[0x104:0x168].hex(),
                "selected_lod_index": 0, "selection_pointer_field": 0x900,
                "selection_table_offset": 0xA00,
                "raw_selection_table_hex": struct.pack("<II", 1, 11).hex(),
            }}
    return {
        "profile_id": "test", "map_name": "synthetic",
        "counts": {"selected_building_fixtures": 1},
        "object_placements": [{
            "id": "test-parent", "native": {"record_offset": 0x800},
            "archetype": {"resource": actor},
            "lods": [{"index": 0, "parts": [copy.deepcopy(part)]},
                     {"index": 1, "parts": [copy.deepcopy(part)]}],
        }],
        "building_fixture_validation": {
            "selected_fixture_count": 1,
            "archetypes": [{"resource": actor, "raw_actor_hex": raw_actor.hex(),
                            "relocations": [[0x184, 0x200]], "fixture_table_offset": 0x200}],
            "inventory": [
                {"parent_offset": 0x800, "actor_hash": 100, "fixture_index": 0,
                 "tag_hash": 11, "selected": True, "rendered": True, "status": "decoded"},
                {"parent_offset": 0x800, "actor_hash": 100, "fixture_index": 1,
                 "tag_hash": 12, "selected": False, "rendered": False,
                 "status": "not-selected-by-native-instance"},
            ],
        },
    }


class FixtureReportValidationTests(unittest.TestCase):
    def test_pre_fixture_v4_report_remains_optional(self):
        validate_building_fixture_report({"schema": {"version": 4}})
        with self.assertRaisesRegex(ValueError, "report is missing"):
            validate_building_fixture_report({"counts": {"selected_building_fixtures": 1}})

    def test_independent_fixture_lod_is_retained_in_every_owner_lod(self):
        validate_building_fixture_report(fixture_manifest())

    def test_removing_fixture_part_from_either_lod_is_rejected(self):
        for index in (0, 1):
            with self.subTest(index=index):
                manifest = fixture_manifest()
                manifest["object_placements"][0]["lods"][index]["parts"].clear()
                with self.assertRaisesRegex(ValueError, "missing a selected fixture"):
                    validate_building_fixture_report(manifest)

    def test_tampered_tag_visual_offset_or_raw_record_is_rejected(self):
        for key, value in (("tag_hash", "0x0000000C"), ("visual_hash", "0x00000066"),
                           ("record_offset", 0x208), ("raw_record_hex", bytes(100).hex())):
            with self.subTest(key=key):
                manifest = fixture_manifest()
                manifest["object_placements"][0]["lods"][0]["parts"][0]["building_fixture"][key] = value
                with self.assertRaisesRegex(ValueError, "native record"):
                    validate_building_fixture_report(manifest)

    def test_selected_tag_must_exist_in_native_selection_bytes(self):
        manifest = fixture_manifest()
        fixture = manifest["object_placements"][0]["lods"][0]["parts"][0]["building_fixture"]
        fixture["raw_selection_table_hex"] = struct.pack("<II", 1, 12).hex()
        with self.assertRaisesRegex(ValueError, "native selection"):
            validate_building_fixture_report(manifest)

    def test_unselected_fixture_and_duplicate_part_are_rejected(self):
        manifest = fixture_manifest()
        parts = manifest["object_placements"][0]["lods"][0]["parts"]
        parts.append(copy.deepcopy(parts[0]))
        with self.assertRaisesRegex(ValueError, "duplicate fixture visual part"):
            validate_building_fixture_report(manifest)
        parts[-1]["building_fixture"]["index"] = 1
        with self.assertRaisesRegex(ValueError, "unselected fixture"):
            validate_building_fixture_report(manifest)

    def test_inventory_cannot_drop_an_authored_fixture(self):
        manifest = fixture_manifest()
        manifest["building_fixture_validation"]["inventory"].pop()
        with self.assertRaisesRegex(ValueError, "missing from inventory"):
            validate_building_fixture_report(manifest)

    def test_native_definition_relocation_and_array_bounds_are_validated(self):
        manifest = fixture_manifest()
        report = manifest["building_fixture_validation"]["archetypes"][0]
        report["relocations"][0][1] += 4
        with self.assertRaisesRegex(ValueError, "native relocation"):
            validate_building_fixture_report(manifest)
        manifest = fixture_manifest()
        report = manifest["building_fixture_validation"]["archetypes"][0]
        raw = bytearray.fromhex(report["raw_actor_hex"])
        struct.pack_into("<I", raw, 0x100, 0xFFFFFFFF)
        report["raw_actor_hex"] = raw.hex()
        with self.assertRaisesRegex(ValueError, "exceed preserved"):
            validate_building_fixture_report(manifest)

    def test_report_count_and_named_area42_gate_are_additional_checks(self):
        manifest = fixture_manifest()
        manifest["counts"]["selected_building_fixtures"] = 0
        with self.assertRaisesRegex(ValueError, "count"):
            validate_building_fixture_report(manifest)
        manifest = fixture_manifest()
        manifest["profile_id"] = "dah1-retail-xbox-us"
        manifest["map_name"] = "area42"
        with self.assertRaisesRegex(ValueError, "must be 76"):
            validate_building_fixture_report(manifest)


if __name__ == "__main__":
    unittest.main()
