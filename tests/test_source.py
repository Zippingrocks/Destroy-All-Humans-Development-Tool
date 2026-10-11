from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dahdev.source import inspect_source


class SourceInspectionTests(unittest.TestCase):
    def test_complete_zeroengine_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("area42.dir", "resident.pkg", "stream.pkg", "discard.pkg"):
                (root / name).touch()

            result = inspect_source(root)

            self.assertTrue(result.complete)
            self.assertEqual(result.container_family, "zeroengine-dir-pkg")
            self.assertEqual(result.map_name, "area42")
            self.assertEqual(result.warnings, ())

    def test_missing_package_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "area42.dir").touch()

            result = inspect_source(root)

            self.assertFalse(result.complete)
            self.assertIn("resident.pkg", result.missing_packages)
            self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()

