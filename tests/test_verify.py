from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from dahdev.cli import DEFAULT_FIXTURE
from dahdev.verify import verification_report


class VerificationTests(unittest.TestCase):
    def test_packaged_and_repository_fixtures_match(self) -> None:
        repository_fixture = Path(__file__).resolve().parents[1] / "fixtures" / "area42-retail-xbox-us.json"
        self.assertEqual(
            json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8")),
            json.loads(repository_fixture.read_text(encoding="utf-8")),
        )

    def test_matching_fixture_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"area42"
            (root / "area42.dir").write_bytes(payload)
            fixture = {
                "schema_version": 1,
                "fixture_id": "test-area42",
                "profile_id": "test-profile",
                "map_name": "area42",
                "source_files": {
                    "area42.dir": {
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
                "reference_files": {},
            }
            fixture_path = root / "fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

            report = verification_report(fixture_path, root)

            self.assertTrue(report["verified"])
            self.assertTrue(report["source"][0]["ok"])

    def test_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "area42.dir").write_bytes(b"wrong")
            fixture = {
                "schema_version": 1,
                "fixture_id": "test-area42",
                "profile_id": "test-profile",
                "map_name": "area42",
                "source_files": {
                    "area42.dir": {
                        "size": 5,
                        "sha256": hashlib.sha256(b"right").hexdigest(),
                    }
                },
                "reference_files": {},
            }
            fixture_path = root / "fixture.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

            report = verification_report(fixture_path, root)

            self.assertFalse(report["verified"])
            self.assertFalse(report["source"][0]["ok"])


if __name__ == "__main__":
    unittest.main()

