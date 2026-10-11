from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileCheck:
    name: str
    path: str
    exists: bool
    expected_size: int | None
    actual_size: int | None
    expected_sha256: str
    actual_sha256: str | None

    @property
    def ok(self) -> bool:
        return (
            self.exists
            and (self.expected_size is None or self.actual_size == self.expected_size)
            and self.actual_sha256 == self.expected_sha256
        )

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["ok"] = self.ok
        return result


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def load_fixture(path: str | Path) -> dict[str, Any]:
    fixture_path = Path(path)
    with fixture_path.open("r", encoding="utf-8") as stream:
        fixture = json.load(stream)
    if fixture.get("schema_version") != 1:
        raise ValueError(f"Unsupported fixture schema: {fixture.get('schema_version')!r}")
    return fixture


def check_files(root: str | Path, expected: dict[str, dict[str, Any]]) -> list[FileCheck]:
    base = Path(root)
    checks: list[FileCheck] = []
    for name, metadata in expected.items():
        candidate = base / name
        exists = candidate.is_file()
        checks.append(
            FileCheck(
                name=name,
                path=str(candidate.resolve()),
                exists=exists,
                expected_size=metadata.get("size"),
                actual_size=candidate.stat().st_size if exists else None,
                expected_sha256=metadata["sha256"].lower(),
                actual_sha256=sha256_file(candidate) if exists else None,
            )
        )
    return checks


def verification_report(
    fixture_path: str | Path,
    source_root: str | Path,
    reference_root: str | Path | None = None,
) -> dict[str, object]:
    fixture = load_fixture(fixture_path)
    source_checks = check_files(source_root, fixture["source_files"])
    reference_checks = (
        check_files(reference_root, fixture.get("reference_files", {}))
        if reference_root is not None
        else []
    )
    all_checks = [*source_checks, *reference_checks]
    return {
        "fixture_id": fixture["fixture_id"],
        "profile_id": fixture["profile_id"],
        "map_name": fixture["map_name"],
        "verified": all(check.ok for check in all_checks),
        "source": [check.to_dict() for check in source_checks],
        "reference": [check.to_dict() for check in reference_checks],
        "reference_requested": reference_root is not None,
        "expected": fixture.get("expected", {}),
    }

