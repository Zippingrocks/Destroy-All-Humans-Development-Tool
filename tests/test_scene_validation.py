from __future__ import annotations

import copy
import hashlib
import struct
import unittest

from dahdev.dah1.scene_validation import validate_complete_scene


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _matrix():
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def fixture():
    vertices = b"".join(struct.pack("<5f4h", *v, 0, 0, 0, 0) for v in
                        ((0, 0, 0, 0, 0), (1, 0, 0, 1, 0), (0, 1, 0, 0, 1)))
    triangles = struct.pack("<3I", 0, 1, 2)
    payload = vertices + triangles
    resource = {"type_hash": "0x1D9D48EC", "name_hash": "0x11223344", "load_group": 0, "offset": 400}
    part = {"geometry": "g", "material_slots": ["m"], "matrix_row_major_f32": _matrix(),
            "bone_index": 1, "table_offset": 1000, "raw_table_record_hex": struct.pack("<2I", 1, 0).hex()}
    lod = {"index": 0, "geometry": "g", "material_slots": ["m"], "parts": [part]}
    native = {"record_offset": 300, "bucket_index": 0, "record_index": 0,
              "list_offset": 200, "list_pointer_field": 0xC, "list_index": 0,
              "raw_header_hex": bytes(64).hex(), "raw_header_sha256": _hash(bytes(64))}
    obj = {"id": "world:0", "native": native, "archetype": {"resource": resource, "native_class": "PropType"},
           "position_f32": [0.0, 0.0, 0.0], "matrix_row_major_f32": _matrix(), "lods": [lod]}
    marker = {"id": "landing:0", "kind": "landing-site", "position_f32": [0.0, 0.0, 0.0]}
    landing = {"id": "landing-visual:0", "native": copy.deepcopy(marker),
               "position_f32": [0.0, 0.0, 0.0], "matrix_row_major_f32": _matrix(), "lods": [copy.deepcopy(lod)]}
    manifest = {
        "schema": {"version": 4}, "profile_id": "unit", "map_name": "test",
        "counts": {"world_placements": 1, "landing_visual_placements": 1,
                   "renderable_world_placements": 1, "landing_sites": 1},
        "object_placements": [obj], "landing_placements": [landing],
        "materials": [{"id": "m"}],
        "geometries": [{"id": "g", "counts": {"vertices": 3, "triangles": 1},
                        "vertex_layout": {"stride": 28, "position": {"offset": 0}, "uv": {"offset": 12}},
                        "segments": [{"material_slot": 0}],
                        "buffers": {"vertices": {"offset": 0, "length": len(vertices), "sha256": _hash(vertices)},
                                    "triangles": {"offset": len(vertices), "length": len(triangles), "sha256": _hash(triangles)}}}],
        "staging": {"native_lists": [{"bucket": 0, "list_offsets": [200], "placements": 1}]},
        "actor_visual_inventory": [{"resource": resource, "placements": 1, "native_class": "PropType", "status": "decoded"}],
        "scene_markers": [marker],
    }
    return manifest, payload


class CompleteSceneValidationTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.payload = fixture()

    def validate(self):
        validate_complete_scene(self.manifest, self.payload)

    def test_complete_generic_fixture_is_not_area42_gated(self):
        self.validate()

    def test_duplicate_native_record_is_rejected(self):
        duplicate = copy.deepcopy(self.manifest["object_placements"][0])
        duplicate["id"] = "world:1"
        self.manifest["object_placements"].append(duplicate)
        self.manifest["counts"]["world_placements"] = 2
        with self.assertRaisesRegex(ValueError, "duplicate native world"):
            self.validate()

    def test_second_native_list_is_accepted_without_first_list(self):
        self.manifest["object_placements"][0]["native"]["list_pointer_field"] = 0x10
        self.validate()

    def test_native_list_index_gap_is_rejected(self):
        self.manifest["object_placements"][0]["native"]["list_index"] = 1
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.validate()

    def test_missing_list_provenance_is_rejected(self):
        del self.manifest["object_placements"][0]["native"]["list_offset"]
        with self.assertRaisesRegex(ValueError, "native list offset"):
            self.validate()

    def test_staging_list_mismatch_is_rejected(self):
        self.manifest["staging"]["native_lists"][0]["list_offsets"] = [900]
        with self.assertRaisesRegex(ValueError, "staging list provenance"):
            self.validate()

    def test_raw_native_header_corruption_is_rejected(self):
        self.manifest["object_placements"][0]["native"]["raw_header_hex"] = (b"x" * 64).hex()
        with self.assertRaisesRegex(ValueError, "header SHA"):
            self.validate()

    def test_secondary_component_geometry_is_checked(self):
        lod = self.manifest["object_placements"][0]["lods"][0]
        second = copy.deepcopy(lod["parts"][0])
        second["geometry"] = "missing"
        lod["parts"].append(second)
        with self.assertRaisesRegex(ValueError, "unknown geometry"):
            self.validate()

    def test_component_matrix_requires_finite_numbers(self):
        self.manifest["object_placements"][0]["lods"][0]["parts"][0]["matrix_row_major_f32"][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "component matrix"):
            self.validate()

    def test_component_attachment_raw_record_must_match_bone(self):
        self.manifest["object_placements"][0]["lods"][0]["parts"][0]["bone_index"] = 2
        with self.assertRaisesRegex(ValueError, "component bone differs"):
            self.validate()

    def test_component_material_slots_are_checked(self):
        self.manifest["geometries"][0]["segments"][0]["material_slot"] = 1
        with self.assertRaisesRegex(ValueError, "material slot is out"):
            self.validate()

    def test_payload_bounds_are_checked(self):
        self.payload = self.payload[:-1]
        with self.assertRaisesRegex(ValueError, "buffer is out"):
            self.validate()

    def test_inventory_requires_every_actor(self):
        self.manifest["actor_visual_inventory"] = []
        with self.assertRaisesRegex(ValueError, "cover every"):
            self.validate()

    def test_inventory_counts_are_cross_checked(self):
        self.manifest["actor_visual_inventory"][0]["placements"] = 2
        with self.assertRaisesRegex(ValueError, "inventory placement count"):
            self.validate()

    def test_inventory_status_cannot_claim_unrendered_actor_decoded(self):
        self.manifest["object_placements"][0]["lods"] = []
        self.manifest["counts"]["renderable_world_placements"] = 0
        with self.assertRaisesRegex(ValueError, "unrenderable placements"):
            self.validate()

    def test_landing_cannot_silently_move_from_native_center(self):
        self.manifest["landing_placements"][0]["position_f32"][2] = 10.0
        with self.assertRaisesRegex(ValueError, "differs from authored"):
            self.validate()

    def test_landing_must_link_native_region_marker(self):
        self.manifest["landing_placements"][0]["native"]["id"] = "not-present"
        with self.assertRaisesRegex(ValueError, "native region marker"):
            self.validate()

    def test_area42_profile_has_independent_complete_inventory_gate(self):
        self.manifest["profile_id"] = "dah1-retail-xbox-us"
        self.manifest["map_name"] = "area42"
        with self.assertRaisesRegex(ValueError, "Area42 complete native inventory"):
            self.validate()
