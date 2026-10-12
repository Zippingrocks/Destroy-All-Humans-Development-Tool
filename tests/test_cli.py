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
