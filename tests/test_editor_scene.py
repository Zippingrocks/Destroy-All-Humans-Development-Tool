from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from dahdev.editor_scene import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    _BufferBuilder,
    _append_building_fixture_parts,
    validate_editor_scene_directory,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BufferBuilderTests(unittest.TestCase):
    def test_selected_fixtures_stay_under_parent_with_independent_lod_fallback(self) -> None:
        local = np.eye(4, dtype=np.float32)
        local[3, :3] = (2, 3, 4)
        component = np.eye(4, dtype=np.float32)
        component[3, 0] = 5
        fixture = SimpleNamespace(lods=[SimpleNamespace(index=0)], matrix=local,
                                  index=7, tag_hash=11, visual_hash=12, offset=90, raw_record=b"native")
        selected = SimpleNamespace(parent=SimpleNamespace(bucket_index=0, index=4),
                                   fixture=fixture, selection=SimpleNamespace(pointer_field=100, flags=0x400, parameter_offset=80,
                                   table_offset=120, raw_table=b"selected"))
        objects = [{"id": "world-placement:00:0004", "preview_status": "native",
                    "lods": [{"index": i, "parts": [{"geometry": "owner"}]} for i in (0, 1)]}]
        def parts(lod):
            self.assertEqual(lod.index, 0)
            return {"parts": [{"geometry": "fixture", "matrix_row_major_f32": component.reshape(-1).tolist()}]}
        with patch("dahdev.editor_scene._parts_lod", side_effect=parts):
            _append_building_fixture_parts(objects, SimpleNamespace(placements=[selected]))
        self.assertEqual(len(objects), 1)
        for lod in objects[0]["lods"]:
            self.assertEqual(len(lod["parts"]), 2)
            part = lod["parts"][1]
            self.assertEqual(part["matrix_row_major_f32"][12:15], [7, 3, 4])
            self.assertEqual(part["building_fixture"]["selected_lod_index"], 0)
            self.assertEqual(part["building_fixture"]["raw_record_hex"], b"native".hex())

    def test_aligns_and_hashes_views(self) -> None:
        builder = _BufferBuilder()
        first = builder.add(b"abc")
        second = builder.add(b"de")
        self.assertEqual(first, {"offset": 0, "length": 3, "sha256": _sha256(b"abc")})
        self.assertEqual(second, {"offset": 16, "length": 2, "sha256": _sha256(b"de")})
        self.assertEqual(bytes(builder.data), b"abc" + bytes(13) + b"de")


class EditorSceneValidationTests(unittest.TestCase):
    def _write_scene(self, root: Path) -> dict[str, object]:
        payload = b"vertices"
        (root / "buffers.bin").write_bytes(payload)
        (root / "textures").mkdir()
        preview = b"png-preview"
        (root / "textures" / "00000001.png").write_bytes(preview)
        view = {"offset": 0, "length": len(payload), "sha256": _sha256(payload)}
        empty = {
            "offset": len(payload),
            "length": 0,
            "sha256": _sha256(b""),
        }
        manifest = {
            "schema": {"name": SCHEMA_NAME, "version": 3},
            "profile_id": "test-profile",
            "map_name": "test-map",
            "read_only": True,
            "buffers": {
                "file": "buffers.bin",
                "byte_length": len(payload),
                "sha256": _sha256(payload),
            },
            "counts": {
                "terrain_placements": 1,
                "terrain_lods": 2,
                "geometries": 1,
                "world_placements": 1,
                "building_placements": 1,
                "building_renderable_placements": 1,
                "prop_placements": 0,
                "prop_renderable_placements": 0,
                "human_placements": 0,
                "human_renderable_placements": 0,
                "human_geometries": 0,
                "scatter_placements": 0,
                "scatter_batches": 0,
                "scatter_geometries": 0,
                "scene_markers": 0,
                "landing_sites": 0,
                "spawn_definitions": 0,
            },
            "geometries": [
                {
                    "id": "geometry:00000001",
                    "buffers": {
                        "vertices": view,
                        "index_strip": empty,
                        "triangles": empty,
                        "segments": empty,
                    },
                }
            ],
            "textures": [
                {
                    "id": "texture:00000001",
                    "preview": {
                        "file": "textures/00000001.png",
                        "sha256": _sha256(preview),
                    },
                }
            ],
            "placements": [
                {
                    "id": "terrain-placement:000",
                    "matrix_row_major_f32": [1, 0, 0, 0] * 4,
                    "lods": [
                        {"index": 0, "geometry": "geometry:00000001"},
                        {"index": 1, "geometry": "geometry:00000001"},
                    ],
                }
            ],
            "object_placements": [
                {
                    "id": "world-placement:00:0000",
                    "editor_collection": "buildings",
                    "matrix_row_major_f32": [1, 0, 0, 0] * 4,
                    "lods": [{"index": 0, "geometry": "geometry:00000001"}],
                }
            ],
            "scatter_placements": [],
            "scatter_batches": [],
            "scene_markers": [],
            "spawn_definitions": [],
            "human_archetypes": [],
        }
        (root / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_validates_buffer_views_and_texture_previews(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_scene(root)
            result = validate_editor_scene_directory(root)
            self.assertEqual(result["schema_version"], 3)
            self.assertEqual(result["counts"]["terrain_lods"], 2)

    def test_rejects_a_changed_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_scene(root)
            (root / "buffers.bin").write_bytes(b"changed!")
            with self.assertRaisesRegex(ValueError, "buffer SHA-256"):
                validate_editor_scene_directory(root)

    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._write_scene(root)
            manifest["buffers"]["file"] = "../buffers.bin"
            (root / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes"):
                validate_editor_scene_directory(root)

    def test_rejects_nonfinite_marker_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._write_scene(root)
            manifest["counts"]["scene_markers"] = 1
            manifest["scene_markers"] = [{"id": "landing:test", "kind": "landing-site",
                "label": "test", "status": "authored", "position_f32": [0, float("nan"), 0]}]
            (root / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "finite"):
                validate_editor_scene_directory(root)

    def test_rejects_unaccounted_scatter_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._write_scene(root)
            manifest["counts"]["scatter_batches"] = 1
            manifest["scatter_batches"] = [{"id": "scatter:test", "instance_count": 1,
                "instance_buffer": {"offset": 0, "length": 8, "sha256": _sha256(b"vertices")}}]
            (root / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scatter instance buffer"):
                validate_editor_scene_directory(root)


if __name__ == "__main__":
    unittest.main()
