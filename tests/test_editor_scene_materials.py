from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from dahdev.dah1.materials import decode_material
from dahdev.editor_scene import (
    _collect_texture_bindings, _material_entry, _texture_dependency_report,
    _validate_native_texture_mips, _write_texture_entries,
)
from test_texture_mips import two_stage_material_fixture


class EditorSceneNativeTextureTests(unittest.TestCase):
    def test_secondary_dependencies_and_original_mip_files_are_exported(self):
        reader, resource = two_stage_material_fixture()
        material = decode_material(reader, resource)
        stages, textures = _collect_texture_bindings(reader, {resource.name_hash: material})
        self.assertEqual(set(textures), {0xB0, 0xB1})
        entry = _material_entry(material, stages[resource.name_hash])
        self.assertEqual(entry["texture_stages"][1]["binding_pointer_field"], 0x70)
        self.assertEqual(entry["texture_stages"][1]["binding"]["texture"], "texture:000000b1")
        self.assertIsNone(entry["texture_stages"][2]["binding"])
        self.assertEqual(entry["actor_material"]["effective_uv_transform"], [4, 0, 0, -1.5, 0, 4, 0, -1.5])
        self.assertEqual(entry["native_sampler"]["mip_filter"], "linear")
        report = _texture_dependency_report(stages)
        self.assertEqual(report["bound_stages"], [1, 1, 0, 0])
        self.assertEqual(report["secondary_only_textures"], 1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = _write_texture_entries(reader, textures, root)
            self.assertEqual(len(entries), 2)
            texture = entries[0]
            mips = texture["preview"]["native_mips"]
            self.assertEqual([m["width"] for m in mips], [8, 4])
            self.assertEqual(mips[0]["file"], texture["preview"]["file"])
            self.assertEqual(mips[0]["sha256"], texture["preview"]["sha256"])
            self.assertFalse(texture["conversion"]["resized"])
            self.assertFalse(texture["conversion"]["generated_mipmaps"])
            self.assertFalse(texture["conversion"]["ai_generated"])
            for image_entry in entries:
                _validate_native_texture_mips(root, image_entry)
            with Image.open(root / mips[1]["file"]) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 255, 0, 255))
            self.assertEqual(mips[1]["native_byte_offset"], 32)
            self.assertEqual(mips[1]["native_byte_length"], 8)
            native = reader.load_group(1).data[32:40]
            self.assertEqual(mips[1]["native_sha256"], hashlib.sha256(native).hexdigest())

    def test_mip_validator_rejects_corruption_resizing_and_unverified_tail(self):
        reader, resource = two_stage_material_fixture()
        _, textures = _collect_texture_bindings(reader, {resource.name_hash: decode_material(reader, resource)})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            texture = _write_texture_entries(reader, textures, root)[0]
            cases = []
            invalid = copy.deepcopy(texture)
            invalid["preview"]["native_mips"][1]["sha256"] = "0" * 64
            cases.append((invalid, "SHA-256"))
            invalid = copy.deepcopy(texture)
            invalid["preview"]["native_mips"][1]["width"] = 8
            cases.append((invalid, "dimensions"))
            invalid = copy.deepcopy(texture)
            invalid["preview"]["native_mips"][1]["native_byte_offset"] = 31
            cases.append((invalid, "gap or overlap"))
            invalid = copy.deepcopy(texture)
            invalid["preview"]["native_mips"].append(copy.deepcopy(invalid["preview"]["native_mips"][-1]))
            cases.append((invalid, "count"))
            invalid = copy.deepcopy(texture)
            invalid["preview"]["native_mips"][1]["file"] = "../escape.png"
            cases.append((invalid, "escape|relative|outside"))
            for invalid, expected in cases:
                with self.assertRaisesRegex(ValueError, expected):
                    _validate_native_texture_mips(root, invalid)


if __name__ == "__main__":
    unittest.main()
