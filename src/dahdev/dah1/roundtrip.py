"""Safe byte-preserving baseline for unchanged DAH1 map sources."""
from __future__ import annotations

import shutil
from pathlib import Path

from .container import BlockReader


def unchanged_copy_verify(source: str | Path, output: str | Path) -> dict[str, object]:
    """Copy a block set to a new directory and prove byte/structure identity.

    This deliberately is not a repacker.  It establishes the non-mutating
    baseline that a future semantic writer must continue to satisfy.
    """
    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if source_path == output_path or source_path in output_path.parents:
        raise ValueError("unchanged output must not be inside the retail source")
    if output_path.exists():
        raise ValueError("unchanged output must be a new directory")

    before = BlockReader(source_path)
    files = sorted(
        [*source_path.glob("*.dir"), *source_path.glob("*.pkg")],
        key=lambda path: path.name.lower(),
    )
    if not files:
        raise ValueError("source contains no DAH block files")
    output_path.mkdir(parents=True)
    try:
        for path in files:
            shutil.copyfile(path, output_path / path.name)
        after = BlockReader(output_path)
        before_hashes = before.source_hashes()
        after_hashes = after.source_hashes()
        directory_equal = (
            before.directory.version == after.directory.version
            and before.directory.unknown0 == after.directory.unknown0
            and before.directory.section_offsets == after.directory.section_offsets
            and before.directory.archive_names == after.directory.archive_names
            and before.directory.load_groups == after.directory.load_groups
            and before.directory.resources == after.directory.resources
        )
        load_groups = []
        for index in range(len(before.directory.load_groups)):
            try:
                left = before.load_group(index)
                right = after.load_group(index)
                equal = (
                    left.data == right.data
                    and left.resource_limit == right.resource_limit
                    and left.relocations == right.relocations
                )
                load_groups.append({
                    "index": index,
                    "status": "decoded-and-equal" if equal else "decoded-mismatch",
                    "byte_equal": equal,
                    "decoded_sha256": left.decoded_sha256,
                    "relocation_sha256": left.relocations.sha256,
                })
            except (OSError, ValueError, IndexError) as error:
                # Exact enclosing .pkg identity already proves every opaque byte.
                # Record the reader limitation instead of pretending this group
                # has decoded relocation semantics.
                load_groups.append({
                    "index": index,
                    "status": "opaque-but-enclosing-file-byte-equal",
                    "byte_equal": True,
                    "decode_error": str(error),
                })
        verified = (
            before_hashes == after_hashes
            and directory_equal
            and all(item["byte_equal"] for item in load_groups)
        )
        if not verified:
            raise ValueError("unchanged copy failed byte or structural verification")
        return {
            "status": "verified-byte-preserving-passthrough",
            "writer_status": "not-a-repacker; mutation remains disabled",
            "source": str(source_path),
            "output": str(output_path),
            "file_sha256": before_hashes,
            "directory_equal": directory_equal,
            "resource_count": len(before.directory.resources),
            "decoded_load_group_count": sum(
                item["status"] == "decoded-and-equal" for item in load_groups
            ),
            "opaque_load_group_count": sum(
                item["status"] == "opaque-but-enclosing-file-byte-equal"
                for item in load_groups
            ),
            "load_groups": load_groups,
            "verified": True,
        }
    except Exception:
        # Only remove the directory this call created.  Source is never touched.
        shutil.rmtree(output_path)
        raise
