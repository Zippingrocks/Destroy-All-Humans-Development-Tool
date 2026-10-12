from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from dahdev.cli import main


class SceneValidationCommandTests(unittest.TestCase):
    report = {
        "schema_version": 4,
        "profile_id": "unit",
        "map_name": "test",
        "counts": {"world_placements": 1},
        "manifest_sha256": "a" * 64,
        "buffer_sha256": "b" * 64,
    }

    def test_json_preserves_full_validator_report(self):
        output = io.StringIO()
        with patch("dahdev.cli.validate_editor_scene_directory", return_value=self.report) as validate:
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["editor-scene-validate", "--scene", "example.dahscene", "--json"]), 0)
            validate.assert_called_once_with("example.dahscene")
        self.assertEqual(json.loads(output.getvalue()), self.report)

    def test_text_does_not_claim_runtime_parity(self):
        output = io.StringIO()
        with patch("dahdev.cli.validate_editor_scene_directory", return_value=self.report):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["editor-scene-validate", "--scene", "example.dahscene"]), 0)
        self.assertIn("not a runtime or native-save test", output.getvalue())
        self.assertIn("World placements:     1", output.getvalue())

    def test_invalid_scene_fails_with_nonzero_exit(self):
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as caught:
                main(["editor-scene-validate", "--scene", directory])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("editor scene manifest not found", errors.getvalue())


class OfflinePreservationCommandTests(unittest.TestCase):
    def test_resource_inventory_json(self):
        report = {
            "resource_count": 3,
            "resource_type_count": 1,
            "families": [],
            "important_limit": "unknown",
        }
        output = io.StringIO()
        with patch("dahdev.cli.resource_coverage_report", return_value=report) as inventory:
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["resource-inventory", "--source", "area42", "--json"]), 0)
        inventory.assert_called_once_with("area42")
        self.assertEqual(json.loads(output.getvalue()), report)

    def test_unchanged_copy_is_explicitly_not_a_writer(self):
        report = {
            "output": "copy",
            "file_sha256": {"a.dir": "a"},
            "resource_count": 3,
            "decoded_load_group_count": 1,
            "opaque_load_group_count": 0,
            "load_groups": [{"index": 0}],
        }
        output = io.StringIO()
        with patch("dahdev.cli.unchanged_copy_verify", return_value=report) as copy:
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    main(["unchanged-copy-verify", "--source", "area42", "--out", "copy"]),
                    0,
                )
        copy.assert_called_once_with("area42", "copy")
        self.assertIn("passthrough baseline only", output.getvalue())
