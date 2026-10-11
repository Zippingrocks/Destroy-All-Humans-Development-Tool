from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

from .compression import unwrap_block


DIR_MAGIC = 0x48BBC7FA
RELOCATION_MAGIC = 0x73934FB2


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class LoadGroupRecord:
    index: int
    unknown_or_id: int
    package_offset: int
    decompressed_size: int
    dependency_offset: int
    flags_or_state: int
    unknown0: int
    unknown1: int
    sentinel0: int
    sentinel1: int
    unknown2: int


@dataclass(frozen=True)
class ResourceRecord:
    index: int
    type_hash: int
    name_hash: int
    load_group: int
    offset: int
    size: int
    flags: int


@dataclass(frozen=True)
class BlockDirectory:
    path: Path
    version: int
    unknown0: int
    section_offsets: tuple[int, ...]
    archive_names: tuple[str, ...]
    load_groups: tuple[LoadGroupRecord, ...]
    resources: tuple[ResourceRecord, ...]


@dataclass(frozen=True)
class RelocationTable:
    pointers: dict[int, int]
    sha256: str


@dataclass(frozen=True)
class LoadedGroup:
    index: int
    archive: str
    package_offset: int
    mode: str
    data: bytes
    resource_limit: int
    decoded_sha256: str
    relocations: RelocationTable
    relocation_location: str


def parse_block_directory(path: str | Path) -> BlockDirectory:
    source = Path(path)
    data = source.read_bytes()
    if len(data) < 56:
        raise ValueError(f"{source} is too small to be a DAH1 block directory")

    magic, version, archive_count, group_count, resource_count, unknown0 = (
        struct.unpack_from("<6I", data)
    )
    if magic != DIR_MAGIC:
        raise ValueError(f"unexpected .dir magic 0x{magic:08X}")

    sections = struct.unpack_from("<8I", data, 24)
    archive_offset, group_offset, resource_offset = sections[:3]

    def require_section(offset: int, count: int, stride: int, label: str) -> None:
        if offset < 56 or offset + 4 + count * stride > len(data):
            raise ValueError(f"{label} section is outside the .dir file")
        actual_count = struct.unpack_from("<I", data, offset)[0]
        if actual_count != count:
            raise ValueError(
                f"{label} count mismatch: header={count}, section={actual_count}"
            )

    require_section(archive_offset, archive_count, 260, "archive")
    require_section(group_offset, group_count, 40, "load-group")
    require_section(resource_offset, resource_count, 24, "resource")

    archives: list[str] = []
    for index in range(archive_count):
        start = archive_offset + 4 + index * 260
        raw_name = data[start : start + 260].split(b"\0", 1)[0]
        archives.append(raw_name.decode("ascii"))

    groups = tuple(
        LoadGroupRecord(index, *struct.unpack_from("<10I", data, group_offset + 4 + index * 40))
        for index in range(group_count)
    )
    resources = tuple(
        ResourceRecord(
            index,
            *struct.unpack_from("<6I", data, resource_offset + 4 + index * 24),
        )
        for index in range(resource_count)
    )

    identities = {(resource.type_hash, resource.name_hash) for resource in resources}
    if len(identities) != len(resources):
        raise ValueError("resource type/name identities are not unique")
    locations = {(resource.load_group, resource.offset) for resource in resources}
    if len(locations) != len(resources):
        raise ValueError("resource load-group/offset locations are not unique")

    return BlockDirectory(
        path=source,
        version=version,
        unknown0=unknown0,
        section_offsets=tuple(sections),
        archive_names=tuple(archives),
        load_groups=groups,
        resources=resources,
    )


def parse_relocation_table(table: bytes, data_limit: int) -> RelocationTable:
    if len(table) < 32:
        raise ValueError("relocation table is shorter than its fixed header")
    magic, count = struct.unpack_from("<II", table)
    if magic != RELOCATION_MAGIC:
        raise ValueError(f"unexpected relocation magic 0x{magic:08X}")
    if len(table) != 32 + count * 8:
        raise ValueError("relocation table length does not match its entry count")
    if table[8:32] != bytes(24):
        raise ValueError("relocation header reserved bytes are non-zero")

    pairs = [
        struct.unpack_from("<II", table, 32 + index * 8) for index in range(count)
    ]
    pointers = dict(pairs)
    if len(pointers) != count:
        raise ValueError("relocation table contains duplicate source pointer fields")
    for source, target in pairs:
        if source % 4 or source + 4 > data_limit:
            raise ValueError(f"relocation source 0x{source:X} is outside resource data")
        if target >= data_limit:
            raise ValueError(f"relocation target 0x{target:X} is outside resource data")

    return RelocationTable(pointers=pointers, sha256=_sha256(table))


class BlockReader:
    """Strict, read-only DAH1 retail Xbox block reader."""

    def __init__(self, block_dir: str | Path):
        self.block_dir = Path(block_dir)
        directories = sorted(self.block_dir.glob("*.dir"))
        if len(directories) != 1:
            raise ValueError(
                f"expected exactly one .dir in {self.block_dir}, found {len(directories)}"
            )
        self.directory = parse_block_directory(directories[0])
        self._packages: dict[str, bytes] = {}
        self._groups: dict[int, LoadedGroup] = {}
        self._by_identity = {
            (resource.type_hash, resource.name_hash): resource
            for resource in self.directory.resources
        }
        self._by_location = {
            (resource.load_group, resource.offset): resource
            for resource in self.directory.resources
        }

    def package_bytes(self, archive: str) -> bytes:
        key = archive.lower()
        if key not in self._packages:
            candidates = [
                path
                for path in self.block_dir.glob("*.pkg")
                if path.stem.lower() == key
            ]
            if len(candidates) != 1:
                raise FileNotFoundError(
                    f"expected one {archive}.pkg in {self.block_dir}, found {len(candidates)}"
                )
            self._packages[key] = candidates[0].read_bytes()
        return self._packages[key]

    def archive_for_group(self, index: int) -> str:
        if index < len(self.directory.archive_names):
            return self.directory.archive_names[index].lower()
        if "stream" in (name.lower() for name in self.directory.archive_names):
            return "stream"
        raise ValueError(f"load group {index} has no archive mapping")

    def load_group(self, index: int) -> LoadedGroup:
        if index in self._groups:
            return self._groups[index]
        if not 0 <= index < len(self.directory.load_groups):
            raise IndexError(f"invalid load-group index {index}")

        record = self.directory.load_groups[index]
        archive = self.archive_for_group(index)
        package = self.package_bytes(archive)
        offset = record.package_offset
        if offset + record.decompressed_size > len(package) and offset + 8 > len(package):
            raise ValueError(f"load group {index} starts outside {archive}.pkg")

        is_wrapped = False
        if offset + 8 <= len(package):
            declared, compressed = struct.unpack_from("<II", package, offset)
            is_wrapped = (
                declared == record.decompressed_size
                and 0 < compressed <= declared
                and offset + 8 + compressed <= len(package)
            )

        if is_wrapped:
            wrapped = unwrap_block(package, offset, record.decompressed_size)
            data = wrapped.data
            package_end = wrapped.end_offset
            mode = "compressed"
        else:
            package_end = offset + record.decompressed_size
            if package_end > len(package):
                raise ValueError(f"raw load group {index} extends beyond {archive}.pkg")
            data = package[offset:package_end]
            mode = "raw"

        if index == 0:
            relocation_offset = _align(package_end, 2048)
            relocation_block = unwrap_block(package, relocation_offset)
            resource_limit = len(data)
            relocation_bytes = relocation_block.data
            relocation_location = f"{archive}.pkg+0x{relocation_offset:X}"
        else:
            marker = struct.pack("<I", RELOCATION_MAGIC)
            candidates: list[int] = []
            cursor = 0
            while True:
                cursor = data.find(marker, cursor)
                if cursor < 0:
                    break
                if cursor + 32 <= len(data):
                    count = struct.unpack_from("<I", data, cursor + 4)[0]
                    if cursor + 32 + count * 8 == len(data):
                        candidates.append(cursor)
                cursor += 1
            resources = [
                resource
                for resource in self.directory.resources
                if resource.load_group == index
            ]
            exact_resource_span = bool(resources) and min(
                resource.offset for resource in resources
            ) == 0 and max(
                resource.offset + resource.size for resource in resources
            ) == len(data)
            if len(candidates) == 1:
                resource_limit = candidates[0]
                relocation_bytes = data[resource_limit:]
                relocation_location = f"loadgroup:{index}+0x{resource_limit:X}"
            elif not candidates and exact_resource_span:
                # Streamed texture groups commonly consist solely of one raw
                # resource payload. There is no pointer-bearing object graph and
                # therefore no terminal relocation table to parse.
                resource_limit = len(data)
                relocation_bytes = b""
                relocation_location = "none (resource payload fills load group)"
            else:
                raise ValueError(
                    f"load group {index} has {len(candidates)} terminal relocation tables"
                )

        relocations = (
            parse_relocation_table(relocation_bytes, resource_limit)
            if relocation_bytes
            else RelocationTable(pointers={}, sha256=_sha256(b""))
        )
        for pointer_offset in relocations.pointers:
            if data[pointer_offset : pointer_offset + 4] != bytes(4):
                raise ValueError(
                    f"unpatched pointer field 0x{pointer_offset:X} is non-zero"
                )

        loaded = LoadedGroup(
            index=index,
            archive=archive,
            package_offset=offset,
            mode=mode,
            data=data,
            resource_limit=resource_limit,
            decoded_sha256=_sha256(data),
            relocations=relocations,
            relocation_location=relocation_location,
        )
        self._groups[index] = loaded
        return loaded

    def resource_by_identity(self, type_hash: int, name_hash: int) -> ResourceRecord | None:
        return self._by_identity.get((type_hash, name_hash))

    def resource_by_location(self, load_group: int, offset: int) -> ResourceRecord | None:
        return self._by_location.get((load_group, offset))

    def resources_of_type(self, type_hash: int) -> tuple[ResourceRecord, ...]:
        return tuple(
            resource
            for resource in self.directory.resources
            if resource.type_hash == type_hash
        )

    def source_hashes(self) -> dict[str, str]:
        paths = [self.directory.path, *sorted(self.block_dir.glob("*.pkg"))]
        return {path.name: _sha256(path.read_bytes()) for path in paths}

    @property
    def loaded_groups(self) -> tuple[LoadedGroup, ...]:
        return tuple(sorted(self._groups.values(), key=lambda group: group.index))

