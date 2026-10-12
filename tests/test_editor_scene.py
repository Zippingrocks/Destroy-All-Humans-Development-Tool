from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dahdev.editor_scene import (
    SCHEMA_NAME,
    SCHEMA_VERSION,
    _BufferBuilder,
    validate_editor_scene_directory,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BufferBuilderTests(unittest.TestCase):
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
            "schema": {"name": SCHEMA_NAME, "version": SCHEMA_VERSION},
            "profile_id": "test-profile",
            "map_name": "test-map",
            "read_only": True,
            "buffers": {
                "file": "buffers.bin",
                "byte_length": len(payload),
                "sha256": _sha256(payload),
            },
            "counts": {"placements": 1, "lods": 2, "geometries": 1},
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
        }
        (root / "scene.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_validates_buffer_views_and_texture_previews(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_scene(root)
            result = validate_editor_scene_directory(root)
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["counts"]["lods"], 2)

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


if __name__ == "__main__":
    unittest.main()
