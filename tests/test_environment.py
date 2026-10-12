from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import struct
import unittest
from pathlib import Path

import numpy as np

from dahdev.dah1.container import BlockReader, LoadedGroup, RelocationTable
from dahdev.dah1.environment import (
    FOG_TYPE, CameraFogProfile, NativeSpan, _camera_mapping, _camera_profiles,
    _decode_fog, _local_assignments, _span, _target, environment_report,
    fog_matrix, fog_plane, read_environment_scene, verify_environment_executable_evidence,
)
from dahdev.dah1.spawns import LuaPrototype, parse_lua4
from dahdev.dah1.transforms import instance_matrix


def synthetic_fog():
    raw = bytearray(0xEC)
    struct.pack_into("<II", raw, 0, FOG_TYPE, FOG_TYPE)
    struct.pack_into("<f3f4f3f3f", raw, 0xB4, 150, 0, -150, 0,
                     -2 ** -0.5, 0, 0, 2 ** -0.5, 1, 1, 1, .6, .5, .4)
    return _decode_fog(NativeSpan(0, 32, bytes(raw), FOG_TYPE, FOG_TYPE, 8, (), "source"))


def local_proto(value=71):
    # This intentionally differs from Area42 to prove values are decoded.
    strings = ("cfgCamera", "alienFogEnd", "dprint", "test")
    code = (12, (1 << 6) | 7, ((value + (1 << 25) - 1) << 6) | 6,
            (1539 << 6) | 20, (2 << 6) | 12, (3 << 6) | 7, 2, 0)
    return LuaPrototype("synthetic", 0, strings, (), (), code, 0)


class EnvironmentUnitTests(unittest.TestCase):
    def test_fog_fields_and_raw_preservation(self):
        fog = synthetic_fog()
        self.assertEqual(fog.extent, 150)
        self.assertEqual(fog.position, (0, -150, 0))
        self.assertEqual(len(fog.span.raw_bytes), 236)
        self.assertEqual(fog.span.sha256, hashlib.sha256(fog.span.raw_bytes).hexdigest())
        np.testing.assert_allclose(fog.color, (.6, .5, .4), atol=3e-8)

    def test_fog_plane_uses_squared_length_not_length(self):
        matrix = np.eye(4, dtype=np.float32)
        matrix[2, 2] = 10
        matrix[3, 2] = -20
        np.testing.assert_allclose(fog_plane(matrix), (0, 0, .1, 2))

    def test_authored_plane_and_three_sample_points(self):
        plane = fog_plane(synthetic_fog().authored_matrix)
        points = np.asarray([[0, 0, 0, 1], [0, -75, 0, 1], [0, -150, 0, 1]])
        np.testing.assert_allclose(points @ plane, (1, .5, 0), atol=2e-7)

    def test_rigid_object_plane_matches_world_plane(self):
        fog = synthetic_fog()
        parent = instance_matrix(np.asarray((0, 0, np.sin(.3), np.cos(.3))), np.asarray((4, 8, 12)), np.float32(1))
        obj = instance_matrix(np.asarray((0, np.sin(.2), 0, np.cos(.2))), np.asarray((17, -31, 2)), np.float32(1))
        matrix = fog_matrix(fog, parent)
        point = np.asarray((5, 11, 7, 1), dtype=np.float32)
        self.assertAlmostEqual(float(point @ fog_plane(matrix, obj)), float((point @ obj) @ fog_plane(matrix)), places=6)

    def test_camera_profile_is_explicit_endpoint_override(self):
        fog = synthetic_fog()
        before = fog.span.raw_bytes
        profile = CameraFogProfile("test", 60, 250, 900, ())
        matrix = fog_matrix(fog, profile=profile)
        np.testing.assert_allclose(np.linalg.norm(matrix[2, :3]), 190, atol=2e-5)
        np.testing.assert_allclose(matrix[3, :3], (0, -250, 0))
        self.assertEqual(fog.span.raw_bytes, before)
        self.assertEqual(fog.extent, 150)

    def test_reject_invalid_plane_matrices(self):
        cases = [np.zeros((4, 4)), np.full((4, 4), np.nan), np.eye(3)]
        zero_axis = np.eye(4)
        zero_axis[2] = 0
        cases.append(zero_axis)
        for matrix in cases:
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                fog_plane(matrix)
        scaled = np.diag((2, 1, 1, 1))
        with self.assertRaisesRegex(ValueError, "rigid"):
            fog_plane(np.eye(4), scaled)

    def test_reject_degenerate_camera_endpoint(self):
        for start, end in ((50, 50), (70, 50), (float("nan"), 50)):
            with self.assertRaises(ValueError):
                fog_matrix(synthetic_fog(), profile=CameraFogProfile("test", start, end, 10, ()))

    def test_reject_invalid_native_fog_fields(self):
        fog = synthetic_fog()
        for offset, value in ((0xB4, 0), (0xB8, float("nan")), (0xD4, 0), (0xE0, 2)):
            raw = bytearray(fog.span.raw_bytes)
            struct.pack_into("<f", raw, offset, value)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                _decode_fog(dataclasses.replace(fog.span, raw_bytes=bytes(raw)))
        with self.assertRaises(ValueError):
            _decode_fog(dataclasses.replace(fog.span, raw_bytes=fog.span.raw_bytes[:-4]))

    def test_span_bounds_identity_and_relocation_provenance(self):
        raw = synthetic_fog().span.raw_bytes
        group = LoadedGroup(0, "test", 0, "raw", bytes(32) + raw, 32 + len(raw), "hash",
                            RelocationTable({8: 32, 48: 36}, "reloc-hash"), "test")
        span = _span(group, 32, len(raw), FOG_TYPE, 8)
        self.assertEqual(span.relocations, ((16, 36),))
        self.assertEqual(_target(group, 8), 32)
        for offset, size, kind in ((32, len(raw) + 4, FOG_TYPE), (33, len(raw) - 1, FOG_TYPE), (32, len(raw), 0)):
            with self.assertRaises(ValueError):
                _span(group, offset, size, kind)
        with self.assertRaisesRegex(ValueError, "relocation"):
            _target(group, 12)

    def test_lua_values_are_decoded_not_fixture_constants(self):
        self.assertEqual(_local_assignments(local_proto(71)), {"alienFogEnd": (71.0, 0)})
        self.assertEqual(_local_assignments(local_proto(-27)), {"alienFogEnd": (-27.0, 0)})

    def test_lua_rejects_control_flow_and_duplicate_assignment(self):
        proto = local_proto()
        code = proto.instructions
        with self.assertRaises(ValueError):
            _local_assignments(dataclasses.replace(proto, instructions=code[:4] + (39,) + code[4:]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _local_assignments(dataclasses.replace(proto, instructions=code[:4] + code))
        with self.assertRaises(ValueError):
            _camera_mapping(dataclasses.replace(proto, instructions=(39,) * 15))


@unittest.skipUnless(os.environ.get("DAHDEV_AREA42_SOURCE"), "set DAHDEV_AREA42_SOURCE for retail golden fixture")
class EnvironmentGoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = read_environment_scene(os.environ["DAHDEV_AREA42_SOURCE"])

    def test_world_direct_references_and_payloads(self):
        scene = self.scene
        self.assertEqual(scene.world_resource.name_hash, 0xB31C6654)
        self.assertEqual(scene.fog.span.offset, 0x8D1680)
        self.assertEqual(scene.fog.span.pointer_field, scene.world_resource.offset + 0x18)
        self.assertEqual(scene.sky.span.pointer_field, scene.world_resource.offset + 0x1C)
        self.assertEqual(scene.fog.span.sha256, "6c95a62d902b582ffc7672c6489ebe5206c8b77b43211d47917a8ebdbb24bb99")
        self.assertEqual(scene.sky.span.sha256, "d8dc6a0975385e45f892e85a29d9f3f90c653c43bfb45a80200ad6166458e5b1")
        np.testing.assert_allclose(scene.fog.color, (.568627, .541176, .478431), atol=1e-7)
        np.testing.assert_allclose(fog_plane(scene.fog.authored_matrix), (0, 1 / 150, 0, 1), atol=2e-7)

    def test_three_distinct_sun_roles(self):
        suns = self.scene.suns
        self.assertEqual([sun.role for sun in suns], ["shadow", "static", "dynamic"])
        self.assertEqual([sun.span.sha256 for sun in suns], [
            "01cfaf8b1e9c323be329800d2a42bfc5e36467d64fd08aff8b488e9ccb704782",
            "d00630674cd88aee70ff4228b634c4a68f155d5571050c33e1c2848227469725",
            "746ce2e74d403ed2b4155a129ffec93ffa30d3111b5ba2aca69c33ff79411876"])
        self.assertEqual([sun.span.name_hash for sun in suns], [0xB1ADF26C, 0xF42BA29F, 0xC18EADC6])
        self.assertEqual(suns[1].quaternion, suns[2].quaternion)
        self.assertNotEqual(suns[0].quaternion, suns[1].quaternion)

    def test_both_sky_visuals_use_native_geometry_and_materials(self):
        sky = self.scene.sky
        self.assertEqual([v.resource.name_hash for v in sky.visuals], [0x741C438A, 0x0C9DF14A])
        self.assertEqual([v.mesh.resource.name_hash for v in sky.visuals], [0x7AD90718, 0xFC7BD8FF])
        self.assertEqual([(len(v.mesh.local_positions), len(v.mesh.triangles), len(v.mesh.segments)) for v in sky.visuals], [(106, 128, 2), (4, 2, 1)])
        self.assertEqual([[m.resource.name_hash for m in v.materials] for v in sky.visuals], [[0x4FE66423, 0xD21014BE], [0xF8B300E3]])
        self.assertEqual([[m.binding.texture_hash for m in v.materials] for v in sky.visuals], [[0x2E041BF3, 0xB5AD66A7], [0xEB27D855]])
        self.assertEqual([[m.render_flags for m in v.materials] for v in sky.visuals], [[0, 0x100], [0x100]])
        for visual in sky.visuals:
            self.assertTrue(np.isfinite(visual.mesh.local_positions).all())
            self.assertTrue(np.isfinite(visual.mesh.local_uvs).all())
            self.assertEqual(visual.mesh.vertex_bytes.nbytes, len(visual.mesh.local_positions) * visual.mesh.stride)

    def test_lua_camera_profiles_and_byte_provenance(self):
        profiles = self.scene.camera_profiles
        self.assertEqual([(p.mode, p.start, p.end, p.farplane) for p in profiles], [("alien", 0, 250, 1000), ("ufo", 60, 250, 250)])
        self.assertEqual([p["sha256"] for p in self.scene.scripts], [
            "0211daa500980794581a1776a73689cbf5a41cc16a661b46f8a66239ea352e53",
            "962b70a175d4bb14574bece7365073cc5fd26bb216faeaa54c87ae3403d29115"])
        self.assertEqual([p["assignment_pc"] for p in profiles[0].provenance], [8, 12, 0])
        self.assertEqual([p["init_call_pc"] for p in profiles[1].provenance], [0, 6, 12])
        for profile in profiles:
            for evidence in profile.provenance:
                self.assertEqual(len(bytes.fromhex(evidence["assignment_bytes_hex"])), 16)
                self.assertEqual(len(bytes.fromhex(evidence["init_call_bytes_hex"])), 24)

    def test_changed_camera_constant_is_structurally_decoded(self):
        reader = BlockReader(os.environ["DAHDEV_AREA42_SOURCE"])
        resource = reader.resource_by_identity(0x42EF1FD7, 3656195570)
        group = reader.load_group(resource.load_group)
        raw = bytearray(group.data)
        proto = parse_lua4(raw[resource.offset:resource.offset + resource.size])
        struct.pack_into("<I", raw, resource.offset + proto.code_offset + 14 * 4,
                         ((333 + (1 << 25) - 1) << 6) | 6)
        # An isolated in-memory group; no source files are changed.
        reader._groups[resource.load_group] = dataclasses.replace(group, data=bytes(raw))
        profiles, _ = _camera_profiles(reader)
        self.assertEqual(profiles[0].end, 333)
        self.assertEqual(profiles[1].end, 250)

    def test_report_is_json_and_does_not_claim_live_parity(self):
        report = environment_report(self.scene)
        json.dumps(report, allow_nan=False)
        self.assertEqual(report["status"], "decoded-native-evidence-not-live-environment")
        self.assertIsNone(report["sky"]["visuals"][0]["materials"][0]["effective_uv_transform"])
        self.assertTrue(any("interpolation" in item for item in report["unresolved"]))
        self.assertEqual(bytes.fromhex(report["fog"]["native"]["raw_bytes_hex"]), self.scene.fog.span.raw_bytes)


@unittest.skipUnless(os.environ.get("DAHDEV_RETAIL_XBE"), "set DAHDEV_RETAIL_XBE for executable pins")
class EnvironmentExecutableTests(unittest.TestCase):
    def test_pinned_executable_and_all_semantic_evidence_spans(self):
        result = verify_environment_executable_evidence(Path(os.environ["DAHDEV_RETAIL_XBE"]))
        self.assertEqual(result["status"], "verified")
        self.assertEqual(len(result["checks"]), 18)


if __name__ == "__main__":
    unittest.main()
