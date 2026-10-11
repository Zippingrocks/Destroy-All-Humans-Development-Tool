from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


PACKAGE_NAMES = ("resident.pkg", "stream.pkg", "discard.pkg")


@dataclass(frozen=True)
class SourceInspection:
    path: str
    container_family: str
    map_name: str | None
    dir_files: tuple[str, ...]
    present_packages: tuple[str, ...]
    missing_packages: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return len(self.dir_files) == 1 and not self.missing_packages

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["complete"] = self.complete
        return result


def inspect_source(path: str | Path) -> SourceInspection:
    root = Path(path)
    if not root.is_dir():
        raise NotADirectoryError(f"Map source is not a directory: {root}")

    files = {entry.name.lower(): entry.name for entry in root.iterdir() if entry.is_file()}
    dir_files = tuple(sorted(name for name in files.values() if name.lower().endswith(".dir")))
    present = tuple(name for name in PACKAGE_NAMES if name in files)
    missing = tuple(name for name in PACKAGE_NAMES if name not in files)
    warnings: list[str] = []

    if not dir_files:
        warnings.append("No .dir map index was found.")
    elif len(dir_files) > 1:
        warnings.append("More than one .dir file was found; map identity is ambiguous.")

    if missing:
        warnings.append("One or more expected package roles are absent.")

    map_name = Path(dir_files[0]).stem if len(dir_files) == 1 else None
    family = "zeroengine-dir-pkg" if dir_files or present else "unknown"

    return SourceInspection(
        path=str(root.resolve()),
        container_family=family,
        map_name=map_name,
        dir_files=dir_files,
        present_packages=present,
        missing_packages=missing,
        warnings=tuple(warnings),
    )

