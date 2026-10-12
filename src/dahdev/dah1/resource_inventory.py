"""Deterministic coverage inventory for every resource family in a DAH1 block."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .container import BlockReader


# Names are only assigned where this repository has a concrete native reader.
# Unnamed hashes remain explicit; they are not reverse-engineered by guessing.
RESOURCE_FAMILIES = {
    0x59CE7BCB: ("world", "decoded"),
    0xE3554C44: ("scene-node", "decoded"),
    0x6FF6291D: ("fog", "decoded"),
    0x438B6A74: ("sun", "decoded"),
    0x958378DE: ("sky", "decoded"),
    0x42EF1FD7: ("lua-script", "partially-decoded"),
    0x7C6FF505: ("skin-visual", "decoded-for-preview"),
    0x72C7D314: ("skin-mesh", "decoded-for-preview"),
    0x9709513E: ("material", "decoded-for-preview"),
    0x3BE99284: ("texture", "decoded-for-preview"),
    0xA3C936AF: ("static-scatter", "decoded-for-preview"),
    0x43BBDAB4: ("static-scatter-payload", "decoded-for-preview"),
    0xD0894AB3: ("regions", "decoded"),
    0x7FDB970F: ("entity-tags", "decoded"),
    0xB31C6654: ("terrain-wrapper", "decoded-for-preview"),
    0x5EE233E1: ("terrain-geometry", "decoded-for-preview"),
    0xFE55FFAF: ("instance-database", "decoded"),
    0x1D9D48EC: ("actor-archetype", "partially-decoded"),
    0x6FC132F2: ("lod", "decoded-for-preview"),
    0x9805A0A6: ("static-visual", "decoded-for-preview"),
    0x2C84BEFF: ("segmented-visual", "decoded-for-preview"),
    0x5B99558D: ("static-mesh", "decoded-for-preview"),
    0x48C2FD5F: ("material-table", "decoded-for-preview"),
}


def resource_coverage_report(source: str | Path | BlockReader) -> dict[str, object]:
    """Account for every directory record without assigning speculative meaning."""
    reader = source if isinstance(source, BlockReader) else BlockReader(source)
    grouped: dict[int, list] = defaultdict(list)
    for record in reader.directory.resources:
        grouped[record.type_hash].append(record)

    families = []
    status_counts = Counter()
    for type_hash, records in sorted(grouped.items()):
        name, status = RESOURCE_FAMILIES.get(type_hash, (None, "preserved-unknown"))
        status_counts[status] += len(records)
        sizes = Counter(record.size for record in records)
        groups = Counter(record.load_group for record in records)
        families.append({
            "type_hash": f"0x{type_hash:08X}",
            "name": name,
            "coverage": status,
            "resource_count": len(records),
            "total_bytes": sum(record.size for record in records),
            "sizes": [{"bytes": size, "count": count} for size, count in sorted(sizes.items())],
            "load_groups": [{"index": index, "count": count} for index, count in sorted(groups.items())],
        })

    return {
        "status": "complete-directory-accounting",
        "source_sha256": reader.source_hashes(),
        "resource_count": len(reader.directory.resources),
        "resource_type_count": len(families),
        "coverage_by_resource": dict(sorted(status_counts.items())),
        "families": families,
        "important_limit": (
            "A preserved-unknown family is byte-accounted but has no claimed semantics. "
            "Collision, navigation, destruction, audio and cinematic ownership remain "
            "unassigned until native relationships are proven."
        ),
    }
